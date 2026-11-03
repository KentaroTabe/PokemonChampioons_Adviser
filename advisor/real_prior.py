"""実戦の相手バンク (tools/real_opponents) を助言側から読む: 相手の選出率の事前分布。

- species_pick_prior(sid): その種が相手ロースターにいたとき実際に選出された割合 (出現が少なければ None)
- slot_weights(priors, mix): 相手スロットの重み。平均 1 を保ちつつ、よく選ばれる種を重く、選ばれない種を軽くする
バンクの読み込みは mtime を見てキャッシュする (接続テスト後に作り直される)。
2026-10-23 (レビュー 2026-10-19 §2): path を渡さないときの読み先は advisor.data_pin.bank_source() (環境変数 ADVICE_REAL_BANK /
ADVICE_REAL_BANK_SHA16、再生の set_override。明示が無ければ従来どおり REAL_BANK_PATH)。版を明示したときは、無い・sha16 が違う・
復元できない版 (blocked) なら InputsPinError で止める (最新へ退避しない)。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from champions_agent.config import (REAL_BANK_PATH, SELECTION_REAL_PRIOR_CLIP, SELECTION_REAL_PRIOR_MIN_APPEAR,
                                    SELECTION_REAL_PRIOR_MIX)

REPO = Path(__file__).resolve().parent.parent
_cache = {"path": None, "mtime": None, "bank": None}


def _source(path: Optional[Path]) -> dict:
    """読み先 (path を渡せばそれ、無ければ advisor.data_pin の版)"""
    if path:
        return {"path": Path(path), "sha16": None, "pinned": False, "blocked": None}
    from advisor.data_pin import bank_source
    return bank_source()


def load_bank(path: Optional[Path] = None) -> Optional[dict]:
    src = _source(path)
    p = src["path"]
    if src["pinned"] or src["blocked"]:
        # 版を明示したとき: 無い・sha16 が違う・blocked なら InputsPinError (read_bank_bytes)
        from advisor.data_pin import read_bank_bytes
        if src["blocked"]:
            read_bank_bytes(src)
        try:
            mtime = p.stat().st_mtime
        except OSError:
            mtime = None
        if mtime is not None and _cache["path"] == str(p) and _cache["mtime"] == mtime:
            return _cache["bank"]
        raw = read_bank_bytes(src)
        bank = json.loads(raw.decode("utf-8"))
        _cache.update({"path": str(p), "mtime": mtime, "bank": bank})
        return bank
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


_ver_cache = {"path": None, "mtime": None, "info": None}


def bank_version(path: Optional[Path] = None) -> Optional[dict]:
    """バンクの版と集計の締切時刻 (2026-10-07 段 0。選出の advice 行の opp_pick_pred に残す)。無ければ None。
    {"path", "sha256" (先頭 16 桁), "mtime", "built_at", "data_until" (このデータまでを使った: 使った対戦の最後の時刻。
    古いバンクには無い), "cutoff" (data_until、無ければ built_at), "cutoff_basis", "n_battles"}。mtime でキャッシュする。
    版を明示したとき (advisor.data_pin) は "pinned": True を足す (無い・sha16 が違えば InputsPinError)"""
    import hashlib
    src = _source(path)
    p = src["path"]
    pinned = bool(src["pinned"] or src["blocked"])
    if pinned:
        from advisor.data_pin import read_bank_bytes
        if src["blocked"]:
            read_bank_bytes(src)      # blocked なら InputsPinError
    try:
        mtime = p.stat().st_mtime
    except OSError:
        if pinned:
            read_bank_bytes(src)      # 無ければ InputsPinError
        return None
    if _ver_cache["path"] == str(p) and _ver_cache["mtime"] == mtime:
        return _ver_cache["info"]
    try:
        raw = read_bank_bytes(src) if pinned else p.read_bytes()
        bank = json.loads(raw.decode("utf-8"))
    except Exception:
        if pinned:
            raise
        return None
    until = bank.get("data_until")
    info = {"path": (str(p) if pinned else str(REAL_BANK_PATH)) if path is None else str(p),
            "sha256": hashlib.sha256(raw).hexdigest()[:16],
            "mtime": round(mtime, 2), "built_at": bank.get("built_at"), "data_until": until,
            "cutoff": until if until is not None else bank.get("built_at"),
            "cutoff_basis": "data_until" if until is not None else "built_at", "n_battles": bank.get("n_battles")}
    if pinned:
        info["pinned"] = True
    _ver_cache.update({"path": str(p), "mtime": mtime, "info": info})
    return info


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
