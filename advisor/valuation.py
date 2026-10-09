"""局面評価 V (docs/ADVICE_ACCURACY_PLAN_1009.md §2.2、2026-10-09 M2b 設計判断 3)。

transition 方式の採点 (advisor.scoring_transition) は Q(a) = E[V(T(state, a, 相手の応手, 分岐))] で行動を比べる。V はその
打ち切り評価 (評価期間 = 1 ターンの解決 + V) で、攻撃・交代・変化技に共通の 1 か所の定義。純粋関数 (ダメージ計算は ctx.damage_fn
が担い、同じ入力に同じ値を返す)。較正前の近似評価なので「勝率」と呼ばない。

式 (重みは champions_agent.config の VALUATION_*。根拠は config の注記、すべて暫定):

    (a) 終了: 自分の選出 (場 + 控え + 状態に無い残り my_unknown) が全滅 → −1。相手の選出 (場 + 判明した控え + 未判明の残り
        opp_unknown) が全滅 → +1。
        両方が同時に全滅 → 0 (引き分けの扱いは未確認。Showdown の勝敗の規則は照合していない)
    (b) 残存戦力 M = (Σ_自分 v(hp) − Σ_相手 v(hp)) / N、v(hp) = α + (1 − α) × hp (生存している個体)、N = VALUATION_TEAM_SIZE、
        α = VALUATION_ALIVE_SHARE。未判明の残り (my_unknown / opp_unknown) は満タン (v = 1) で数える
    (c) 後続の対面 C = (P_自分 × v(相手の場) − P_相手 × v(自分の場)) / N
        P_自分 / P_相手 = 次の 1 回の撃ち合いで「相手より先に (または耐えてから) 倒す」確率。場の個体の実技
        (自分: ctx.my_moves = 選べる技・観測と登録の技、相手: ctx.opp_moves = 判明技 + 使用率の推定) で、各技の 1 回の使用の
        倒せる確率 k (乱数の代表点・命中・タスキ・がんじょう・ばけのかわ・きょけんとつげき・壁込み) と行動順 s (優先度 → 素早さ
        (おいかぜ・トリックルーム)、同速 0.5) から
            P_自分 = s·k1 + (1 − s)·(1 − k2)·k1、P_相手 = (1 − s)·k2 + s·(1 − k1)·k2
        自分は C を最大にする技、相手は C を最小にする技を選ぶ (max_m min_o)。行動できない状態 (ねむり・こおり) は k = 0、まひは
        k に 7/8 を掛ける (チャンピオンズの行動不能 1/8)。ねむけ (あくび)・ねばねばネット・まきびしは C に入らない
        (次のターンより先の効果。段 C で評価期間を延ばすときに扱う)
    V = clip(VALUATION_SCALE × (M + VALUATION_MATCHUP_WEIGHT × C), ±VALUATION_NONTERMINAL_MAX)  (終了していない局面)

自滅・反動・HP の消費 (self_cost) は遷移 T の結果の状態に入る (HP・ひんし) ので、V で別に減点しない。
VALUATION_MATCHUP_WEIGHT < 1 なので、V は両者の HP について単調 (自分の HP が減ると M の減少が C の変化を上回る)。

継続方策の仮定: ひんし後の繰り出し先・交代技の繰り出し先は遷移 (advisor.transition) の best_bench (静的評価が最良の 1 体) で
決まり、V はその後の状態を評価する。2 ターン目以降の行動は C の 1 回の撃ち合い (互いに倒す確率が最大の技) だけで近似する。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from advisor.damage import FieldView, MonView
from advisor.transition import (SimSide, _ability_kinds, _frac_of, default_damage_fn, field_against, multihit_counts,
                                priority, side_speed, transition_effects)
from champions_agent import config as C

_EPS = 1e-9


@dataclass
class ValuationContext:
    """V の計算に使う、局面の間で変わらない情報 (scoring_transition が 1 回の評価ごとに作る)"""
    my_moves: dict                      # species_id → 技 id の組 (自分の個体の実技)
    opp_moves: dict                     # species_id → 技 id の組 (相手の技の候補)
    my_field: Optional[FieldView] = None    # 自分が攻撃するときの場 (壁は SimSide.screens があればそちら)
    opp_field: Optional[FieldView] = None
    opp_unknown: int = 0                # 相手の選出のうち未判明の残り (満タンの生存として数える)
    my_unknown: int = 0                 # 自分の選出のうち状態に無い残り (選出の印が足りないとき。満タンの生存として数える)
    team_size: int = C.VALUATION_TEAM_SIZE
    damage_fn: Callable = default_damage_fn
    _cache: dict = field(default_factory=dict)


# ------------------------------------------------------------------ 部品 (純粋)
def mon_value(hp: float, alive_share: float = None) -> float:
    """個体の価値 v(hp) = α + (1 − α) × hp (ひんしなら 0)"""
    a = C.VALUATION_ALIVE_SHARE if alive_share is None else float(alive_share)
    if hp <= 0:
        return 0.0
    return a + (1.0 - a) * max(0.0, min(1.0, hp))


def _alive(side: SimSide) -> list:
    out = [side.active_hp] if side.active_hp > 0 else []
    return out + [hp for _v, hp in side.bench if hp > 0]


def terminal_value(me: SimSide, opp: SimSide, ctx: ValuationContext) -> Optional[float]:
    """(a) 終了: 自分の全滅 −1 / 相手の全滅 (未判明の残り込み) +1 / 両方 0。終了していなければ None"""
    mine = len(_alive(me)) + max(0, int(ctx.my_unknown))
    theirs = len(_alive(opp)) + max(0, int(ctx.opp_unknown))
    if mine == 0 and theirs == 0:
        return 0.0
    if mine == 0:
        return -1.0
    if theirs == 0:
        return 1.0
    return None


def material(me: SimSide, opp: SimSide, ctx: ValuationContext) -> float:
    """(b) 残存戦力 M ∈ [−1, 1]"""
    n = max(1, int(ctx.team_size))
    mine = sum(mon_value(hp) for hp in _alive(me)) + max(0, int(ctx.my_unknown)) * mon_value(1.0)
    theirs = sum(mon_value(hp) for hp in _alive(opp)) + max(0, int(ctx.opp_unknown)) * mon_value(1.0)
    return max(-1.0, min(1.0, (mine - theirs) / n))


def view_key(v: MonView) -> tuple:
    """MonView のダメージ・素早さに効く部分のキー (キャッシュ用)"""
    return (v.species_id, tuple(v.types or ()), tuple(sorted((v.boosts or {}).items())), v.ability, v.item, v.status,
            tuple(sorted((v.ev or {}).items())), tuple(sorted((v.nature or {}).items())), v.spe_bounds)


def side_key(s: SimSide) -> tuple:
    scr = tuple(sorted((s.screens or {}).items())) if s.screens is not None else None
    return (view_key(s.active), round(s.active_hp, 4), scr, s.tailwind, tuple(sorted(s.volatiles)))


def act_prob(view: MonView) -> float:
    """次の撃ち合いで行動できる確率: ねむり・こおり 0、まひ 1 − 1/8 (チャンピオンズ)、ほか 1"""
    if view.status in ("sleep", "freeze"):
        return 0.0
    if view.status == "paralysis":
        num, den = _frac_of("paralysis_full_para", (1, 8))
        return 1.0 - num / den
    return 1.0


def ko_prob(att: SimSide, dfn: SimSide, move_id: str, fieldv: Optional[FieldView], ctx: ValuationContext) -> float:
    """att の場の個体が move_id を 1 回使って dfn の場の個体を倒す確率 (命中込み、行動できるかは含めない)。

    単発: 最小〜最大を等間隔に TRANSITION_ROLL_POINTS 点置いた乱数のうち残り HP 以上の割合。HP 満タンのきあいのタスキ・がんじょう
    (かたやぶりで無視) は単発では倒せない。ばけのかわは単発を無効にする。連続技: 回数の分布 (multihit_counts / multiaccuracy) と
    1 発ごとの平均で合計が残り HP を超える確率 (ばけのかわは 1 発目を除き、タスキ・がんじょうは 2 発以上で倒せる)"""
    from advisor import effects as E
    from advisor.dex import get_dex
    if att.active_hp <= 0 or dfn.active_hp <= 0 or not move_id:
        return 0.0
    mv = get_dex().move(move_id) or {}
    entry = E.move_entry(move_id)
    if (mv.get("category") or entry.get("category")) == "Status":
        return 0.0
    d = ctx.damage_fn(att.active, att.active_hp, dfn.active, dfn.active_hp, move_id, field_against(fieldv, dfn))
    if not d or float(d.get("avg") or 0.0) <= 0:
        return 0.0
    glaive = 2.0 if "glaiverush" in dfn.volatiles else 1.0
    acc_pct = d.get("accuracy")
    acc = 1.0 if (acc_pct is None or glaive > 1.0) else max(0.0, min(1.0, float(acc_pct) / 100.0))
    hp = dfn.active_hp
    tv = dfn.active
    te = transition_effects()
    ukinds, umisc = _ability_kinds(att.active.ability)
    mold = "ignore_target_ability" in umisc
    _tk, tmisc = _ability_kinds(tv.ability)
    full_hp = hp >= 1.0 - 1e-6
    sash = full_hp and ((tv.item or "") in (te.get("survive_one_hit_items") or ())
                        or ("survive_ohko_at_full_hp" in tmisc and not mold))
    disguise = (tv.species_id in (te.get("disguise_species") or {}) and not mold) or \
        (tv.species_id in (te.get("iceface_species") or {}) and not mold and (mv.get("category") == "Physical"))
    mh = entry.get("multihit")
    if not mh:
        if sash or disguise:
            return 0.0
        n = max(1, int(C.TRANSITION_ROLL_POINTS))
        lo, hi = float(d["min"]) / 100.0 * glaive, float(d["max"]) / 100.0 * glaive
        rolls = [lo + (hi - lo) * k / (n - 1) for k in range(n)] if n > 1 else [(lo + hi) / 2]
        return acc * sum(1 for r in rolls if r >= hp - _EPS) / len(rolls)
    hits_noacc = float(d.get("hits") or 1.0) or 1.0
    unit = float(d["avg"]) / 100.0 / hits_noacc * glaive
    varpow = entry.get("variable_power") == "hit_count"

    def kills(k: int) -> bool:
        dmgs = [unit * (j if varpow else 1) for j in range(1, k + 1)]
        if disguise:
            dmgs = dmgs[1:]
        if not dmgs:
            return False
        if sash and len(dmgs) < 2:
            return False
        return sum(dmgs) >= hp - _EPS

    if entry.get("multiaccuracy"):
        n = int(mh if isinstance(mh, int) else mh[-1])
        return sum((acc ** k) * ((1.0 - acc) if k < n else 1.0) for k in range(1, n + 1) if kills(k))
    return acc * sum(p for c, p in multihit_counts(entry, att.active) if kills(c))


def first_prob(a: SimSide, a_move: Optional[str], b: SimSide, b_move: Optional[str], fieldv: Optional[FieldView]) -> float:
    """a が b より先に行動する確率 (優先度 → 素早さ (おいかぜ込み)、トリックルームは逆、同速は speed_tie_prob)"""
    pa = priority(a_move, a.active, fieldv) if a_move else 0
    pb = priority(b_move, b.active, fieldv) if b_move else 0
    if pa != pb:
        return 1.0 if pa > pb else 0.0
    sa, sb = side_speed(a, fieldv), side_speed(b, fieldv)
    if sa == sb:
        return float(transition_effects().get("speed_tie_prob", 0.5))
    faster = sa > sb
    if fieldv is not None and fieldv.trick_room:
        faster = not faster
    return 1.0 if faster else 0.0


def matchup(me: SimSide, opp: SimSide, ctx: ValuationContext) -> float:
    """(c) 後続の対面 C ∈ [−1/N, 1/N] (式はモジュールの説明)"""
    if me.active_hp <= 0 or opp.active_hp <= 0:
        return 0.0
    key = ("C", side_key(me), side_key(opp))
    if key in ctx._cache:
        return ctx._cache[key]
    fieldv = ctx.my_field or ctx.opp_field
    my_moves = list(ctx.my_moves.get(me.active.species_id) or ()) or [None]
    opp_moves = list(ctx.opp_moves.get(opp.active.species_id) or ()) or [None]
    a_me, a_opp = act_prob(me.active), act_prob(opp.active)
    k_me = {m: (a_me * ko_prob(me, opp, m, ctx.my_field, ctx) if m else 0.0) for m in my_moves}
    k_opp = {o: (a_opp * ko_prob(opp, me, o, ctx.opp_field, ctx) if o else 0.0) for o in opp_moves}
    v_me, v_opp = mon_value(me.active_hp), mon_value(opp.active_hp)
    best = None
    for m in my_moves:
        worst = None
        for o in opp_moves:
            s = first_prob(me, m, opp, o, fieldv)
            k1, k2 = k_me[m], k_opp[o]
            p_me = s * k1 + (1.0 - s) * (1.0 - k2) * k1
            p_opp = (1.0 - s) * k2 + s * (1.0 - k1) * k2
            c = p_me * v_opp - p_opp * v_me
            worst = c if worst is None else min(worst, c)
        best = worst if best is None else max(best, worst)
    out = (best or 0.0) / max(1, int(ctx.team_size))
    ctx._cache[key] = out
    return out


def value(me: SimSide, opp: SimSide, ctx: ValuationContext) -> float:
    """V(state) ∈ [−1, 1] (式はモジュールの説明)"""
    t = terminal_value(me, opp, ctx)
    if t is not None:
        return t
    v = C.VALUATION_SCALE * (material(me, opp, ctx) + C.VALUATION_MATCHUP_WEIGHT * matchup(me, opp, ctx))
    lim = C.VALUATION_NONTERMINAL_MAX
    return max(-lim, min(lim, v))
