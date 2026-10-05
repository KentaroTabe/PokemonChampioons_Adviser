"""WIN / LOSE の画面からの勝敗の読み取り (純粋計算。pipeline が毎フレーム呼ぶ)。

対戦の終わりは 1) 対戦画面の左下に勝負の文言 → 2) 左右に 2 人のトレーナーが並び、勝った側に黄色の WIN (月桂樹つき)、
負けた側に灰色の LOSE が大きく出る画面 (数秒) → 3) ランク画面、の順。自分は左。
2026-10-06 第18回接続テスト: 1) の文言を 15 戦中 6 戦で読めず (フレームの破棄率 40%)、3 戦が誤り / 不明になった。
2) は数秒出ていて色の特徴がはっきりしているので、文言にもレートにも頼らずに勝敗を確定できる。
判定は 3 つの色の割合 (zones.WIN_LOSE、閾値は champions_agent/config): 両側の名前の帯 (左 = 青、右 = 赤紫) があり、
片側の文字の枠だけが黄色い。
"""
from __future__ import annotations

from typing import Optional

import cv2
import numpy as np

from champions_agent.config import (WIN_LOSE_HSV, WIN_LOSE_PLATE_MIN, WIN_LOSE_TEXT_YELLOW_MAX_OTHER,
                                    WIN_LOSE_TEXT_YELLOW_MIN)
from vision import zones
from vision.zones import crop


def _ratio(img, key: str) -> float:
    if img is None or img.size == 0:
        return 0.0
    lo, hi = WIN_LOSE_HSV[key]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array(lo), np.array(hi))
    return cv2.countNonZero(mask) / float(img.shape[0] * img.shape[1])


def features(img) -> dict:
    """{"left_yellow", "right_yellow", "left_plate", "right_plate"} (各枠の色の割合)"""
    z = zones.WIN_LOSE
    return {"left_yellow": _ratio(crop(img, z["left_text"]), "yellow"),
            "right_yellow": _ratio(crop(img, z["right_text"]), "yellow"),
            "left_plate": _ratio(crop(img, z["left_plate"]), "blue"),
            "right_plate": _ratio(crop(img, z["right_plate"]), "magenta")}


def judge(f: dict, plate_min: float = WIN_LOSE_PLATE_MIN, yellow_min: float = WIN_LOSE_TEXT_YELLOW_MIN,
          yellow_max_other: float = WIN_LOSE_TEXT_YELLOW_MAX_OTHER) -> Optional[str]:
    """features() の値から "win" / "loss" / None (この画面ではない、または文字がまだ出ていない)。純粋"""
    if f.get("left_plate", 0.0) < plate_min or f.get("right_plate", 0.0) < plate_min:
        return None
    left, right = f.get("left_yellow", 0.0), f.get("right_yellow", 0.0)
    if left >= yellow_min and right <= yellow_max_other:
        return "win"
    if right >= yellow_min and left <= yellow_max_other:
        return "loss"
    return None


def read_win_lose(img) -> Optional[str]:
    """フレームから自分の勝敗を読む。"win" / "loss" / None"""
    if img is None or img.size == 0:
        return None
    return judge(features(img))
