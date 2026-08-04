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
RISK_WEIGHT = 0.4   # 推奨値 = (1-w)*期待値 + w*保証値 (中立局面での基準)

# 相手の積み技 (起点警告と応手評価用)。網羅より安定を優先した代表セット
SETUP_MOVE_IDS = {
    "swordsdance", "nastyplot", "dragondance", "calmmind", "irondefense",
    "bulkup", "quiverdance", "shellsmash", "agility", "curse", "howl",
    "workup", "coil", "shiftgear", "victorydance", "tidyup",
}


def dynamic_risk_weight(position: float) -> float:
    """局面の有利不利で保証値の重みを変える (定説「負けている時は分散を
    取り、勝っている時は堅く」)。

    position: 最善手の期待値 (≒この局面の評価)。
    優勢 (+0.3以上) → w=0.6 で択を避けて堅く寄せる。
    劣勢 (−0.3以下) → w=0.15 で期待値 (アップサイド) に賭ける。
    中立 (0) では従来の RISK_WEIGHT≒0.4 に一致し、挙動の連続性を保つ。
    """
    lo, hi = -0.3, 0.3
    w_min, w_max = 0.15, 0.6
    t = min(1.0, max(0.0, (position - lo) / (hi - lo)))
    return w_min + t * (w_max - w_min)

# --- 補助技の効果モデル (探索で「効果ゼロの技」にしないため) ---
PROTECT_MOVES = {"protect", "detect", "banefulbunker", "spikyshield",
                 "burningbulwark", "silktrap"}
HEAL_MOVES = {"recover": 0.5, "roost": 0.5, "slackoff": 0.5,
              "softboiled": 0.5, "milkdrink": 0.5, "shoreup": 0.5,
              "moonlight": 0.5, "morningsun": 0.5, "synthesis": 0.5,
              "strengthsap": 0.4, "junglehealing": 0.25, "lifedew": 0.25}
SETUP_MOVES = {
    "swordsdance": {"atk": 2}, "nastyplot": {"spa": 2},
    "dragondance": {"atk": 1, "spe": 1}, "calmmind": {"spa": 1, "spd": 1},
    "bulkup": {"atk": 1, "def": 1}, "irondefense": {"def": 2},
    "quiverdance": {"spa": 1, "spd": 1, "spe": 1},
    "shellsmash": {"atk": 2, "spa": 2, "spe": 2, "def": -1, "spd": -1},
    "agility": {"spe": 2}, "howl": {"atk": 1}, "curse": {"atk": 1, "def": 1},
    "victorydance": {"atk": 1, "def": 1, "spe": 1},
}
STATUS_MOVES = {"willowisp": "burn", "thunderwave": "paralysis",
                "toxic": "toxic", "spore": "sleep", "sleeppowder": "sleep",
                "hypnosis": "sleep", "darkvoid": "sleep", "glare": "paralysis",
                "nuzzle": "paralysis", "yawn": "sleep"}
HAZARD_MOVES = {"stealthrock"}
# ふいうち系: 相手が「攻撃技を選んでいて、まだ動いていない」場合のみ成功
SUCKER_MOVES = {"suckerpunch", "thunderclap"}


def _is_attack_action(act: "Action") -> bool:
    """その行動が攻撃技か (ふいうち系の成功判定用)"""
    if act.kind != "move" or not act.move_id:
        return False
    if act.move_id in PROTECT_MOVES or act.move_id in HEAL_MOVES or \
            act.move_id in SETUP_MOVES or act.move_id in STATUS_MOVES or \
            act.move_id in HAZARD_MOVES:
        return False
    mv = get_dex().move(act.move_id)
    return bool(mv and mv.get("category") != "Status" and mv.get("power"))


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


def _priority(move_id: Optional[str], view: Optional[MonView] = None) -> int:
    """技の優先度 (特性補正込み: いたずらごころ=変化技+1 等)"""
    mv = get_dex().move(move_id) if move_id else None
    if not mv:
        return 0
    pri = mv.get("priority", 0)
    ab = (view.ability or "") if view is not None else ""
    if ab == "prankster" and (mv.get("category") or "") == "Status":
        pri += 1
    elif ab == "galewings" and (mv.get("type") or "") == "Flying" \
            and view is not None and view.hp_frac >= 0.999:
        pri += 1
    return pri


