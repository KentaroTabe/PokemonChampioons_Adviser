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


def _setup_boosted(view: MonView, moves: list) -> Optional[MonView]:
    """積み技を持つ場合、1回積んだ後のビューを返す (無ければ None)"""
    from dataclasses import replace
    from advisor.search import SETUP_MOVES
    best_id, best_gain = None, 0
    for mid in moves:
        deltas = SETUP_MOVES.get(mid)
        if not deltas:
            continue
        gain = sum(v for k, v in deltas.items() if k in ("atk", "spa", "spe"))
        if gain > best_gain:
            best_id, best_gain = mid, gain
    if best_id is None:
        return None
    deltas = SETUP_MOVES[best_id]
    boosts = dict(view.boosts or {})
    for k, v in deltas.items():
        boosts[k] = max(-6, min(6, boosts.get(k, 0) + v))
    return replace(view, boosts=boosts)


def _race_turns(view: MonView, hp: float, moves: list,
                opp: MonView, opp_hp: float, opp_moves: list) -> tuple:
    """(撃破に必要なターン数, その系列での実効ビュー)。

    積み技を持ち、かつ相手の打点に3ターン以上の猶予がある場合は
    「1ターン積んでから殴る」系列も評価し、速い方を採る
    (積みエース評価の底上げ: 積んだ後の性能込みで対面を測る)。
    """
    dmg = _best_dmg(view, opp, moves)
    if dmg <= 0:
        return None, view
    turns = math.ceil((opp_hp * 100) / dmg)
    su = _setup_boosted(view, moves)
    if su is not None:
        dmg_opp = _best_dmg(opp, view, opp_moves)
        survive = math.inf if dmg_opp <= 0 else \
            math.ceil((hp * 100) / dmg_opp)
        if survive >= 3:
            dmg_su = _best_dmg(su, opp, moves)
            if dmg_su > 0:
                t_su = 1 + math.ceil((opp_hp * 100) / dmg_su)
                if t_su < turns:
                    return t_su, su
    return turns, view


def duel_score(a: MonView, a_hp: float, a_moves: list,
               b: MonView, b_hp: float, b_moves: list,
               fieldv=None) -> Optional[float]:
    """a対bの対面スコア [-1, 1]。正=aが有利。

    タイプ相性だけでなく「実際に対面で戦った場合の勝敗」を表す:
    最大打点 (命中込み) での撃破ターン数の差 + 先手権 (特性込みの
    実効素早さ) で連続値にする。積み技持ちは「積んでから殴る」系列も
    込みで評価する。判定不能 (双方打点なし) は None。
    """
    turns_a, eff_a = _race_turns(a, a_hp, a_moves, b, b_hp, b_moves)
    turns_b, eff_b = _race_turns(b, b_hp, b_moves, a, a_hp, a_moves)
    if turns_a is None and turns_b is None:
        return None
    if turns_a is None:
        return -1.0
    if turns_b is None:
        return 1.0
    from advisor.damage import effective_speed
    a_first = effective_speed(eff_a, fieldv) >= effective_speed(eff_b, fieldv)
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
