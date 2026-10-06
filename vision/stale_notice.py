"""場のポケモンが替わったあと、前の個体向けの助言が表示に残っている間に出す通知 (純粋計算。server.py が advice_update として
配信する)。

2026-09-29 第16回接続テスト: こだわりスカーフのサザンドラに交代したあと、処理落ちで次の決定画面を取りこぼす間、アシレーヌ
向けの技の推奨が出たままだった → 場の個体が助言の対象と違ったら、一度だけ「前の助言は無効です」と通知する。
2026-10-06 第18回接続テスト (15 戦で通知 33 回):
- 助言の第一推奨が交代で、そのとおりに交代した場合にも「無効です」と出て、正しく進んでいるのに誤りに見えた (14 回)
- メガシンカで自分の個体の種族 id が基本種とメガ後の間で揺れ、交代していないのに通知が出た
- 対象を対戦をまたいで持っていたので、次の対戦の先発が出たときにも前の対戦の最後の個体と比べて通知が出た
→ 個体の同一性は場の枠 (active_index) と対戦の世代 (battle_seq) で見る。第一推奨どおりの交代は「助言どおり」と伝える。
"""
from __future__ import annotations

from typing import Optional


def _active(state: dict) -> tuple:
    """(場の枠の index, その個体の dict)。場の個体が決まっていなければ (None, {})"""
    pl = (state or {}).get("player") or {}
    idx = pl.get("active_index")
    party = pl.get("party") or []
    if isinstance(idx, int) and 0 <= idx < len(party):
        return idx, party[idx]
    return None, {}


def advice_target(state: dict, advice: Optional[dict]) -> dict:
    """助言を出した時点の「対象 (自分の場の枠) と第一推奨」。server が次の助言まで持ち、stale_advice_notice に渡す"""
    idx, mon = _active(state)
    best = (advice or {}).get("best") or {}
    return {"battle_seq": (state or {}).get("battle_seq"), "index": idx, "species_id": mon.get("species_id"),
            "best_kind": best.get("kind"), "best_id": best.get("id"), "best_name": best.get("name")}


def _same_species(a_id: Optional[str], b_id: Optional[str]) -> bool:
    """種族 id が同じ個体を指すか (メガ後の id と基本種の id は同じ個体)"""
    if not a_id or not b_id:
        return False
    from advisor.infer import _base_species_id
    return _base_species_id(a_id) == _base_species_id(b_id)


def stale_advice_notice(target: Optional[dict], state: dict) -> Optional[dict]:
    """助言の対象 (advice_target) と今の対戦状態から通知を作る。通知が要らなければ None。
    - 同じ対戦で、場の枠が助言の対象から替わった → 通知する
    - 助言の第一推奨が「その個体への交代」だった → 助言どおりに進んでいる (followed=True。無効とは言わない)
    - 同じ枠のまま (メガシンカ・種族 id の揺れ)、別の対戦、場の個体が未確定 → 通知しない"""
    if not target or target.get("index") is None:
        return None
    if (state or {}).get("battle_seq") != target.get("battle_seq"):
        return None
    idx, cur = _active(state)
    if idx is None or idx == target["index"] or not cur.get("species_id"):
        return None
    name = cur.get("species_ja") or cur["species_id"]
    followed = target.get("best_kind") == "switch" and (
        _same_species(target.get("best_id"), cur.get("species_id"))
        or (bool(target.get("best_name")) and target.get("best_name") == cur.get("species_ja")))
    if followed:
        reason = f"助言どおり {name} に交代しました (次の決定画面で {name} 向けの助言を出します)"
    else:
        reason = f"場のポケモンが {name} に代わりました。前の助言は無効です (次の決定画面で更新します)"
    return {"ok": False, "stale": True, "followed": followed, "kind": "battle", "reason": reason}