def _speed(view: MonView, fieldv: Optional[FieldView] = None) -> float:
    """実効素早さ (すいすい/ようりょくそ等の特性込み)"""
    from advisor.damage import effective_speed
    return effective_speed(view, fieldv)


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
                       _priority(my_act.move_id, me.active),
                       _speed(me.active, my_field)))
    if opp_act.kind == "move" and opp.active_hp > 0:
        movers.append(("opp", opp_act.move_id,
                       _priority(opp_act.move_id, opp.active),
                       _speed(opp.active, my_field)))
    trick_room = bool(my_field and my_field.trick_room)
    movers.sort(key=lambda m: (-m[2], m[3] if trick_room else -m[3]))

    protected = {"me": False, "opp": False}
    moved: set = set()
    for who, move_id, _pri, _spe in movers:
        atk_side, def_side = ("me", "opp") if who == "me" else ("opp", "me")
        atk = me if who == "me" else opp
        dfn = opp if who == "me" else me
        if atk.active_hp <= 0:
            continue
        moved.add(who)

        # ふいうち系: 相手が攻撃技を選んでいて未行動の場合のみ成功する
        if move_id in SUCKER_MOVES:
            def_act = opp_act if who == "me" else my_act
            if def_side in moved or not _is_attack_action(def_act):
                continue   # 失敗 (交代/変化技/相手行動済み)

        # --- 補助技の効果 ---
        if move_id in PROTECT_MOVES:
            protected[atk_side] = True
            continue
        if move_id in HEAL_MOVES:
            healed = min(1.0, atk.active_hp + HEAL_MOVES[move_id])
            atk = replace(atk, active_hp=healed)
        elif move_id in SETUP_MOVES:
            boosts = dict(atk.active.boosts or {})
            for k, d in SETUP_MOVES[move_id].items():
                boosts[k] = max(-6, min(6, (boosts.get(k) or 0) + d))
            atk = replace(atk, active=replace(atk.active, boosts=boosts))
        elif move_id in HAZARD_MOVES:
            dfn = replace(dfn, stealth_rock=True)
        elif move_id in STATUS_MOVES:
            if dfn.active.status is None and not protected[def_side]:
                dfn = replace(dfn,
                              active=replace(dfn.active,
                                             status=STATUS_MOVES[move_id]))
        else:
            # --- 攻撃技 ---
            if not protected[def_side]:
                dmg = _dmg_frac(replace(atk.active, hp_frac=atk.active_hp),
                                dfn.active, dfn.active_hp, move_id,
                                my_field if who == "me" else opp_field, roll)
                dfn = replace(dfn, active_hp=max(0.0, dfn.active_hp - dmg))
                if dfn.active_hp <= 0:
                    nxt = _best_bench(dfn, atk)
                    if nxt is not None:
                        dfn = _apply_switch(dfn, nxt)
        me, opp = (atk, dfn) if who == "me" else (dfn, atk)
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
           depth: int = 2,
           leaf_value_fn=None) -> dict:
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
                    # 現盤面の静的評価と次手読みの平均 (読み違いに頑健)。
                    # RL価値関数があれば葉評価にブレンドする (学習結果の反映)
                    base = 0.5 * static_eval(m2, o2) + 0.5 * leaf
                    if leaf_value_fn is not None:
                        rv = leaf_value_fn(m2, o2)
                        if rv is not None:
                            base = 0.7 * base + 0.3 * rv
                    v += w * base
                else:
                    v += w * static_eval(m2, o2)
            matrix.append({"my": ma.label, "opp": oa.label, "v": round(v, 3)})
            expected += oa.prob * v
            if v < worst:
                worst, worst_reply = v, oa.label
        results.append({
            "label": ma.label, "kind": ma.kind,
            "move_id": ma.move_id, "bench_index": ma.bench_index,
            "expected": round(expected, 3),
            "worst": round(worst, 3),
            "worst_reply": worst_reply,
            "risky": (expected - worst) > 0.35,
        })

    # 状況依存のリスク調整: 局面評価 (=最善手の期待値) で保証値の重みを
    # 変えてから推奨順を決める。優勢なら択を避け、劣勢なら賭ける
    position = max(r["expected"] for r in results)
    w = dynamic_risk_weight(position)
    for r in results:
        r["recommended"] = round((1 - w) * r["expected"] + w * r["worst"], 3)
    results.sort(key=lambda r: -r["recommended"])

    # 起点警告: 最悪応手が積み技の行動 (その行動を選ぶと相手の最善が
    # 積みになる = 起点を与えている)
    setup_bait = [{"my": r["label"], "opp": r["worst_reply"]}
                  for r in results
                  if r.get("worst_reply") in SETUP_MOVE_IDS]
    return {"actions": results, "matrix": matrix,
            "risk_weight": round(w, 3), "position": round(position, 3),
            "setup_bait": setup_bait}
