"""多世界探索 (P7) のテスト — 純粋計算のみ (DB・対戦は使わない)。

    scripts/run_test.sh test_belief_search

相手型の仮説ごとの search() 結果を仮説重みで統合する aggregate_worlds と、
SpreadEstimator.top_k (事後確率の正規化・刈り込み) を検証する。
"""
from __future__ import annotations

import math

from advisor.ev_infer import SpreadEstimator
from advisor.search import _finalize, aggregate_worlds


def _world(actions):
    """[(label, expected, worst)] -> search() と同じ形の結果 (推奨順ソート済み)"""
    res = [{"label": l, "kind": "move", "move_id": l, "bench_index": None,
            "expected": e, "worst": w, "worst_reply": "x",
            "risky": (e - w) > 0.35} for l, e, w in actions]
    return _finalize(res, matrix=[{"my": l, "opp": "x", "v": e}
                                  for l, e, _ in actions])


def test_single_world_equals_search():
    """世界が1つなら統合結果は元の探索結果と同じ順位・値になる"""
    w = _world([("eq", 0.4, 0.1), ("sd", 0.3, 0.2)])
    out = aggregate_worlds([w], [1.0])
    assert [a["label"] for a in out["actions"]] == \
        [a["label"] for a in w["actions"]]
    assert out["actions"][0]["expected"] == w["actions"][0]["expected"]
    assert out["belief"] == {"k": 1, "coverage": 1.0, "stability": 1.0}
    print("test_single_world_equals_search OK")


def test_weighted_average_and_stability():
    """期待値は重み平均、安定度は統合後の最善が各世界でも最善だった重みの和"""
    w1 = _world([("eq", 0.6, 0.3), ("sd", 0.2, 0.1)])   # 世界1: eq が最善
    w2 = _world([("eq", 0.0, -0.2), ("sd", 0.5, 0.4)])  # 世界2: sd が最善
    out = aggregate_worlds([w1, w2], [0.75, 0.25])
    by = {a["label"]: a for a in out["actions"]}
    assert math.isclose(by["eq"]["expected"], 0.75 * 0.6 + 0.25 * 0.0, abs_tol=1e-3)
    assert math.isclose(by["sd"]["expected"], 0.75 * 0.2 + 0.25 * 0.5, abs_tol=1e-3)
    assert out["actions"][0]["label"] == "eq"
    assert math.isclose(out["belief"]["stability"], 0.75, abs_tol=1e-6)
    assert math.isclose(by["sd"]["support"], 0.25, abs_tol=1e-6)
    assert by["eq"]["expected_var"] > by["sd"]["expected_var"] * 0 and by["eq"]["expected_var"] > 0
    assert out["belief"]["k"] == 2
    print("test_weighted_average_and_stability OK")


def test_weights_normalized_and_coverage_kept():
    """重みは世界の和で正規化し、被覆率は呼び出し側の値を保持する"""
    w1 = _world([("a", 0.5, 0.5)])
    w2 = _world([("a", 0.1, 0.1)])
    out = aggregate_worlds([w1, w2], [0.6, 0.2], coverage=0.8)
    assert math.isclose(out["actions"][0]["expected"], 0.75 * 0.5 + 0.25 * 0.1, abs_tol=1e-3)
    assert out["belief"]["coverage"] == 0.8
    print("test_weights_normalized_and_coverage_kept OK")


def test_action_missing_in_some_world():
    """一部の世界にしか無い行動は、現れた世界の重みで正規化する"""
    w1 = _world([("a", 0.5, 0.5), ("b", 0.9, 0.9)])
    w2 = _world([("a", 0.5, 0.5)])
    out = aggregate_worlds([w1, w2], [0.5, 0.5])
    by = {a["label"]: a for a in out["actions"]}
    assert math.isclose(by["b"]["expected"], 0.9, abs_tol=1e-3)
    assert math.isclose(by["a"]["expected"], 0.5, abs_tol=1e-3)
    print("test_action_missing_in_some_world OK")


def test_empty_worlds_returns_none():
    assert aggregate_worlds([], []) is None
    assert aggregate_worlds([{"actions": []}], [1.0]) is None
    print("test_empty_worlds_returns_none OK")


def _estimator_with(logws):
    est = SpreadEstimator.__new__(SpreadEstimator)
    est.species_id = "garchomp"
    est.hyps = [{"nature": f"n{i}", "evs": {"atk": 252}, "item": f"i{i}",
                 "logw": lw} for i, lw in enumerate(logws)]
    est.n_obs = 0
    est.spe_lower = est.spe_upper = None
    est._choice_locked = False
    return est


def test_top_k_normalizes_over_all_hypotheses():
    """上位kの重みは全仮説で正規化した事後確率 (和 = 被覆率 ≤ 1)"""
    est = _estimator_with([math.log(0.5), math.log(0.3), math.log(0.2)])
    top = est.top_k(2)
    assert [h["nature"] for h in top] == ["n0", "n1"]
    assert math.isclose(sum(h["weight"] for h in top), 0.8, abs_tol=1e-3)
    assert est.top_k(1)[0]["item"] == est.best()["item"]   # k=1 は最尤仮説
    print("test_top_k_normalizes_over_all_hypotheses OK")


def test_top_k_prunes_light_hypotheses():
    """min_weight 未満は刈り込むが、最低1つは返す"""
    est = _estimator_with([math.log(0.9), math.log(0.09), math.log(0.01)])
    assert len(est.top_k(3, min_weight=0.05)) == 2
    assert len(est.top_k(3, min_weight=0.99)) == 1
    assert est.top_k(0) == []
    print("test_top_k_prunes_light_hypotheses OK")


if __name__ == "__main__":
    test_single_world_equals_search()
    test_weighted_average_and_stability()
    test_weights_normalized_and_coverage_kept()
    test_action_missing_in_some_world()
    test_empty_worlds_returns_none()
    test_top_k_normalizes_over_all_hypotheses()
    test_top_k_prunes_light_hypotheses()
