"""同時手番の探索エンジン (択の利得行列 + 2手読み)。

最上位帯の意思決定に必要な「択」を扱う:
- 自分の行動 (技+交代) × 相手の行動 (予測技+交代) の利得行列を構築
- 各ペアについて1ターンをシミュレート (優先度/素早さ順、ダメージロール群化)
- 結果盤面を「次ターンの1手読み」で評価する = 実質2手読み
- 行動ごとに 期待値 (相手の行動分布で加重) と 保証値 (相手最善=ミニマックス)
  を算出し、期待値と保証値が乖離する行動には択リスクを付ける

簡略化 (v1):
- 追加効果 (怯み/能力低下/状態異常付与) は未シミュレート
- ダメージロールは {min, avg, max} の3群 (重み 0.25/0.5/0.25) — foul play方式
- ひんし時の後続は静的評価が最良の1体に固定
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Optional

from advisor.damage import MonView, FieldView, calc_damage
from advisor.dex import get_dex

ROLL_GROUPS = ((0.25, "min"), (0.5, "avg"), (0.25, "max"))
RISK_WEIGHT = 0.4   # 推奨値 = (1-w)*期待値 + w*保証値


@dataclass
class SimSide:
    """探索用の軽量な陣営状態"""
    active: MonView
    active_hp: float                  # 0..1
    bench: list = field(default_factory=list)   # [(MonView, hp_frac)]
    stealth_rock: bool = False

    def alive_count(self) -> int:
        return (1 if self.active_hp > 0 else 0) + \
            sum(1 for _, hp in self.bench if hp > 0)


@dataclass
class Action:
    kind: str                 # "move" | "switch"
    move_id: Optional[str] = None
    bench_index: Optional[int] = None
    label: str = ""
    prob: float = 1.0         # 相手側: 行動分布の重み


def _hazard_frac(mon: MonView, side: SimSide) -> float:
    if not side.stealth_rock:
        return 0.0
    mult = get_dex().effectiveness("Rock", mon.types)
    return 0.125 * mult


def _priority(move_id: Optional[str]) -> int:
    mv = get_dex().move(move_id) if move_id else None
    return mv.get("priority", 0) if mv else 0


def _speed(view: MonView) -> float:
    spe = view.stat("spe")
    if view.item == "choicescarf":
        spe *= 1.5
    if view.status == "paralysis":
        spe *= 0.5
    return spe


def static_eval(me: SimSide, opp: SimSide) -> float:
    """盤面の静的評価 [-1, 1]。生存HP合計の差 + 数的優位ボーナス"""
    def total(side: SimSide) -> float:
        t = max(0.0, side.active_hp)
        for _, hp in side.bench:
            t += max(0.0, hp)
        return t

    n_me, n_opp = me.alive_count(), opp.alive_count()
    if n_me == 0:
        return -1.0
    if n_opp == 0:
        return 1.0
    hp_diff = total(me) - total(opp)
    count_diff = n_me - n_opp
    return max(-1.0, min(1.0, hp_diff * 0.22 + count_diff * 0.18))


def _dmg_frac(attacker: MonView, defender: MonView, def_hp: float,
              move_id: str, fieldv: Optional[FieldView], roll: str) -> float:
    """与ダメージ (防御側最大HP比 0..1)"""
    try:
        d = calc_damage(replace(attacker), replace(defender, hp_frac=def_hp),
                        move_id, fieldv)
    except Exception:
        return 0.0
    return max(0.0, d[roll] / 100.0)


def _best_bench(side: SimSide, opp: SimSide) -> Optional[int]:
    """ひんし時の後続 (静的評価最大の1体に固定)"""
    best, best_v = None, -9.9
    for i, (view, hp) in enumerate(side.bench):
        if hp <= 0:
            continue
        v = hp - _hazard_frac(view, side)
        if v > best_v:
            best, best_v = i, v
    return best


def _apply_switch(side: SimSide, idx: int) -> SimSide:
    view, hp = side.bench[idx]
    hp = max(0.0, hp - _hazard_frac(view, side))
    new_bench = list(side.bench)
    new_bench[idx] = (side.active, side.active_hp)
    return replace(side, active=view, active_hp=hp, bench=new_bench)


def simulate_turn(me: SimSide, opp: SimSide, my_act: Action, opp_act: Action,
                  my_field: Optional[FieldView], opp_field: Optional[FieldView],
                  roll: str) -> tuple:
    """1ターンを解決し (me', opp') を返す (副作用なし)"""
    me = replace(me, bench=list(me.bench))
    opp = replace(opp, bench=list(opp.bench))

    # 交代は攻撃より先に解決
    if my_act.kind == "switch":
        me = _apply_switch(me, my_act.bench_index)
    if opp_act.kind == "switch":
        opp = _apply_switch(opp, opp_act.bench_index)

    movers = []
    if my_act.kind == "move" and me.active_hp > 0:
        movers.append(("me", my_act.move_id,
                       _priority(my_act.move_id), _speed(me.active)))
    if opp_act.kind == "move" and opp.active_hp > 0:
        movers.append(("opp", opp_act.move_id,
                       _priority(opp_act.move_id), _speed(opp.active)))
    trick_room = bool(my_field and my_field.trick_room)
    movers.sort(key=lambda m: (-m[2], m[3] if trick_room else -m[3]))

    for who, move_id, _pri, _spe in movers:
        if who == "me":
            if me.active_hp <= 0:
                continue
            dmg = _dmg_frac(replace(me.active, hp_frac=me.active_hp),
                            opp.active, opp.active_hp, move_id, my_field, roll)
            opp = replace(opp, active_hp=max(0.0, opp.active_hp - dmg))
            if opp.active_hp <= 0:
                nxt = _best_bench(opp, me)
                if nxt is not None:
                    opp = _apply_switch(opp, nxt)
        else:
            if opp.active_hp <= 0:
                continue
            dmg = _dmg_frac(replace(opp.active, hp_frac=opp.active_hp),
                            me.active, me.active_hp, move_id, opp_field, roll)
            me = replace(me, active_hp=max(0.0, me.active_hp - dmg))
            if me.active_hp <= 0:
                nxt = _best_bench(me, opp)
                if nxt is not None:
                    me = _apply_switch(me, nxt)
    return me, opp


def _my_actions(me: SimSide, my_moves: list) -> list:
    acts = [Action("move", move_id=m, label=m) for m in my_moves]
    for i, (view, hp) in enumerate(me.bench):
        if hp > 0:
            acts.append(Action("switch", bench_index=i,
                               label=f"交代:{view.name_ja or view.species_id}"))
    return acts


def _opp_actions(opp: SimSide, opp_move_pool: list) -> list:
    """相手の行動候補。技は予測プール、交代はベンチ全員 (重みは控えめ)"""
    total = sum(w for _, w in opp_move_pool) or 1.0
    acts = [Action("move", move_id=m, label=m, prob=0.85 * w / total)
            for m, w in opp_move_pool]
    alive = [(i, v) for i, (v, hp) in enumerate(opp.bench) if hp > 0]
    for i, view in alive:
        acts.append(Action("switch", bench_index=i,
                           label=f"交代:{view.name_ja or view.species_id}",
                           prob=0.15 / len(alive)))
    if not any(a.kind == "switch" for a in acts):
        # 交代先がない場合は技の重みを正規化し直す
        s = sum(a.prob for a in acts) or 1.0
        for a in acts:
            a.prob /= s
    return acts


def _position_value(me: SimSide, opp: SimSide, my_moves: list,
                    opp_move_pool: list, my_field, opp_field) -> float:
    """1手読みの盤面評価: 自分の最善行動の期待値 (ロールはavgのみ)"""
    if me.alive_count() == 0:
        return -1.0
    if opp.alive_count() == 0:
        return 1.0
    opp_acts = _opp_actions(opp, opp_move_pool)
    best = -9.9
    for ma in _my_actions(me, my_moves):
        v = 0.0
        for oa in opp_acts:
            m2, o2 = simulate_turn(me, opp, ma, oa, my_field, opp_field, "avg")
            v += oa.prob * static_eval(m2, o2)
        best = max(best, v)
    return best


def search(me: SimSide, opp: SimSide, my_moves: list, opp_move_pool: list,
           my_field: Optional[FieldView] = None,
           opp_field: Optional[FieldView] = None,
           depth: int = 2) -> dict:
    """利得行列を構築し、行動ごとの期待値/保証値/択リスクを返す。

    my_moves: 自分の技ID列。opp_move_pool: [(move_id, weight)]。
    戻り値 {"actions": [{label, kind, expected, worst, worst_reply,
                          recommended, risky}], "matrix": {...}}
    """
    my_acts = _my_actions(me, my_moves)
    opp_acts = _opp_actions(opp, opp_move_pool)
    if not my_acts or not opp_acts:
        return {"actions": [], "matrix": None}

    results = []
    matrix = []   # JSON化のためタプルキー辞書ではなく行のリストで持つ
    for ma in my_acts:
        expected, worst, worst_reply = 0.0, 9.9, None
        for oa in opp_acts:
            v = 0.0
            for w, roll in ROLL_GROUPS:
                m2, o2 = simulate_turn(me, opp, ma, oa,
                                       my_field, opp_field, roll)
                if depth >= 2:
                    leaf = _position_value(m2, o2, my_moves, opp_move_pool,
                                           my_field, opp_field)
                    # 現盤面の静的評価と次手読みの平均 (読み違いに頑健)
                    v += w * (0.5 * static_eval(m2, o2) + 0.5 * leaf)
                else:
                    v += w * static_eval(m2, o2)
            matrix.append({"my": ma.label, "opp": oa.label, "v": round(v, 3)})
            expected += oa.prob * v
            if v < worst:
                worst, worst_reply = v, oa.label
        recommended = (1 - RISK_WEIGHT) * expected + RISK_WEIGHT * worst
        results.append({
            "label": ma.label, "kind": ma.kind,
            "move_id": ma.move_id, "bench_index": ma.bench_index,
            "expected": round(expected, 3),
            "worst": round(worst, 3),
            "worst_reply": worst_reply,
            "recommended": round(recommended, 3),
            "risky": (expected - worst) > 0.35,
        })
    results.sort(key=lambda r: -r["recommended"])
    return {"actions": results, "matrix": matrix}
