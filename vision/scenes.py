"""フレームのシーン分類。

色ヒューリスティクスで以下を判定する:
- selection:   選出画面 (6匹から3匹選ぶ)
- standby:     選出完了後の待機画面 (対戦準備中)
- command:     バトル中のコマンド選択 (たたかう/ポケモン)
- move_select: バトル中の技選択 (4技リスト表示)
- watch:       様子を見る画面 (詳細パネル)
- field_check: 「場の状況」確認画面 (ランク倍率・場の効果の全画面オーバーレイ)
- battle_hud:  バトルHUDは出ているが上記以外 (結果待ち等)
- field:       HUDなしのフィールドシーン (アニメーション/メッセージ)
"""
from __future__ import annotations

import time

import cv2
import numpy as np

from vision import zones
from vision.zones import crop

# 「場の状況」画面のアンカー文言判定 (OCRは重いのでスロットリングする)
_FIELD_CHECK_ANCHOR_ZONE = {"x0": 0.45, "y0": 0.19, "x1": 0.75, "y1": 0.30}
_fc_cache = {"ts": 0.0, "result": False, "sig": None}

# 「対戦準備中」(選出確認) 画面のアンカー文言 (タイマー右)
_PREP_ANCHOR_ZONE = {"x0": 0.495, "y0": 0.090, "x1": 0.625, "y1": 0.140}
_prep_cache = {"ts": 0.0, "result": False, "sig": None}


def _looks_like_battle_prep(img) -> bool:
    """中央上の「対戦準備中」文言をOCRで確認する (スロットリング付き)"""
    now = time.time()
    sig = int(img[::64, ::64].mean() * 10)
    if now - _prep_cache["ts"] < 1.0 and sig == _prep_cache["sig"]:
        return _prep_cache["result"]
    _prep_cache["ts"] = now
    _prep_cache["sig"] = sig
    from vision import ocr
    from vision.normalize import similarity
    text = ocr.read_zone_text(img, _PREP_ANCHOR_ZONE)
    result = bool(text) and ("準備中" in text
                             or similarity(text[:5], "対戦準備中") >= 0.6)
    _prep_cache["result"] = result
    return result


def _looks_like_field_check(img) -> bool:
    """固定文言「効果と場の状態」ピルをOCRで確認する (同一画面向けスロットリング)"""
    now = time.time()
    # 画面が変わっていなければ1秒間は前回結果を使う (フレーム署名で判別)
    sig = int(img[::64, ::64].mean() * 10)
    if now - _fc_cache["ts"] < 1.0 and sig == _fc_cache["sig"]:
        return _fc_cache["result"]
    _fc_cache["ts"] = now
    _fc_cache["sig"] = sig
    from vision import ocr
    from vision.normalize import similarity
    text = ocr.read_zone_text(img, _FIELD_CHECK_ANCHOR_ZONE)
    result = bool(text) and (
        "効果と場の" in text or similarity(text[:7], "効果と場の状態") >= 0.6)
    _fc_cache["result"] = result
    return result


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


def _hp_bar_pixels(img) -> int:
    """HPバーの色 (緑/黄/赤) の画素数。バトルHUDの存在確認に使う"""
    if img is None or img.size == 0:
        return 0
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    green = cv2.inRange(hsv, np.array([35, 90, 110]), np.array([85, 255, 255]))
    yellow = cv2.inRange(hsv, np.array([18, 90, 130]), np.array([34, 255, 255]))
    red1 = cv2.inRange(hsv, np.array([0, 110, 120]), np.array([9, 255, 255]))
    red2 = cv2.inRange(hsv, np.array([170, 110, 120]), np.array([180, 255, 255]))
    return int(cv2.countNonZero(green) + cv2.countNonZero(yellow)
               + cv2.countNonZero(red1) + cv2.countNonZero(red2))


