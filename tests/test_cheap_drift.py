"""実験 14 (tools/team_build/experiments/cheap_drift) の純粋関数のテスト: 120 通りの点の広がり・順位相関・最良の一致。

    python -m tests.test_cheap_drift
"""
from __future__ import annotations

from itertools import permutations

from tools.team_build.experiments import cheap_drift as CD

PERMS = list(permutations(range(6), 3))


def _scores(fn):
    return [(p, fn(i, p)) for i, p in enumerate(PERMS)]


def test_perm_stats():
    base = _scores(lambda i, p: 0.3 + 0.4 * (i / len(PERMS)))          # 0.3 → 0.7 に広がる
    same = CD.perm_stats(base, base)
    assert same["n_perms"] == 120 and same["spread_ratio"] == 1.0 and same["rho"] == 1.0 and same["top1_same"] and same["top3_overlap"] == 3
    assert same["shift"] == 0.0
    # 平らになる: 全部ほぼ同じ値 (基準率 0.45) → 広がりの比が小さい、平均が基準率へ動く
    flat = _scores(lambda i, p: 0.45 + 0.001 * (i % 3))
    st = CD.perm_stats(base, flat)
    assert st["spread_ratio"] is not None and st["spread_ratio"] < 0.05 and abs(st["shift"] - (0.45 + 0.001 - 0.5)) < 0.01
    # 順位が逆: 広がりは同じで順位相関が −1、最良が違う
    rev = _scores(lambda i, p: 0.7 - 0.4 * (i / len(PERMS)))
    st2 = CD.perm_stats(base, rev)
    assert abs(st2["spread_ratio"] - 1.0) < 1e-6 and st2["rho"] == -1.0 and not st2["top1_same"] and st2["top3_overlap"] == 0
    # perm の集合が違えば共通部分、3 通り未満なら None
    assert CD.perm_stats(base[:2], base[:2]) is None
    part = CD.perm_stats(base, base[:50])
    assert part["n_perms"] == 50
    print("test_perm_stats OK")


def test_summarize_and_interpret():
    rows = [{"spread_ratio": 0.3, "rho": 0.9, "top1_same": True, "top3_overlap": 2, "shift": -0.1},
            {"spread_ratio": 0.4, "rho": 0.8, "top1_same": False, "top3_overlap": 1, "shift": -0.12},
            {"spread_ratio": 0.6, "rho": 0.7, "top1_same": True, "top3_overlap": 3, "shift": -0.08}, None]
    s = CD.summarize(rows)
    assert s["n"] == 3 and s["spread_ratio_median"] == 0.4 and s["rho_median"] == 0.8 and s["verdict"] == "flattened"
    assert s["top1_same_share"] == round(2 / 3, 3) and s["top3_overlap_median"] == 2 and s["shift_median"] == -0.1
    s2 = CD.summarize([{"spread_ratio": 0.9, "rho": 0.2, "top1_same": False, "top3_overlap": 0, "shift": 0.0}])
    assert s2["verdict"] == "reshuffled" and s2["share_rho_below_0_5"] == 1.0
    s3 = CD.summarize([{"spread_ratio": 0.95, "rho": 0.95, "top1_same": True, "top3_overlap": 3, "shift": 0.0}])
    assert s3["verdict"] == "similar"
    assert CD.summarize([])["verdict"] == "no_data"
    assert "平ら" in CD.interpret(s, {"gain_pct": 3.2}) and "+3.2%" in CD.interpret(s, {"gain_pct": 3.2})
    assert "入れ替わ" in CD.interpret(s2, None) and "変わらない" in CD.interpret(s3, {})
    print("test_summarize_and_interpret OK")


def main() -> None:
    test_perm_stats()
    test_summarize_and_interpret()
    print("ALL OK")


if __name__ == "__main__":
    main()
