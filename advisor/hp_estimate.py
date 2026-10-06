"""技イベントからの期待ダメージ推定 (決定的反映層の部品)。

画面からHPが読めない時間帯の被弾で状態のHPが固着する問題への対策として、
「相手のXのY!」の技イベントを根拠に、ダメージ計算の平均 × 命中率を
防御側から引く。実読みが来れば上書きされる前提の推定値。
"""
from __future__ import annotations

from typing import Optional


def _active_entry(state: dict, side_name: str) -> Optional[dict]:
    side = state.get(side_name) or {}
    idx = side.get("active_index")
    party = side.get("party") or []
    if idx is None or idx >= len(party):
        return None
    return party[idx]


def expected_damage_pct(state: dict, attacker_side: str, move_id: str,
                        resolver=None) -> Optional[float]:
    """攻撃側アクティブの move_id が防御側アクティブに与える期待ダメージ (%)。

    変化技・無効相性・計算不能なら None。命中率 (図鑑) を掛けた期待値を返す。
    """
    from advisor.dex import get_dex
    from advisor.damage import calc_damage
    from advisor.engine import build_mon_view, build_field_view

    mv = get_dex().move(move_id)
    if not mv or str(mv.get("category") or "").lower() == "status":
        return None
    defender_side = "opponent" if attacker_side == "player" else "player"
    atk_p = _active_entry(state, attacker_side)
    def_p = _active_entry(state, defender_side)
    if not atk_p or not def_p:
        return None
    atk = build_mon_view(atk_p, resolver, side=attacker_side)
    dfn = build_mon_view(def_p, resolver, side=defender_side)
    if atk is None or dfn is None:
        return None
    d = calc_damage(atk, dfn, move_id, build_field_view(state, attacker_side))
    if (d.get("type_mult") or 0.0) <= 0 or (d.get("avg") or 0.0) <= 0:
        return None
    acc = mv.get("accuracy")
    acc_f = 1.0
    if isinstance(acc, (int, float)) and not isinstance(acc, bool) and 0 < acc < 100:
        acc_f = float(acc) / 100.0
    return round(float(d["avg"]) * acc_f, 1)
