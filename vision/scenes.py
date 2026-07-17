"""フレームのシーン分類。

色ヒューリスティクスで以下を判定する:
- selection:   選出画面 (6匹から3匹選ぶ)
- standby:     選出完了後の待機画面 (対戦準備中)
- command:     バトル中のコマンド選択 (たたかう/ポケモン)
- move_select: バトル中の技選択 (4技リスト表示)
- watch:       様子を見る画面 (詳細パネル)
- battle_hud:  バトルHUDは出ているが上記以外 (結果待ち等)
- field:       HUDなしのフィールドシーン (アニメーション/メッセージ)
"""
from __future__ import annotations

import cv2
import numpy as np

from vision import zones
from vision.zones import crop


def _ratio_in_range(img, lower, upper) -> float:
    if img is None or img.size == 0:
        return 0.0
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array(lower), np.array(upper))
    return cv2.countNonZero(mask) / float(img.shape[0] * img.shape[1])


def _crimson_ratio(img) -> float:
    """相手側UIの赤紫 (クリムゾン) 検出"""
    if img is None:
        return 0.0
    return (_ratio_in_range(img, [160, 80, 70], [180, 255, 255])
            + _ratio_in_range(img, [0, 80, 70], [8, 255, 255]))


def _purple_ratio(img) -> float:
    """自分側UIの青紫検出"""
    return _ratio_in_range(img, [105, 60, 60], [140, 255, 255])


def _white_ratio(img) -> float:
    return _ratio_in_range(img, [0, 0, 180], [180, 60, 255])


def classify(img) -> dict:
    """シーンと根拠スコアを返す。{"scene": str, "scores": {...}}"""
    scores = {}

    # --- バトルHUD: 相手バナー(赤) + 自分バナー(紫) ---
    opp_banner = _crimson_ratio(crop(img, zones.BATTLE["opp_banner"]))
    my_banner = _purple_ratio(crop(img, zones.BATTLE["my_banner"]))
    scores["opp_banner"] = round(opp_banner, 3)
    scores["my_banner"] = round(my_banner, 3)
    hud = opp_banner > 0.15 and my_banner > 0.10

    # --- 様子を見る画面: 中央の大きな紫パネル + 能力タブ(黄緑) ---
    center = _purple_ratio(crop(img, zones.WATCH["center_panel"]))
    tab = _ratio_in_range(crop(img, zones.WATCH["tab_bar"]), [30, 100, 120], [50, 255, 255])
    scores["watch_center"] = round(center, 3)
    scores["watch_tab"] = round(tab, 3)
    if center > 0.45 and tab > 0.04:
        return {"scene": "watch", "scores": scores}

    # --- 選出画面: 相手パーティパネル(赤) + 選出完了バー(紫) ---
    opp_panel = _crimson_ratio(crop(img, zones.SELECTION_OPP[0]["panel"]))
    complete_bar = _purple_ratio(crop(img, zones.SELECTION["complete_bar"]))
    my_sel_panel = _purple_ratio(crop(img, zones.SELECTION_MY[0]["panel"]))
    scores["sel_opp_panel"] = round(opp_panel, 3)
    scores["sel_complete"] = round(complete_bar, 3)
    scores["sel_my_panel"] = round(my_sel_panel, 3)

    if not hud and opp_panel > 0.30 and my_sel_panel > 0.25:
        if complete_bar > 0.20:
            return {"scene": "selection", "scores": scores}
        return {"scene": "standby", "scores": scores}

    if hud:
        # --- 技選択: 技行 (濃紺〜紫のピル) が2行以上 ---
        pill_rows = 0
        for row in zones.MOVE_ROWS:
            pill = _ratio_in_range(crop(img, row["row"]), [100, 60, 25], [145, 255, 230])
            if pill > 0.35:
                pill_rows += 1
        scores["move_pill_rows"] = pill_rows
        if pill_rows >= 3:
            return {"scene": "move_select", "scores": scores}

        # --- コマンド選択: 右下の丸ボタン (紫) ---
        buttons = _purple_ratio(crop(img, zones.BATTLE["action_buttons"]))
        scores["action_buttons"] = round(buttons, 3)
        if buttons > 0.10:
            return {"scene": "command", "scores": scores}
        return {"scene": "battle_hud", "scores": scores}

    return {"scene": "field", "scores": scores}
