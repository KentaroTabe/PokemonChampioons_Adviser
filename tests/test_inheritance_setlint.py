"""I-05 型検査 (引き継ぎ評価 段階 A、docs/INHERITANCE_EVALUATION_PLAN_20261031.md §5 I-05) のテスト。

境界例 (tests/fixtures/inheritance/set_edge_cases.jsonl) を旧 lint (tools/team_build/set_lint) と自動修理に当てる。
期待値は「今の挙動の記録」ではなく計画書の要件で書く:
  R1 ルール上不合法な型を通さない (0 件)。合法性は fixture の expected_legal (validate-team で確かめて記入) を参照
  R2 指定した型を黙って変えない (0 件)。修理の変更は理由 (repairs) と注記 (lint_repair) で説明され、指定の型 (custom) は触らない
  R3 修理で形態・制約 (種・性別形態・メガ石) を壊さない
要件を満たさない境界例は xfail にせず fail として数える (2026-10-31 開発者指示。結果は I-05 の report.md に書く)。
合法だが旧 lint が誤りにする型 (戦術的な拒否・修理) は要件違反ではなく「警告止まりの候補」として一覧を出すだけ。

    python -m tests.test_inheritance_setlint
"""
from __future__ import annotations

import sys

from tools.inheritance_eval import setlint_audit as A
from tools.team_build import set_lint as L
from tools.team_build import sets as S

REQUIRED_KINDS = ("prio_phys_on_special", "choice_status", "av_status", "mega_form_item_mismatch", "three_moves",
                  "duplicate_move", "adamant_special_only", "trick_choice", "eviolite_final")


def _cases() -> list:
    return A.load_edge_cases()


def _repair(case: dict):
    s = case["set"]
    cand = A.candidate_of(s)
    return L.repair_candidate(cand, usage_moves=list(case.get("usage_moves") or []), legal_item=S.legal_item)


def _repairs_of(fixed) -> list:
    note = next((n for n in (fixed.notes or []) if n.startswith("lint_repair:")), None)
    return note.split(":", 1)[1].split(";") if note else []


# ------------------------------------------------------------------ 純粋な部品
def test_pure_helpers() -> None:
    assert A.points_str({"h": 32, "b": 20, "d": 14}) == "32/0/20/0/14/0"
    assert A.points_str({}) is None
    assert A.filter_validator_errors(["You must bring at least 6 Pokémon (your team has 1).", "X can't learn Y."]) == ["X can't learn Y."]
    before = {"species": "a", "ability": "b", "item": "choicescarf", "nature": "jolly", "evs": "1/2/3/4/5/6", "moves": ["m1", "m2"]}
    after = dict(before, item="leftovers")
    assert A.explain_changes(before, after, ["item:choice_status"]) == {"changed": ["item"], "unexplained": []}
    assert A.explain_changes(before, after, ["nature_move"]) == {"changed": ["item"], "unexplained": ["item"]}
    assert A.explain_changes(before, dict(after, ability="z"), ["item:choice_status"])["unexplained"] == ["ability"]
    # アクロバットの側を外す修理は技の変更の理由で、持ち物の変更の理由ではない
    assert A.explain_changes(before, dict(after, moves=["m1"]), ["item:acrobatics->move"])["unexplained"] == ["item"]
    stone = lambda it: bool(it) and it.endswith("ite")
    assert A.form_breaks({"species": "x", "item": "xite"}, {"species": "x", "item": "leftovers"}, stone) == ["mega_stone_removed"]
    assert A.form_breaks({"species": "x", "item": "xite"}, {"species": "x", "item": "yite"}, stone) == ["mega_stone_swapped"]
    assert A.form_breaks({"species": "x", "item": None}, {"species": "x", "item": "xite"}, stone) == ["mega_stone_added"]
    assert A.form_breaks({"species": "xf", "item": None}, {"species": "x", "item": None}, stone) == ["species_changed"]
    assert A.gate_outcome([], None) == "pass" and A.gate_outcome(["item"], {"x": 1}) == "repaired"
    assert A.gate_outcome(["item"], None) == "rejected"
    assert A.illegal_passed(False, "pass", None) is True and A.illegal_passed(False, "rejected", None) is False
    assert A.illegal_passed(False, "repaired", False) is True and A.illegal_passed(False, "repaired", True) is False
    assert A.illegal_passed(True, "pass", None) is False and A.illegal_passed(None, "pass", None) is None
    assert A.remaining_illegal_elements({"item": "assaultvest", "moves": ["a", "a"], "evs": "32/32/0/0/0/32"},
                                        {"item": "assaultvest", "duplicate_moves": True, "evs_total": True}) == \
        ["item", "duplicate_moves", "evs_total"]
    assert A.remaining_illegal_elements({"item": "leftovers", "moves": ["a", "b"], "evs": "2/32/0/0/0/32"},
                                        {"item": "assaultvest", "duplicate_moves": True, "evs_total": True}) == []
    print("test_pure_helpers OK")


