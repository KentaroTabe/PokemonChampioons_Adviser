"""詰み筋・勝ち筋の判定 (1v1マッチアップ行列)。

残存メンバー同士の「誰が誰に勝つか」を毎ターン評価し、
- 勝ち筋: 相手の残り全員に勝てる自分のポケモン (温存/通す提案)
- 負け筋: 自分の残り全員に勝つ相手のポケモン (先に処理する提案)
を提示する。上位勢の「勝ち筋を意識した数ターン先のプレイング」に相当。

1v1の勝敗判定 (簡略モデル):
- 双方が最大打点の技を撃ち続けると仮定し、撃破に必要なターン数を比較
  (終盤評価の matchup_matrix は自滅技・HP の 1/2 を失う技を撃ち続けない: duel_judgement。2026-10-09 M2a)
- 先手側はターン数が同じでも勝ち (行動順で1回多く殴れる)
- foul playと同様、隠れ情報は使用率予測 (opponent_move_pool) で補完
"""
from __future__ import annotations

import math
from functools import lru_cache
from typing import Optional

from advisor.damage import MonView, calc_damage
from advisor.search import _speed


def _best_dmg(attacker: MonView, defender: MonView, moves: list, fieldv=None) -> float:
    """最大打点 (平均乱数×命中率, 防御側最大HP%) を返す。fieldv (天候/フィールド) は省略時なし"""
    from advisor.dex import get_dex as _gd
    best = 0.0
    for mid in moves:
        try:
            d = calc_damage(attacker, defender, mid, fieldv)
        except Exception:
            continue
        if "expected" in d:          # 命中 (天候・特性・連続技の 1 発ごとの判定込み) まで掛けた期待値 (advisor.damage)
            best = max(best, d["expected"])
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
                opp: MonView, opp_hp: float, opp_moves: list, fieldv=None) -> tuple:
    """(撃破に必要なターン数, その系列での実効ビュー)。

    積み技を持ち、かつ相手の打点に3ターン以上の猶予がある場合は
    「1ターン積んでから殴る」系列も評価し、速い方を採る
    (積みエース評価の底上げ: 積んだ後の性能込みで対面を測る)。
    fieldv (天候/フィールド) は両者のダメージに掛かる。省略時なし
    """
    dmg = _best_dmg(view, opp, moves, fieldv)
    if dmg <= 0:
        return None, view
    turns = math.ceil((opp_hp * 100) / dmg)
    su = _setup_boosted(view, moves)
    if su is not None:
        dmg_opp = _best_dmg(opp, view, opp_moves, fieldv)
        survive = math.inf if dmg_opp <= 0 else \
            math.ceil((hp * 100) / dmg_opp)
        if survive >= 3:
            dmg_su = _best_dmg(su, opp, moves, fieldv)
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


@lru_cache(maxsize=1)
def _non_repeatable_kinds() -> frozenset:
    """「繰り返し撃てる最大打点」にしない自分の損失の種類 (advisor.transition.self_cost_kind の値。
    advisor/data/transition_effects.json の endgame_non_repeatable_kinds)"""
    from advisor.transition import transition_effects
    return frozenset(transition_effects().get("endgame_non_repeatable_kinds")
                     or ("selfko", "half_hp", "half_hp_on_damage"))


def split_self_cost_moves(moves: list) -> tuple:
    """技を (繰り返し撃てる技, 外した技 [(move_id, kind)]) に分ける (純粋。2026-10-09 M2a)。
    外すのは自滅 (だいばくはつ等) と HP の 1/2 を失う技 (てっていこうせん等)。反動技 (すてみタックル等) は外さない
    (与ダメージに比例する損失で、繰り返し撃てる。反動の HP は 1 対 1 判定では従来どおり数えない)"""
    from advisor.transition import self_cost_kind
    kinds = _non_repeatable_kinds()
    rep, excluded = [], []
    for mid in moves or []:
        k = self_cost_kind(mid)
        if k in kinds:
            excluded.append((mid, k))
        else:
            rep.append(mid)
    return rep, excluded


def duel_judgement(a: MonView, a_hp: float, a_moves: list,
                   b: MonView, b_hp: float, b_moves: list) -> tuple:
    """終盤評価 (matchup_matrix) の 1 対 1 判定: (aが勝つか True/False/None, 判定保留の印 [dict])。2026-10-09 M2a。

    旧 duel は自滅技・HP の 1/2 を失う技も「最大打点を撃ち続ける」の候補にしていた (だいばくはつ 3 連発で勝ち等)。
    ここでは次のとおり判定する (1 回の使用後の状態を共通遷移で解く方式は段 B の範囲なので採らない):
      1. その技を外した判定 (繰り返し撃てる技だけ) と、外さない判定 (旧 duel) が一致すれば、その結果 (外した技が勝敗を左右しない)
      2. 食い違い、外した技がすべて自滅で、どれも 1 回で相手を倒せないなら、外した判定 (撃てば使用者がひんしで負ける)
      3. それ以外 (1 回で倒せる自滅 = 相打ち、HP の 1/2 を失う技の使いどころ) は判定保留 None と印
         {"side": "a"/"b", "move_id", "kind", "lethal_once"} を返す"""
    a_rep, a_ex = split_self_cost_moves(a_moves)
    b_rep, b_ex = split_self_cost_moves(b_moves)
    if not a_ex and not b_ex:
        return duel(a, a_hp, a_moves, b, b_hp, b_moves), []
    r_rep = duel(a, a_hp, a_rep, b, b_hp, b_rep)
    r_all = duel(a, a_hp, a_moves, b, b_hp, b_moves)
    if r_rep == r_all:
        return r_rep, []
    marks = []
    for side, atk, dfd, dfd_hp, ex in (("a", a, b, b_hp, a_ex), ("b", b, a, a_hp, b_ex)):
        for mid, kind in ex:
            lethal = _best_dmg(atk, dfd, [mid]) >= dfd_hp * 100
            marks.append({"side": side, "move_id": mid, "kind": kind, "lethal_once": bool(lethal)})
    if all(m["kind"] == "selfko" and not m["lethal_once"] for m in marks):
        return r_rep, []
    return None, marks


def matchup_matrix(my_mons: list, opp_mons: list) -> dict:
    """my_mons/opp_mons: [(name, MonView, hp_frac, move_ids)]

    戻り値 {"matrix": {(my, opp): bool|None},
            "win_conditions": [my names], "lose_threats": [opp names],
            "unsupported": [{"my", "opp", "side": "my"/"opp", "move_id", "kind", "lethal_once"}]}
    1 対 1 の判定は duel_judgement (自滅技・HP の 1/2 を失う技を繰り返し撃てる扱いにしない。2026-10-09 M2a)。
    判定保留 (None) の組は unsupported に印を残す (勝ち筋・負け筋には数えない)
    """
    matrix = {}
    unsupported = []
    for mn, mv, mhp, mmoves in my_mons:
        for on, ov, ohp, omoves in opp_mons:
            r, marks = duel_judgement(mv, mhp, mmoves, ov, ohp, omoves)
            matrix[(mn, on)] = r
            for m in marks:
                unsupported.append({"my": mn, "opp": on, "side": "my" if m["side"] == "a" else "opp",
                                    "move_id": m["move_id"], "kind": m["kind"], "lethal_once": m["lethal_once"]})

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
            "lose_threats": lose_threats, "unsupported": unsupported}


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
