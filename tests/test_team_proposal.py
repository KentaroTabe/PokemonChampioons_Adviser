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
    print("\nALL OK")


if __name__ == "__main__":
    main()