def detect_move_pills(img) -> list:
    """技リストのピル (白い縁取り線のペア) を検出する。

    ピルの上下境界は明るい白線として現れる。白線の水平プロファイルから
    「ピル高さ相当 (領域高の15〜21%) の間隔のペア」を組む。
    アリーナ床やリザルト画面のパネルはこの構造を持たないため誤検出しない。
    戻り値: [(top_rel, bottom_rel)] 画面高さに対する相対座標
    """
    region = zones.MOVES_REGION
    c = crop(img, region)
    if c is None:
        return []
    h = c.shape[0]
    hsv = cv2.cvtColor(c, cv2.COLOR_BGR2HSV)
    white = cv2.inRange(hsv, np.array([0, 0, 180]), np.array([180, 100, 255]))
    prof = (white > 0).mean(axis=1)

    lines = []
    for i, r in enumerate(prof):
        if r > 0.30:
            if lines and i - lines[-1][1] <= 3:
                lines[-1] = (lines[-1][0], i)
            else:
                lines.append((i, i))
    centers = [(a + b) // 2 for a, b in lines]

    gap_min, gap_max = 0.145 * h, 0.215 * h
    pills = []
    used = set()
    for i, a in enumerate(centers):
        if i in used:
            continue
        for j in range(i + 1, len(centers)):
            if j in used:
                continue
            gap = centers[j] - a
            if gap_min <= gap <= gap_max:
                pills.append((a, centers[j]))
                used.add(i)
                used.add(j)
                break
            if gap > gap_max:
                break
    # 下端の白線が検出できなかった上端線 (選択中で暗い等) は中央値の高さで補完
    if pills:
        med_gap = sorted(b - a for a, b in pills)[len(pills) // 2]
        for i, a in enumerate(centers):
            if i not in used and a + med_gap <= h:
                if not any(p[0] - 5 <= a <= p[1] + 5 for p in pills):
                    pills.append((a, a + med_gap))
    pills.sort()

    ry0, span = region["y0"], region["y1"] - region["y0"]
    return [(ry0 + span * a / h, ry0 + span * b / h) for a, b in pills]


# シーン種別の定数 (シーン名の唯一の源)。classify が返す文字列と一致する。
# 下流 (battle_logger / tools) はこの定数を参照し、リテラルを直書きしない
SCENE_SELECTION = "selection"
SCENE_STANDBY = "standby"
SCENE_COMMAND = "command"
SCENE_MOVE_SELECT = "move_select"
SCENE_WATCH = "watch"
SCENE_BATTLE_HUD = "battle_hud"
SCENE_FIELD = "field"
SCENE_FIELD_CHECK = "field_check"


def classify(img) -> dict:
    """シーンと根拠スコアを返す。{"scene": str, "scores": {...}}"""
    scores = {}

    # --- バトルHUD: 相手バナー(赤) + 自分バナー(紫) + 自分のHPバー ---
    # (リザルト画面等の紫パネルをHUDと誤認しないよう、HPバーの色画素も要求する)
    opp_banner = _crimson_ratio(crop(img, zones.BATTLE["opp_banner"]))
    my_banner = _purple_ratio(crop(img, zones.BATTLE["my_banner"]))
    bar_px = _hp_bar_pixels(crop(img, zones.BATTLE["my_hp_bar"]))
    scores["opp_banner"] = round(opp_banner, 3)
    scores["my_banner"] = round(my_banner, 3)
    scores["my_hp_bar_px"] = bar_px
    hud = opp_banner > 0.15 and my_banner > 0.10 and bar_px > 30

    # --- 場の状況画面: 画面中央を覆う藍色(自分)/深紅(相手)の大パネル + アンカー文言 ---
    # 自分側ページは藍色パネル+タブのライム枠が「様子を見る」の条件にも合致するため、
    # 様子を見る判定より先にアンカー文言 (効果と場の状態) を確認する。
    # パネル色相は照明で振れるため、広いレンジで「大パネルが覆っている」ことだけ見る
    center_cover = _ratio_in_range(crop(img, zones.WATCH["center_panel"]),
                                   [90, 30, 40], [180, 255, 255])
    scores["fc_center_cover"] = round(center_cover, 3)
    if center_cover > 0.45 and _looks_like_field_check(img):
        return {"scene": "field_check", "scores": scores}

    # --- 様子を見る画面: 中央の大きな紫パネル + 能力タブ(黄緑) ---
    center = _purple_ratio(crop(img, zones.WATCH["center_panel"]))
    tab = _ratio_in_range(crop(img, zones.WATCH["tab_bar"]), [30, 100, 120], [50, 255, 255])
    scores["watch_center"] = round(center, 3)
    scores["watch_tab"] = round(tab, 3)
    if center > 0.45 and tab > 0.04:
        return {"scene": "watch", "scores": scores}

    # --- 対戦準備中 (選出確認) 画面: 左に自分ロスター+右に相手ロスター ---
    # 選出画面より行が内側に配置されるため selection 判定に掛からず、背景の
    # 炎・レーザー演出が battle_hud/command 条件を偶発的に満たして「幻のHUD」
    # が付与されていた (2026-07-24監査)。色の粗選別 → 文言アンカーで確定する
    prep_left = _purple_ratio(crop(img, {"x0": 0.150, "y0": 0.140,
                                         "x1": 0.310, "y1": 0.880})) + \
        _ratio_in_range(crop(img, {"x0": 0.150, "y0": 0.140,
                                   "x1": 0.310, "y1": 0.880}),
                        [30, 80, 120], [55, 255, 255])
    prep_right = _crimson_ratio(crop(img, {"x0": 0.690, "y0": 0.140,
                                           "x1": 0.850, "y1": 0.880}))
    scores["prep_left"] = round(prep_left, 3)
    scores["prep_right"] = round(prep_right, 3)
    if prep_left > 0.20 and prep_right > 0.20 and _looks_like_battle_prep(img):
        return {"scene": "standby", "scores": scores}

    # --- 選出画面: 相手パーティパネル(赤) + 選出完了バー(紫) ---
    # 自分側パネルは先頭2行のどちらかで判定する。カーソルが乗った行は
    # ライム色にハイライトされ紫判定が失敗するため、単一行だけを見ると
    # カーソルが先頭行にある間ずっと選出画面を取り逃す
    # (2026-08-18 欠陥#9: 2/3選出済みの選出画面が field に誤分類され、
    #  持ち物リストのOCRから item_player_marble の誤イベントまで発火した)。
    # ライム (カーソル行) も選出行の証拠として加算する
    opp_panel = _crimson_ratio(crop(img, zones.SELECTION_OPP[0]["panel"]))
    complete_bar = _purple_ratio(crop(img, zones.SELECTION["complete_bar"]))
    my_sel_panel = max(
        _purple_ratio(crop(img, z["panel"]))
        + _ratio_in_range(crop(img, z["panel"]), [30, 100, 120], [50, 255, 255])
        for z in zones.SELECTION_MY[:2])
    scores["sel_opp_panel"] = round(opp_panel, 3)
    scores["sel_complete"] = round(complete_bar, 3)
    scores["sel_my_panel"] = round(my_sel_panel, 3)

    # 選出3シグナル (相手赤パネル+自分紫パネル+選出完了バー) が揃う場合は
    # HUD判定より優先する。選出画面の背景演出 (炎・レーザー) がHUD条件を
    # 偶発的に満たし、選出中にcommand/battle_hud誤分類が出ていた。
    # 対戦画面でこの3ゾーンが同時に条件を満たすことはない
    if opp_panel > 0.30 and my_sel_panel > 0.25 and complete_bar > 0.20:
        return {"scene": "selection", "scores": scores}

    if not hud and opp_panel > 0.30 and my_sel_panel > 0.25:
        return {"scene": "standby", "scores": scores}

    if hud:
        # --- 技選択: 白い縁取り線ペアのピルが2行以上 ---
        pills = detect_move_pills(img)
        scores["move_pill_rows"] = len(pills)
        if len(pills) >= 2:
            return {"scene": "move_select", "scores": scores}

        # --- コマンド選択: 右下の丸ボタン (紫) ---
        buttons = _purple_ratio(crop(img, zones.BATTLE["action_buttons"]))
        scores["action_buttons"] = round(buttons, 3)
        if buttons > 0.10:
            return {"scene": "command", "scores": scores}
        return {"scene": "battle_hud", "scores": scores}

    return {"scene": "field", "scores": scores}
