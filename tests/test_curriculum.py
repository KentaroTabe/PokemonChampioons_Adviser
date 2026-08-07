"""苦手カリキュラム (H5) のテスト。

    python -m tests.test_curriculum
"""
from __future__ import annotations

import json
import random
import tempfile
from pathlib import Path


def test_weight_formula():
    from tools.team_weights import to_weights
    # 平均0.4。負率0.9 -> 1+3*0.5=2.5 (上限) / 0.1 -> 1-0.9=0.5 (下限)
    w = to_weights([0.4, 0.4, 0.9, 0.1, 0.2])
    assert w[2] == 2.5 and w[3] == 0.5, w
    assert abs(w[0] - (1 + 3 * (0.4 - 0.4))) < 0.01
    print("test_weight_formula OK")


def test_load_team_weights_robustness():
    from champions_agent.env.ranked_teams import load_team_weights
    d = Path(tempfile.mkdtemp())
    ok = d / "w.json"
    ok.write_text(json.dumps({"weights": [1.0] * 60}), encoding="utf-8")
    assert load_team_weights(str(ok), 60) == [1.0] * 60
    # 長さ不一致 / 非正値 / 壊れたJSON / 未指定 -> None (一様のまま)
    bad_len = d / "b1.json"
    bad_len.write_text(json.dumps({"weights": [1.0] * 59}), encoding="utf-8")
    assert load_team_weights(str(bad_len), 60) is None
    bad_val = d / "b2.json"
    bad_val.write_text(json.dumps({"weights": [0.0] + [1.0] * 59}),
                       encoding="utf-8")
    assert load_team_weights(str(bad_val), 60) is None
    assert load_team_weights(str(d / "nofile.json"), 60) is None
    assert load_team_weights(None, 60) is None
    print("test_load_team_weights_robustness OK")


def test_weighted_sampling():
    """重み付き抽選が実際に分布を歪めること"""
    from champions_agent.env.ranked_teams import RankedTeambuilder
    tb = RankedTeambuilder(top_n=10, include_external=False,
                           rng=random.Random(7))
    n = len(tb.teams)
    weights = [1.0] * n
    weights[0] = 5.0
    tb.weights = weights
    counts = {i: 0 for i in range(n)}
    # yield_teamはpacked形式を返すので、抽選だけを直接検証する
    for _ in range(2000):
        text = tb.rng.choices(tb.teams, weights=tb.weights, k=1)[0]
        counts[tb.teams.index(text)] += 1
    expected0 = 2000 * 5.0 / (n - 1 + 5.0)
    assert abs(counts[0] - expected0) < expected0 * 0.25, \
        (counts[0], expected0)
    print(f"test_weighted_sampling OK (重み5の構築: {counts[0]}回 / "
          f"期待{expected0:.0f}回)")


def test_sweep_variant_env():
    """curr条件がOPP_TEAM_WEIGHTSを学習に渡し、評価では外すこと"""
    from tools.reward_sweep import ALL_VARIANTS
    curr = next(v for v in ALL_VARIANTS if v["name"] == "curr")
    assert "OPP_TEAM_WEIGHTS" in (curr.get("env") or {})
    ko = next(v for v in ALL_VARIANTS if v["name"] == "ko")
    assert ko["override"] == curr["override"], "報酬が揃っていない"
    assert not ko.get("env"), "対照条件に余計なenvがある"
    print("test_sweep_variant_env OK")


if __name__ == "__main__":
    test_weight_formula()
    test_load_team_weights_robustness()
    test_weighted_sampling()
    test_sweep_variant_env()
    print("\nALL OK")
