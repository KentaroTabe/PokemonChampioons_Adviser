"""I-05 型検査 (引き継ぎ評価 段階 A、docs/INHERITANCE_EVALUATION_PLAN_20261031.md §5 I-05) のテスト。

境界例 (tests/fixtures/inheritance/set_edge_cases.jsonl) を旧 lint (tools/team_build/set_lint) と自動修理に当てる。
期待値は「今の挙動の記録」ではなく計画書の要件で書く:
  R1 ルール上不合法な型を通さない (0 件)。合法性は fixture の expected_legal (validate-team で確かめて記入) を参照。
     2026-10-31 レビュー判断 5 (docs/INHERITANCE_PHASE_A_REVIEW_20261031.md) で、試験の対象を旧 lint 単体から構築の実経路
     (legal_item → 門 (lint_gate) → チーム文 (to_showdown_text) → validate_team_text) に替えた。旧 lint は合法性の検証器ではなく、
     lint 単体で不合法 9 件中 8 件を通す測定値 (I-05) は assert せず出力に残す (report_lint_only_r1)。
     validate-team (ローカル Showdown の CLI) が無い環境 (CI: pokemon-showdown/ はコミットしない) では、検証の段は理由つきの SKIP
     (合法とは扱わない。環境の欠如で、期待値の緩和ではない)。CLI があるのに起動できなければ fail。
     厳格モード (環境変数 INH_R1_REQUIRE_SHOWDOWN=1、名前は config の INH_R1_REQUIRE_SHOWDOWN_ENV) では SKIP せず fail にする。
     GitHub CI の job showdown-validator は検証対象の commit の Showdown を取得・build し、その dir を環境変数 SHOWDOWN_DIR
     (INH_R1_SHOWDOWN_DIR_ENV) で渡して厳格モードで実行する (2026-10-31 追加レビュー判断 3)
  R2 指定した型を黙って変えない (0 件)。修理の変更は理由 (repairs) と注記 (lint_repair) で説明され、指定の型 (custom) は触らない
  R3 修理で形態・制約 (種・性別形態・メガ石) を壊さない
要件を満たさない境界例は xfail にせず fail として数える (2026-10-31 開発者指示。結果は I-05 の report.md に書く)。
合法だが旧 lint が誤りにする型 (戦術的な拒否・修理) は要件違反ではなく「警告止まりの候補」として一覧を出すだけ。

    python -m tests.test_inheritance_setlint
    INH_R1_REQUIRE_SHOWDOWN=1 SHOWDOWN_DIR=<pokemon-showdown の dir> python -m tests.test_inheritance_setlint   # 厳格モード
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

from champions_agent.config import (INH_R1_REQUIRE_SHOWDOWN_ENV, INH_R1_SHOWDOWN_DIR_ENV, INH_SETLINT_WORKERS,
                                    TRAINING_BATTLE_FORMAT)
from tools.inheritance_eval import setlint_audit as A
from tools.team_build import set_lint as L
from tools.team_build import sets as S

REQUIRED_KINDS = ("prio_phys_on_special", "choice_status", "av_status", "mega_form_item_mismatch", "three_moves",
                  "duplicate_move", "adamant_special_only", "trick_choice", "eviolite_final")
# R1 の検証の形式 = 構築の既定の規制 (tools/team_build/spec.py の BuildSpec.regulation の既定値)
R1_FORMAT = TRAINING_BATTLE_FORMAT
# 実経路は 6 体のチーム文を検証するので、残り 5 枠を fixture の合法例から埋める (検査する型と種・持ち物が重ならないものを先頭から)
R1_FILLER_CASE_IDS = ("E01_prio_phys_on_special", "E16_field_dup", "E19_chesto_no_rest", "E04_choice_trick_status",
                      "E18_mega_acrobatics", "E10_adamant_special_only", "E20_type_item_no_move", "E25_mega_ability_in_team")
R1_TEAM_SIZE = 6


class SkipTest(Exception):
    """環境に無いもの (ローカル Showdown の CLI 等) が要る検査を、理由つきで飛ばす (合格にも不合格にも数えない)"""


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
# ------------------------------------------------------------------ R1: 構築の実経路
def r1_strict(env=None) -> bool:
    """厳格モード (環境変数 INH_R1_REQUIRE_SHOWDOWN_ENV が "1")。validate-team を使えないときに SKIP せず fail にする"""
    return (os.environ if env is None else env).get(INH_R1_REQUIRE_SHOWDOWN_ENV, "") == "1"


def r1_unavailable(why: str, strict: bool) -> Exception:
    """validate-team を使えないときに送る例外。厳格モードは AssertionError (fail)、通常は SkipTest (理由つき SKIP)"""
    msg = f"validate-team を実行できない: {why}。検証の前の段は test_r1_gate_stage"
    if strict:
        return AssertionError(f"{msg} (厳格モード {INH_R1_REQUIRE_SHOWDOWN_ENV}=1 のため SKIP にしない)")
    return SkipTest(msg)


def showdown_dir_for_r1(env=None):
    """validate-team に使う pokemon-showdown の dir。使えなければ (None, 理由)。
    環境変数 INH_R1_SHOWDOWN_DIR_ENV (SHOWDOWN_DIR) が立っていればその dir だけを見る (CI の clone 先。黙って他へ退避しない)。
    立っていなければ sets.SHOWDOWN_DIR → 本体の作業ツリーの dir (ワークツリーには無いため)。
    dist が無いと CLI が build (書き込み) を始めるので使えない扱い"""
    env = os.environ if env is None else env
    if not shutil.which("node"):
        return None, "node が PATH に無い"
    explicit = env.get(INH_R1_SHOWDOWN_DIR_ENV, "")
    dirs = [Path(explicit)] if explicit else [Path(S.SHOWDOWN_DIR), A.main_repo_root() / "pokemon-showdown"]
    for d in dirs:
        if (d / "pokemon-showdown").exists():
            if not (d / "dist").exists():
                return None, f"{d}/dist が無い (CLI が build を始めるため使わない)"
            return d, ""
    if explicit:
        return None, f"{INH_R1_SHOWDOWN_DIR_ENV}={explicit} に pokemon-showdown の CLI が無い"
    return None, "pokemon-showdown/ が無い (コミットしないデータ。CI では検証の段を実行しない)"


def test_r1_showdown_selection() -> None:
    """厳格モードと Showdown の dir の選び方 (純粋。validate-team は呼ばない)"""
    assert r1_strict({INH_R1_REQUIRE_SHOWDOWN_ENV: "1"}) is True
    assert r1_strict({}) is False and r1_strict({INH_R1_REQUIRE_SHOWDOWN_ENV: "0"}) is False
    assert isinstance(r1_unavailable("x", strict=True), AssertionError)
    assert isinstance(r1_unavailable("x", strict=False), SkipTest)
    with tempfile.TemporaryDirectory() as tmp:
        missing = str(Path(tmp) / "nothing")
        sd, why = showdown_dir_for_r1({INH_R1_SHOWDOWN_DIR_ENV: missing})
        if shutil.which("node"):
            # 明示した dir に CLI が無ければ、本体の作業ツリーの Showdown があってもそこへ退避しない
            assert sd is None and INH_R1_SHOWDOWN_DIR_ENV in why and missing in why, (sd, why)
            d = Path(tmp) / "ps"
            d.mkdir()
            (d / "pokemon-showdown").write_text("")
            sd, why = showdown_dir_for_r1({INH_R1_SHOWDOWN_DIR_ENV: str(d)})
            assert sd is None and "dist が無い" in why, (sd, why)
            (d / "dist").mkdir()
            assert showdown_dir_for_r1({INH_R1_SHOWDOWN_DIR_ENV: str(d)}) == (d, "")
        else:
            assert sd is None and "node" in why
    print("test_r1_showdown_selection OK")


def r1_route(case: dict, route: str):
    """境界例の型を構築の実経路の門に通す。戻り値 (門を通った型 or None, 段の記録)。
    route "custom"   : 指定の型 (source custom)。門 (sets.lint_gate) は触らずに通す → 検証だけが合法性を見る
    route "generated": 代表型・生成型。joint_stage.rep_entry と同じ順に set_sanity / legal_item で落とし、
                       sets.lint_gate (直せれば直し、直せなければ gate_rejects で落とす) に通す"""
    s = case["set"]
    um = list(case.get("usage_moves") or [])
    if route == "custom":
        cand = A.candidate_of(s, "custom")
    else:
        cand = A.candidate_of(s, "representative")
        sanity = S.set_sanity(cand)
        if sanity:
            return None, f"set_sanity で落ちた {sanity}"
        if not S.legal_item(cand.item):
            return None, f"legal_item で落ちた ({cand.item})"
    gated = S.lint_gate([cand], source="inh_r1", usage_moves=um)
    if not gated:
        return None, "lint_gate で落ちた"
    out = gated[0]
    return out, ("門を素通り" if out is cand else "門で修理 " + ";".join(_repairs_of(out)))


def r1_team(cand, fillers: list) -> list:
    """門を通った型 + 種・持ち物が重ならない合法例 5 体 → 6 体 (アイテムクローズ・種族の重複で落ちないように)"""
    team, species, items = [cand], {cand.species_id}, {cand.item} - {None}
    for f in fillers:
        if len(team) >= R1_TEAM_SIZE:
            break
        if f.species_id in species or (f.item and f.item in items):
            continue
        team.append(f)
        species.add(f.species_id)
        items.add(f.item)
    assert len(team) == R1_TEAM_SIZE, f"{cand.species_id}: 埋める合法例が足りない"
    return team


def _r1_fillers() -> list:
    by_id = {c["case_id"]: c for c in _cases()}
    out = []
    for cid in R1_FILLER_CASE_IDS:
        assert by_id[cid]["expected_legal"], f"{cid}: 埋める型は合法例だけ"
        out.append(A.candidate_of(by_id[cid]["set"], "custom"))
    return out


def r1_plan() -> list:
    """全境界例 × 2 経路の門の結果と、検証するチーム文。[{case_id, route, legal, stage, text or None}]"""
    fillers = _r1_fillers()
    rows = []
    for c in _cases():
        for route in ("custom", "generated"):
            out, stage = r1_route(c, route)
            text = S.to_showdown_text(r1_team(out, fillers)) if out is not None else None
            rows.append({"case_id": c["case_id"], "route": route, "legal": c["expected_legal"], "stage": stage, "text": text})
    return rows


def test_r1_gate_stage() -> None:
    """R1 の前半 (検証の前の段、CI でも回る): 経路と 6 体のチーム文が組めること。どの不合法例が検証の段まで届くかを出力する。
    検証の前に落ちた不合法例は「通さない」側。届いたものの合否は test_r1_real_path_with_showdown が決める"""
    rows = r1_plan()
    illegal = [r for r in rows if not r["legal"]]
    assert len({r["case_id"] for r in illegal}) == sum(1 for c in _cases() if not c["expected_legal"])
    for r in rows:
        if r["text"] is not None:
            assert len(S.team_blocks(r["text"])) == R1_TEAM_SIZE, r["case_id"]
    # 指定の型は門を素通りする (合法性を見るのは validate_team_text だけ)
    assert all(r["stage"] == "門を素通り" for r in rows if r["route"] == "custom"), \
        [r for r in rows if r["route"] == "custom" and r["stage"] != "門を素通り"]
    reach = [r for r in illegal if r["text"] is not None]
    print(f"test_r1_gate_stage OK (不合法 {len(illegal)} 行 (9 件 × 2 経路) のうち検証の段に届く {len(reach)} 行)")
    for r in illegal:
        print(f"  {r['case_id']} [{r['route']}]: {r['stage']}{' → validate_team_text へ' if r['text'] is not None else ''}")


def test_r1_real_path_with_showdown() -> None:
    """R1: 不合法な境界例 (expected_legal False) が、構築の実経路 (門 → チーム文 → validate_team_text) を 2 経路とも通らない。
    validate-team が要る (ローカル Showdown の CLI。無ければ理由つきで SKIP、厳格モードでは fail)。
    前提の確認として、合法な境界例を同じ 6 体のチーム文で検証すると通ること (形式の取り違えや埋める型の不具合で
    全部が落ちて R1 が空振りに合格するのを防ぐ) も見る"""
    sd, why = showdown_dir_for_r1()
    if sd is None:
        raise r1_unavailable(why, r1_strict())
    rows = [r for r in r1_plan() if r["text"] is not None]
    results = S.validate_team_texts([r["text"] for r in rows], R1_FORMAT, workers=INH_SETLINT_WORKERS, showdown_dir=sd)
    run_failed = [f"{r['case_id']} [{r['route']}]: {errs[:1]}" for r, (ok, errs) in zip(rows, results)
                  if errs and str(errs[0]).startswith(S.VALIDATE_RUN_FAILED)]
    assert not run_failed, "validate-team が起動できない (合法とは扱わない):\n  " + "\n  ".join(run_failed)
    control = [f"{r['case_id']} [{r['route']}]: {errs[:3]}" for r, (ok, errs) in zip(rows, results)
               if r["legal"] and r["route"] == "custom" and not ok]
    assert not control, "合法な境界例が同じ組み方のチーム文で検証を通らない (R1 の前提が崩れている):\n  " + "\n  ".join(control)
    bad = [f"{r['case_id']} [{r['route']}]: {r['stage']} → validate_team_text が通した" for r, (ok, _e) in zip(rows, results)
           if not r["legal"] and ok]
    assert not bad, "不合法な型を実経路が通した境界例 %d 件:\n  " % len(bad) + "\n  ".join(bad)
    n_illegal = sum(1 for r in rows if not r["legal"])
    print(f"test_r1_real_path_with_showdown OK ({R1_FORMAT}, {sd}: 検証に届いた不合法 {n_illegal} 行はすべて拒否、"
          f"合法 {sum(1 for r in rows if r['legal'] and r['route'] == 'custom')} 件は通過)")
    for r, (ok, errs) in zip(rows, results):
        if not r["legal"]:
            print(f"  {r['case_id']} [{r['route']}]: {errs[:2]}")


def report_lint_only_r1() -> None:
    """測定 (assert しない): 旧 lint 単体 (lint_set + 修理) が不合法な境界例を何件通すか。I-05 の 8/9 の再現。
    「lint だけでは合法性を保証できない」証拠として残す (docs/KNOWN_ISSUES.md)"""
    passed, illegal = [], [c for c in _cases() if not c["expected_legal"]]
    for c in illegal:
        s = c["set"]
        res = L.lint_set(s["species"], s.get("ability"), s.get("item"), s.get("nature"), list(s["moves"]))
        if not res["errors"]:
            passed.append(f"{c['case_id']}: 旧 lint の誤りなし ({'; '.join(c.get('validator_messages') or [])})")
            continue
        fixed = _repair(c)
        if fixed is not None:
            left = A.remaining_illegal_elements(A.set_of(fixed), c["illegal_elements"])
            if left:
                passed.append(f"{c['case_id']}: 修理した出力に不合法の原因が残る {left}")
    print(f"測定 (assert しない): 旧 lint 単体は不合法な境界例 {len(passed)}/{len(illegal)} 件を通す\n  " + "\n  ".join(passed))


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
             test_r1_showdown_selection, test_r1_gate_stage, test_r1_real_path_with_showdown, test_r2_no_silent_change, test_r3_repair_keeps_form]
    failed, skipped = [], []
    for t in tests:
        try:
            t()
        except SkipTest as e:
            skipped.append(t.__name__)
            print(f"{t.__name__} SKIP: {e}")
        except AssertionError as e:
            failed.append(t.__name__)
            print(f"{t.__name__} FAILED: {e}")
    report_lint_only_r1()
    report_legal_tactical()
    if failed:
        print(f"FAILED {len(failed)}/{len(tests)}: {', '.join(failed)}")
        sys.exit(1)
    print("ALL OK" + (f" (SKIP {len(skipped)}: {', '.join(skipped)})" if skipped else ""))


if __name__ == "__main__":
    main()
