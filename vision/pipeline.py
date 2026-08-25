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

import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import cv2
import numpy as np

from vision import zones, ocr, scenes, extractors
from vision.events import EventParser
from vision.normalize import NameResolver
from vision.state import BattleStateV2

# ひんし観測からこの秒数以内の「選出画面」判定は死に出し画面とみなして
# 却下する (last_faint は交代の着地で先にクリアされるため、実際の窓は
# もっと短いことが多い。この値は取り逃し時の上限)
DEATH_SWITCH_GUARD_SEC = 20.0
# 選出以外のシーンがこのフレーム数連続したら選出画面の「訪問」を終了する
# (選出中の演出による単発の誤分類では訪問を跨がない)
SELECTION_LEAVE_FRAMES = 5


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
        self._in_selection = False  # 選出画面に確定滞在中か (離脱は5フレーム要求)
        self._sel_visit = False     # 確定選出画面の「訪問中」フラグ (再リセット防止)
        self._sel_leave = 0         # 選出以外が連続したフレーム数 (訪問終了判定)
        self._sel_anchor: dict = {"ok": None, "ts": 0.0}  # 対戦中の選出アンカー確認
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
    def _tick_field_effects(self) -> None:
        """新ターン確定時に天候/フィールド/トリックルームの残りターンを減算し、
        尽きたら消す。

        終了ダイアログはフレーム間引きで取り逃すことがある (実測: 天候終了の
        見逃し)。残りターンは開始メッセージ (5に設定) と場の状況画面 (n/m) で
        供給されるため、経過で自動失効させれば状態が古いまま残らない。
        終了メッセージを拾えた場合はそちらが先に消す (二重処理は無害)
        """
        f = self.state.field
        if f.weather and f.weather_turns is not None:
            f.weather_turns -= 1
            if f.weather_turns <= 0:
                self.state.log_event(
                    "system", f"天候({f.weather})がターン経過で終了と推定",
                    event_id="weather_expire")
                f.weather, f.weather_turns = None, None
        if f.terrain and f.terrain_turns is not None:
            f.terrain_turns -= 1
            if f.terrain_turns <= 0:
                self.state.log_event(
                    "system", f"フィールド({f.terrain})がターン経過で終了と推定",
                    event_id="terrain_expire")
                f.terrain, f.terrain_turns = None, None
        if f.trick_room and f.trick_room_turns is not None:
            f.trick_room_turns -= 1
            if f.trick_room_turns <= 0:
                self.state.log_event(
                    "system", "トリックルームがターン経過で終了と推定",
                    event_id="trickroom_expire")
                f.trick_room, f.trick_room_turns = False, None

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
    def _smooth_scene(self, scene: str, prev_scene: str) -> str:
        """シーン遷移のスムージング。

        - 通常の遷移は2フレーム連続で確定 (単発の誤分類を無視)
        - 選出画面に確定滞在中 (3フレーム連続で突入) は、離脱に5フレーム
          連続を要求する。選出中の炎・レーザー演出で選出シグナルが落ちつつ
          HUD条件が偶発成立し、commandが2フレーム通過する事象が残ったため。
          実際の対戦開始は連続フレームで来るので約1秒の遅延で済む
        """
        if scene != self._pending_scene:
            self._pending_scene = scene
            self._pending_count = 1
        else:
            self._pending_count += 1
        need = 5 if (self._in_selection and scene != "selection") else 2
        if (scene != prev_scene and self._pending_count < need
                and prev_scene not in (None, "unknown")):
            scene = prev_scene   # 未確定フレームは前のシーン扱い
        if scene == "selection":
            if self._selection_streak >= 2:   # このフレームで3連続目
                self._in_selection = True
        elif self._in_selection and self._pending_count >= need:
            self._in_selection = False
        return scene

    # ------------------------------------------------------------------
    def _selection_anchor_ok(self, img) -> bool:
        """選出画面のアンカー (「選出完了」バーのN/3表記) が読めるか。

        1秒スロットルでOCRし、結果をキャッシュする。対戦が本当に終わって
        次の選出画面に入った場合はアンカーが読めるため、そこで通過する。
        """
        now = time.time()
        if now - self._sel_anchor["ts"] < 1.0 and self._sel_anchor["ok"] is not None:
            return self._sel_anchor["ok"]
        self._sel_anchor["ts"] = now
        try:
            txt = ocr.read_zone_text(img, zones.SELECTION["complete_bar"],
                                     mode="panel") or ""
        except Exception:
            txt = ""
        # OCR崩れに耐える判定 (「0/3 選出完了」が 'DJ3運出買了' 等になる)
        ok = ("選出" in txt or "完了" in txt
              or bool(re.search(r"\d\s*/\s*3", txt))
              or ("了" in txt and ("出" in txt or "3" in txt)))
        self._sel_anchor["ok"] = ok
        return ok

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
            scene = self._smooth_scene(scene, prev_scene)

            # 対戦中の「選出画面」検知は交代画面等の誤検知であることがある
            # (実戦でT3中にリセット+ログ分割が発生)。対戦中に選出画面と
            # 認めるのは「選出完了/N/3」アンカーが実際に読めた場合のみ。
            # さらに、ひんし直後は死に出し (次のポケモンを選ぶ) 画面である
            # 可能性が高く、アンカーOCRの偶発通過で対戦中リセットが起きた
            # (2026-08-11: これが battle_active を落とし、以降の対戦が
            # 連結される連鎖の起点になった)。last_faint は交代で着地すると
            # クリアされるため、本物の次対戦の選出画面には残らない
            if scene == "selection" and self.state.battle_active:
                lf = getattr(self.state, "last_faint", None)
                in_death_switch = bool(
                    lf and time.time() - lf.get("ts", 0)
                    < DEATH_SWITCH_GUARD_SEC)
                if in_death_switch or not self._selection_anchor_ok(img):
                    scene = prev_scene if prev_scene != "selection" else "field"

        self.state.scene = scene
        fired: list = []

        # 選出画面のデバウンス: 対戦中の1フレーム誤分類で状態を壊さないよう、
        # 連続して選出画面と判定された場合のみ「選出画面にいる」とみなす
        if scene == "selection":
            self._selection_streak += 1
        else:
            self._selection_streak = 0
        selection_confirmed = single_shot or self._selection_streak >= 3

        # 新しい対戦の開始検知: 確定選出画面への「入場」で、状態に前の対戦の
        # 内容が残っていればリセットする。
        # 従来条件の battle_active は、対戦中の誤リセットで一度 False に
        # なると本物の次選出でもリセットが走らず、以降の全対戦の状態とログが
        # 連結され続けた (2026-08-11の実測: 1ファイルに約3試合)。
        # turn>0 を「前の対戦が載っている」証拠として併用する。turn は選出
        # 画面滞在中には増えないため、訪問エッジ判定 (_sel_visit) と合わせて
        # 同一選出内での再リセット (抽出済みロスターの消去) は起きない
        if scene == "selection":
            self._sel_leave = 0
            if selection_confirmed and not self._sel_visit:
                self._sel_visit = True
                if self.state.turn > 0 or self.state.battle_active:
                    self.reset()
                    self._selection_streak = 3
                    self.state.scene = scene
        else:
            self._sel_leave += 1
            if self._sel_visit and self._sel_leave >= SELECTION_LEAVE_FRAMES:
                self._sel_visit = False

        # ターンカウント: 「前回のコマンド画面以降に行動解決 (field/standby) を
        # 観測した」場合のみ、コマンド画面復帰を新ターンとみなす。
        # command<->move_select<->watch<->battle_hud間の分類揺れや画面往復では
        # 加算しない (遷移元シーンでの判定は揺れの経路に依存して過剰加算した)
        if scene in ("field", "standby"):
            self._resolution_seen = True
        elif scene == "command" and self._resolution_seen:
            self._resolution_seen = False
            self.state.turn += 1
            # 新ターンの行動選択に到達 = とんぼ交代の保留は解消済み
            # (無効化されて交代が発生しなかったケースのフラグ滞留を防ぐ)
            self.state.pending_pivot_switch = False
            self._tick_field_effects()

        heavy = self._should_run_heavy(scene, force=single_shot)

        if scene == "selection" and heavy and selection_confirmed:
            extractors.extract_selection(img, self.state, self.resolver)
        elif scene in ("command", "move_select", "battle_hud"):
            if heavy:
                extractors.extract_battle_hud(img, self.state, self.resolver)
                if scene == "move_select":
                    extractors.extract_move_select(img, self.state,
                                                   self.resolver)
            else:
                # 自分HPの実数はHUDに常時表示される。heavy間隔待ちだと
                # 2回安定の確定まで実測60秒超かかり、古い自分HPのまま
                # 交代助言が計算された (2026-08-20 第5回) ため毎フレーム読む
                extractors.extract_my_hud(img, self.state, self.resolver)
        elif scene == "watch":
            if heavy:
                extractors.extract_watch(img, self.state, self.resolver)
            else:
                # 側柱 (自分HP実数値/相手HP%) は毎フレーム読む。交代メニューの
                # 滞在は短く、1.5秒間隔ではひんしのゼロ読み確定 (2回) が
                # 間に合わないことがある (2026-08-18 第2回テスト)
                extractors.extract_watch_side_columns(
                    img, self.state, self.resolver)
        elif scene == "field_check" and heavy:
            extractors.extract_field_check(img, self.state, self.resolver)
        elif scene == scenes.SCENE_RESULT:
            # リザルト画面は順位/レート行を直接OCRして終了検出に流す。
            # 従来は field 誤分類時のメッセージ枠OCRに依存し、検出が決着から
            # 約10秒遅れ (連戦の表示窓ぎりぎり) だった (2026-08-25 第9回)
            fired += self._process_text_regions(img, [
                ("message", zones.RESULT["rate_row"]),
            ], single_shot)

        if heavy:
            # 自分側の静的情報 (タイプ=図鑑 / 持ち物・特性=登録) を補完する。
            # 画面読みだけだと「種族判明済みなのにタイプ欄が空」が残る
            try:
                extractors.backfill_player_static(self.state, self.resolver)
            except Exception:
                pass

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
            fired += self._process_text_regions(img, [
                ("message", zones.MESSAGE["text"]),
                ("left_popup", zones.MESSAGE["left_popup"]),
                ("right_popup", zones.MESSAGE["right_popup"]),
            ], single_shot)

        return self.state.to_dict(), fired

    # ------------------------------------------------------------------
    def _needs_ocr(self, img, source: str, zone, single_shot: bool):
        """OCRすべきクロップを返す (不要なら None)。状態の更新もここで行う。

        マスクは「縁取り文字が存在するか / 内容が変わったか」の検知に使い、
        OCR自体は生のクロップに対して行う (精度が大きく向上する)。
        OCR本体は呼び出し側が並列に実行するため、ここでは判定だけを行う。
        """
        crop_img = zones.crop(img, zone)
        if crop_img is None:
            return None
        mask = ocr.outlined_text_mask(crop_img)
        if single_shot:
            return crop_img if mask is not None else None

        # ストリーム: 技使用メッセージは表示時間が短く「安定待ち」では
        # 取りこぼすため、内容が変わったら即OCRする (同一テキストは
        # EventParser側の重複排除で二重処理されない)
        if mask is None:
            self._last_masks[source] = None
            return None
        prev = self._last_masks.get(source)
        changed = (prev is None or prev.shape != mask.shape
                   or cv2.countNonZero(cv2.absdiff(prev, mask)) > 250)
        if not changed:
            return None
        self._last_masks[source] = mask
        if time.time() - self._last_ocr_ts.get(source, 0.0) < 0.3:
            return None
        self._last_ocr_ts[source] = time.time()
        return crop_img

    def _process_text_regions(self, img, regions: list, single_shot: bool) -> list:
        """メッセージ/ポップアップ領域をまとめて処理する。

        Apple Vision の呼び出しは1回あたり数十msかかり、field シーンでは
        これが処理時間の大半を占める (実測: fieldが全体の54%)。OCRだけを
        並列実行し、状態を触る解析は元の順序で逐次に行う
        (フレーム落ち削減。解析順を保つのは帰属ロジックの前提のため)
        """
        pending = [(src, self._needs_ocr(img, src, zone, single_shot))
                   for src, zone in regions]
        pending = [(src, crop) for src, crop in pending if crop is not None]
        if not pending:
            return []
        if len(pending) == 1:
            texts = [ocr.read_crop_direct(pending[0][1])]
        else:
            with ThreadPoolExecutor(max_workers=len(pending)) as pool:
                texts = list(pool.map(lambda c: ocr.read_crop_direct(c),
                                      [c for _, c in pending]))
        fired = []
        for (src, _), text in zip(pending, texts):
            if text:
                fired += self.parser.parse(text, source=src)
        return fired
