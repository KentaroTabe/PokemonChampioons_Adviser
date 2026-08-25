"""構築提案の運用ゲート (tools/team_proposal) のテスト。

    scripts/run_test.sh test_team_proposal

実対戦は行わない: 純粋な判定ロジック (条件評価・登録欠落・受入検定の
対応のある比較・evolve引数の組み立て) のみを検証する。
"""
from __future__ import annotations

from tools.team_proposal import (
    Condition, MIN_ACCEPT_BATTLES, MIN_EVAL_BATTLES, VAL_GAIN_GATE_PCT,
    evaluate_conditions, hard_ok, paired_verdict, registration_gaps,
    render_report,
)


def _inputs(**over) -> dict:
    """全条件が通る測定値のベース"""
    base = {
        "usage_age_days": 0.5,
        "ranked_team_count": 250,
        "policy_best_exists": True,
        "showdown_listening": True,
        "party": {f"モン{i}": {} for i in range(6)},
        "party_gaps": {},
        "sel_general_exists": True,
        "sel_val": {"gain_pct": 7.3, "split": "unseen_teams",
                    "at": "2026-08-20 04:00"},
        "sel_in_dist": True,
        "meta_pool_size": 60,
        "forecast_available": False,
        "archive_size": 3,
        "battles": MIN_EVAL_BATTLES,
        "accept_battles": MIN_ACCEPT_BATTLES,
    }
    base.update(over)
    return base


def test_stage1_all_hard_pass():
    conds = evaluate_conditions(_inputs(), stage=1)
    assert hard_ok(conds), [c.cid for c in conds if c.hard and not c.passed]
    print("test_stage1_all_hard_pass OK")


def test_stage1_blocks_on_stale_usage_and_missing_party():
    conds = evaluate_conditions(
        _inputs(usage_age_days=12.0, party={"ア": {}, "イ": {}}), stage=1)
    assert not hard_ok(conds)
    failed = {c.cid for c in conds if c.hard and not c.passed}
    assert {"C1", "S1-1"} <= failed, failed
    print("test_stage1_blocks_on_stale_usage_and_missing_party OK")


def test_unmeasured_hard_condition_blocks():
    """未計測 (None) の必須条件は不合格として扱う"""
    conds = evaluate_conditions(_inputs(ranked_team_count=None), stage=1)
    c2 = next(c for c in conds if c.cid == "C2")
    assert c2.ok is None and not c2.passed and not hard_ok(conds)
    print("test_unmeasured_hard_condition_blocks OK")


def test_stage2_soft_conditions_do_not_block():
    """外挿・アーカイブ・選出モデル系は推奨条件 (未達でも運用可)"""
    conds = evaluate_conditions(
        _inputs(forecast_available=False, archive_size=0,
                sel_val=None, sel_in_dist=False), stage=2)
    assert hard_ok(conds)
    soft_failed = {c.cid for c in conds if not c.hard and not c.passed}
    assert {"S2-3", "S2-4", "M1", "M2"} <= soft_failed, soft_failed
    print("test_stage2_soft_conditions_do_not_block OK")


def test_selection_val_gate_requires_unseen_split():
    """ランダム分割の改善率は構成汎化の証拠にならない (M1不合格)"""
    conds = evaluate_conditions(
        _inputs(sel_val={"gain_pct": 9.9, "split": "random"}), stage=1)
    m1 = next(c for c in conds if c.cid == "M1")
    assert m1.ok is False, m1
    conds2 = evaluate_conditions(
        _inputs(sel_val={"gain_pct": VAL_GAIN_GATE_PCT,
                         "split": "unseen_teams"}), stage=1)
    m1b = next(c for c in conds2 if c.cid == "M1")
    assert m1b.ok is True, m1b
    print("test_selection_val_gate_requires_unseen_split OK")


