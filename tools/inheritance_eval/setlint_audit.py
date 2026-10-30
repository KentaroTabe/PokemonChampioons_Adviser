"""I-05 の型検査 (引き継ぎ評価 段階 A、2026-10-31 開発者指示。docs/INHERITANCE_EVALUATION_PLAN_20261031.md §5 I-05 の後半)。

固定した合法セット集に、旧 lint (tools/team_build/set_lint の lint_set / gate_rejects) と自動修理 (repair_candidate。
生成の門 sets.lint_gate / role_sets / gen_sets / joint_stage が使うもの) を当て、拒否・変更された型と理由、形態・制約の破壊を
全件列挙する。合法性の参照はローカル Showdown の validate-team (サーバーは起動しない)。lint や修理は直さない (記録だけ)。

セット集 (cases.jsonl に出典を付ける):
  registered  config/my_team.json の登録 (本人の型)。名前の変換は vision.normalize.NameResolver (変換の正しさは I-01 の範囲)
  run_final   logs/build_search/runs/*/final/team.json と final/alternatives/*/team.json (構築 run の勝者・最終候補。読み取りだけ)
  smogon      Smogon chaos (logs/usage_smogon/<月>/) の種ごとに 持ち物 1 位 × 特性 1 位 × 技 上位 4 × 配分 1 位 を機械的に組んだ型。
              周辺分布の積なので実在する型ではない。指標は合法なものだけに数え、不合法だった数は別に残す
  edge        tests/fixtures/inheritance/set_edge_cases.jsonl (手で作る境界例。期待する合法性は validate-team で確かめて記入)

1 体ずつ検査するので、チーム単位の誤り (6 体未満) は INH_SETLINT_IGNORED_ERRORS で捨てる。構築 run の最終候補はチーム全体でも検査する。

    python -m tools.inheritance_eval.setlint_audit --out <本体>/logs/inheritance_eval/phaseA_20261031/I-05
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import subprocess
import time
from collections import Counter
from pathlib import Path
from typing import Callable, Optional

from champions_agent.config import (INH_SETLINT_FORMAT, INH_SETLINT_IGNORED_ERRORS, INH_SETLINT_SMOGON_TOP,
                                    INH_SETLINT_TIME_BUDGET_SEC, INH_SETLINT_WORKERS)

REPO = Path(__file__).resolve().parents[2]
EDGE_FIXTURE = REPO / "tests" / "fixtures" / "inheritance" / "set_edge_cases.jsonl"
TEST_ID = "I-05"
RUN_ID = "phaseA_20261031"
STAT_ORDER = ("hp", "atk", "def", "spa", "spd", "spe")
REG_STAT_KEYS = {"h": "hp", "a": "atk", "b": "def", "c": "spa", "d": "spd", "s": "spe",
                 "hp": "hp", "atk": "atk", "def": "def", "spa": "spa", "spd": "spd", "spe": "spe"}
CATEGORY = {"registered": "観測事実", "run_final": "生成物", "smogon": "合成結果", "edge": "合成結果"}
SPLIT = {"registered": "diag", "run_final": "diag", "smogon": "diag", "edge": "dev"}
# 修理の理由 (repairs のコード) → 説明できる項目。ここに無い項目 (種・特性・配分) を修理が変えたら「黙った変更」
EXPLAINS = {"nature": lambda r: r == "nature_move",
            "item": lambda r: r.startswith("item:") and r != "item:acrobatics->move",
            "moves": lambda r: r in ("field_dup", "few_moves", "item:acrobatics->move")}
SET_FIELDS = ("species", "ability", "item", "nature", "evs", "moves")


# ------------------------------------------------------------------ 純粋な部品
def points_str(points: Optional[dict]) -> Optional[str]:
    """{"h": 32, "b": 20} / {"hp": 32, ...} → "32/0/20/0/0/0" (能力ポイント)。空なら None"""
    pts = {k: 0 for k in STAT_ORDER}
    for k, v in (points or {}).items():
        key = REG_STAT_KEYS.get(str(k).lower())
        if key:
            pts[key] = int(v or 0)
    return "/".join(str(pts[k]) for k in STAT_ORDER) if any(pts.values()) else None


def filter_validator_errors(errors: list, ignored_prefixes=INH_SETLINT_IGNORED_ERRORS) -> list:
    """validate-team の誤りから、1 体ずつの検査では意味の無いチーム単位の誤りを捨てる"""
    return [e for e in errors if not any(str(e).startswith(p) for p in ignored_prefixes)]


def set_view(s: dict) -> dict:
    """比較用の型 (順序つきの技、None と空の区別をしない)"""
    return {"species": s.get("species"), "ability": s.get("ability") or None, "item": s.get("item") or None,
            "nature": (s.get("nature") or None) and str(s["nature"]).lower(), "evs": s.get("evs") or None,
            "moves": [m for m in (s.get("moves") or []) if m]}


def explain_changes(before: dict, after: dict, repairs: list) -> dict:
    """修理の前後の差 → {"changed": [項目], "unexplained": [項目]} (純粋)。
    unexplained = 変わったのに、その項目を説明する修理の理由 (EXPLAINS) が repairs に無い項目 (= 黙った変更)"""
    b, a = set_view(before), set_view(after)
    changed = [f for f in SET_FIELDS if b[f] != a[f]]
    unexplained = [f for f in changed if not any(EXPLAINS.get(f, lambda r: False)(r) for r in repairs or [])]
    return {"changed": changed, "unexplained": unexplained}


def form_breaks(before: dict, after: dict, is_mega_stone: Callable[[Optional[str]], bool]) -> list:
    """修理が壊した形態・制約 (純粋): 種 (性別形態・フォルム) の変更、メガ石を外した / 別の石に替えた、石を持たせた"""
    out = []
    if before.get("species") != after.get("species"):
        out.append("species_changed")
    bi, ai = before.get("item"), after.get("item")
    if is_mega_stone(bi) and bi != ai:
        out.append("mega_stone_removed" if not is_mega_stone(ai) else "mega_stone_swapped")
    if not is_mega_stone(bi) and is_mega_stone(ai):
        out.append("mega_stone_added")
    return out


def gate_outcome(lint_errors: list, repaired: Optional[dict]) -> str:
    """生成の門の結果: 誤りなし → pass、直せた → repaired、直せない → rejected (BUILD_SET_LINT_GATE が真のとき)"""
    if not lint_errors:
        return "pass"
    return "repaired" if repaired is not None else "rejected"


def illegal_passed(legal: Optional[bool], outcome: str, repaired_legal: Optional[bool]) -> Optional[bool]:
    """ルール上不合法な型を門が通したか。合法性が不明なら None。
    不合法で誤りなし (pass)、または直した結果も不合法 (repaired かつ repaired_legal False) なら通した"""
    if legal is None:
        return None
    if legal:
        return False
    if outcome == "pass":
        return True
    if outcome == "repaired":
        return None if repaired_legal is None else (not repaired_legal)
    return False


def remaining_illegal_elements(s: dict, illegal_elements: Optional[dict]) -> list:
    """境界例の不合法の原因 (fixture の illegal_elements) が型 s に残っているか (純粋)。残っている原因の列。
    item / move / ability / species は値の一致、duplicate_moves は技の重複、evs_total は能力ポイントの合計 > BUILD_GEN_POINT_BUDGET"""
    out = []
    for k, v in (illegal_elements or {}).items():
        if k == "item" and (s.get("item") or None) == v:
            out.append(k)
        elif k == "move" and v in (s.get("moves") or []):
            out.append(k)
        elif k == "ability" and s.get("ability") == v:
            out.append(k)
        elif k == "species" and s.get("species") == v:
            out.append(k)
        elif k == "duplicate_moves" and len(set(s.get("moves") or [])) < len(s.get("moves") or []):
            out.append(k)
        elif k == "evs_total":
            from champions_agent.config import BUILD_GEN_POINT_BUDGET
            from champions_agent.data.build_meta import parse_points
            pts = parse_points(s.get("evs")) or {}
            if sum(pts.values()) > BUILD_GEN_POINT_BUDGET:
                out.append(k)
    return out


def chaos_top_set(name: str, entry: dict, spread_unit: str, canonical: Callable[[str], str],
                  is_known: Callable[[str], bool]) -> dict:
    """chaos の 1 種 → 持ち物 1 位 × 特性 1 位 × 技 上位 4 × 配分 1 位 の型 (純粋。周辺分布の積で、実在する型ではない)。
    メガの項目は基本種 + その石 (特性欄は chaos のとおりメガ後の特性になる)"""
    from advisor.usage_smogon import EMPTY_KEYS, NO_ITEM_KEYS, mega_base_id, parse_spread, species_project_id

    def top(d: dict, skip=()) -> Optional[str]:
        rows = sorted(((k, float(v)) for k, v in (d or {}).items() if k not in skip), key=lambda x: (-x[1], x[0]))
        return rows[0][0] if rows else None
    sid = species_project_id(name, canonical)
    base = mega_base_id(sid, is_known)
    moves = [k for k, _v in sorted(((k, float(v)) for k, v in (entry.get("Moves") or {}).items() if k not in EMPTY_KEYS),
                                   key=lambda x: (-x[1], x[0]))[:4]]
    spread_key = top(entry.get("Spreads") or {})
    sp = parse_spread(spread_key, spread_unit) if spread_key else None
    return {"species": base or sid, "ability": top(entry.get("Abilities") or {}), "item": top(entry.get("Items") or {}, NO_ITEM_KEYS),
            "nature": (sp or {}).get("nature"), "evs": points_str((sp or {}).get("points")), "moves": moves,
            "chaos_name": name, "mega_entry": base is not None}


def registered_set(name_ja: str, entry: dict, resolve: Callable[[str, str], Optional[str]],
                   resolve_species: Callable[[str], Optional[str]], nature_en: Callable[[str], Optional[str]]) -> tuple:
    """my_team.json の 1 項目 → (型, None) または (None, 除外の理由)。resolve(text, category) は名前 → id"""
    if not entry.get("技") or not entry.get("能力ポイント"):
        return None, "登録が不完全 (技または能力ポイントなし)"
    sid = entry.get("種族ID") or resolve_species(name_ja)
    if not sid:
        return None, f"種の名前を変換できない: {name_ja}"
    unresolved = []
    item = resolve(entry["持ち物"], "items") if entry.get("持ち物") else None
    if entry.get("持ち物") and not item:
        unresolved.append(("持ち物", entry["持ち物"]))
    ability = resolve(entry["特性"], "abilities") if entry.get("特性") else None
    if entry.get("特性") and not ability:
        unresolved.append(("特性", entry["特性"]))
    moves = []
    for mj in entry.get("技") or []:
        m = resolve(mj, "moves")
        if not m:
            unresolved.append(("技", mj))
        else:
            moves.append(m)
    nat = nature_en(entry["性格"]) if entry.get("性格") else None
    if entry.get("性格") and not nat:
        unresolved.append(("性格", entry["性格"]))
    if unresolved:
        return None, "名前を変換できない: " + ", ".join(f"{k}={v}" for k, v in unresolved)
    return {"species": str(sid).lower(), "ability": ability, "item": item, "nature": (nat or "").lower() or None,
            "evs": points_str(entry.get("能力ポイント")), "moves": moves}, None


def team_text_sets(text: str) -> list:
    """Showdown 本文 → [型] (tools/team_build/sets.parse_set_text。EVs 行は能力ポイント)"""
    from tools.team_build.sets import parse_set_text
    return [{"species": c.species_id, "ability": c.ability, "item": c.item, "nature": c.nature, "evs": c.evs,
             "moves": list(c.moves)} for c in parse_set_text(text).values()]


def candidate_of(s: dict, source: str = "inh_audit"):
    from tools.team_build.sets import SetCandidate
    return SetCandidate(s["species"], s.get("ability"), s.get("item"), s.get("nature"), s.get("evs"), list(s.get("moves") or []),
                        source)


def set_of(c) -> dict:
    return {"species": c.species_id, "ability": c.ability, "item": c.item, "nature": c.nature, "evs": c.evs,
            "moves": list(c.moves or [])}


def showdown_text(s: dict) -> str:
    from tools.team_build.sets import to_showdown_text
    return to_showdown_text([candidate_of(s)])


def mega_ability_dispute(s: dict, legal_abilities: Callable[[str], Optional[set]], is_mega_stone) -> bool:
    """メガ石を持つ型の特性欄に、基本種の特性に無いもの (メガ後の特性) が入っているか。
    Showdown の validate-team はメガ石を持つと メガ後の特性も通すが、実機の登録では基本種の特性しか選べない (争点)"""
    if not is_mega_stone(s.get("item")) or not s.get("ability"):
        return False
    legal = legal_abilities(s["species"])
    return bool(legal) and s["ability"] not in legal


# ------------------------------------------------------------------ 副作用 (git / Showdown / ファイル)
def main_repo_root() -> Path:
    """ワークツリーから本体の作業ツリーの根 (git の共通 dir の親)"""
    try:
        out = subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=str(REPO),
                             capture_output=True, text=True, timeout=10).stdout.strip()
        if out:
            return Path(out).parent
    except Exception:
        pass
    return REPO


def git_head(path: Path) -> Optional[str]:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(path), capture_output=True, text=True,
                              timeout=10).stdout.strip() or None
    except Exception:
        return None


def sha256_file(path: Path) -> Optional[str]:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except Exception:
        return None


def validate_many(texts: list, fmt: str, showdown_dir: Path, workers: int) -> list:
    """[本文] → [(legal or None, errors)]。legal None = 検査そのものが動かなかった (VALIDATE_RUN_FAILED)"""
    from tools.team_build.sets import VALIDATE_RUN_FAILED, validate_team_texts
    out = []
    for ok, errs in validate_team_texts(texts, fmt, workers=workers, showdown_dir=showdown_dir):
        if errs and str(errs[0]).startswith(VALIDATE_RUN_FAILED):
            out.append((None, errs))
            continue
        kept = filter_validator_errors(errs)
        out.append((ok or not kept, kept))
    return out


def load_edge_cases(path: Path = EDGE_FIXTURE) -> list:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def collect_cases(main_root: Path, smogon_path: Path, smogon_top: Optional[int]) -> tuple:
    """セット集 → ([case], 除外の行, 読んだデータの記録, チーム単位の検査対象 [(case_id, 本文, 出典)])"""
    cases, excluded, data_read, teams = [], [], {}, []
    seen: dict = {}

    def add(source: str, case_id: str, s: dict, origin: dict, extra: Optional[dict] = None):
        key = (source, json.dumps(set_view(s), sort_keys=True))
        if key in seen:
            seen[key]["origin"].setdefault("also", []).append(origin)
            return
        c = {"case_id": case_id, "source": source, "set": s, "origin": origin, **(extra or {})}
        seen[key] = c
        cases.append(c)

    # 1. 登録
    reg_path = main_root / "config" / "my_team.json"
    data_read["my_team"] = {"path": str(reg_path), "sha256": sha256_file(reg_path)}
    from tools.evaluate_team import nature_en_of
    from vision.normalize import NameResolver
    resolver = NameResolver()

    def resolve(text: str, cat: str) -> Optional[str]:
        r = resolver.resolve(text, cat, cutoff=0.85)
        return str(r[1]) if r else None

    def resolve_species(text: str) -> Optional[str]:
        r = resolver.resolve_species(text, cutoff=0.85)
        return str(r[1]) if r else None
    reg = json.loads(reg_path.read_text(encoding="utf-8"))
    for i, (ja, entry) in enumerate(reg.items()):
        cid = f"reg:{i:02d}:{ja}"
        s, why = registered_set(ja, entry, resolve, resolve_species, nature_en_of)
        if s is None:
            excluded.append({"case_id": cid, "source": "registered", "input": {"name_ja": ja, "entry": entry}, "reason": why})
            continue
        add("registered", cid, s, {"file": "config/my_team.json", "name_ja": ja})
    # 2. 構築 run の最終候補 (勝者 + alternatives)
    runs = main_root / "logs" / "build_search" / "runs"
    finals = sorted(runs.glob("*/final/team.json")) + sorted(runs.glob("*/final/alternatives/*/team.json"))
    data_read["run_finals"] = []
    for p in finals:
        rel = str(p.relative_to(main_root))
        data_read["run_finals"].append({"path": rel, "sha256": sha256_file(p)})
        t = json.loads(p.read_text(encoding="utf-8"))
        run = p.relative_to(runs).parts[0]
        cand = t.get("candidate_id")
        teams.append((f"team:{run}:{cand}", t.get("text") or "", {"file": rel, "candidate_id": cand}))
        for s in team_text_sets(t.get("text") or ""):
            add("run_final", f"run:{run}:{cand}:{s['species']}", s, {"file": rel, "run": run, "candidate_id": cand})
    # 3. Smogon chaos の上位
    data_read["smogon"] = {"path": str(smogon_path), "sha256": sha256_file(smogon_path)}
    man = smogon_path.with_name(smogon_path.name.replace(".json", ".manifest.json"))
    if man.exists():
        data_read["smogon"]["manifest"] = json.loads(man.read_text(encoding="utf-8"))
    payload = json.loads(smogon_path.read_text(encoding="utf-8"))
    from advisor.usage_smogon import detect_spread_unit, legal_vocab
    vocab = legal_vocab()
    data = payload.get("data") or {}
    unit = detect_spread_unit(data)
    ranked = sorted(data, key=lambda k: (-float(data[k].get("usage") or 0), k))
    top_n = len(ranked) if smogon_top is None else min(int(smogon_top), len(ranked))
    data_read["smogon"].update({"n_species": len(ranked), "n_used": top_n, "spread_unit": unit})
    for rank, name in enumerate(ranked):
        cid = f"smogon:{rank + 1:03d}:{name}"
        if rank >= top_n:
            excluded.append({"case_id": cid, "source": "smogon", "input": {"chaos_name": name},
                             "reason": f"使用率の上位 {smogon_top} 種で切った (時間の上限)", "result": "unexecuted"})
            continue
        s = chaos_top_set(name, data[name], unit, vocab["canonical"], vocab["is_known"])
        add("smogon", cid, s, {"file": str(smogon_path.name), "chaos_name": name, "usage_rank": rank + 1,
                               "usage": data[name].get("usage")})
    # 4. 境界例
    data_read["edge_fixture"] = {"path": str(EDGE_FIXTURE.relative_to(REPO)), "sha256": sha256_file(EDGE_FIXTURE)}
    for e in load_edge_cases():
        add("edge", f"edge:{e['case_id']}", dict(e["set"]), {"file": str(EDGE_FIXTURE.relative_to(REPO)), "intent": e.get("intent")},
            {"edge": e})
    return cases, excluded, data_read, teams


def usage_moves_index(smogon_path: Path) -> dict:
    """種 (メガは基本種にまとめる) → 技の重みの降順 (修理の補充に渡す使用率の技。生成の門は使用率 DB の技を渡す)"""
    from advisor.usage_smogon import EMPTY_KEYS, legal_vocab, mega_base_id, species_project_id
    v = legal_vocab()
    data = (json.loads(smogon_path.read_text(encoding="utf-8")).get("data") or {})
    acc: dict = {}
    for name, e in data.items():
        sid = species_project_id(name, v["canonical"])
        g = mega_base_id(sid, v["is_known"]) or sid
        for m, w in (e.get("Moves") or {}).items():
            if m not in EMPTY_KEYS:
                acc.setdefault(g, Counter())[m] += float(w)
    return {g: [m for m, _ in cnt.most_common()] for g, cnt in acc.items()}


# ------------------------------------------------------------------ 実行
def audit(out_dir: Path, showdown_dir: Path, smogon_path: Path, fmt: str = INH_SETLINT_FORMAT,
          smogon_top: Optional[int] = INH_SETLINT_SMOGON_TOP, workers: int = INH_SETLINT_WORKERS,
          budget_sec: float = INH_SETLINT_TIME_BUDGET_SEC) -> dict:
    t0 = time.time()
    main_root = main_repo_root()
    from tools.team_build import set_lint as L
    from tools.team_build.sets import legal_item, lint_gate
    from vision.abilities import _load_forms
    info = L.default_info()
    forms = _load_forms()
    cases, excluded, data_read, teams = collect_cases(main_root, smogon_path, smogon_top)
    usage_idx = usage_moves_index(smogon_path)
    L.rejects_snapshot(reset=True)

    # 旧 lint / 門 / 修理 (先に全件。修理の出力も合法性を見るので validate は後でまとめて)
    for c in cases:
        ts = time.time()
        s = c["set"]
        cand = candidate_of(s)
        res = L.lint_set(s["species"], s.get("ability"), s.get("item"), s.get("nature"), list(s.get("moves") or []), info=info)
        gate = L.gate_rejects(cand, info=info, source=f"inh:{c['source']}")
        um = (c.get("edge") or {}).get("usage_moves") or usage_idx.get(s["species"]) or []
        fixed = L.repair_candidate(cand, info=info, usage_moves=list(um), legal_item=legal_item) if res["errors"] else None
        custom = candidate_of(s, "custom")
        gated_custom = lint_gate([custom], source="inh_custom", usage_moves=list(um))
        c["lint"] = {"errors": res["errors"], "warnings": res["warnings"], "detail": _jsonable(res["detail"]),
                     "gate_rejects": gate is not None}
        c["outcome"] = gate_outcome(res["errors"], fixed)
        c["repaired"] = set_of(fixed) if fixed is not None else None
        c["repair_notes"] = list(fixed.notes) if fixed is not None else []
        c["repairs"] = next((n.split(":", 1)[1].split(";") for n in c["repair_notes"] if n.startswith("lint_repair:")), [])
        c["custom_unchanged"] = bool(len(gated_custom) == 1 and gated_custom[0] is custom and set_of(custom) == s_view(s))
        c["usage_moves_n"] = len(um)
        c["lint_sec"] = time.time() - ts
    blocked = L.repairs_snapshot()["blocked"]
    rejects_by_code = L.rejects_snapshot(reset=True)

    # 合法性の参照 (validate-team): 入力と修理の出力
    texts, idx = [], []
    for i, c in enumerate(cases):
        texts.append(showdown_text(c["set"]))
        idx.append((i, "input"))
        if c["repaired"] is not None:
            texts.append(showdown_text(c["repaired"]))
            idx.append((i, "repaired"))
    team_texts = [t for _cid, t, _o in teams]
    remaining = budget_sec - (time.time() - t0)
    tv = time.time()
    if remaining <= 0:
        results = [(None, ["時間の上限で未実施"])] * len(texts)
        team_results = [(None, ["時間の上限で未実施"])] * len(team_texts)
    else:
        results = validate_many(texts, fmt, showdown_dir, workers)
        # チーム全体 (アイテムクローズ・種族の重複等) は捨てる誤りなしで検査する
        from tools.team_build.sets import validate_team_texts
        team_results = [(ok if not (errs and "実行失敗" in str(errs[0])) else None, errs)
                        for ok, errs in validate_team_texts(team_texts, fmt, workers=workers, showdown_dir=showdown_dir)]
    validate_sec = time.time() - tv
    per_validate = validate_sec / max(1, len(texts) + len(team_texts))
    for (i, kind), (ok, errs) in zip(idx, results):
        cases[i][f"legal_{kind}"] = ok
        cases[i][f"validator_{kind}"] = errs
    for c in cases:
        c["text"] = showdown_text(c["set"])
        c["dispute_mega_ability"] = mega_ability_dispute(c["set"], lambda sp: forms.get(sp), L.is_mega_stone)
        c["dispute_mega_ability_repaired"] = bool(c["repaired"]) and mega_ability_dispute(c["repaired"], lambda sp: forms.get(sp),
                                                                                          L.is_mega_stone)

    meas, case_rows = [], []
    for c in cases:
        src = c["source"]
        edge = c.get("edge") or {}
        legal = c.get("legal_input")
        rlegal = c.get("legal_repaired")
        expected_src = f"Showdown validate-team {fmt} @ {git_head(showdown_dir) or '?'}" + \
                       (" (fixture 作成時に記入した期待値と照合)" if src == "edge" else "")
        case_rows.append({"test_id": TEST_ID, "case_id": c["case_id"], "source": src, "category": CATEGORY[src], "split": SPLIT[src],
                          "input": c["set"], "showdown_text": c["text"], "origin": c["origin"],
                          "public_boundary": "型そのもの (公開・非公開の区別なし。合法性と常識規則の検査だけ)",
                          "expected": {"legal": edge.get("expected_legal") if src == "edge" else legal,
                                       "illegal_elements": edge.get("illegal_elements"),
                                       "requirement": ["不合法な型を通さない", "指定した型を黙って変えない", "修理で形態・制約を壊さない"]},
                          "expected_source": expected_src})
        base = {"test_id": TEST_ID, "case_id": c["case_id"], "category": CATEGORY[src], "input": c["set"],
                "expected_source": expected_src, "split": SPLIT[src]}
        el = c["lint_sec"] + per_validate * (2 if c["repaired"] else 1)
        # (1) 門 (lint + gate_rejects + 修理) が不合法を通したか
        ip = illegal_passed(legal, c["outcome"], rlegal)
        if legal is None:
            r1, why1 = "unexecuted", "合法性の参照が得られない (validate-team 未実施 / 実行失敗)"
        elif ip is None:
            r1, why1 = "unexecuted", "修理の出力の合法性が得られない"
        elif ip:
            r1 = "fail"
            why1 = ("不合法なのに旧 lint の誤りなし (門を通る)" if c["outcome"] == "pass" else "修理の出力も不合法 (門を通る)") + \
                   ": " + "; ".join(c.get("validator_input") or [])
        else:
            r1 = "pass"
            why1 = {"pass": "合法・誤りなし", "repaired": "誤りを修理 (修理の出力は合法)" if legal else "不合法を修理して合法化",
                    "rejected": "誤りで門が落とす"}[c["outcome"]] if legal else \
                ("不合法を門が落とす (理由は常識規則: " + "+".join(c["lint"]["errors"]) + ")" if c["outcome"] == "rejected"
                 else "不合法を修理して合法化")
        if src == "smogon" and legal is False:
            r1, why1 = "excluded", "合成した型が不合法 (周辺分布の積。合法な型だけを指標に使う): " + "; ".join(c.get("validator_input") or [])
        if src == "edge" and legal is not None and edge.get("expected_legal") is not None and bool(edge["expected_legal"]) != bool(legal):
            why1 += f" / fixture の期待 (expected_legal={edge['expected_legal']}) と validate-team の結果が違う"
        observed = {"legal": legal, "validator": c.get("validator_input"), "lint_errors": c["lint"]["errors"],
                    "lint_warnings": c["lint"]["warnings"], "lint_detail": c["lint"]["detail"], "outcome": c["outcome"],
                    "repaired": c["repaired"], "repairs": c["repairs"], "repaired_legal": rlegal,
                    "repaired_validator": c.get("validator_repaired"), "dispute_mega_ability": c["dispute_mega_ability"]}
        meas.append({**base, "asset": "set_lint.gate (lint_set + gate_rejects + repair_candidate)",
                     "expected": {"legal": legal, "requirement": "ルール上不合法な型を通さない (0 件)"},
                     "result": r1, "reason": why1, "elapsed_sec": round(el, 4), "observed": observed})
        # (2) 修理が黙って変えたか・形態を壊したか
        if c["repaired"] is None:
            r2, why2, ch = "excluded", ("修理なし (誤りなし)" if not c["lint"]["errors"] else "修理できず門が落とす"), None
        else:
            ch = explain_changes(c["set"], c["repaired"], c["repairs"])
            fb = form_breaks(c["set"], c["repaired"], L.is_mega_stone)
            noted = any(n.startswith("lint_repair:") for n in c["repair_notes"])
            bad = ch["unexplained"] or fb or not noted
            r2 = "fail" if bad else "pass"
            why2 = ("黙った変更: " + ",".join(ch["unexplained"]) if ch["unexplained"] else "") + \
                   (" 形態・制約の破壊: " + ",".join(fb) if fb else "") + ("" if noted else " 注記 lint_repair なし")
            why2 = why2.strip() or f"変更 {','.join(ch['changed'])} はすべて理由 {';'.join(c['repairs'])} で説明され、注記あり"
            ch = {**ch, "form_breaks": fb}
        meas.append({**base, "asset": "set_lint.repair_candidate",
                     "expected": {"no_silent_change": True, "keep_form": True, "requirement": "指定した型を黙って変えない (0 件)"},
                     "result": r2, "reason": why2, "elapsed_sec": round(c["lint_sec"], 4),
                     "observed": {"before": set_view(c["set"]), "after": c["repaired"], "repairs": c["repairs"],
                                  "notes": c["repair_notes"], "changes": ch}})
        # (3) 指定の型 (source custom) の経路: sets.lint_gate は触らない
        meas.append({**base, "asset": "sets.lint_gate(source=custom)",
                     "expected": {"unchanged": True, "requirement": "指定した型を黙って変えない (0 件)"},
                     "result": "pass" if c["custom_unchanged"] else "fail",
                     "reason": "指定の型はそのまま返る" if c["custom_unchanged"] else "指定の型が変わった / 落ちた",
                     "elapsed_sec": round(c["lint_sec"], 4), "observed": {"unchanged": c["custom_unchanged"]}})
    for ex in excluded:
        meas.append({"test_id": TEST_ID, "case_id": ex["case_id"], "category": CATEGORY[ex["source"]], "input": ex["input"],
                     "asset": "set_lint.gate (lint_set + gate_rejects + repair_candidate)", "expected": None,
                     "expected_source": None, "split": SPLIT[ex["source"]], "result": ex.get("result", "excluded"),
                     "reason": ex["reason"], "elapsed_sec": 0.0})
    for (cid, _t, origin), (ok, errs) in zip(teams, team_results):
        meas.append({"test_id": TEST_ID, "case_id": cid, "category": "生成物", "input": origin,
                     "asset": "showdown.validate-team (チーム全体)", "expected": {"legal": True, "requirement": "最終候補は合法"},
                     "expected_source": f"Showdown validate-team {fmt}", "split": "diag",
                     "result": "unexecuted" if ok is None else ("pass" if ok else "fail"),
                     "reason": "合法" if ok else "; ".join(errs), "elapsed_sec": round(per_validate, 4)})
    wall = time.time() - t0
    summary = summarize(cases, excluded, teams, team_results, rejects_by_code, blocked)
    manifest = {"test_id": TEST_ID, "run_id": RUN_ID, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                "code": {"base_commit": "768bae44", "worktree_head": git_head(REPO), "branch": "feat/inh-setlint",
                         "tool": "tools/inheritance_eval/setlint_audit.py", "tool_sha256": sha256_file(Path(__file__)),
                         "set_lint_sha256": sha256_file(REPO / "tools/team_build/set_lint.py"),
                         "sets_sha256": sha256_file(REPO / "tools/team_build/sets.py")},
                "rules_reference": {"engine": "pokemon-showdown validate-team (CLI、サーバーは起動しない)", "dir": str(showdown_dir),
                                    "commit": git_head(showdown_dir), "format": fmt,
                                    "ignored_team_errors": list(INH_SETLINT_IGNORED_ERRORS)},
                "data_read": data_read,
                "conditions": {"smogon_top": smogon_top, "workers": workers, "lint_info": "DexInfo (advisor/data の図鑑 + 効果表)",
                               "repair_legal_item": "tools.team_build.sets.legal_item (生成の門と同じ)",
                               "repair_usage_moves": "Smogon chaos の技の重みの降順 (メガは基本種にまとめる。境界例は fixture の usage_moves を優先)",
                               "repair_learnset": None, "BUILD_SET_LINT_GATE": L.BUILD_SET_LINT_GATE},
                "seed": None, "time_budget_sec": budget_sec, "wall_sec": round(wall, 2), "validate_sec": round(validate_sec, 2),
                "n_validate_calls": len(texts) + len(team_texts)}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    with open(out_dir / "cases.jsonl", "w", encoding="utf-8") as f:
        for r in case_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
        for ex in excluded:
            f.write(json.dumps({"test_id": TEST_ID, "case_id": ex["case_id"], "source": ex["source"],
                                "category": CATEGORY[ex["source"]], "split": SPLIT[ex["source"]], "input": ex["input"],
                                "excluded": ex["reason"]}, ensure_ascii=False) + "\n")
    with open(out_dir / "measurements.jsonl", "w", encoding="utf-8") as f:
        for r in meas:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return {"manifest": manifest, "summary": summary}


def s_view(s: dict) -> dict:
    return {"species": s["species"], "ability": s.get("ability"), "item": s.get("item"), "nature": s.get("nature"),
            "evs": s.get("evs"), "moves": list(s.get("moves") or [])}


def _jsonable(x):
    if isinstance(x, dict):
        return {k: _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in (sorted(x) if isinstance(x, (set, frozenset)) else x)]
    return x


def summarize(cases: list, excluded: list, teams: list, team_results: list, rejects_by_code: dict, blocked: dict) -> dict:
    """指標の集計 (純粋に近い: cases は audit が埋めたもの)"""
    by_src: dict = {}
    for c in cases:
        src = c["source"]
        legal = c.get("legal_input")
        b = by_src.setdefault(src, Counter())
        b["n"] += 1
        b[f"legal_{legal}"] += 1
        if src == "smogon" and legal is False:
            continue                                   # 合成の型の不合法は指標から除く
        ip = illegal_passed(legal, c["outcome"], c.get("legal_repaired"))
        b["illegal_passed"] += 1 if ip else 0
        b["illegal_rejected_or_fixed"] += 1 if (legal is False and ip is False) else 0
        b[f"outcome_{c['outcome']}"] += 1
        if legal:
            b["legal_but_error"] += 1 if c["lint"]["errors"] else 0
            b["legal_but_rejected"] += 1 if c["outcome"] == "rejected" else 0
            b["legal_but_repaired"] += 1 if c["outcome"] == "repaired" else 0
            b["legal_warning_only"] += 1 if (c["lint"]["warnings"] and not c["lint"]["errors"]) else 0
        if c["repaired"] is not None:
            ch = explain_changes(c["set"], c["repaired"], c["repairs"])
            b["silent_change"] += 1 if ch["unexplained"] else 0
            from tools.team_build.set_lint import is_mega_stone
            b["form_broken"] += 1 if form_breaks(c["set"], c["repaired"], is_mega_stone) else 0
            b["repair_made_illegal"] += 1 if (legal and c.get("legal_repaired") is False) else 0
        b["custom_changed"] += 0 if c["custom_unchanged"] else 1
        b["dispute_mega_ability"] += 1 if c["dispute_mega_ability"] else 0
    reasons = Counter()
    reasons_legal = Counter()
    for c in cases:
        if c["source"] == "smogon" and c.get("legal_input") is False:
            continue
        for code in c["lint"]["errors"]:
            d = c["lint"]["detail"].get(code) or {}
            why = code if code != "item" else "item:" + "+".join(d.get("problems") or [])
            reasons[f"{c['source']}:{why}:{c['outcome']}"] += 1
            if c.get("legal_input"):
                reasons_legal[f"{why}:{c['outcome']}"] += 1
    validator_msgs: dict = {}
    for c in cases:
        for e in c.get("validator_input") or []:
            validator_msgs.setdefault(str(e), []).append(c["case_id"])
    # 合法なのに旧 lint が誤りにした型 (警告止まりの候補) と、争点 (Showdown を無条件に真実としない)
    legal_errors = [{"case_id": c["case_id"], "errors": c["lint"]["errors"],
                     "detail": {k: v for k, v in c["lint"]["detail"].items() if k in c["lint"]["errors"]},
                     "outcome": c["outcome"], "repairs": c["repairs"],
                     "before": set_view(c["set"]), "after": c["repaired"]}
                    for c in cases if c.get("legal_input") and c["lint"]["errors"]]
    disputes = [{"case_id": c["case_id"], "kind": "mega_ability_in_team",
                 "note": "メガ石を持つ型の特性欄にメガ後の特性。validate-team は通すが実機の登録では基本種の特性しか選べない",
                 "ability": c["set"].get("ability"), "item": c["set"].get("item")}
                for c in cases if c["dispute_mega_ability"]]
    return {"by_source": {k: dict(v) for k, v in by_src.items()},
            "legal_but_lint_error": legal_errors, "disputes": disputes,
            "illegal_cases": [{"case_id": c["case_id"], "validator": c.get("validator_input"), "lint_errors": c["lint"]["errors"],
                               "outcome": c["outcome"], "repaired_legal": c.get("legal_repaired"),
                               "illegal_passed": illegal_passed(False, c["outcome"], c.get("legal_repaired"))}
                              for c in cases if c.get("legal_input") is False],
            "totals": {k: sum(v.get(k, 0) for v in by_src.values())
                       for k in ("n", "illegal_passed", "illegal_rejected_or_fixed", "silent_change", "form_broken",
                                 "repair_made_illegal", "custom_changed", "legal_but_error", "legal_but_rejected",
                                 "legal_but_repaired", "dispute_mega_ability")},
            "excluded": Counter(f"{e['source']}:{e.get('result', 'excluded')}" for e in excluded),
            "lint_reasons": dict(sorted(reasons.items())), "lint_reasons_on_legal": dict(sorted(reasons_legal.items())),
            "gate_rejects_by_code": rejects_by_code, "repair_blocked": blocked,
            "validator_messages": dict(sorted(validator_msgs.items())),
            "teams": [{"case_id": cid, "legal": ok, "errors": errs} for (cid, _t, _o), (ok, errs) in zip(teams, team_results)]}


def main() -> None:
    ap = argparse.ArgumentParser(description="I-05 型検査: 合法セット集に旧 lint / 自動修理を当てて全件列挙する")
    root = main_repo_root()
    ap.add_argument("--out", default=str(root / "logs" / "inheritance_eval" / RUN_ID / TEST_ID))
    ap.add_argument("--showdown-dir", default=str(root / "pokemon-showdown"))
    ap.add_argument("--smogon", default=str(root / "logs" / "usage_smogon" / "2026-09" / "gen9championsbssregmc-1500.json"))
    ap.add_argument("--smogon-top", type=int, default=INH_SETLINT_SMOGON_TOP, help="使用率の上位何種まで (省略時は config。None = 全種)")
    ap.add_argument("--workers", type=int, default=INH_SETLINT_WORKERS)
    a = ap.parse_args()
    r = audit(Path(a.out), Path(a.showdown_dir), Path(a.smogon), smogon_top=a.smogon_top, workers=a.workers)
    print(json.dumps({"wall_sec": r["manifest"]["wall_sec"], "totals": r["summary"]["totals"],
                      "excluded": r["summary"]["excluded"]}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
