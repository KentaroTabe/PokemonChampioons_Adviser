"""タイプアイコンの分類。

判定はグリフ形状 (dHash) 主体。テンプレートは実キャプチャ由来の
images/type_templates_real/{タイプ}_{n}.png (tools/make_type_templates.py で追加可能)。
OBS経由のキャプチャは色味が変わるため、色は補助 (テンプレートが無いタイプの
フォールバックと、形状スコアが拮抗した場合のタイブレーク) にのみ使う。

テンプレートが無いタイプ (でんき/どく/いわ 等) は代表色との距離で判定する。
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import cv2
import numpy as np

REAL_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "images" / "type_templates_real"

# 各タイプの代表色 (BGR)。テンプレート未整備タイプのフォールバック用
TYPE_COLORS = {
    "ノーマル": (159, 161, 159),
    "ほのお": (74, 70, 212),
    "みず": (217, 132, 106),
    "でんき": (0, 192, 250),
    "くさ": (41, 161, 63),
    "こおり": (242, 221, 149),
    "かくとう": (135, 186, 235),
    "どく": (203, 65, 145),
    "じめん": (91, 119, 163),
    "ひこう": (227, 182, 141),
    "エスパー": (121, 65, 239),
    "むし": (131, 194, 192),
    "いわ": (129, 169, 175),
    "ゴースト": (112, 65, 112),
    "ドラゴン": (199, 132, 142),
    "あく": (78, 77, 98),
    "はがね": (192, 179, 147),
    "フェアリー": (216, 131, 217),
}

_DHASH_SIZE = 16
_HASH_ACCEPT = 70        # ハミング距離がこれ以下なら形状一致とみなす (max 256)
_COLOR_CUTOFF = 50.0     # 色フォールバックのLab距離しきい値

_TEMPLATES: Optional[dict] = None   # {type: [{"hash": ndarray, "lab": ndarray}]}


def _dhash(bgr) -> np.ndarray:
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    resized = cv2.resize(gray, (_DHASH_SIZE + 1, _DHASH_SIZE), interpolation=cv2.INTER_AREA)
    return (resized[:, 1:] > resized[:, :-1]).flatten()


def _mean_lab(bgr) -> np.ndarray:
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    return lab.reshape(-1, 3).mean(axis=0).astype(np.float32)


def _core(crop_img):
    """角丸の外側 (背景パネル) の混入を減らすため中央部を使う"""
    h, w = crop_img.shape[:2]
    my, mx = int(h * 0.10), int(w * 0.10)
    return crop_img[my:h - my, mx:w - mx]


def _load_templates() -> dict:
    global _TEMPLATES
    if _TEMPLATES is not None:
        return _TEMPLATES
    _TEMPLATES = {}
    if REAL_TEMPLATE_DIR.exists():
        for p in sorted(REAL_TEMPLATE_DIR.iterdir()):
            if p.suffix.lower() not in (".png", ".jpg", ".jpeg"):
                continue
            name = p.stem.rsplit("_", 1)[0]
            img = cv2.imread(str(p))
            if img is None or img.shape[0] < 8 or img.shape[1] < 8:
                continue
            core = _core(img)
            _TEMPLATES.setdefault(name, []).append({
                "hash": _dhash(core),
                "lab": _mean_lab(core),
            })
    return _TEMPLATES


def _lab_of_color(bgr) -> np.ndarray:
    arr = np.uint8([[list(map(int, bgr))]])
    return cv2.cvtColor(arr, cv2.COLOR_BGR2LAB)[0, 0].astype(np.float32)


_TYPE_LABS = {name: _lab_of_color(c) for name, c in TYPE_COLORS.items()}


def classify_type_icon(crop_img, hash_accept: int = _HASH_ACCEPT,
                       color_cutoff: float = _COLOR_CUTOFF) -> Optional[str]:
    """タイプアイコン画像からタイプ名 (日本語) を返す。判定不能なら None"""
    if crop_img is None or crop_img.size == 0:
        return None
    if crop_img.shape[0] < 10 or crop_img.shape[1] < 10:
        return None

    core = _core(crop_img)

    # 空スロット (単タイプのポケモンはtype2側にのみアイコンが出る) の棄却。
    # アイコンは白い模様との高コントラストで分散が大きい (実測: 空≒5, アイコン≒70)
    # 空スロットに加え、画面遷移中の薄暗いフレームも棄却する
    # (実測: 空≒5, 遷移中≒23-30, 通常アイコン≒60-80)
    gray_std = float(cv2.cvtColor(core, cv2.COLOR_BGR2GRAY).std())
    if gray_std < 40.0:
        return None
    qhash = _dhash(core)
    qlab = _mean_lab(core)

    templates = _load_templates()
    scored = []
    color_ranked = []
    for name, entries in templates.items():
        best_h = min(int(np.count_nonzero(qhash != e["hash"])) for e in entries)
        best_c = min(float(np.linalg.norm(qlab - e["lab"])) for e in entries)
        # 形状 (0..1) + 色 (Lab距離/100) の複合スコア。
        # 形状が似たタイプ同士 (はがね/エスパー等) は色で分離できる
        total = best_h / 256.0 + 0.7 * (best_c / 100.0)
        scored.append((total, best_h, name))
        color_ranked.append((best_c, name))

    if scored:
        scored.sort()
        total0, h0, n0 = scored[0]
        # 弱い一致 (ぼやけたフレーム等) は誤確定を避けて None を返し、
        # 次のフレームでの再試行に委ねる (選出画面は繰り返し抽出される)
        if h0 <= 60 or total0 <= 0.45:
            return n0

    # 形状で決まらない場合: 色のみのフォールバック。
    # OBS経由は色味がシフトするため、静的な代表色より実キャプチャ由来の
    # テンプレート色を優先する (静的色だと でんき→かくとう 等の混同が起きる)。
    # 静的代表色はテンプレート未整備タイプ (どく/いわ) の補完にのみ使う
    for name, tl in _TYPE_LABS.items():
        if name not in templates:
            color_ranked.append((float(np.linalg.norm(qlab - tl)), name))
    color_ranked.sort()
    if color_ranked:
        dist, name = color_ranked[0]
        # 2位との距離差が小さい場合は判定しない (みず/ドラゴン等の青系同士を
        # 色だけで当てずっぽうに選ばない。次フレームのグリフ照合に委ねる)
        margin = color_ranked[1][0] - dist if len(color_ranked) > 1 else 99.0
        if dist <= color_cutoff and margin >= 5.0:
            return name
    return None
