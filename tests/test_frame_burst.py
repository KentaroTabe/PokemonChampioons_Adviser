"""連続フレームの保存の計画 (vision/frame_burst.BurstPlanner、2026-10-07 段 0) と、古い保存の掃除 (tools/cleanup_logs) の検証。

    python -m tests.test_frame_burst
"""
from __future__ import annotations

from pathlib import Path

from tools.cleanup_logs import old_burst_dirs
from vision.frame_burst import BurstPlanner
from vision.scenes import SCENE_COMMAND, SCENE_FIELD, SCENE_SELECTION


def test_planner_starts_on_every_other_battle_and_stops_at_count():
    bp = BurstPlanner(count=2, seconds=30.0, every=2, enabled=True)
    t = 1000.0
    assert bp.on_processed(SCENE_SELECTION, 5, t) is None            # 1 戦目: 決定画面まで待つ
    b = bp.on_processed(SCENE_COMMAND, 5, t + 1)
    assert b and b["id"] == "burst_1001" and b["t_end"] == 1031.0
    assert bp.active(t + 20) == "burst_1001" and bp.active(t + 40) is None
    assert bp.on_processed(SCENE_COMMAND, 5, t + 50) is None         # 同じ対戦では 1 回だけ
    assert bp.on_processed(SCENE_COMMAND, 6, t + 100) is None        # 2 戦目は飛ばす (2 戦ごと)
    assert bp.on_processed(SCENE_FIELD, 7, t + 200) is None          # 3 戦目: 決定画面で始める
    b3 = bp.on_processed(SCENE_COMMAND, 7, t + 201)
    assert b3 and bp.started == 2
    assert bp.on_processed(SCENE_COMMAND, 9, t + 400) is None        # 回数の上限
    off = BurstPlanner(count=3, seconds=30.0, every=1, enabled=False)
    assert off.on_processed(SCENE_COMMAND, 1, t) is None and off.active(t) is None
    print("test_planner_starts_on_every_other_battle_and_stops_at_count OK")


def test_planner_does_not_overlap():
    bp = BurstPlanner(count=3, seconds=30.0, every=1, enabled=True)
    assert bp.on_processed(SCENE_COMMAND, 1, 0.0)
    assert bp.on_processed(SCENE_COMMAND, 2, 10.0) is None           # 前の保存の最中は始めない
    assert bp.on_processed(SCENE_COMMAND, 2, 40.0)                   # 終わった後の同じ対戦の決定画面で始める
    print("test_planner_does_not_overlap OK")


def test_old_burst_dirs():
    dirs = [Path("burst_300"), Path("burst_100"), Path("burst_200"), Path("burst_x")]
    assert old_burst_dirs(dirs, 2) == [Path("burst_x"), Path("burst_100")]
    assert old_burst_dirs(dirs, 10) == []
    print("test_old_burst_dirs OK")


def main():
    test_planner_starts_on_every_other_battle_and_stops_at_count()
    test_planner_does_not_overlap()
    test_old_burst_dirs()
    print("ALL OK")


if __name__ == "__main__":
    main()
