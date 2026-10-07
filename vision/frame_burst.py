"""連続フレームの保存の計画 (2026-10-07 段 0、docs/USEFULNESS_VERIFICATION_PLAN_1007.md §2 の局面の標本 (3))。

約 10 秒おきの保存フレーム (DEBUG_DUMP_FRAMES=1) では、短時間しか出ない表示 (メッセージ・HP バーの動き) の取り逃しや
HP の固着、破棄フレームの救済を検証できない。1 回の起動 (接続テスト) につき FRAME_BURST_COUNT 回、FRAME_BURST_SECONDS 秒間、
受信したフレームを全部保存する。開始は FRAME_BURST_BATTLE_EVERY 戦ごとの、対戦の最初の決定画面 (command)。

BurstPlanner は時刻と場面だけで決める純粋な状態機械 (保存そのものは server が別スレッドで行う)。
"""
from __future__ import annotations

from typing import Optional

from champions_agent.config import (FRAME_BURST_BATTLE_EVERY, FRAME_BURST_COUNT, FRAME_BURST_ENABLED,
                                    FRAME_BURST_SECONDS)
from vision.scenes import SCENE_COMMAND


class BurstPlanner:
    def __init__(self, count: int = FRAME_BURST_COUNT, seconds: float = FRAME_BURST_SECONDS,
                 every: int = FRAME_BURST_BATTLE_EVERY, enabled: bool = FRAME_BURST_ENABLED):
        self.count = int(count)
        self.seconds = float(seconds)
        self.every = max(1, int(every))
        self.enabled = bool(enabled)
        self.started = 0                 # この起動で始めた回数
        self.battle_index = 0            # この起動で見た対戦の番号 (1 から)
        self._seq = None
        self._done_this_battle = False
        self.current: Optional[dict] = None   # {"id", "t_start", "t_end"}

    def on_processed(self, scene: Optional[str], battle_seq, now: float) -> Optional[dict]:
        """処理したフレームの場面と対戦の世代番号を渡す。保存を始めるときだけ {"id", "t_start", "t_end"} を返す"""
        if battle_seq != self._seq:
            self._seq = battle_seq
            self.battle_index += 1
            self._done_this_battle = False
        if not self.enabled or self.started >= self.count or self._done_this_battle:
            return None
        if self.active(now) is not None:
            return None
        if scene != SCENE_COMMAND or (self.battle_index - 1) % self.every != 0:
            return None
        self._done_this_battle = True
        self.started += 1
        self.current = {"id": f"burst_{int(now)}", "t_start": round(now, 2), "t_end": round(now + self.seconds, 2)}
        return dict(self.current)

    def active(self, now: float) -> Optional[str]:
        """いま保存中ならその id (保存先のディレクトリ名)。そうでなければ None"""
        if self.current and now <= self.current["t_end"]:
            return self.current["id"]
        return None
