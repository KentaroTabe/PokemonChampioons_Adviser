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
    banned: list = field(default_factory=list)        # 使わない (config/banned_species.txt + 依頼の追加)
    owned: list = field(default_factory=list)         # 使える候補を限定するとき (空なら 参戦種 − banned)
    banned_source: dict = field(default_factory=dict)  # 使わないリストの出どころ (file / sha256 / file_count / extra / unknown)
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


REPO = Path(__file__).resolve().parent.parent.parent
BANNED_FILE = REPO / "config" / "banned_species.txt"
POKEDEX_TS = REPO / "pokemon-showdown" / "data" / "pokedex.ts"
FORMATS_DATA_CURRENT = REPO / "pokemon-showdown" / "data" / "mods" / "champions" / "formats-data.ts"
FORMATS_DATA_PREVIOUS = REPO / "pokemon-showdown" / "data" / "mods" / "championsregmb" / "formats-data.ts"


def illegal_ids_from_text(text: str) -> set:
    """formats-data.ts の本文 → tier Illegal の種族 id 集合 (vision.normalize.champions_illegal_ids と同じ規則)。純粋"""
    out = set()
    for m in re.finditer(r"^\t(\w+): \{([^}]*)\}", text or "", flags=re.M):
        if '"Illegal"' in m.group(2):
            out.add(m.group(1))
    return out


def new_species_from_texts(current_text: str, previous_text: str) -> list:
    """前レギュレーションで Illegal、今のレギュレーションで合法になった id (昇順)。純粋"""
    return sorted(illegal_ids_from_text(previous_text) - illegal_ids_from_text(current_text))


def new_species_ids(current_path: Path = FORMATS_DATA_CURRENT, previous_path: Path = FORMATS_DATA_PREVIOUS) -> list:
    """今期に追加された基本種の id (champions mod と前レギュの凍結 mod の formats-data の差。メガフォルムと図鑑に無い id は除く)。
    どちらかが読めなければ空 (所持の補完をしない)"""
    try:
        cur = Path(current_path).read_text(encoding="utf-8")
        prev = Path(previous_path).read_text(encoding="utf-8")
    except OSError:
        return []
    ids = new_species_from_texts(cur, prev)
    try:
        from advisor.dex import get_dex
        from advisor.gimmick import is_mega_form
        dex = get_dex()
        return [s for s in ids if dex.species(s) and not is_mega_form(s)]
    except Exception:
        return ids


# ---- 使わないポケモン (config/banned_species.txt) ----
# 2026-09-25 ユーザー決定: 所持リストは持たず「使わないリスト」だけで管理する。提案される構築にはリストの種を使わない。
# 相手のパーティには適用しない (opponents.synthetic_pool は最新環境の全種から合成する)。


def parse_banned_text(text: str) -> list:
    """ファイル本文 → 名前の列 (1 行 1 体、# 以降は注記、空行は無視、重複は最初だけ)。純粋"""
    out = []
    for line in (text or "").splitlines():
        token = line.split("#", 1)[0].strip()
        if token and token not in out:
            out.append(token)
    return out


def resolve_banned(tokens: list, legal: Optional[set] = None) -> tuple:
    """名前の列 → (id の列, 解決できない名前の列)。日本語名は NameResolver で id に。
    legal (参戦種) を渡すと、参戦種に無い id (綴り違いなど) も「解決できない」に入れる。純粋 (名前解決を除く)"""
    ids, unknown = [], []
    for tok in tokens:
        sid = resolve_species_token(tok)
        ok = bool(sid) and re.fullmatch(r"[a-z0-9]+", sid) is not None and (legal is None or sid in legal)
        if not ok:
            unknown.append(tok)
        elif sid not in ids:
            ids.append(sid)
    return ids, unknown


def _sha256(text: str) -> str:
    import hashlib
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:12]


def read_banned_file(path: Path = BANNED_FILE, legal: Optional[set] = None) -> dict:
    """使わないポケモンのファイル → {"path", "exists", "tokens", "ids", "unknown", "sha256"}。無ければ空のリスト"""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
        exists = True
    except OSError:
        text, exists = "", False
    tokens = parse_banned_text(text)
    ids, unknown = resolve_banned(tokens, legal)
    return {"path": str(p), "exists": exists, "tokens": tokens, "ids": ids, "unknown": unknown, "sha256": _sha256(text)}


