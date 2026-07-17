"""OCRユーティリティ。

EasyOCR (日本語+英語) を遅延ロードし、UIの文字種別ごとに前処理を変えて読む。

- 白文字+黒縁取り (メッセージ/ポップアップ): 縁取り検証つき白マスク
- パネル上の白文字 (名前/技名など): 白マスク
- 数字 (HP/PP/COMMAND): allowlist付き
"""
from __future__ import annotations

import warnings
from typing import Optional

import cv2
import numpy as np

warnings.filterwarnings("ignore", category=UserWarning, module="torch.utils.data.dataloader")

_reader = None


def get_reader():
    global _reader
    if _reader is None:
        import easyocr
        print("[vision.ocr] Loading EasyOCR model...")
        _reader = easyocr.Reader(["ja", "en"], gpu=True)
        print("[vision.ocr] EasyOCR model loaded.")
    return _reader


def _pad_invert(mask, pad=20):
    """白マスク -> 黒文字/白背景 のOCR入力へ"""
    inverted = cv2.bitwise_not(mask)
    return cv2.copyMakeBorder(inverted, pad, pad, pad, pad,
                              cv2.BORDER_CONSTANT, value=255)


def white_text_mask(img, val_min=170, sat_max=70, scale=2.5):
    """パネル上の白文字を抽出してOCR入力画像を返す"""
    if img is None or img.size == 0:
        return None
    resized = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    hsv = cv2.cvtColor(resized, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([0, 0, val_min]), np.array([180, sat_max, 255]))
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    if cv2.countNonZero(mask) < 20:
        return None
    return _pad_invert(mask)


def outlined_text_mask(img, scale=2.5):
    """縁取り文字 (白文字+黒フチ、背景は任意のゲーム画面) を抽出する。

    白画素のうち「近傍に暗画素があるもの」だけを残すことで、
    背景の白っぽい模様 (観客席など) を除去する。
    """
    if img is None or img.size == 0:
        return None
    resized = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    hsv = cv2.cvtColor(resized, cv2.COLOR_BGR2HSV)
    white = cv2.inRange(hsv, np.array([0, 0, 175]), np.array([180, 75, 255]))
    gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
    dark = cv2.inRange(gray, 0, 90)
    # 黒フチを膨張させ、その近傍にある白画素のみ文字とみなす
    kernel = np.ones((9, 9), np.uint8)
    near_dark = cv2.dilate(dark, kernel)
    mask = cv2.bitwise_and(white, near_dark)
    kernel2 = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel2)
    # 小さすぎる成分 (ノイズ) を除去
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    out = np.zeros_like(mask)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] >= 25:
            out[labels == i] = 255
    if cv2.countNonZero(out) < 60:
        return None
    return _pad_invert(out)


def read_crop_direct(crop_img, scale=2.0, allowlist: Optional[str] = None) -> str:
    """マスク処理をせず、拡大した生画像を直接OCRする。

    縁取り文字はEasyOCRが直接読む方が精度が高い (マスクは検知/安定化用)。
    """
    if crop_img is None or crop_img.size == 0:
        return ""
    resized = cv2.resize(crop_img, None, fx=scale, fy=scale,
                         interpolation=cv2.INTER_CUBIC)
    reader = get_reader()
    kwargs = {"detail": 0}
    if allowlist:
        kwargs["allowlist"] = allowlist
    res = reader.readtext(resized, **kwargs)
    return "".join(res).replace(" ", "") if res else ""


def read_text(processed, allowlist: Optional[str] = None) -> str:
    """前処理済み画像をOCRして連結テキストを返す"""
    if processed is None:
        return ""
    reader = get_reader()
    kwargs = {"detail": 0}
    if allowlist:
        kwargs["allowlist"] = allowlist
    res = reader.readtext(processed, **kwargs)
    return "".join(res).replace(" ", "") if res else ""


def read_zone_text(img, zone, mode="panel", allowlist: Optional[str] = None,
                   val_min=170) -> str:
    """ゾーンを切り出してOCR。mode: 'panel' | 'outline'"""
    from vision.zones import crop
    c = crop(img, zone)
    if c is None:
        return ""
    if mode == "outline":
        processed = outlined_text_mask(c)
    else:
        processed = white_text_mask(c, val_min=val_min)
    return read_text(processed, allowlist)


def parse_fraction(text: str):
    """'197/197' 形式 -> (cur, max)。読めなければ None"""
    import re
    if not text:
        return None
    m = re.search(r"(\d+)\s*[/1lI|]\s*(\d+)", text)
    if m:
        cur, mx = int(m.group(1)), int(m.group(2))
        if 0 < mx <= 999 and cur <= mx * 2:
            return (min(cur, mx), mx)
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 2 and len(digits) % 2 == 0:
        half = len(digits) // 2
        cur, mx = int(digits[:half]), int(digits[half:])
        if 0 < mx <= 999 and cur <= mx:
            return (cur, mx)
    return None


def parse_percent(text: str):
    """'79%' -> 79。読めなければ None"""
    import re
    if not text:
        return None
    digits = re.sub(r"\D", "", text)
    if not digits:
        return None
    val = int(digits)
    if val > 100:
        # '100' の誤読 / % が数字扱いされた場合の補正
        if str(val).startswith("100"):
            return 100
        val = int(str(val)[:2])
    return val if 0 <= val <= 100 else None


def hp_bar_ratio(img) -> Optional[float]:
    """HPバー領域から残量比率 (0..1) を色で推定する。

    バーの色は 緑(高) / 黄(中) / 赤(低)。バー背景は暗色。
    """
    if img is None or img.size == 0:
        return None
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    green = cv2.inRange(hsv, np.array([35, 90, 110]), np.array([85, 255, 255]))
    yellow = cv2.inRange(hsv, np.array([18, 90, 130]), np.array([34, 255, 255]))
    red1 = cv2.inRange(hsv, np.array([0, 110, 120]), np.array([9, 255, 255]))
    red2 = cv2.inRange(hsv, np.array([170, 110, 120]), np.array([180, 255, 255]))
    fill = cv2.bitwise_or(cv2.bitwise_or(green, yellow), cv2.bitwise_or(red1, red2))

    col = (fill > 0).sum(axis=0)
    h, w = fill.shape
    filled_cols = (col > h * 0.3).astype(np.uint8)
    if filled_cols.sum() < 2:
        return 0.0 if (fill > 0).sum() < 10 else None
    # バーは左詰め。右端の連続した空きを除いた割合
    idx = np.where(filled_cols > 0)[0]
    left, right = idx[0], idx[-1]
    # バー全幅はゾーン幅とみなす (ゾーンをバーにフィットさせる前提)
    ratio = (right - left + 1) / float(w)
    return max(0.0, min(1.0, ratio))
