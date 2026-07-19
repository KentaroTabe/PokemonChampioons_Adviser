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
    """テキスト描画アニメーション完了 (数フレーム変化なし) を検知する。

    トリガー後は done フラグが立ち、内容が大きく変わる (次のメッセージへの
    切り替わり) までは再トリガーしない。固定クールダウンは使わない
    (以前の25フレーム≒5秒のクールダウンは、連続で流れるメッセージを
    軒並み取りこぼす原因になっていた)。
    """

    def __init__(self, stable_frames=3, diff_threshold=200):
        self.prev = None
        self.stable_count = 0
        self.done = False
        self.stable_frames = stable_frames
        self.diff_threshold = diff_threshold

    def update(self, mask) -> bool:
        """処理済みマスクを受け取り、OCRすべきタイミングなら True"""
        if mask is None:
            self.prev = None
            self.stable_count = 0
            self.done = False
            return False

        trigger = False
        if self.prev is not None and self.prev.shape == mask.shape:
            changed = cv2.countNonZero(cv2.absdiff(self.prev, mask))
            if self.done:
                # 表示内容が切り替わったら次のメッセージの検知を再開
                if changed > self.diff_threshold * 4:
                    self.done = False
                    self.stable_count = 0
            elif changed <= self.diff_threshold:
                self.stable_count += 1
                if self.stable_count >= self.stable_frames:
                    trigger = True
                    self.done = True
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
            "left_popup": RegionStabilizer(stable_frames=2),
            "right_popup": RegionStabilizer(stable_frames=2),
        }
        self._last_masks: dict = {}     # source -> 前回OCR時のマスク
        self._last_ocr_ts: dict = {}    # source -> 前回OCR時刻
        self._last_heavy = {}       # scene -> last heavy extraction time
        self._selection_streak = 0  # 選出画面が連続何フレーム続いているか
        self._pending_scene = None  # シーン遷移の確定待ち (2フレーム連続で確定)
        self._pending_count = 0
        self._resolution_seen = True  # 前回command以降にfield/standbyを見たか
        self._heavy_interval = {
            "selection": 2.0,
            "command": 1.5,
            "move_select": 1.5,
            "watch": 1.5,
            "field_check": 1.5,
            "battle_hud": 2.5,
            "field_hp": 1.0,   # フィールドシーン中の軽量HP追跡 (疑似シーンキー)
        }

    # ------------------------------------------------------------------
    def reset(self):
        self.state.reset_battle()
        self.parser = EventParser(self.state, self.resolver)
        self._resolution_seen = True

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

        # シーン遷移は2フレーム連続で確定する。選出画面中の背景演出 (炎・
        # レーザー等) で単発のbattle_hud/command誤分類が起き、ターン誤加算・
        # HUD抽出による状態汚染・ログ分割につながった (実運用で観測)
        if not single_shot:
            if scene != self._pending_scene:
                self._pending_scene = scene
                self._pending_count = 1
            else:
                self._pending_count += 1
            if (scene != prev_scene and self._pending_count < 2
                    and prev_scene not in (None, "unknown")):
                scene = prev_scene   # 未確定の単発フレームは前のシーン扱い

        self.state.scene = scene
        fired: list = []

        # 選出画面のデバウンス: 対戦中の1フレーム誤分類で状態を壊さないよう、
        # 連続して選出画面と判定された場合のみ「選出画面にいる」とみなす
        if scene == "selection":
            self._selection_streak += 1
        else:
            self._selection_streak = 0
        selection_confirmed = single_shot or self._selection_streak >= 3

        # 新しい対戦の開始検知: バトル終了後に選出画面へ戻ったらリセット
        if scene == "selection" and selection_confirmed and self.state.battle_active:
            self.reset()
            self._selection_streak = 3
            self.state.scene = scene

        # ターンカウント: 「前回のコマンド画面以降に行動解決 (field/standby) を
        # 観測した」場合のみ、コマンド画面復帰を新ターンとみなす。
        # command<->move_select<->watch<->battle_hud間の分類揺れや画面往復では
        # 加算しない (遷移元シーンでの判定は揺れの経路に依存して過剰加算した)
        if scene in ("field", "standby"):
            self._resolution_seen = True
        elif scene == "command" and self._resolution_seen:
            self._resolution_seen = False
            self.state.turn += 1

        heavy = self._should_run_heavy(scene, force=single_shot)

        if scene == "selection" and heavy and selection_confirmed:
            extractors.extract_selection(img, self.state, self.resolver)
        elif scene in ("command", "move_select", "battle_hud") and heavy:
            extractors.extract_battle_hud(img, self.state, self.resolver)
            if scene == "move_select":
                extractors.extract_move_select(img, self.state, self.resolver)
        elif scene == "watch" and heavy:
            extractors.extract_watch(img, self.state, self.resolver)
        elif scene == "field_check" and heavy:
            extractors.extract_field_check(img, self.state, self.resolver)

        # --- メッセージ / ポップアップ (HUDが消えるフィールドシーンのみ。
        #     HUD表示中はタイマー等の誤OCRを防ぐため読まない) ---
        if scene == "field":
            # 技アニメーション中のHP変化を追い、直前の技イベントとダメージを
            # 対応付けられるようにする (HUDバナー表示中のみ内部でOCRする)
            if self._should_run_heavy("field_hp", force=single_shot):
                try:
                    extractors.extract_field_hp(img, self.state)
                except Exception:
                    pass
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

        # マスクは「縁取り文字が存在するか / 内容が変わったか」の検知に使い、
        # OCR自体は生のクロップに対して行う (精度が大きく向上する)
        if single_shot:
            if mask is None:
                return []
            text = ocr.read_crop_direct(crop_img)
            if not text:
                return []
            return self.parser.parse(text, source=source)

        # ストリーム: 技使用メッセージは表示時間が短く「安定待ち」では
        # 取りこぼすため、内容が変わったら即OCRする (同一テキストは
        # EventParser側の重複排除で二重処理されない)
        if mask is None:
            self._last_masks[source] = None
            return []
        now = time.time()
        prev = self._last_masks.get(source)
        changed = (prev is None or prev.shape != mask.shape
                   or cv2.countNonZero(cv2.absdiff(prev, mask)) > 250)
        if changed and now - self._last_ocr_ts.get(source, 0.0) >= 0.3:
            self._last_masks[source] = mask
            self._last_ocr_ts[source] = now
            text = ocr.read_crop_direct(crop_img)
            if text:
                return self.parser.parse(text, source=source)
        elif changed:
            self._last_masks[source] = mask
        return []