def test_registered_and_chaos_conversion() -> None:
    names = {("しめったいわ", "items"): "damprock", ("あめふらし", "abilities"): "drizzle", ("ぼうふう", "moves"): "hurricane",
             ("なみのり", "moves"): "surf"}
    resolve = lambda t, c: names.get((t, c))
    entry = {"能力ポイント": {"h": 32, "b": 20, "d": 14}, "性格": "のんき", "持ち物": "しめったいわ", "特性": "あめふらし",
             "技": ["ぼうふう", "なみのり"]}
    s, why = A.registered_set("ペリッパー", entry, resolve, lambda t: "pelipper", lambda n: "Relaxed")
    assert why is None and s == {"species": "pelipper", "ability": "drizzle", "item": "damprock", "nature": "relaxed",
                                 "evs": "32/0/20/0/14/0", "moves": ["hurricane", "surf"]}
    s, why = A.registered_set("ペリッパー", dict(entry, 技=["ぼうふう", "ふめいなわざ"]), resolve, lambda t: "pelipper", lambda n: "Relaxed")
    assert s is None and "ふめいなわざ" in why
    s, why = A.registered_set("ペリッパー", {"種族ID": "pelipper"}, resolve, lambda t: None, lambda n: None)
    assert s is None and "不完全" in why
    entry = {"Abilities": {"aerilate": 10.0}, "Items": {"salamencite": 10.0, "nothing": 0.5},
             "Spreads": {"Adamant:1/32/0/0/1/32": 3.0, "Jolly:2/32/0/0/0/32": 1.0},
             "Moves": {"doubleedge": 9.0, "dragondance": 8.0, "": 5.0, "earthquake": 7.0, "roost": 6.0, "fireblast": 1.0}}
    known = {"salamence", "salamencemega"}
    s = A.chaos_top_set("Salamence-Mega", entry, "points", lambda x: x, lambda x: x in known)
    assert s["species"] == "salamence" and s["item"] == "salamencite" and s["ability"] == "aerilate" and s["mega_entry"]
    assert s["moves"] == ["doubleedge", "dragondance", "earthquake", "roost"] and s["nature"] == "adamant"
    assert s["evs"] == "1/32/0/0/1/32"
    print("test_registered_and_chaos_conversion OK")


def test_fixture_shape() -> None:
    cases = _cases()
    assert len(cases) >= 16, len(cases)
    ids = [c["case_id"] for c in cases]
    assert len(ids) == len(set(ids))
    for kind in REQUIRED_KINDS:
        assert any(kind in i for i in ids), kind
    for c in cases:
        assert isinstance(c.get("expected_legal"), bool), c["case_id"]
        assert "validate-team" in (c.get("expected_legal_source") or ""), c["case_id"]
        if not c["expected_legal"]:
            assert c.get("illegal_elements"), c["case_id"]
    assert any(c["expected_legal"] for c in cases) and any(not c["expected_legal"] for c in cases)
    print("test_fixture_shape OK")