def _merge_banned(spec: "BuildSpec", info: dict) -> None:
    """ファイルの id を spec.banned の先頭に (依頼の指定は extra として後ろに残す)、明示の owned からも外す"""
    extra = [s for s in spec.banned if s not in info["ids"]]
    spec.banned = list(info["ids"]) + extra
    spec.banned_source = {"file": info["path"], "exists": info["exists"], "sha256": info["sha256"],
                          "file_count": len(info["ids"]), "unknown": list(info["unknown"]), "extra": extra}
    if spec.owned:
        spec.owned = [s for s in spec.owned if s not in set(spec.banned)]


def apply_banned_file(spec: "BuildSpec", path: Path = BANNED_FILE, legal: Optional[set] = None) -> dict:
    """読み込んだ spec (古い request.json など) にも使わないリストを常に効かせる。戻り値: 読み込み結果"""
    info = read_banned_file(path, legal)
    _merge_banned(spec, info)
    if spec.banned:
        spec.provenance["banned"] = "resolved"
    return info


def battle_only_ids_from_text(text: str) -> set:
    """Showdown の pokedex.ts の本文 → battleOnly (戦闘中だけのフォルム: ギルガルド ブレード等) の id 集合。純粋"""
    out = set()
    for m in re.finditer(r"^\t(\w+): \{(.*?)^\t\},", text or "", flags=re.M | re.S):
        if "battleOnly" in m.group(2):
            out.add(m.group(1))
    return out


def battle_only_ids(path: Path = POKEDEX_TS) -> set:
    try:
        return battle_only_ids_from_text(Path(path).read_text(encoding="utf-8"))
    except OSError:
        return set()


def selectable_species_ids(legal: Optional[set] = None) -> list:
    """選べる種 = 参戦種 − メガ後のフォルム − 戦闘中だけのフォルム (昇順)。legal 省略時は legal_species_ids()"""
    from advisor.gimmick import is_mega_form
    legal = set(legal) if legal is not None else legal_species_ids()
    bo = battle_only_ids()
    return sorted(s for s in legal if s not in bo and not is_mega_form(s))


def usable_species_ids(banned, legal: Optional[set] = None) -> list:
    """使える候補 = 選べる種 − 使わないリスト"""
    b = set(banned or ())
    return [s for s in selectable_species_ids(legal) if s not in b]


def banned_in_members(members, banned) -> list:
    """並び (id の列) に含まれる使わない種 (順序保持)。不変条件の検査に使う。純粋"""
    b = set(banned or ())
    return [m for m in (members or []) if m in b]


def legal_species_ids() -> set:
    """参戦種の id 集合 (champions_dex に載っていて Illegal でないもの)"""
    from advisor.dex import get_dex
    from vision.normalize import champions_illegal_ids
    dex = get_dex()
    illegal = champions_illegal_ids()
    return {sid for sid in dex.species_ids() if sid not in illegal} if hasattr(dex, "species_ids") \
        else set()


def parse_form(form: dict, owned: Optional[list] = None, banned_path: Path = BANNED_FILE,
               legal: Optional[set] = None) -> BuildSpec:
    """フロント/チャットの構造入力 → BuildSpec (明示された項目は resolved)。
    使わないポケモンは常に banned_path のファイルから読み、依頼の banned はその run だけの追加。
    owned (使える候補) は省略時 参戦種 − banned"""
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
    _merge_banned(spec, read_banned_file(banned_path, legal))
    if spec.banned:
        prov["banned"] = "resolved"
    if not spec.owned:
        spec.owned = list(owned) if owned is not None else usable_species_ids(spec.banned, legal)
        prov["owned"] = "inferred"
    spec.owned = [s for s in spec.owned if s not in set(spec.banned)]
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
        problems.append("使える種が空 (参戦種の読み込みに失敗したか、owned の指定が空)")
    if len(owned) < 6:
        problems.append(f"使える種が 6 体未満 ({len(owned)}): 6 体構築を組めない")
    unknown_banned = (spec.banned_source or {}).get("unknown") or []
    if unknown_banned:
        problems.append(f"使わないリストに解決できない名前: {unknown_banned} ({(spec.banned_source or {}).get('file')})")
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