def test_registration_gaps():
    entries = {
        "完全": {"技": ["a", "b", "c", "d"], "性格": "ようき",
               "能力ポイント": {"h": 1}, "持ち物": "x", "特性": "y"},
        "技不足": {"技": ["a"], "性格": "ようき", "能力ポイント": {"h": 1}},
        "空": {},
    }
    gaps = registration_gaps(entries)
    assert "完全" not in gaps
    assert gaps["技不足"] == ["技1/4"], gaps["技不足"]
    assert set(gaps["空"]) == {"技", "性格", "能力ポイント"}, gaps["空"]
    print("test_registration_gaps OK")


def test_paired_verdict():
    # 提案が20戦中+6勝の差 → 採用推奨
    a = [1] * 16 + [0] * 4
    b = [1] * 10 + [0] * 10
    v = paired_verdict(a, b)
    assert v["verdict"] == "採用推奨" and v["mean"] > 0, v
    # 同一 → 誤差の範囲 (差の分散0でもクラッシュしない)
    v2 = paired_verdict([1, 0, 1, 0], [1, 0, 1, 0])
    assert v2["verdict"] == "差は誤差の範囲", v2
    # 現行が上 → 現行維持
    v3 = paired_verdict(b, a)
    assert v3["verdict"] == "現行維持", v3
    # 検定不能
    assert paired_verdict([], [1])["verdict"] == "検定不能"
    print("test_paired_verdict OK")


def test_render_report_marks():
    conds = [
        Condition("X1", "必須OK", True, True, "ok"),
        Condition("X2", "必須NG", True, False, "ng", "直し方"),
        Condition("X3", "推奨NG", False, None, "未計測", "測り方"),
    ]
    text = render_report(conds, stage=1)
    assert "✅ [X1/必須]" in text and "❌ [X2/必須]" in text \
        and "⚠️ [X3/推奨]" in text
    assert "運用不可" in text and "直し方" in text
    print("test_render_report_marks OK")


def test_evolve_args_stage_semantics():
    import argparse
    from tools.team_proposal import _evolve_args
    ns = argparse.Namespace(
        population=10, generations=3, battles=60, concurrency=3,
        forecast_mix=0.3, archive_mix=0.2, locked="ミミッキュ",
        max_changes=2, set_mut=0.5, seed=7)
    a1 = _evolve_args(1, ns)
    assert a1.seed_myteam is True and a1.max_changes == 2
    assert a1.forecast_mix == 0.0 and a1.archive_mix == 0.0, \
        "段階1は現行メタのみで評価する"
    assert a1.update_archive is False, "提案運用はPSROアーカイブを汚さない"
    a2 = _evolve_args(2, ns)
    assert a2.seed_myteam is False and a2.forecast_mix == 0.3
    print("test_evolve_args_stage_semantics OK")


def test_mutate_item_clause_uses_alternative():
    """種族入替時のアイテムクローズ衝突は None ではなく使用率次点へ差し替える
    (2026-08-21 第8回: 持ち物なしのマスカーニャが提案された)"""
    import random

    import tools.evolve_teams as ev

    # どちらのスロットが入れ替わっても残る側が choicescarf を持つ
    # (= 新セットのscarfは必ず衝突する) 構成にして決定的にする
    team = ("Mimikyu @ choicescarf\nLevel: 50\n- playrough\n\n"
            "Staraptor @ choicescarf\nLevel: 50\n- bravebird")
    row = {"pokemon_name": "meowscarada", "item_name": "choicescarf",
           "ability_name": "protean", "tera_type": None, "nature": "jolly",
           "evs": "0/32/0/0/0/32", "weight": 10.0,
           "move1": "flowertrick", "move2": "knockoff",
           "move3": "uturn", "move4": "tripleaxel"}
    prev = ev._usage_cache
    ev._usage_cache = {"meowscarada": {
        "moves": [], "spreads": [],
        "items": [("choicescarf", 40.0), ("focussash", 30.0)]}}
    try:
        out = ev.mutate(team, [row], random.Random(0))
        blk = next(b for b in out.split("\n\n") if "eowscarada" in b)
        head = blk.split("\n")[0]
        assert " @ " in head, f"持ち物なしで生成された: {head}"
        item = head.split(" @ ")[1].strip().lower().replace(" ", "")
        assert item == "focussash", head   # scarf衝突→次点の未使用品
    finally:
        ev._usage_cache = prev
    print("test_mutate_item_clause_uses_alternative OK")