# ------------------------------------------------------------------ 要件 (計画書 §5 I-05)
def test_r1_illegal_not_passed() -> None:
    """R1: 不合法な型 (expected_legal False) を門が通さない。誤りなし = 通す。修理した出力に不合法の原因が残る = 通す"""
    bad = []
    for c in _cases():
        if c["expected_legal"]:
            continue
        s = c["set"]
        res = L.lint_set(s["species"], s.get("ability"), s.get("item"), s.get("nature"), list(s["moves"]))
        if not res["errors"]:
            bad.append(f"{c['case_id']}: 旧 lint の誤りなし ({'; '.join(c.get('validator_messages') or [])})")
            continue
        fixed = _repair(c)
        if fixed is not None:
            left = A.remaining_illegal_elements(A.set_of(fixed), c["illegal_elements"])
            if left:
                bad.append(f"{c['case_id']}: 修理した出力に不合法の原因が残る {left}")
    assert not bad, "不合法な型を通した境界例 %d 件:\n  " % len(bad) + "\n  ".join(bad)
    print("test_r1_illegal_not_passed OK")


def test_r2_no_silent_change() -> None:
    """R2: 修理の変更はすべて理由で説明され、注記 lint_repair が付く。指定の型 (custom) は門も基本の型もそのまま返す"""
    bad = []
    for c in _cases():
        s = c["set"]
        fixed = _repair(c)
        if fixed is not None:
            ch = A.explain_changes(s, A.set_of(fixed), _repairs_of(fixed))
            if ch["unexplained"]:
                bad.append(f"{c['case_id']}: 理由のない変更 {ch['unexplained']}")
            if not _repairs_of(fixed):
                bad.append(f"{c['case_id']}: 注記 lint_repair なし")
        custom = A.candidate_of(s, "custom")
        gated = S.lint_gate([custom], usage_moves=list(c.get("usage_moves") or []))
        if len(gated) != 1 or gated[0] is not custom or A.set_of(gated[0]) != A.s_view(s):
            bad.append(f"{c['case_id']}: 指定の型が lint_gate で変わった / 落ちた")
        base = S.base_set(None, 0, s["species"], custom=custom)
        if base is not custom:
            bad.append(f"{c['case_id']}: 指定の型が base_set で替わった")
    assert not bad, "指定した型を黙って変えた境界例 %d 件:\n  " % len(bad) + "\n  ".join(bad)
    print("test_r2_no_silent_change OK")


def test_r3_repair_keeps_form() -> None:
    """R3: 修理は種 (性別形態・フォルム) とメガ石を変えない"""
    bad = []
    for c in _cases():
        fixed = _repair(c)
        if fixed is None:
            continue
        fb = A.form_breaks(c["set"], A.set_of(fixed), L.is_mega_stone)
        if fb:
            bad.append(f"{c['case_id']}: {fb}")
    assert not bad, "修理で形態・制約を壊した境界例 %d 件:\n  " % len(bad) + "\n  ".join(bad)
    print("test_r3_repair_keeps_form OK")


def report_legal_tactical() -> None:
    """合法だが旧 lint が誤りにする境界例 (警告止まりの候補)。要件違反ではないので一覧を出すだけ"""
    rows = []
    for c in _cases():
        if not c["expected_legal"]:
            continue
        s = c["set"]
        res = L.lint_set(s["species"], s.get("ability"), s.get("item"), s.get("nature"), list(s["moves"]))
        if res["errors"]:
            fixed = _repair(c)
            rows.append(f"{c['case_id']}: 誤り {res['errors']} → {'修理 ' + ';'.join(_repairs_of(fixed)) if fixed else '門が落とす'}")
    print("合法だが旧 lint が誤りにする境界例 (警告止まりの候補) %d 件:\n  " % len(rows) + "\n  ".join(rows))


def main() -> None:
    tests = [test_pure_helpers, test_registered_and_chaos_conversion, test_fixture_shape,
             test_r1_illegal_not_passed, test_r2_no_silent_change, test_r3_repair_keeps_form]
    failed = []
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed.append(t.__name__)
            print(f"{t.__name__} FAILED: {e}")
    report_legal_tactical()
    if failed:
        print(f"FAILED {len(failed)}/{len(tests)}: {', '.join(failed)}")
        sys.exit(1)
    print("ALL OK")


if __name__ == "__main__":
    main()
