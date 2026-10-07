"""連続フレームの保存の計画 (2026-10-07 段 0、docs/USEFULNESS_VERIFICATION_PLAN_1007.md §2 の局面の標本 (3))。

約 10 秒おきの保存フレーム (DEBUG_DUMP_FRAMES=1) では、短時間しか出ない表示 (メッセージ・HP バーの動き) の取り逃しや
HP の固着、破棄フレームの救済を検証できない。1 回の起動 (接続テスト) につき FRAME_BURST_COUNT 回、FRAME_BURST_SECONDS 秒間、
受信したフレームを全部保存する。開始は FRAME_BURST_BATTLE_EVERY 戦ごとの対戦の、最初の決定画面 (FRAME_BURST_START_SCENES)。

2026-10-07 実機確認: 3 戦目は command が 1 度も認識されず (move_select 3 / battle_hud 1 / field 6 など)、保存が始まらなかった。
認識失敗を調べる標本が認識成功時にしか残らないのを避けるため、
- 開始の場面に move_select を足す
- 開始の場面が来なくても、対戦の場面に入った最初のフレーム (FALLBACK_SKIP_SCENES = 選出・待機 以外の最初のフレーム) から
  FRAME_BURST_FALLBACK_SEC 秒で始める (reason "timeout")。選出画面で対戦の世代 (battle_seq) が変わるので、世代が変わった時刻から
  数えると選出の時間 (数十秒) で決定画面より先に発火し、選出画面を保存してしまう
- 始めなかった対戦は on_battle_end が理由 (no_start_scene / count_exhausted / not_every / disabled) を返す

BurstPlanner は時刻と場面だけで決める純粋な状態機械 (保存そのものは server が別スレッドで行う)。
"""
from __future__ import annotations

from typing import Optional

from champions_agent.config import (FRAME_BURST_BATTLE_EVERY, FRAME_BURST_COUNT, FRAME_BURST_ENABLED,
                                    FRAME_BURST_FALLBACK_SEC, FRAME_BURST_SECONDS)
from vision.scenes import SCENE_COMMAND, SCENE_MOVE_SELECT, SCENE_SELECTION, SCENE_STANDBY

# 保存を始める場面 (対戦の最初の決定画面)
FRAME_BURST_START_SCENES = (SCENE_COMMAND, SCENE_MOVE_SELECT)
# timeout の起点にしない場面 (対戦の前の選出・待機)。この場面以外の最初のフレームから FALLBACK_SEC を数える
FALLBACK_SKIP_SCENES = (SCENE_SELECTION, SCENE_STANDBY)

REASON_TIMEOUT = "timeout"
SKIP_NO_START_SCENE = "no_start_scene"     # 対象の対戦だが、開始の場面も timeout も来なかった
SKIP_COUNT_EXHAUSTED = "count_exhausted"   # 対象の対戦だが、この起動の回数 (FRAME_BURST_COUNT) を使い切っていた
SKIP_NOT_EVERY = "not_every"               # EVERY 戦ごとの対象ではない対戦
SKIP_DISABLED = "disabled"                 # FRAME_BURST_ENABLED = False


def scene_reason(scene: str) -> str:
    return f"scene:{scene}"


class BurstPlanner:
    def __init__(self, count: int = FRAME_BURST_COUNT, seconds: float = FRAME_BURST_SECONDS,
                 every: int = FRAME_BURST_BATTLE_EVERY, enabled: bool = FRAME_BURST_ENABLED,
                 fallback_sec: float = FRAME_BURST_FALLBACK_SEC, start_scenes: tuple = FRAME_BURST_START_SCENES):
        self.count = int(count)
        self.seconds = float(seconds)
        self.every = max(1, int(every))
        self.enabled = bool(enabled)
        self.fallback_sec = float(fallback_sec)
        self.start_scenes = tuple(start_scenes)
        self.started = 0                 # この起動で始めた回数
        self.battle_index = 0            # この起動で見た対戦の番号 (1 から)
        self._seq = None
        self._done_this_battle = False
        self._ended = False              # この対戦の on_battle_end を返した
        self._battle_t0: Optional[float] = None   # この対戦で選出・待機以外の場面を最初に見た時刻 (timeout の起点)
        self.current: Optional[dict] = None   # {"id", "t_start", "t_end", "reason", "battle_seq", "battle_index"}

    def is_new_battle(self, battle_seq) -> bool:
        """このフレームで対戦が切り替わるか (server が on_processed より前に、前の対戦の on_battle_end を書くために使う)"""
        return battle_seq != self._seq

    def is_target(self) -> bool:
        """いまの対戦が EVERY 戦ごとの対象か"""
        return self.battle_index >= 1 and (self.battle_index - 1) % self.every == 0

    def on_processed(self, scene: Optional[str], battle_seq, now: float) -> Optional[dict]:
        """処理したフレームの場面と対戦の世代番号を渡す。保存を始めるときだけ
        {"id", "t_start", "t_end", "reason" ("scene:<場面>" / "timeout"), "battle_seq", "battle_index"} を返す"""
        if battle_seq != self._seq:
            self._seq = battle_seq
            self.battle_index += 1
            self._done_this_battle = False
            self._ended = False
            self._battle_t0 = None
        if self._battle_t0 is None and scene not in FALLBACK_SKIP_SCENES:
            self._battle_t0 = now
        if not self.enabled or self.started >= self.count or self._done_this_battle:
            return None
        if self.active(now) is not None:
            return None
        if not self.is_target():
            return None
        if scene in self.start_scenes:
            reason = scene_reason(scene)
        elif self._battle_t0 is not None and now - self._battle_t0 >= self.fallback_sec:
            reason = REASON_TIMEOUT
        else:
            return None
        self._done_this_battle = True
        self.started += 1
        self.current = {"id": f"burst_{int(now)}", "t_start": round(now, 2), "t_end": round(now + self.seconds, 2),
                        "reason": reason, "battle_seq": battle_seq, "battle_index": self.battle_index}
        return dict(self.current)

    def on_battle_end(self, now: float) -> Optional[dict]:
        """いまの対戦の終わり (次の対戦への切り替え・停止の前) に呼ぶ。保存を始めなかった対戦なら
        {"skipped": True, "reason", "battle_seq", "battle_index"} を返す (始めた対戦・まだ対戦を見ていない・2 回目の呼び出しは None)"""
        if self.battle_index < 1 or self._done_this_battle or self._ended:
            return None
        self._ended = True
        if not self.enabled:
            reason = SKIP_DISABLED
        elif not self.is_target():
            reason = SKIP_NOT_EVERY
        elif self.started >= self.count:
            reason = SKIP_COUNT_EXHAUSTED
        else:
            reason = SKIP_NO_START_SCENE
        return {"skipped": True, "reason": reason, "battle_seq": self._seq, "battle_index": self.battle_index}

    def active(self, now: float) -> Optional[str]:
        """いま保存中ならその id (保存先のディレクトリ名)。そうでなければ None"""
        if self.current and now <= self.current["t_end"]:
            return self.current["id"]
        return None