def test_has_build_requires_substance():
    """空エントリ (全None) は登録済み扱いにしない (第8回: マスカーニャ)"""
    import advisor.my_team as mt

    orig = mt._load
    mt._load = lambda: {
        "空": {"特性": None, "持ち物": None, "技": None},
        "技のみ": {"技": ["じゃれつく"]},
        "配分のみ": {"能力ポイント": {"h": 1}},
    }
    try:
        assert mt.has_build("空") is False
        assert mt.has_build("技のみ") is True
        assert mt.has_build("配分のみ") is True
        assert mt.has_build("未登録") is False
        assert mt.has_build(None) is False
    finally:
        mt._load = orig
    print("test_has_build_requires_substance OK")


def test_latest_selection_roster_reads_newest_log():
    """現在の6体の推定は直近対戦ログの選出ロスターを最優先する (2026-08-22:
    パーティ変更直後、技登録の有無による推定が旧構成へ引きずられ、
    選出データ収集が旧構成で走った)"""
    import json
    import tempfile
    import time
    from pathlib import Path

    from tools.evaluate_team import _latest_selection_roster

    d = Path(tempfile.mkdtemp())
    rec = {"type": "scene", "scene": "selection", "t": time.time(),
           "state": {"player": {"party": [
               {"ja": n} for n in ("ア", "イ", "ウ", "エ", "オ", "カ")]}}}
    (d / "battle_20990101_000000.jsonl").write_text(
        json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
    assert _latest_selection_roster(log_dir=d) == \
        ["ア", "イ", "ウ", "エ", "オ", "カ"]
    assert _latest_selection_roster(log_dir=Path(tempfile.mkdtemp())) == []
    print("test_latest_selection_roster_reads_newest_log OK")


def test_myteam_text_completes_missing_evs():
    """能力ポイント未登録の種は使用率配分で補完する (2026-08-22:
    0ポイントのままShowdownバリデーションに拒否され収集が全滅した)。
    championsの spread 行は「性格のみ」と「配分のみ」が混在するため、
    EVを持つ行と性格を持つ行を別々に最頻選択する。"""
    import advisor.my_team as mt
    import tools.evolve_teams as ev
    from tools.evaluate_team import build_myteam_text

    orig_load = mt._load
    orig_cache = ev._usage_cache
    mt._load = lambda: {"マスカーニャ": {}}
    ev._usage_cache = {"meowscarada": {
        "moves": [("flowertrick", 90.0), ("knockoff", 80.0),
                  ("uturn", 70.0), ("tripleaxel", 60.0)],
        "items": [],
        "spreads": [("jolly", None, 57.7),          # 性格のみ (最頻)
                    (None, "2/32/0/0/0/32", 40.0)]  # 配分のみ
    }}
    try:
        text = build_myteam_text()
        blk = next(b for b in text.split("\n\n") if "eowscarada" in b)
        assert "EVs: 2 HP / 32 Atk / 32 Spe" in blk, blk
        assert "Jolly Nature" in blk, blk
    finally:
        mt._load = orig_load
        ev._usage_cache = orig_cache
    print("test_myteam_text_completes_missing_evs OK")


def test_myteam_text_completes_ability_and_item():
    """特性・持ち物未登録は使用率最頻で補完する (2026-08-25 第9回:
    特性が五十音順先頭の「しんりょく」になり、持ち物なしのまま提案された)"""
    import advisor.my_team as mt
    import advisor.sets as sets_mod
    import tools.evolve_teams as ev
    from tools.evaluate_team import build_myteam_text

    class _Pred:
        def predict(self, sid):
            return {"moves": [("flowertrick", 90.0), ("knockoff", 80.0),
                              ("uturn", 70.0), ("tripleaxel", 60.0)],
                    "items": [("focussash", 40.0), ("choicescarf", 30.0)],
                    "abilities": [("protean", 80.0), ("overgrow", 20.0)],
                    "found": True}

    orig_load, orig_pred = mt._load, sets_mod.get_predictor
    orig_cache = ev._usage_cache
    mt._load = lambda: {"マスカーニャ": {}}
    sets_mod.get_predictor = lambda: _Pred()
    ev._usage_cache = {"meowscarada": {
        "moves": [], "items": [],
        "spreads": [("jolly", "2/32/0/0/0/32", 57.7)]}}
    try:
        text = build_myteam_text()
        blk = next(b for b in text.split("\n\n") if "eowscarada" in b)
        head = blk.split("\n")[0]
        assert " @ " in head and "ocussash" in head.lower(), \
            f"持ち物が補完されていない: {head}"
        assert "Ability: protean" in blk, blk
    finally:
        mt._load = orig_load
        sets_mod.get_predictor = orig_pred
        ev._usage_cache = orig_cache
    print("test_myteam_text_completes_ability_and_item OK")


def test_mutate_set_item_change_ignores_change_limit():
    """種族の変更上限を使い切っていても持ち物は全枠変更できる
    (2026-08-25 第9回指摘: 持ち物は2体縛りでも6体全ての変更を許可)"""
    import random

    import tools.evolve_teams as ev
    from tools.evolve_teams import Constraint, mutate_set

    seed = ("Mimikyu @ lifeorb\nLevel: 50\n- playrough\n\n"
            "Staraptor @ choicescarf\nLevel: 50\n- bravebird")
    # 2枠目が別種に置き換わり済み (max_changes=1 到達) → 種族・技・配分の
    # 変異は置換済み枠に限られるが、持ち物はミミッキュ枠でも変わる
    team = ("Mimikyu @ lifeorb\nLevel: 50\n- playrough\n\n"
            "Garchomp @ choicescarf\nLevel: 50\n- earthquake")
    c = Constraint(seed, [], 1)
    assert c.mutable_slots(team) == [1], "前提: 変更可能枠は置換済みの1枠のみ"
    prev = ev._usage_cache
    # ミミッキュにだけitem候補を与える (他の変異は不発になる構成)
    ev._usage_cache = {"mimikyu": {
        "moves": [], "spreads": [], "items": [("redcard", 50.0)]}}
    try:
        out = mutate_set(team, random.Random(0), c)
        head = out.split("\n\n")[0].split("\n")[0]
        assert "redcard" in head.replace(" ", "").lower(), \
            f"変更上限到達時にミミッキュの持ち物が変わらなかった: {head}"
    finally:
        ev._usage_cache = prev
    print("test_mutate_set_item_change_ignores_change_limit OK")


def main() -> None:
    test_stage1_all_hard_pass()
    test_stage1_blocks_on_stale_usage_and_missing_party()
    test_unmeasured_hard_condition_blocks()
    test_stage2_soft_conditions_do_not_block()
    test_selection_val_gate_requires_unseen_split()
    test_registration_gaps()
    test_paired_verdict()
    test_render_report_marks()
    test_evolve_args_stage_semantics()
    test_mutate_item_clause_uses_alternative()
    test_has_build_requires_substance()
    test_latest_selection_roster_reads_newest_log()
    test_myteam_text_completes_missing_evs()
    test_myteam_text_completes_ability_and_item()
    test_mutate_set_item_change_ignores_change_limit()
    print("\nALL OK")


if __name__ == "__main__":
    main()
