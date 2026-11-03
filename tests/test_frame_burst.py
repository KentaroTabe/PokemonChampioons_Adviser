"""連続フレームの保存の計画 (vision/frame_burst.BurstPlanner、2026-10-07 段 0) と、古い保存の掃除 (tools/cleanup_logs) の検証。

    python -m tests.test_frame_burst
"""
from __future__ import annotations

from pathlib import Path

from tools.cleanup_logs import old_burst_dirs
from vision import frame_burst as FB
from vision.frame_burst import BurstPlanner
from vision.scenes import (SCENE_BATTLE_HUD, SCENE_COMMAND, SCENE_FIELD, SCENE_MOVE_SELECT, SCENE_SELECTION,
                           SCENE_STANDBY)


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


def test_planner_starts_on_move_select():
    """2026-10-07 実機確認: command が 1 度も認識されない対戦でも、move_select で始める"""
    bp = BurstPlanner(count=3, seconds=30.0, every=1, enabled=True, fallback_sec=60.0)
    assert bp.on_processed(SCENE_SELECTION, 1, 0.0) is None
    assert bp.on_processed(SCENE_BATTLE_HUD, 1, 10.0) is None
    b = bp.on_processed(SCENE_MOVE_SELECT, 1, 12.0)
    assert b and b["reason"] == "scene:move_select" and b["battle_seq"] == 1 and b["battle_index"] == 1
    assert b["reason"] == FB.scene_reason(SCENE_MOVE_SELECT)
    c = BurstPlanner(count=3, seconds=30.0, every=1, enabled=True).on_processed(SCENE_COMMAND, 4, 0.0)
    assert c["reason"] == "scene:command" and c["battle_seq"] == 4
    print("test_planner_starts_on_move_select OK")


def test_planner_starts_on_timeout():
    """開始の場面が来なくても、対戦の場面に入った最初のフレーム (選出・待機以外) から FALLBACK_SEC で始める"""
    bp = BurstPlanner(count=3, seconds=30.0, every=1, enabled=True, fallback_sec=60.0)
    for t in (0.0, 30.0, 61.0, 90.0):                                   # 選出・待機は起点にしない (60 秒を超えても始めない)
        assert bp.on_processed(SCENE_SELECTION if t < 61 else SCENE_STANDBY, 1, t) is None
    assert bp.on_processed(SCENE_FIELD, 1, 100.0) is None               # 起点 = 100
    assert bp.on_processed(SCENE_BATTLE_HUD, 1, 159.9) is None
    assert bp.on_processed(SCENE_SELECTION, 1, 150.0) is None           # 起点の後の選出の誤分類は起点を動かさない
    b = bp.on_processed(SCENE_FIELD, 1, 160.0)
    assert b and b["reason"] == FB.REASON_TIMEOUT == "timeout" and b["battle_index"] == 1
    assert bp.on_battle_end(170.0) is None                              # 始めた対戦は skipped にしない
    # 次の対戦: 起点は対戦ごとに数え直す (前の対戦の起点からの経過では始めない)
    assert bp.on_processed(SCENE_SELECTION, 2, 300.0) is None
    assert bp.on_processed(SCENE_FIELD, 2, 310.0) is None
    assert bp.on_processed(SCENE_FIELD, 2, 369.0) is None
    assert bp.on_processed(SCENE_FIELD, 2, 370.0)["reason"] == "timeout"
    print("test_planner_starts_on_timeout OK")


def test_planner_skipped_reasons():
    """始めなかった対戦の理由 4 種 (no_start_scene / count_exhausted / not_every / disabled)"""
    bp = BurstPlanner(count=1, seconds=30.0, every=2, enabled=True, fallback_sec=60.0)
    assert bp.on_battle_end(0.0) is None                                # まだ対戦を見ていない
    assert bp.is_new_battle(1)
    bp.on_processed(SCENE_SELECTION, 1, 0.0)
    bp.on_processed(SCENE_FIELD, 1, 10.0)                               # 対象 (1 戦目) だが開始の場面も timeout も無い
    assert not bp.is_new_battle(1) and bp.is_new_battle(2)
    sk = bp.on_battle_end(20.0)
    assert sk == {"skipped": True, "reason": FB.SKIP_NO_START_SCENE, "battle_seq": 1, "battle_index": 1}, sk
    assert sk["reason"] == "no_start_scene"
    assert bp.on_battle_end(21.0) is None                               # 同じ対戦で 2 回は返さない
    bp.on_processed(SCENE_COMMAND, 2, 100.0)                            # 2 戦目は対象外
    assert bp.on_battle_end(110.0)["reason"] == FB.SKIP_NOT_EVERY == "not_every"
    assert bp.on_processed(SCENE_COMMAND, 3, 200.0)                     # 3 戦目で 1 回使い切る
    assert bp.on_battle_end(210.0) is None
    bp.on_processed(SCENE_COMMAND, 4, 300.0)
    assert bp.on_battle_end(310.0)["reason"] == "not_every"
    assert bp.on_processed(SCENE_COMMAND, 5, 400.0) is None             # 5 戦目は対象だが回数の上限
    sk5 = bp.on_battle_end(410.0)
    assert sk5["reason"] == FB.SKIP_COUNT_EXHAUSTED == "count_exhausted" and sk5["battle_index"] == 5
    off = BurstPlanner(count=3, seconds=30.0, every=1, enabled=False)
    assert off.on_processed(SCENE_COMMAND, 7, 0.0) is None
    assert off.on_battle_end(10.0) == {"skipped": True, "reason": FB.SKIP_DISABLED, "battle_seq": 7, "battle_index": 1}
    print("test_planner_skipped_reasons OK")


def test_old_burst_dirs():
    dirs = [Path("burst_300"), Path("burst_100"), Path("burst_200"), Path("burst_x")]
    assert old_burst_dirs(dirs, 2) == [Path("burst_x"), Path("burst_100")]
    assert old_burst_dirs(dirs, 10) == []
    print("test_old_burst_dirs OK")


def main():
    test_planner_starts_on_every_other_battle_and_stops_at_count()
    test_planner_does_not_overlap()
    test_planner_starts_on_move_select()
    test_planner_starts_on_timeout()
    test_planner_skipped_reasons()
    test_old_burst_dirs()
    print("ALL OK")


if __name__ == "__main__":
    main()
