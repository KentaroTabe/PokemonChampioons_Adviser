"""詰み筋・勝ち筋の判定 (1v1マッチアップ行列)。

残存メンバー同士の「誰が誰に勝つか」を毎ターン評価し、
- 勝ち筋: 相手の残り全員に勝てる自分のポケモン (温存/通す提案)
- 負け筋: 自分の残り全員に勝つ相手のポケモン (先に処理する提案)
を提示する。上位勢の「勝ち筋を意識した数ターン先のプレイング」に相当。

1v1の勝敗判定 (簡略モデル):
- 双方が最大打点の技を撃ち続けると仮定し、撃破に必要なターン数を比較
- 先手側はターン数が同じでも勝ち (行動順で1回多く殴れる)
- foul playと同様、隠れ情報は使用率予測 (opponent_move_pool) で補完
"""
from __future__ import annotations

import math
from typing import Optional

from advisor.damage import MonView, calc_damage
from advisor.search import _speed


def _best_dmg(attacker: MonView, defender: MonView, moves: list) -> float:
    """最大打点 (平均乱数×命中率, 防御側最大HP%) を返す"""
    from advisor.dex import get_dex as _gd
    best = 0.0
    for mid in moves:
        try:
            d = calc_damage(attacker, defender, mid, None)
        except Exception:
            continue
        mv = _gd().move(mid)
        acc = ((mv.get("accuracy") or 100) / 100.0) if mv else 1.0
        best = max(best, d["avg"] * acc)
    return best


def duel_score(a: MonView, a_hp: float, a_moves: list,
               b: MonView, b_hp: float, b_moves: list,
               fieldv=None) -> Optional[float]:
    """a対bの対面スコア [-1, 1]。正=aが有利。

    タイプ相性だけでなく「実際に対面で戦った場合の勝敗」を表す:
    最大打点 (命中込み) での撃破ターン数の差 + 先手権 (特性込みの
    実効素早さ) で連続値にする。判定不能 (双方打点なし) は None。
    """
    dmg_a = _best_dmg(a, b, a_moves)
    dmg_b = _best_dmg(b, a, b_moves)
    if dmg_a <= 0 and dmg_b <= 0:
        return None
    if dmg_a <= 0:
        return -1.0
    if dmg_b <= 0:
        return 1.0
    turns_a = math.ceil((b_hp * 100) / dmg_a)   # aがbを倒すのに必要なターン
    turns_b = math.ceil((a_hp * 100) / dmg_b)
    from advisor.damage import effective_speed
    a_first = effective_speed(a, fieldv) >= effective_speed(b, fieldv)
    margin = (turns_b - turns_a) + (0.5 if a_first else -0.5)
    return math.tanh(margin * 0.8)


def duel(a: MonView, a_hp: float, a_moves: list,
         b: MonView, b_hp: float, b_moves: list) -> Optional[bool]:
    """a対bの1v1。aが勝つならTrue、負けるならFalse、判定不能はNone"""
    s = duel_score(a, a_hp, a_moves, b, b_hp, b_moves)
    if s is None:
        return None
    return s > 0


def matchup_matrix(my_mons: list, opp_mons: list) -> dict:
    """my_mons/opp_mons: [(name, MonView, hp_frac, move_ids)]

    戻り値 {"matrix": {(my, opp): bool|None},
            "win_conditions": [my names], "lose_threats": [opp names]}
    """
    matrix = {}
    for mn, mv, mhp, mmoves in my_mons:
        for on, ov, ohp, omoves in opp_mons:
            matrix[(mn, on)] = duel(mv, mhp, mmoves, ov, ohp, omoves)

    win_conditions = []
    for mn, _, _, _ in my_mons:
        results = [matrix[(mn, on)] for on, _, _, _ in opp_mons]
        if results and all(r is True for r in results):
            win_conditions.append(mn)
    lose_threats = []
    for on, _, _, _ in opp_mons:
        results = [matrix[(mn, on)] for mn, _, _, _ in my_mons]
        if results and all(r is False for r in results):
            lose_threats.append(on)
    return {"matrix": matrix, "win_conditions": win_conditions,
            "lose_threats": lose_threats}


def endgame_note(result: dict, n_opp_unknown: int = 0) -> str:
    """判定結果を1行の日本語ノートにする"""
    parts = []
    if result["win_conditions"]:
        names = "・".join(result["win_conditions"])
        suffix = " (未確認の相手が残っている点に注意)" if n_opp_unknown else ""
        parts.append(f"勝ち筋: {names} が相手の残り全員に勝てる見込み。"
                     f"大切に扱い、通す盤面を作る{suffix}")
    if result["lose_threats"]:
        names = "・".join(result["lose_threats"])
        parts.append(f"⚠負け筋: 相手の {names} にこちらの残り全員が不利。"
                     "ランク補正や交代戦で削ってから処理する必要あり")
    return " / ".join(parts)
