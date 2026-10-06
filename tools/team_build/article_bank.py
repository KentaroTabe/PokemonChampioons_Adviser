"""記事バンク (docs/ARTICLE_BANK_DESIGN_1006.md §3.5〜§5): 解析結果 → 本文を含まない記録 → 検査 → バンクの保存・読み出し。

- build_record: article_parse.parse_article の結果 + 出典・メタ → case の記録 (unresolved_names は入れない)
- validate_record: 6 体・4 技・参戦種・配分の整合 (ポイント合計 / 252 表示との対応 / 実数値の再計算) ・メガ形態と石 ・選出規則の個体
- assert_no_prose: 記録や LLM の入力に かな・漢字を含む文字列が無いこと (本文・引用の混入の門)
- llm_payload: LLM に渡す部分集合 (個体の型、主張、選出規則、未確定項目の分類と参照 id)
- host_allowed: ホストごとの可否 (fetch / send_llm が allow のときだけ通す。unknown は進めない)
- save_bank / load_bank: logs/articles/bank/<version>/cases.jsonl + manifest.json (version = 内容のハッシュ)
純粋関数 (save/load 以外はファイルに触れない)。テストは tests/test_article_parse.py。
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Optional

from champions_agent.config import (BUILD_ARTICLE_EV252_OFFSET, BUILD_ARTICLE_EV252_PER_POINT, BUILD_ARTICLE_MAX_MEMBERS,
                                    BUILD_ARTICLE_MOVES_PER_SET, BUILD_GEN_EV_POINT_CAP, BUILD_GEN_POINT_BUDGET)
from tools.team_build.article_parse import PARSER_VERSION, STAT_ORDER, mega_table

SCHEMA_VERSION = "article_case/1"
REPO = Path(__file__).resolve().parent.parent.parent
DEFAULT_BANK_DIR = REPO / "logs" / "articles" / "bank"
_PROSE_RE = re.compile(r"[ぁ-んァ-ヶ一-龥]")
PAYLOAD_MEMBER_KEYS = ("id", "species_id", "base_species_id", "mega_stone", "item", "nature", "ability", "points", "ev252", "actual", "moves")


def build_record(parsed: dict, source: dict, meta: Optional[dict] = None, case_id: Optional[str] = None) -> dict:
    """解析結果 → case の記録。本文・文・解決できなかった名前は入れない (unresolved_names はローカルの辞書補修用で別扱い)"""
    members = [{k: v for k, v in m.items() if k != "display"} for m in parsed.get("members", [])]
    body = {"members": members, "claims": parsed.get("claims", []), "selection_rules": parsed.get("selection_rules", []),
            "selection_combinable": parsed.get("selection_combinable"), "unresolved": parsed.get("unresolved", [])}
    digest = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    status = "failed" if not members else ("incomplete" if len(members) < BUILD_ARTICLE_MAX_MEMBERS or any(not m["moves"] for m in members)
                                           else ("warnings" if (parsed.get("warnings") or any(m["warnings"] for m in members)) else "ok"))
    return {"case_id": case_id or f"case_{digest}", "source": dict(source or {}), "meta": dict(meta or {}), **body,
            "counts": dict(parsed.get("counts", {})), "warnings": list(parsed.get("warnings", [])),
            "versions": {"parser": parsed.get("parser_version", PARSER_VERSION), "dictionary": parsed.get("dictionary_version"),
                         "schema": SCHEMA_VERSION}, "status": status}


def _expected_ev252(points: int) -> int:
    return BUILD_ARTICLE_EV252_PER_POINT * points - BUILD_ARTICLE_EV252_OFFSET if points > 0 else 0


def expected_actual(species_id: str, points: Optional[dict], nature: Optional[str], dex=None) -> Optional[list]:
    """能力ポイントと性格から実数値 (Lv50、個体値 31) を再計算する。種族値が無ければ None。記事の実数値との整合の検査にだけ使う"""
    from advisor.dex import calc_hp, calc_stat, get_dex
    from advisor.my_team import nature_multipliers
    sp = (dex or get_dex()).species(species_id)
    if not sp or not sp.get("baseStats"):
        return None
    mult = nature_multipliers(nature or "") or {}
    pts = points or {}
    out = []
    for k in STAT_ORDER:
        ev = BUILD_ARTICLE_EV252_PER_POINT * int(pts.get(k, 0) or 0)       # calc_stat は努力値 (ev // 4 を Lv50 で半分にする) を取る
        base = int(sp["baseStats"][k])
        out.append(calc_hp(base, ev) if k == "hp" else calc_stat(base, ev, mult.get(k, 1.0)))
    return out


def validate_record(record: dict, legal_ids: Optional[set] = None, dex=None) -> list:
    """記録の検査 → 問題の列 (空なら OK)。警告の文言は id と数値だけ (本文を含まない)"""
    problems: list = []
    members = record.get("members", [])
    if len(members) != BUILD_ARTICLE_MAX_MEMBERS:
        problems.append(f"members:{len(members)}")
    bases = [m.get("base_species_id") for m in members]
    if len(set(bases)) != len(bases):
        problems.append("duplicate_base_species")
    mega = mega_table()
    for m in members:
        mid = m.get("id")
        if legal_ids and m.get("species_id") not in legal_ids and m.get("base_species_id") not in legal_ids:
            problems.append(f"{mid}:species_not_legal:{m.get('species_id')}")
        if len(m.get("moves") or []) != BUILD_ARTICLE_MOVES_PER_SET:
            problems.append(f"{mid}:moves:{len(m.get('moves') or [])}")
        if len(set(m.get("moves") or [])) != len(m.get("moves") or []):
            problems.append(f"{mid}:duplicate_move")
        pts = m.get("points")
        if pts:
            if any(v > BUILD_GEN_EV_POINT_CAP for v in pts.values()):
                problems.append(f"{mid}:point_cap")
            if sum(pts.values()) != BUILD_GEN_POINT_BUDGET:
                problems.append(f"{mid}:point_total:{sum(pts.values())}")
        ev = m.get("ev252")
        if pts and ev:
            for k in STAT_ORDER:
                if _expected_ev252(int(pts.get(k, 0) or 0)) != int(ev.get(k, 0) or 0):
                    problems.append(f"{mid}:ev252_mismatch:{k}")
        act = m.get("actual")
        if act is not None and len(act) != len(STAT_ORDER):
            problems.append(f"{mid}:actual_len:{len(act)}")
        elif act and pts:
            exp = expected_actual(m.get("species_id"), pts, m.get("nature"), dex)
            if exp and exp != list(act):
                problems.append(f"{mid}:actual_mismatch:" + ",".join(k for k, a, e in zip(STAT_ORDER, act, exp) if a != e))
        sid = m.get("species_id")
        if sid in mega["forms"]:
            _base, stone = mega["forms"][sid]
            if stone and m.get("item") != stone:
                problems.append(f"{mid}:mega_item:{m.get('item')}")
        elif m.get("item") in mega["stones"] and mega["stones"][m["item"]] != sid:
            problems.append(f"{mid}:stone_without_form")
    ids = {m.get("id") for m in members}
    for i, r in enumerate(record.get("selection_rules", [])):
        if not set(r.get("selected_members") or []) <= ids:
            problems.append(f"rule{i}:members_outside_team")
        if r.get("lead") is not None and r["lead"] not in (r.get("selected_members") or []):
            problems.append(f"rule{i}:lead_outside_selection")
        if any(k in r for k in ("probability", "prob", "weight")):
            problems.append(f"rule{i}:probability_not_allowed")
    for i, c in enumerate(record.get("claims", [])):
        if c.get("subject") != "team" and c.get("subject") not in ids:
            problems.append(f"claim{i}:subject_unknown")
        if c.get("basis") != "author_explicit":
            problems.append(f"claim{i}:basis:{c.get('basis')}")
    return problems


def assert_no_prose(obj, path: str = "$") -> None:
    """記録・LLM の入力に かな・漢字を含む文字列が無いことを確かめる (本文・引用の混入の門)。違反は場所と長さだけを報告する"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if _PROSE_RE.search(str(k)):
                raise ValueError(f"prose key at {path} (len {len(str(k))})")
            assert_no_prose(v, f"{path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            assert_no_prose(v, f"{path}[{i}]")
    elif isinstance(obj, str) and _PROSE_RE.search(obj):
        raise ValueError(f"prose value at {path} (len {len(obj)})")


def llm_payload(record: dict) -> dict:
    """LLM に渡す部分集合: 個体の型 (id と数値)、主張、選出規則、未確定項目の分類と参照 id。本文・出典 URL・メタの自由記述は含まない"""
    payload = {"case_id": record.get("case_id"), "regulation": (record.get("meta") or {}).get("regulation"),
               "members": [{k: m.get(k) for k in PAYLOAD_MEMBER_KEYS} for m in record.get("members", [])],
               "claims": [dict(c) for c in record.get("claims", [])],
               "selection_rules": [dict(r) for r in record.get("selection_rules", [])],
               "selection_combinable": record.get("selection_combinable"),
               "unresolved": [{"category": u.get("category"), "source_ref": u.get("source_ref")} for u in record.get("unresolved", [])]}
    assert_no_prose(payload)
    return payload


def host_allowed(policy: dict, host: str, purpose: str = "fetch") -> bool:
    """host_policy.json の {host: {"fetch": allow|unknown|deny, "send_llm": ...}}。allow のときだけ True (unknown は進めない)"""
    entry = (policy or {}).get(host) or {}
    return entry.get(purpose) == "allow"


def bank_version(cases: list) -> str:
    return hashlib.sha256(json.dumps(cases, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def save_bank(cases: list, bank_dir: Path = DEFAULT_BANK_DIR) -> Path:
    """cases → <bank_dir>/<version>/cases.jsonl + manifest.json。保存前に本文の混入を検査する"""
    for c in cases:
        assert_no_prose({k: v for k, v in c.items() if k not in ("source", "meta")})
    version = bank_version(cases)
    out = Path(bank_dir) / version
    out.mkdir(parents=True, exist_ok=True)
    (out / "cases.jsonl").write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases), encoding="utf-8")
    from collections import Counter
    regs = Counter(str((c.get("meta") or {}).get("regulation")) for c in cases)
    (out / "manifest.json").write_text(json.dumps({"version": version, "schema": SCHEMA_VERSION, "parser": PARSER_VERSION, "n_cases": len(cases),
                                                   "by_regulation": dict(regs), "by_status": dict(Counter(c.get("status") for c in cases))},
                                                  ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return out


def load_bank(version: str, bank_dir: Path = DEFAULT_BANK_DIR) -> list:
    """固定版の読み出し (version を指定する。最新を暗黙に選ばない)"""
    path = Path(bank_dir) / version / "cases.jsonl"
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
