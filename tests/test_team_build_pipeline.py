"""測定段パイプラインの純粋関数 (screening 生存の選抜、選出方策 variant の選択) のテスト。

    python -m tests.test_team_build_pipeline
"""
from __future__ import annotations

from tools.team_build import pipeline as P


def _arm(arm_id, mean, state="uncertain", eliminated_at=None, model=None):
    return {"arm_id": arm_id, "state": state, "eliminated_at": eliminated_at, "selection_model": model,
            "result": {"mean": mean} if mean is not None else None}


def test_select_survivors_orders_and_caps():
    res = {"arms": [_arm("A", -0.16, "degraded"), _arm("B", -0.09), _arm("C", -0.04),
                    _arm("D", 0.01, "equivalent"), _arm("E", -0.03, eliminated_at=100), _arm("F", None)]}
    assert P.select_survivors(res, None) == ["D", "C", "B", "F"]        # Δ 降順、degraded / 脱落は除外、None は末尾
    assert P.select_survivors(res, 2) == ["D", "C"]
    assert P.select_survivors({"arms": []}, 3) == []
    print("test_select_survivors_orders_and_caps OK")


def test_choose_variants_picks_best_non_degraded():
    res = {"arms": [
        _arm(P.variant_arm_id("L00", "fresh"), -0.18, "degraded", model="f0"),
        _arm(P.variant_arm_id("L00", "generic"), -0.12, "uncertain", model="g"),
        _arm(P.variant_arm_id("L03", "fresh"), 0.01, "uncertain", model="f3"),
        _arm(P.variant_arm_id("L03", "generic"), -0.27, "degraded", model="g"),
        _arm(P.variant_arm_id("L05", "fresh"), -0.20, "degraded", model="f5"),
        _arm(P.variant_arm_id("L05", "generic"), -0.19, "degraded", model="g"),
    ]}
    chosen = P.choose_variants(res)
    assert set(chosen) == {"L00", "L03"}, chosen                       # L05 は両 variant とも degraded
    assert chosen["L00"]["variant"] == "generic" and chosen["L00"]["selection_model"] == "g"
    assert chosen["L03"]["variant"] == "fresh" and chosen["L03"]["selection_model"] == "f3"
    assert chosen["L03"]["delta"] == 0.01
    assert P.split_variant("L03@fresh") == ("L03", "fresh")
    assert P.split_variant("L03") == ("L03", "fresh")
    assert P.split_variant("L03_C027@generic") == ("L03_C027", "generic")
    print("test_choose_variants_picks_best_non_degraded OK")


def test_surrogate_quality_metrics():
    from tools.team_build.review_run import spearman, surrogate_quality
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == 1.0
    assert spearman([1, 2, 3, 4], [40, 30, 20, 10]) == -1.0
    assert spearman([1, 2], [1, 2]) is None
    sets = [{"candidate_id": c, "ok": True, "score": s} for c, s in
            (("A", 1.3), ("B", 1.2), ("C", 1.1), ("D", 1.0), ("E", 0.9))] + [{"candidate_id": "X", "ok": False, "score": 2.0}]
    # 代理順位 A > B > C > D > E、実測は D > C > B > A (E は未測定)
    res = {"arms": [_arm("A", -0.26), _arm("B", -0.26), _arm("C", -0.12), _arm("D", -0.04), _arm("X", 0.5)]}
    q = surrogate_quality(sets, res, k=2)
    assert q["n"] == 4 and q["k"] == 2
    assert q["spearman"] is not None and q["spearman"] < 0, q
    assert q["precision_at_k"] == 0.0 and q["best_surrogate_rank"] == 4, q
    assert abs(q["regret_at_k"] - 0.22) < 1e-9, q          # -0.04 − (−0.26)
    assert surrogate_quality(sets, {"arms": []}) == {"n": 0}
    print("test_surrogate_quality_metrics OK")


if __name__ == "__main__":
    test_select_survivors_orders_and_caps()
    test_choose_variants_picks_best_non_degraded()
    test_surrogate_quality_metrics()
