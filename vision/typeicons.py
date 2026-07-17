"""タイプアイコンの分類。

タイプアイコンは「単色の角丸パネル + 白抜き模様」。
パネル色 (Lab色空間距離) を第一判定とし、色が近いタイプ同士は
images/type_templates のテンプレート (dHash) で判別する。
テンプレートが無いタイプも色だけで判定できる。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import cv2
import numpy as np

TYPE_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "images" / "type_templates"

# 各タイプの代表色 (BGR)。SV/チャンピオンズ系UIの配色
TYPE_COLORS = {
    "ノーマル": (159, 161, 159),
    "ほのお": (41, 40, 230),
    "みず": (239, 128, 41),
    "でんき": (0, 192, 250),
    "くさ": (41, 161, 63),
    "こおり": (243, 206, 61),
    "かくとう": (0, 128, 255),
    "どく": (203, 65, 145),
    "じめん": (33, 81, 145),
    "ひこう": (239, 185, 129),
    "エスパー": (121, 65, 239),
    "むし": (25, 161, 145),
    "いわ": (129, 169, 175),
    "ゴースト": (112, 65, 112),
    "ドラゴン": (225, 96, 80),
    "あく": (78, 77, 98),
    "はがね": (184, 161, 96),
    "フェアリー": (239, 112, 239),
}

_TEMPLATE_HASHES: Optional[dict] = None
_DHASH_SIZE = 16


def _dhash(gray) -> np.ndarray:
    resized = cv2.resize(gray, (_DHASH_SIZE + 1, _DHASH_SIZE), interpolation=cv2.INTER_AREA)
    return (resized[:, 1:] > resized[:, :-1]).flatten()


def _load_templates() -> dict:
    global _TEMPLATE_HASHES
    if _TEMPLATE_HASHES is not None:
        return _TEMPLATE_HASHES
    _TEMPLATE_HASHES = {}
    if TYPE_TEMPLATE_DIR.exists():
        for p in TYPE_TEMPLATE_DIR.iterdir():
            if p.suffix.lower() not in (".png", ".jpg", ".jpeg"):
                continue
            tmpl = cv2.imread(str(p), cv2.IMREAD_UNCHANGED)
            if tmpl is None:
                continue
            if tmpl.ndim == 3 and tmpl.shape[2] == 4:
                alpha = tmpl[:, :, 3]
                x, y, w, h = cv2.boundingRect(alpha)
                tmpl = tmpl[y:y + h, x:x + w, :3]
            gray = cv2.cvtColor(tmpl, cv2.COLOR_BGR2GRAY)
            _TEMPLATE_HASHES[p.stem] = _dhash(gray)
    return _TEMPLATE_HASHES


def _panel_color(crop_img) -> Optional[np.ndarray]:
    """アイコンのパネル色 (白い模様と背景を除いた中央値) を推定"""
    if crop_img is None or crop_img.size == 0:
        return None
    h, w = crop_img.shape[:2]
    if h < 6 or w < 6:
        return None
    # 中央領域のみ使用 (角丸の外側=背景の混入を防ぐ)
    cy0, cy1 = int(h * 0.18), int(h * 0.85)
    cx0, cx1 = int(w * 0.18), int(w * 0.85)
    core = crop_img[cy0:cy1, cx0:cx1]
    hsv = cv2.cvtColor(core, cv2.COLOR_BGR2HSV)
    # 白い模様 (低彩度・高輝度) を除外
    not_white = cv2.inRange(hsv, np.array([0, 40, 40]), np.array([180, 255, 255]))
    pixels = core[not_white > 0]
    if len(pixels) < 20:
        # ノーマル/いわ等の低彩度タイプ: 白よりやや暗い画素を採用
        not_bright = cv2.inRange(hsv, np.array([0, 0, 60]), np.array([180, 255, 215]))
        pixels = core[not_bright > 0]
        if len(pixels) < 20:
            return None
    return np.median(pixels, axis=0)


def _lab(bgr) -> np.ndarray:
    arr = np.uint8([[list(map(int, bgr))]])
    return cv2.cvtColor(arr, cv2.COLOR_BGR2LAB)[0, 0].astype(np.float32)


_TYPE_LABS = {name: _lab(c) for name, c in TYPE_COLORS.items()}


def classify_type_icon(crop_img, color_cutoff=45.0) -> Optional[str]:
    """タイプアイコン画像からタイプ名 (日本語) を返す。判定不能なら None"""
    color = _panel_color(crop_img)
    if color is None:
        return None
    lab = _lab(color)
    ranked = sorted(
        ((float(np.linalg.norm(lab - tl)), name) for name, tl in _TYPE_LABS.items()),
    )
    best_dist, best_name = ranked[0]
    second_dist, second_name = ranked[1]
    if best_dist > color_cutoff:
        return None

    # 色が拮抗している場合はテンプレート (模様の形) で判別
    if second_dist - best_dist < 12.0:
        templates = _load_templates()
        gray = cv2.cvtColor(crop_img, cv2.COLOR_BGR2GRAY)
        qh = _dhash(gray)
        cand = [(d, n) for d, n in ranked[:3] if n in templates]
        if cand:
            best_t = min(
                cand,
                key=lambda dn: int(np.count_nonzero(qh != templates[dn[1]])),
            )
            return best_t[1]
    return best_name
