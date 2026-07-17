"""フレーム処理パイプライン。

毎フレーム:
 1. シーン分類 (色ヒューリスティクス、軽量)
 2. シーンに応じた抽出器を間引き実行 (OCRは重いので周期/変化検知で制御)
 3. メッセージ / ポップアップの安定化検知 -> OCR -> イベント解析

使い方:
    from vision.pipeline import VisionPipeline
    pipe = VisionPipeline()
    state_dict, events = pipe.process(frame)          # ストリーム (安定化検知あり)
    state_dict, events = pipe.process(img, single_shot=True)  # 静止画
"""
from __future__ import annotations

import time
from typing import Optional

import cv2
import numpy as np

from vision import zones, ocr, scenes, extractors
from vision.events import EventParser
from vision.normalize import NameResolver
from vision.state import BattleStateV2


class RegionStabilizer:
    """テキスト描画アニメーション完了 (数フレーム変化なし) を検知する"""

    def __init__(self, stable_frames=3, diff_threshold=200, cooldown=25):
        self.prev = None
        self.stable_count = 0
        self.done = False
        self.stable_frames = stable_frames
        self.diff_threshold = diff_threshold
        self.cooldown = cooldown
        self.cooldown_left = 0

    def update(self, mask) -> bool:
        """処理済みマスクを受け取り、OCRすべきタイミングなら True"""
        if mask is None:
            self.prev = None
            self.stable_count = 0
            self.done = False
            self.cooldown_left = 0
            return False

        if self.cooldown_left > 0:
            self.cooldown_left -= 1
            self.prev = mask
            return False

        trigger = False
        if self.prev is not None and self.prev.shape == mask.shape:
            changed = cv2.countNonZero(cv2.absdiff(self.prev, mask))
            if self.done:
                if changed > self.diff_threshold * 4:
                    self.done = False
                    self.stable_count = 0
            elif changed <= self.diff_threshold:
                self.stable_count += 1
                if self.stable_count >= self.stable_frames:
                    trigger = True
                    self.done = True
                    self.cooldown_left = self.cooldown
            else:
                self.stable_count = 0
        self.prev = mask
        return trigger


class VisionPipeline:
    def __init__(self):
        self.state = BattleStateV2()
        self.resolver = NameResolver()
        self.parser = EventParser(self.state, self.resolver)
        self._stabilizers = {
            "message": RegionStabilizer(),
            "left_popup": RegionStabilizer(stable_frames=2, cooldown=20),
            "right_popup": RegionStabilizer(stable_frames=2, cooldown=20),
        }
        self._last_heavy = {}       # scene -> last heavy extraction time
        self._heavy_interval = {
            "selection": 2.0,
            "command": 1.5,
            "move_select": 1.5,
            "watch": 1.5,
            "battle_hud": 2.5,
        }

    # ------------------------------------------------------------------
    def reset(self):
        self.state.reset_battle()
        self.parser = EventParser(self.state, self.resolver)

    # ------------------------------------------------------------------
    def _should_run_heavy(self, scene: str, force: bool) -> bool:
        if force:
            return True
        interval = self._heavy_interval.get(scene)
        if interval is None:
            return False
        now = time.time()
        if now - self._last_heavy.get(scene, 0.0) >= interval:
            self._last_heavy[scene] = now
            return True
        return False

    # ------------------------------------------------------------------
    def process(self, img, single_shot: bool = False):
        """1フレーム処理。戻り値: (state_dict, fired_events)"""
        if img is None:
            return self.state.to_dict(), []

        result = scenes.classify(img)
        scene = result["scene"]
        prev_scene = self.state.scene
        self.state.scene = scene
        fired: list = []

        # 新しい対戦の開始検知: バトル終了後に選出画面へ戻ったらリセット
        if scene == "selection" and prev_scene not in ("selection", "standby", "unknown"):
            if self.state.battle_active:
                self.reset()
                self.state.scene = scene

        heavy = self._should_run_heavy(scene, force=single_shot)

        if scene == "selection" and heavy:
            extractors.extract_selection(img, self.state, self.resolver)
        elif scene in ("command", "move_select", "battle_hud") and heavy:
            extractors.extract_battle_hud(img, self.state, self.resolver)
            if scene == "move_select":
                extractors.extract_move_select(img, self.state, self.resolver)
        elif scene == "watch" and heavy:
            extractors.extract_watch(img, self.state, self.resolver)

        # --- メッセージ / ポップアップ (HUDが消えるフィールドシーンのみ。
        #     HUD表示中はタイマー等の誤OCRを防ぐため読まない) ---
        if scene == "field":
            fired += self._process_text_region(img, "message", zones.MESSAGE["text"],
                                               single_shot)
            fired += self._process_text_region(img, "left_popup",
                                               zones.MESSAGE["left_popup"], single_shot)
            fired += self._process_text_region(img, "right_popup",
                                               zones.MESSAGE["right_popup"], single_shot)

        return self.state.to_dict(), fired

    # ------------------------------------------------------------------
    def _process_text_region(self, img, source: str, zone, single_shot: bool) -> list:
        crop_img = zones.crop(img, zone)
        if crop_img is None:
            return []
        mask = ocr.outlined_text_mask(crop_img)

        # マスクは「縁取り文字が存在するか」の検知と安定化判定に使い、
        # OCR自体は生のクロップに対して行う (精度が大きく向上する)
        if single_shot:
            if mask is None:
                return []
            text = ocr.read_crop_direct(crop_img)
            if not text:
                return []
            return self.parser.parse(text, source=source)

        stab = self._stabilizers[source]
        if stab.update(mask):
            text = ocr.read_crop_direct(crop_img)
            if text:
                return self.parser.parse(text, source=source)
        return []
