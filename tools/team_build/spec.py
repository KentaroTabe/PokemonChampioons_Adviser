"""BuildSpec (S0): 依頼の正規化。

フィールドごとに provenance (resolved = ユーザーの明示 / inferred = LLM や既定からの推測 / unknown) を持つ。
LLM で自由文を解析した場合も、そのまま実行せず validate_spec で schema と制約の整合を通す
(docs/TEAM_BUILDING_IMPLEMENTATION.md §9-1, §10)。

objective: max_wr (既定) / stable (E[WR] − λ·Risk) / easy (遵守モデル込み WR) / favorites (固定枠制約下の max_wr)
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from champions_agent.config import (
    BUILD_SCHEMA_VERSION, BUILD_USER_MODELS, TRAINING_BATTLE_FORMAT)

OBJECTIVES = ("max_wr", "stable", "easy", "favorites")
STYLES = ("any", "offense", "balance", "bulky_offense", "cycle", "setup", "speed_control",
          "anti_meta", "stall")
PROFILES = ("fast", "medium", "full")
PROVENANCE = ("resolved", "inferred", "unknown")


def _toid(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def resolve_species_token(token: str) -> str:
    """"kingambit" / "Kingambit" / "ドドゲザン" → showdown id。解決できなければ元の文字列 (検証で弾く)"""
    token = (token or "").strip()
    if not token:
        return ""
    sid = _toid(token)
    if sid:
        return sid
    try:
        from vision.normalize import NameResolver
        r = NameResolver().resolve_species(token, cutoff=0.85)
        if r:
            return r[1]
    except Exception:
        pass
    return token


@dataclass
class BuildSpec:
    schema_version: str = BUILD_SCHEMA_VERSION
    objective: str = "max_wr"
    favorites: list = field(default_factory=list)     # hard constraint (必ず入れる)
    locked: list = field(default_factory=list)        # favorites と同義の入力互換
    banned: list = field(default_factory=list)        # 使わない (未所持など)
    owned: list = field(default_factory=list)         # 所持 (空なら my_team.json から)
    style: str = "any"
    user_policy: str = "full"                         # easy のときの遵守モデル既定
    profile: str = "full"
    regulation: str = TRAINING_BATTLE_FORMAT
    notes: str = ""
    rules: list = field(default_factory=list)         # コンセプト規則 (tools/team_build/rules.RULES の名前、hard constraint)
    required_moves: dict = field(default_factory=dict)  # 技 + ポケモンの指定 {species_id: [move_id]}: その種を使う型に必ず入れる
    custom_sets: dict = field(default_factory=dict)     # 指定の型 {species_id: {item, ability, nature, evs, moves}}: 使用率より優先
    provenance: dict = field(default_factory=dict)    # field -> resolved | inferred | unknown

    def to_dict(self) -> dict:
        return asdict(self)


def resolve_move_token(token: str) -> str:
    """"psychicterrain" / "Psychic Terrain" / "サイコフィールド" → 技 id。解決できなければ id 化した文字列 (検証で弾く)"""
    token = (token or "").strip()
    if not token:
        return ""
    sid = _toid(token)
    if sid and re.fullmatch(r"[a-z0-9]+", sid) and not re.search(r"[^\x00-\x7f]", token):
        return sid
    try:
        from vision.normalize import NameResolver
        r = NameResolver().resolve(token, "moves", cutoff=0.85)
        if r:
            return str(r[1])
    except Exception:
        pass
    return sid or token


def parse_required_moves(value) -> dict:
    """"種:技/技, 種:技" (日本語可、区切りは , 、 改行 / ： :) または {種: [技]} → {species_id: [move_id]}。純粋 (名前解決を除く)"""
    if not value:
        return {}
    pairs = []
    if isinstance(value, dict):
        pairs = [(k, v if isinstance(v, (list, tuple)) else re.split(r"[/+・\s]+", str(v))) for k, v in value.items()]
    else:
        for entry in re.split(r"[,、\n]+", str(value)):
            if not entry.strip():
                continue
            sp, _, mv = re.sub("：", ":", entry).partition(":")
            pairs.append((sp, re.split(r"[/+・\s]+", mv)))
    out: dict = {}
    for sp, mvs in pairs:
        sid = resolve_species_token(sp)
        ids = [resolve_move_token(m) for m in mvs if str(m).strip()]
        ids = [m for m in ids if m]
        if sid and ids:
            out.setdefault(sid, [])
            out[sid].extend(m for m in ids if m not in out[sid])
    return out


def parse_custom_sets(value) -> dict:
    """Showdown 形式の型本文 (str) または {species_id: row} → {species_id: row (item/ability/nature/evs/moves/source)}"""
    if not value:
        return {}
    if isinstance(value, dict):
        return {resolve_species_token(k): dict(v) for k, v in value.items() if v}
    from tools.team_build.sets import parse_set_text
    return {sid: {"item": c.item, "ability": c.ability, "nature": c.nature, "evs": c.evs, "moves": list(c.moves),
                  "source": c.source} for sid, c in parse_set_text(str(value)).items()}


def owned_species_ids() -> list:
    """config/my_team.json の登録種 → showdown id (種族ID があればそれ、無ければ名前解決)"""
    from advisor.my_team import _load, registered_species_id
    from vision.normalize import NameResolver
    resolver = NameResolver()
    out = []
    for ja in _load().keys():
        sid = registered_species_id(ja)
        if not sid:
            r = resolver.resolve_species(ja, cutoff=0.9)
            sid = r[1] if r else None
        if sid and sid not in out:
            out.append(sid)
    return out


def legal_species_ids() -> set:
    """参戦種の id 集合 (champions_dex に載っていて Illegal でないもの)"""
    from advisor.dex import get_dex
    from vision.normalize import champions_illegal_ids
    dex = get_dex()
    illegal = champions_illegal_ids()
    return {sid for sid in dex.species_ids() if sid not in illegal} if hasattr(dex, "species_ids") \
        else set()


def parse_form(form: dict, owned: Optional[list] = None) -> BuildSpec:
    """フロント/チャットの構造入力 → BuildSpec (明示された項目は resolved)"""
    spec = BuildSpec()
    prov = {}
    for key in ("objective", "style", "user_policy", "profile", "regulation", "notes"):
        if form.get(key) not in (None, ""):
            setattr(spec, key, str(form[key]))
            prov[key] = "resolved"
    for key in ("favorites", "locked", "banned", "owned"):
        vals = form.get(key)
        if vals:
            if isinstance(vals, str):
                vals = [v for v in re.split(r"[,、\s/]+", vals) if v]
            setattr(spec, key, [resolve_species_token(v) for v in vals if resolve_species_token(v)])
            prov[key] = "resolved"
    rules = form.get("rules")
    if rules:
        if isinstance(rules, str):
            rules = [v for v in re.split(r"[,、\s/]+", rules) if v]
        spec.rules = [str(v).strip() for v in rules if str(v).strip()]
        prov["rules"] = "resolved"
    if form.get("moves"):
        spec.required_moves = parse_required_moves(form["moves"])
        prov["required_moves"] = "resolved"
    if form.get("sets"):
        spec.custom_sets = parse_custom_sets(form["sets"])
        prov["custom_sets"] = "resolved"
    if not spec.owned:
        spec.owned = list(owned) if owned is not None else owned_species_ids()
        prov["owned"] = "inferred"
    spec.favorites = sorted(set(spec.favorites) | set(spec.locked))
    spec.locked = list(spec.favorites)
    if spec.favorites and spec.objective == "max_wr" and "objective" not in prov:
        spec.objective, prov["objective"] = "favorites", "inferred"
    for key in ("objective", "style", "user_policy", "profile"):
        prov.setdefault(key, "inferred")
    spec.provenance = prov
    return spec


def validate_spec(spec: BuildSpec, legal: Optional[set] = None) -> list:
    """schema と制約の整合を検査し、問題の一覧 (空なら OK) を返す"""
    problems = []
    if spec.objective not in OBJECTIVES:
        problems.append(f"objective が不正: {spec.objective}")
    if spec.style not in STYLES:
        problems.append(f"style が不正: {spec.style}")
    if spec.user_policy not in BUILD_USER_MODELS:
        problems.append(f"user_policy が不正: {spec.user_policy}")
    if spec.profile not in PROFILES:
        problems.append(f"profile が不正: {spec.profile}")
    owned = set(spec.owned)
    if not owned:
        problems.append("所持ポケモンが空 (config/my_team.json に登録するか owned を指定)")
    if len(owned) < 6:
        problems.append(f"所持が 6 体未満 ({len(owned)}): 6 体構築を組めない")
    for sid in spec.favorites:
        if sid not in owned:
            problems.append(f"固定枠 {sid} が所持にない")
        if sid in spec.banned:
            problems.append(f"固定枠 {sid} が除外にも入っている")
    if len(spec.favorites) > 6:
        problems.append("固定枠が 6 体を超えている")
    if legal:
        for sid in list(spec.favorites) + list(spec.banned):
            if sid not in legal:
                problems.append(f"{sid} は図鑑に無いか現在のレギュレーションで使用不可")
        unknown_owned = [sid for sid in owned if sid not in legal]
        if unknown_owned:
            problems.append(f"所持に未知/使用不可の種: {unknown_owned[:5]}")
    if spec.objective == "favorites" and not spec.favorites:
        problems.append("objective=favorites だが固定枠が無い")
    if spec.rules:
        from tools.team_build.rules import RULES
        for r in spec.rules:
            if r not in RULES:
                problems.append(f"rules に未知の規則: {r} (定義済み: {sorted(RULES)})")
    if spec.required_moves or spec.custom_sets:
        try:
            from advisor.dex import get_dex
            dex = get_dex()
        except Exception:
            dex = None
        for sid, mvs in spec.required_moves.items():
            if sid not in owned:
                problems.append(f"技指定の {sid} が所持にない")
            for m in mvs:
                if dex is not None and dex.move(m) is None:
                    problems.append(f"技指定 {sid}:{m} は図鑑に無い技")
        for sid, row in spec.custom_sets.items():
            if sid not in owned:
                problems.append(f"型指定の {sid} が所持にない")
            if not row.get("moves"):
                problems.append(f"型指定の {sid} に技が無い")
            for m in row.get("moves") or []:
                if dex is not None and dex.move(m) is None:
                    problems.append(f"型指定 {sid}:{m} は図鑑に無い技")
    return problems


def save_spec(spec: BuildSpec, run_dir: Path) -> Path:
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    p = run_dir / "request.json"
    p.write_text(json.dumps(spec.to_dict(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return p


def load_spec(path: Path) -> BuildSpec:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    spec = BuildSpec(**{k: v for k, v in d.items() if k in BuildSpec.__dataclass_fields__})
    return spec
