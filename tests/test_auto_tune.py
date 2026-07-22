"""自律チューニング (auto_tune) の判定ロジックのテスト。

    scripts/run_test.sh test_auto_tune
"""
from __future__ import annotations

from champions_agent.train.auto_tune import (LADDER, MIN_CYCLES, decide)


def _fresh():
    return {"ladder_index": 0, "applied_at": 1.0, "trials": [], "settled": False}


def test_insufficient_data_keeps_config():
    st = _fresh()
    out = decide(dict(st), [0.5] * (MIN_CYCLES - 1))
    assert out["ladder_index"] == 0 and not out["trials"]
    print("test_insufficient_data_keeps_config OK")


def test_plateau_advances_ladder():
    st = _fresh()
    out = decide(dict(st), [0.5] * MIN_CYCLES)   # 試行1: 0.5
    # 最初の試行は「過去最良と同値」なので延長される
    assert out["ladder_index"] == 0 and len(out["trials"]) == 1
    # 延長後も改善なし (+EPS未満) -> 次の設定へ
    out2 = decide(out, [0.51] * MIN_CYCLES)
    assert out2["ladder_index"] == 1, out2
    print("test_plateau_advances_ladder OK")


def test_improvement_extends():
    st = _fresh()
    st["trials"] = [{"config": LADDER[0], "mean_bench": 0.50, "n_cycles": 7}]
    st["ladder_index"] = 1
    # 設定1で明確な改善 (0.56 > 0.50+0.02) -> 延長
    out = decide(dict(st), [0.56] * MIN_CYCLES)
    assert out["ladder_index"] == 1, out
    print("test_improvement_extends OK")


def test_settles_on_best():
    st = _fresh()
    st["ladder_index"] = len(LADDER) - 1
    st["trials"] = [
        {"config": LADDER[0], "mean_bench": 0.50, "n_cycles": 7},
        {"config": LADDER[1], "mean_bench": 0.62, "n_cycles": 7},  # 最良
        {"config": LADDER[2], "mean_bench": 0.48, "n_cycles": 7},
    ]
    out = decide(dict(st), [0.52] * MIN_CYCLES)   # 最終試行
    assert out["settled"] is True
    assert LADDER[out["ladder_index"]] == LADDER[1], out["ladder_index"]
    print("test_settles_on_best OK")


if __name__ == "__main__":
    test_insufficient_data_keeps_config()
    test_plateau_advances_ladder()
    test_improvement_extends()
    test_settles_on_best()
    print("\nALL OK")
