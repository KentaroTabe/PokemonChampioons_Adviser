"""実戦の相手バンク (tools/real_opponents) を助言側から読む: 相手の選出率の事前分布。

- species_pick_prior(sid): その種が相手ロースターにいたとき実際に選出された割合 (出現が少なければ None)
- slot_weights(priors, mix): 相手スロットの重み。平均 1 を保ちつつ、よく選ばれる種を重く、選ばれない種を軽くする
バンクの読み込みは mtime を見てキャッシュする (接続テスト後に作り直される)。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from champions_agent.config import (REAL_BANK_PATH, SELECTION_REAL_PRIOR_CLIP, SELECTION_REAL_PRIOR_MIN_APPEAR,
                                    SELECTION_REAL_PRIOR_MIX)

REPO = Path(__file__).resolve().parent.parent
_cache = {"path": None, "mtime": None, "bank": None}


def load_bank(path: Optional[Path] = None) -> Optional[dict]:
    p = Path(path) if path else REPO / REAL_BANK_PATH
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return None
    if _cache["path"] == str(p) and _cache["mtime"] == mtime:
        return _cache["bank"]
    try:
        bank = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        bank = None
    _cache.update({"path": str(p), "mtime": mtime, "bank": bank})
    return bank


def species_pick_prior(sid: str, bank: Optional[dict] = None,
                       min_appear: int = SELECTION_REAL_PRIOR_MIN_APPEAR) -> Optional[float]:
    bank = bank if bank is not None else load_bank()
    if not bank:
        return None
    stats = bank.get("species") or {}
    s = stats.get(sid)
    if s is None:
        # フォルム id (rotomwash / raichualola 等) は画面認識では基本種名で記録されることが多い → 最長の前方一致
        cands = [k for k in stats if len(k) >= 4 and sid.startswith(k)]
        if cands:
            s = stats[max(cands, key=len)]
    if not s or (s.get("appear") or 0) < min_appear:
        return None
    return s["picked"] / s["appear"]


def slot_weights(priors: list, mix: float = SELECTION_REAL_PRIOR_MIX, clip: tuple = SELECTION_REAL_PRIOR_CLIP) -> list:
    """priors: 相手スロットごとの選出率 (None = 情報なし)。戻り値: スロットの重み (情報なしは 1.0)。純粋。
    w = 1 + mix × (p − mean) / mean を clip し、平均が 1 になるよう再正規化する"""
    known = [p for p in priors if p is not None]
    if not known or mix <= 0:
        return [1.0 for _ in priors]
    mean = sum(known) / len(known)
    if mean <= 0:
        return [1.0 for _ in priors]
    lo, hi = clip
    raw = [(min(hi, max(lo, 1.0 + mix * (p - mean) / mean)) if p is not None else 1.0) for p in priors]
    known_w = [w for w, p in zip(raw, priors) if p is not None]
    scale = len(known_w) / sum(known_w) if known_w else 1.0
    return [round(w * scale, 4) if p is not None else 1.0 for w, p in zip(raw, priors)]
