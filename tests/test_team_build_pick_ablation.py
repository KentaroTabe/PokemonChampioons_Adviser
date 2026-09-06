"""選出方策 ablation の解析 (純粋関数) と S8a 結果の再利用のテスト。

    python -m tests.test_team_build_pick_ablation
"""
from __future__ import annotations

import json
import random
import tempfile
from pathlib import Path

from tools.team_build import pick_ablation as PA


def _outs(rng: random.Random, shared: list, p: float) -> list:
    return [1 if s < p else 0 for s in shared]


def test_analyze_uplift_and_reversal():
    rng = random.Random(3)
    shared = [rng.random() for _ in range(300)]
    # A: teampreview / generic では B より上、fresh では大きく下 (順位反転、差は CI 超え)
    results = {
        ("A", "teampreview"): _outs(rng, shared, 0.70), ("A", "generic"): _outs(rng, shared, 0.71),
        ("A", "fresh"): _outs(rng, shared, 0.72),
        ("B", "teampreview"): _outs(rng, shared, 0.50), ("B", "generic"): _outs(rng, shared, 0.55),
        ("B", "fresh"): _outs(rng, shared, 0.90),
        ("reference", "teampreview"): _outs(rng, shared, 0.80), ("reference", "generic"): _outs(rng, shared, 0.81),
        ("reference", "fresh"): _outs(rng, shared, 0.80), ("reference", "production"): _outs(rng, shared, 0.86),
    }
    a = PA.analyze(results, ["A", "B"])
    assert a["cells"]["A/teampreview"]["n"] == 300
    ub = a["uplift"]["B"]
    assert ub["total"]["mean"] > 0.3 and ub["total"]["state"] == "improved", ub
    assert ub["adapt"]["mean"] > 0.3 and ub["generic"]["mean"] < 0.15, ub
    assert a["uplift"]["A"]["total"]["state"] in ("equivalent", "uncertain"), a["uplift"]["A"]
    assert a["incumbent_advantage"]["mean"] > 0, a["incumbent_advantage"]
    assert a["orders"]["teampreview"] == ["A", "B"] and a["orders"]["fresh"] == ["B", "A"]
    assert a["orders"]["generic"] == ["A", "B"]
    revs = a["reversals"]
    assert len(revs["teampreview_vs_fresh"]) == 1 and revs["teampreview_vs_fresh"][0]["resolved"] is True, revs
    assert len(revs["generic_vs_fresh"]) == 1, revs
    d = a["vs_reference"]["B"]
    assert d["delta_tp"]["state"] == "degraded" and d["delta_fresh"]["state"] == "improved", d
    assert d["delta_final"]["mean"] > 0            # 候補 fresh (0.90) − 参照 production (0.86)
    md = PA.to_markdown(a, ["A", "B"])
    assert "B_incumbent" in md and "反転 (teampreview → fresh)" in md, md
    print("test_analyze_uplift_and_reversal OK")


def test_analyze_partial_conditions():
    rng = random.Random(5)
    shared = [rng.random() for _ in range(300)]
    results = {
        ("A", "teampreview"): _outs(rng, shared, 0.70), ("A", "fresh"): _outs(rng, shared, 0.71),
        ("B", "teampreview"): _outs(rng, shared, 0.60), ("B", "fresh"): _outs(rng, shared, 0.62),
        ("reference", "teampreview"): _outs(rng, shared, 0.80),
    }
    a = PA.analyze(results, ["A", "B"])
    assert a["reversals"]["teampreview_vs_fresh"] == [] and a["reversals"]["generic_vs_fresh"] == []
    assert a["incumbent_advantage"] is None
    assert "delta_fresh" not in a["vs_reference"]["A"] and "delta_final" not in a["vs_reference"]["A"]
    assert a["uplift"]["A"]["generic"] is None and a["uplift"]["A"]["total"] is not None
    md = PA.to_markdown(a, ["A", "B"])
    assert "順位反転なし (teampreview → fresh)" in md, md
    print("test_analyze_partial_conditions OK")


def test_reuse_outcomes_contiguous_only():
    with tempfile.TemporaryDirectory() as d:
        ev = Path(d)
        def w(stage, arm, off, n, outs):
            (ev / f"{stage}_{arm}_{off}_{n}.json").write_text(json.dumps({"outcomes": outs}), encoding="utf-8")
        w("s08a_screen", "L00", 0, 100, [1] * 100)
        w("s08a_screen", "L00", 100, 200, [0] * 200)
        w("s08a_screen", "L01", 0, 100, [1] * 100)
        w("s08a_screen", "L01", 300, 300, [1] * 300)      # 100〜300 が無い → 連続部分だけ
        w("s08a_screen", "L02", 0, 100, [1] * 50)         # 宣言戦数と不一致 → 使わない
        assert len(PA.reuse_outcomes(ev, "s08a_screen", "L00", 300)) == 300
        assert len(PA.reuse_outcomes(ev, "s08a_screen", "L00", 250)) == 250
        assert len(PA.reuse_outcomes(ev, "s08a_screen", "L01", 300)) == 100
        assert PA.reuse_outcomes(ev, "s08a_screen", "L02", 300) == []
        assert PA.reuse_outcomes(ev, "s08a_screen", "L09", 300) == []
    print("test_reuse_outcomes_contiguous_only OK")


if __name__ == "__main__":
    test_analyze_uplift_and_reversal()
    test_analyze_partial_conditions()
    test_reuse_outcomes_contiguous_only()
