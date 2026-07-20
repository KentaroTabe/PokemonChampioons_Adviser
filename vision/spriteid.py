"""ポケモンアイコン画像による種族特定。

タイプアイコンから使用率ベースで候補を数体に絞った後 (advisor/infer.py)、
選出画面のポケモンアイコンを図鑑スプライト (images/templetes/{図鑑番号}.png) と
照合して種族を1体に特定する。

全1000種との照合は誤りやすいが、候補が2〜5体なら視覚照合で高精度に決まる。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import cv2
import numpy as np

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "images" / "templetes"

NORM = 48
_sprite_cache: dict = {}


def _load_sprite(num: int):
    """図鑑番号のスプライトを正規化して返す (bgr, mask) or None"""
    if num in _sprite_cache:
        return _sprite_cache[num]
    path = TEMPLATE_DIR / f"{num}.png"
    result = None
    if path.exists():
        img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if img is not None and img.ndim == 3 and img.shape[2] == 4:
            alpha = img[:, :, 3]
            x, y, w, h = cv2.boundingRect(alpha)
            if w > 4 and h > 4:
                bgr = cv2.resize(img[y:y+h, x:x+w, :3], (NORM, NORM),
                                 interpolation=cv2.INTER_AREA)
                mask = cv2.resize(alpha[y:y+h, x:x+w], (NORM, NORM),
                                  interpolation=cv2.INTER_NEAREST)
                result = (bgr, mask)
    _sprite_cache[num] = result
    return result


def _extract_foreground(crop_img):
    """アイコン領域から前景 (スプライト) を切り出す。背景=四隅の中央値色"""
    if crop_img is None or crop_img.size == 0:
        return None
    h, w = crop_img.shape[:2]
    if h < 12 or w < 12:
        return None
    c = max(2, min(h, w) // 6)
    corners = np.vstack([crop_img[:c, :c].reshape(-1, 3),
                         crop_img[:c, -c:].reshape(-1, 3),
                         crop_img[-c:, :c].reshape(-1, 3),
                         crop_img[-c:, -c:].reshape(-1, 3)])
    bg = np.median(corners, axis=0)
    dist = np.linalg.norm(crop_img.astype(np.float32) - bg, axis=2)
    fg = (dist > 45).astype(np.uint8) * 255
    fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    coords = cv2.findNonZero(fg)
    if coords is None:
        return None
    x, y, bw, bh = cv2.boundingRect(coords)
    if bw < 8 or bh < 8:
        return None
    bgr = cv2.resize(crop_img[y:y+bh, x:x+bw], (NORM, NORM),
                     interpolation=cv2.INTER_AREA)
    mask = cv2.resize(fg[y:y+bh, x:x+bw], (NORM, NORM),
                      interpolation=cv2.INTER_NEAREST)
    return bgr, mask


def _hist(bgr, mask):
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h = cv2.calcHist([hsv], [0, 1], mask, [16, 8], [0, 180, 0, 256])
    cv2.normalize(h, h, 0, 1, cv2.NORM_MINMAX)
    return h


def _dhash(bgr, mask):
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    if mask is not None:
        valid = gray[mask > 0]
        fill = int(valid.mean()) if valid.size else 0
        gray = np.where(mask > 0, gray, fill).astype(np.uint8)
    r = cv2.resize(gray, (17, 16), interpolation=cv2.INTER_AREA)
    return (r[:, 1:] > r[:, :-1]).flatten()


def identify_species_color(icon_crop, candidates: list,
                           accept: float = 0.38, margin: float = 0.05) -> Optional[tuple]:
    """フルカラーのアイコン (バトルHUD等) を候補スプライトと色+形状で照合する。

    選出画面のシルエット調アイコンと違い、バトルHUDのアイコンは原色なので
    色ヒストグラムが有効。
    戻り値: (species_id, 日本語名, スコア) or None
    """
    if not candidates:
        return None
    from advisor.dex import get_dex
    dex = get_dex()

    fg = _extract_foreground(icon_crop)
    if fg is None:
        return None
    q_bgr, q_mask = fg
    q_hist = _hist(q_bgr, q_mask)
    q_hash = _dhash(q_bgr, q_mask)

    scored = []
    for sid, prior, ja in candidates:
        sp = dex.species(sid)
        if sp is None:
            continue
        sprite = _load_sprite(sp["num"])
        if sprite is None:
            continue
        t_bgr, t_mask = sprite
        hist_corr = max(0.0, float(cv2.compareHist(q_hist, _hist(t_bgr, t_mask),
                                                    cv2.HISTCMP_CORREL)))
        hamming = int(np.count_nonzero(q_hash != _dhash(t_bgr, t_mask)))
        dhash_sim = 1.0 - hamming / q_hash.size
        visual = 0.6 * hist_corr + 0.4 * dhash_sim
        total = visual * (0.8 + 0.2 * min(prior * 2, 1.0))
        scored.append((total, visual, sid, ja))

    if not scored:
        return None
    scored.sort(reverse=True)
    total0, visual0, sid0, ja0 = scored[0]
    if visual0 < accept:
        return None
    if len(scored) >= 2 and total0 - scored[1][0] < margin:
        return None
    return (sid0, ja0, round(visual0, 3))


def _silhouette_score(q_mask, t_mask) -> float:
    """シルエット (前景マスク) のIoU。選出画面のアイコンは暗色シルエット調で
    色情報が失われているため、形状で照合する"""
    q = q_mask > 0
    t = t_mask > 0
    inter = np.logical_and(q, t).sum()
    union = np.logical_or(q, t).sum()
    return float(inter) / union if union else 0.0


def _mask_dhash(mask) -> np.ndarray:
    r = cv2.resize(mask, (17, 16), interpolation=cv2.INTER_AREA)
    return (r[:, 1:] > r[:, :-1]).flatten()


PRIOR_AUTO_ACCEPT = 0.85   # 使用率がこの確率以上なら視覚照合なしで確定


def identify_species(icon_crop, candidates: list,
                     accept: float = 0.55, margin: float = 0.03) -> Optional[tuple]:
    """アイコン画像を候補種族のスプライトと照合して特定する。

    candidates: [(species_id, prior確率, 日本語名)] (advisor.infer の出力)
    - 候補が実質1体 (prior >= 0.85) なら使用率だけで確定
    - 複数候補はシルエット形状 (IoU + マスクdHash) で判別
    戻り値: (species_id, 日本語名, スコア) or None (確信が持てない場合)
    """
    if not candidates:
        return None

    # 使用率が支配的なら視覚照合なしで確定
    sid, prior, ja = candidates[0]
    if prior >= PRIOR_AUTO_ACCEPT:
        return (sid, ja, round(prior, 3))

    from advisor.dex import get_dex
    dex = get_dex()

    fg = _extract_foreground(icon_crop)
    if fg is None:
        return None
    _q_bgr, q_mask = fg
    q_hash = _mask_dhash(q_mask)

    scored = []
    for sid, prior, ja in candidates:
        sp = dex.species(sid)
        if sp is None:
            continue
        sprite = _load_sprite(sp["num"])
        if sprite is None:
            continue
        _t_bgr, t_mask = sprite
        iou = _silhouette_score(q_mask, t_mask)
        hamming = int(np.count_nonzero(q_hash != _mask_dhash(t_mask)))
        dhash_sim = 1.0 - hamming / q_hash.size
        visual = 0.6 * iou + 0.4 * dhash_sim
        total = visual * (0.7 + 0.3 * min(prior * 2, 1.0))
        scored.append((total, visual, sid, ja))

    if not scored:
        return None
    scored.sort(reverse=True)
    total0, visual0, sid0, ja0 = scored[0]
    if visual0 < accept:
        return None
    if len(scored) >= 2 and total0 - scored[1][0] < margin:
        return None
    return (sid0, ja0, round(visual0, 3))
