"""transition 方式の採点 (2026-10-09 M2b、docs/ADVICE_ACCURACY_PLAN_1009.md 段 B・§8.4 の設計判断 1〜10)。

各候補 a (技・通常交代・交代技・ひんし後の繰り出し) について

    Q(a) = Σ_応手 p(o) × Σ_分岐 p(b) × V(T(state, a, o, b))        (期待値。順位に使う)
    Q_worst(a) = min_応手 Σ_分岐 p(b) × V(T(state, a, o, b))       (悲観値。候補の属性に残すだけ)

T は共通の遷移 advisor.transition.resolve_turn_branches (行動順・同速・まひ・命中・乱数の代表点・連続技の回数・ひるみの分岐、
耐える効果・交代技の繰り出し)、V は advisor.valuation.value (1 ターンの解決 + 打ち切り評価)。比べる行動の評価期間は揃っている
(すべて 1 ターン + V。ひんし後の繰り出しは相手が行動しない 0 ターン + V で、繰り出しの候補どうしだけを比べる)。

- 応手 (opponent_responses): 全候補で同じ集合と確率 (設計判断 5)。期待値と悲観値の混合比は決めていない (順位は期待値だけ)。
- 旧補正は使わない (設計判断 9): RL の加点 (確率は rl_prob に残すだけ)、行動前に倒される見込みの一律 ×0.25 (遷移の分岐で扱う)、
  「最善交代 +2」(交代技は遷移で解決する)、交代先の架空の代表技 (反撃は V の後続の対面で、生存した分岐の実技だけ)。
- スコアは Q を 0〜100 に線形変換した値 (q_to_score の 1 か所)。legacy の点数とは尺度が違うので混ぜない。
- 退避 (設計判断 10): 自分の候補の解決に未対応の効果 (TurnResult.unsupported_by["me"]) が出た、計算予算 (TRANSITION_BUDGET_MS) を
  超えた、相手の場の個体が分からない / 対応待ち / ひんし、のどれかなら、その決定全体を legacy に退避する (score が ok=False と
  fallback の理由を返し、engine が legacy の結果と scoring_fallback を出す)。未評価の技を低得点にして比較を成立させない。
  相手の応手の解決に出た未対応の効果は退避せず、近似として opp_unmodeled に残す (相手の行動の効果は応手モデル自体が近似のため)。

継続方策 (対戦の仕様ではなく評価のための仮定): ひんし後の繰り出し先と交代技の繰り出し先は transition.best_bench、2 ターン目以降は
V の後続の対面 (1 回の撃ち合い) で近似する。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from advisor.damage import FieldView, MonView, calc_damage
from advisor.dex import get_dex
from advisor.transition import (Action, SimSide, apply_switch, field_against, resolve_turn_branches)
from advisor.valuation import ValuationContext, value, view_key
from champions_agent import config as C


def q_to_score(q: float) -> float:
    """Q ∈ [−1, 1] → 表示と並べ替えのスコア 0〜100 (線形。変換はこの 1 か所)"""
    return round(50.0 + 50.0 * float(q), 1)


# ------------------------------------------------------------------ ダメージ計算のキャッシュ (1 回の評価の中だけ)
def _field_key(f: Optional[FieldView]) -> Optional[tuple]:
    if f is None:
        return None
    return (f.weather, f.terrain, f.trick_room, f.reflect, f.light_screen, f.aurora_veil)


def make_damage_fn(my_species: set, my_ctx: Optional[dict], opp_ctx: Optional[dict]) -> Callable:
    """calc_damage を盤面の文脈 (fainted_allies) 込みで呼び、結果をキャッシュする関数 (純粋な計算の再利用)。

    キャッシュのキーは攻撃側・防御側の MonView のダメージに効く部分 (valuation.view_key) と、HP の区分 (攻撃側: 満タン / 1/3 超 /
    以下、防御側: 満タンか)。HP に連続的に依存する技 (ふんか・きしかいせい 等) は同じ区分の中で最初の HP の値を使う (近似)。
    文脈は攻撃側の種族が自分の選出にあれば自分側 (同じ種族どうしの対面では自分側の文脈を使う近似)"""
    cache: dict = {}

    def fn(att_view: MonView, att_hp: float, def_view: MonView, def_hp: float, move_id: str,
           fieldv: Optional[FieldView]) -> Optional[dict]:
        key = (view_key(att_view), att_hp >= 0.999, att_hp > 1 / 3, view_key(def_view), def_hp >= 0.999, move_id,
               _field_key(fieldv))
        if key in cache:
            return cache[key]
        ctx = my_ctx if att_view.species_id in my_species else opp_ctx
        try:
            from dataclasses import replace
            d = calc_damage(replace(att_view, hp_frac=att_hp), replace(def_view, hp_frac=def_hp), move_id, fieldv,
                            ctx=ctx)
        except Exception:
            d = None
        cache[key] = d
        return d

    return fn


# ------------------------------------------------------------------ 相手の応手の最小モデル (計画書 段 B-5、設計判断 5)
def opponent_responses(opp: SimSide, me: SimSide, pool: list, damage_fn: Callable, opp_field: Optional[FieldView], *,
                       switch_prob: Optional[float] = None, decay: Optional[float] = None,
                       mega_min_prob: Optional[float] = None, include_switch: bool = True) -> list:
    """相手の応手の集合と確率 [(確率, Action)] (純粋。確率の合計は 1)。transition 方式の全候補で同じものを使う。

    pool: [(move_id, 重み)] (engine.opponent_move_pool。判明技 = 100、推定技 = 使用率 × 減衰)。重みは「所持」の事前分布
    (判明技 = 1、推定技 = 重み / 100) にだけ使い、行動確率は別に決める: 相手にとっての期待ダメージ (命中込み、自分の場の個体の
    残り HP で頭打ち) の高い順に decay の等比 (1, decay, decay², ...) を掛け、所持の事前分布を掛けて、1 − switch_prob に正規化する。
    交代は switch_prob を判明している控え (生存) に等分する。控えが無ければ技に配り直す (未判明の控えへの交代は解決できない)。
    相手のメガシンカは search.opp_mega_split (石の事前分布の確率で「技 + メガ」に分ける)。
    判明技が 1 つでも、ほかの技・交代の候補があれば 100% にはならない"""
    from advisor.search import opp_mega_split
    ps = C.OPP_SWITCH_PROB if switch_prob is None else float(switch_prob)
    r = C.OPP_MOVE_RANK_DECAY if decay is None else float(decay)
    dex = get_dex()
    rows = []
    for idx, (mid, w) in enumerate(pool or []):
        mv = dex.move(mid)
        if not mv:
            continue
        prior = 1.0 if float(w) >= 100.0 else max(0.0, min(1.0, float(w) / 100.0))
        if prior <= 0:
            continue
        exp = 0.0
        if mv.get("category") != "Status" and opp.active_hp > 0 and me.active_hp > 0:
            d = damage_fn(opp.active, opp.active_hp, me.active, me.active_hp, mid, field_against(opp_field, me))
            if d:
                exp = min(me.active_hp, float(d.get("expected") or 0.0) / 100.0)
        rows.append((mid, prior, exp, idx))
    rows.sort(key=lambda t: (-t[2], t[3]))
    move_w = [(mid, (r ** rank) * prior) for rank, (mid, prior, _e, _i) in enumerate(rows)]
    alive = [i for i, (_v, hp) in enumerate(opp.bench) if hp > 0] if include_switch else []
    tot_m = sum(w for _m, w in move_w)
    if tot_m <= 0:
        move_w = []
    p_switch = ps if (alive and move_w) else (1.0 if alive else 0.0)
    acts = [Action("move", move_id=m, label=m, prob=(1.0 - p_switch) * w / tot_m) for m, w in move_w]
    for i in alive:
        v = opp.bench[i][0]
        acts.append(Action("switch", bench_index=i, label=f"交代:{v.name_ja or v.species_id}",
                           prob=p_switch / len(alive)))
    if mega_min_prob is None:
        from champions_agent.config import SEARCH_OPP_MEGA_MIN_PROB
        mega_min_prob = SEARCH_OPP_MEGA_MIN_PROB
    acts = opp_mega_split(acts, opp, mega_min_prob)
    if not acts:
        return [(1.0, Action("pass", label="(応手なし)"))]
    s = sum(a.prob for a in acts) or 1.0
    return [(a.prob / s, a) for a in acts]


# ------------------------------------------------------------------ 入力
@dataclass
class Candidate:
    kind: str                 # "move" | "switch"
    id: str
    name: str
    action: Action
    mode: str                 # "move" | "switch" | "replace" (ひんし後の繰り出し: 相手は行動しない)
    party_index: Optional[int] = None


@dataclass
class TransitionInputs:
    me: SimSide
    opp: SimSide
    my_field: Optional[FieldView]
    opp_field: Optional[FieldView]
    candidates: list
    responses: Optional[list]          # [(確率, Action)]。None = 相手は行動しない (ひんし後の繰り出し等)
    vctx: ValuationContext
    damage_fn: Callable
    rl_probs: dict = field(default_factory=dict)
    legacy_by_key: dict = field(default_factory=dict)   # (kind, id) → legacy の行動 (self_cost 等の欄を写す)
    notes: list = field(default_factory=list)


def _fallback(reason: str, t0: float, **extra) -> dict:
    return {"ok": False, "fallback": reason, "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1), **extra}


def score(inp: TransitionInputs, budget_ms: Optional[float] = None) -> dict:
    """候補ごとの Q / Q_worst / 分岐の要約を計算する (純粋。入力は書き換えない)。

    戻り値: 成功 {"ok": True, "actions": [...] (スコア順), "elapsed_ms", "n_branches", "responses", "opp_unmodeled"} /
    退避 {"ok": False, "fallback": 理由, "elapsed_ms", ...}"""
    t0 = time.perf_counter()
    budget = C.TRANSITION_BUDGET_MS if budget_ms is None else float(budget_ms)
    deadline = t0 + budget / 1000.0
    if not inp.candidates:
        return _fallback("no_candidates", t0)
    out = []
    n_total = 0
    opp_unmodeled: set = set()
    for cand in inp.candidates:
        per_resp = []      # (確率, 期待値, 応手のラベル)
        p_skip = p_ko = p_faint = 0.0
        n_br = 0
        if cand.mode == "replace" or inp.responses is None:
            me2 = apply_switch(inp.me, cand.action.bench_index) if cand.action.kind == "switch" else inp.me
            v = value(me2, inp.opp, inp.vctx)
            per_resp.append((1.0, v, None))
            p_faint = 1.0 if me2.active_hp <= 0 else 0.0
            n_br = 1
        else:
            for p_o, oa in inp.responses:
                brs = resolve_turn_branches(inp.me, inp.opp, cand.action, oa, inp.my_field, inp.opp_field,
                                            damage_fn=inp.damage_fn)
                e = 0.0
                for p_b, res in brs:
                    if res.unsupported_by.get("me"):
                        w, eff, src = res.unsupported_by["me"][0]
                        return _fallback(f"unsupported:{eff}:{src}", t0, candidate=cand.id)
                    for w, eff, src in res.unsupported_by.get("opp") or ():
                        opp_unmodeled.add(f"{eff}:{src}")
                    e += p_b * value(res.me, res.opp, inp.vctx)
                    if cand.kind == "move" and any(w == "me" and k == "skip" and d == "fainted_before_acting"
                                                   for w, k, d in res.events):
                        p_skip += p_o * p_b
                    if res.fainted.get("opp"):
                        p_ko += p_o * p_b
                    if res.fainted.get("me"):
                        p_faint += p_o * p_b
                n_br += len(brs)
                per_resp.append((p_o, e, oa.label))
                if time.perf_counter() > deadline:
                    return _fallback("budget", t0, candidate=cand.id, budget_ms=budget)
        n_total += n_br
        q = sum(p * e for p, e, _l in per_resp)
        worst_p, worst_e, worst_l = min(per_resp, key=lambda t: t[1])
        out.append((cand, q, worst_e, worst_l, {"n": n_br, "p_ko_before_act": round(p_skip, 4),
                                                 "p_ko": round(p_ko, 4), "p_faint": round(p_faint, 4)}))
    # 並べ替えは丸める前の Q (同じ Q は候補の順 = 技の枠の順 → 交代の順のまま)
    out.sort(key=lambda t: -round(t[1], 9))      # 浮動小数の誤差で同じ Q の順が入れ替わらないように 9 桁で比べる
    actions = [_action_dict(inp, cand, q, qw, wl, br) for cand, q, qw, wl, br in out]
    return {"ok": True, "actions": actions, "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
            "n_branches": n_total,
            "responses": [{"label": a.label + ("+メガ" if a.mega and not a.label.endswith("+メガ") else ""),
                           "p": round(p, 4)} for p, a in (inp.responses or [])],
            "opp_unmodeled": sorted(opp_unmodeled)}


def _action_dict(inp: TransitionInputs, cand: Candidate, q: float, q_worst: float, worst_label, br: dict) -> dict:
    legacy = inp.legacy_by_key.get((cand.kind, cand.id)) or {}
    parts = []
    if cand.kind == "move":
        d = inp.damage_fn(inp.me.active, inp.me.active_hp, inp.opp.active, inp.opp.active_hp, cand.action.move_id,
                          field_against(inp.my_field, inp.opp)) if inp.opp.active_hp > 0 else None
        if d and float(d.get("max") or 0) > 0:
            txt = f"予測ダメージ {d['min']:.0f}〜{d['max']:.0f}%"
            if d.get("accuracy") is not None and float(d["accuracy"]) < 100:
                txt += f" (命中{int(d['accuracy'])})"
            parts.append(txt)
    elif cand.mode == "replace":
        parts.append("ひんし後の繰り出し (相手は行動しない)")
    q_txt = f"Q {q:+.3f}"
    if worst_label:
        q_txt += f" (最悪の応手 {worst_label}: {q_worst:+.3f})"
    parts.append(q_txt)
    if cand.kind == "move" and br["p_ko_before_act"] > 0:
        parts.append(f"先に倒される {br['p_ko_before_act']:.0%}")
    if br["p_ko"] > 0:
        parts.append(f"倒す {br['p_ko']:.0%}")
    if cand.kind == "switch" and br["p_faint"] > 0:
        parts.append(f"交代先が倒される {br['p_faint']:.0%}")
    if legacy.get("self_cost"):
        from advisor.engine import self_cost_text
        sc = self_cost_text(legacy["self_cost"])
        if sc:
            parts.append(sc)
    if legacy.get("from_registration"):
        parts.append("登録からの補完")
    a = {"kind": cand.kind, "id": cand.id, "name": cand.name, "score": q_to_score(q), "reason": " / ".join(parts),
         "q": round(q, 4), "q_worst": round(q_worst, 4), "rl_prob": inp.rl_probs.get(
             cand.name if cand.kind == "move" else f"交代:{cand.name}"),
         "branches": br, "mode": cand.mode}
    for k in ("self_cost", "may_fail", "from_registration"):
        if legacy.get(k):
            a[k] = legacy[k]
    if cand.party_index is not None and legacy.get("hp_uncertain"):
        a["hp_uncertain"] = True
    return a


# ------------------------------------------------------------------ 状態辞書 → 入力 (engine.evaluate_common から呼ぶ)
def _screens_of(side_state: dict) -> dict:
    s = side_state.get("screens") or {}
    return {"reflect": bool(s.get("reflect")), "light_screen": bool(s.get("light_screen")),
            "aurora_veil": bool(s.get("aurora_veil"))}


def _hazards(side_state: dict) -> dict:
    hz = side_state.get("hazards") or {}
    return {"stealth_rock": bool(hz.get("stealth_rock")), "spikes": int(hz.get("spikes") or 0),
            "sticky_web": bool(hz.get("sticky_web"))}


def _seen(p: dict) -> bool:
    """相手の個体が対戦に出たと分かっているか (HP を読んだ・ひんし・技が判明)"""
    return p.get("hp_percent") is not None or p.get("status") == "fainted" or bool(p.get("revealed_moves"))


def build_inputs(state: dict, resolver, *, my_view: MonView, opp_view: Optional[MonView], opp_p: Optional[dict],
                 pool: list, legal_move_ids: list, my_field: FieldView, opp_field: FieldView, my_ctx: dict,
                 opp_ctx: dict, my_fainted: bool, pivot_pending: bool, i_am_faster: Optional[bool],
                 legacy_actions: list, rl_hint: Optional[dict], opp_pending: bool) -> tuple:
    """engine.evaluate_common の共有入力 (MonView・合法手・補完した技・相手の型の仮定・控え) から TransitionInputs を作る。

    戻り値 (TransitionInputs, None) / 作れなければ (None, 退避の理由)。自分の控えは legacy の交代候補と同じ規則 (選出済み・
    ひんしでない・場でない)。相手の控えは対戦に出たと分かっている個体 (_seen) だけ (登録 6 体を残存戦力にしない。計画書 §2.1)。
    相手の未判明の残り = 選出数 (team_size があれば) − 判明した数、無ければ残数 − 判明した生存数 (残数も無ければ選出数 − 判明した数)"""
    from advisor.engine import (_hp_frac_of, _opp_mega_forms, _rl_probs, build_mon_view, complete_moves_from_registration,
                                opponent_move_pool, registered_moves_of)
    from advisor.party import battle_party_indices
    from vision.state import roster_slots
    if opp_view is None or opp_p is None:
        return None, "opp_unknown"
    if opp_pending:
        return None, "opp_pending"
    dex = get_dex()
    my_state, opp_state = state["player"], state["opponent"]
    my_idx, opp_idx = my_state.get("active_index"), opp_state.get("active_index")
    opp_hp = _hp_frac_of(opp_p)
    if opp_p.get("status") == "fainted" or opp_hp <= 0:
        if not my_fainted:
            return None, "opp_fainted"

    # 自分の控え (legacy の交代候補と同じ規則)
    allowed = battle_party_indices(my_state)
    bench, bench_meta = [], []
    my_moves = {my_view.species_id: tuple(m for m in legal_move_ids if dex.move(m))}
    for i, p in enumerate(my_state["party"]):
        if i == my_idx or p.get("status") == "fainted":
            continue
        if allowed is not None and i not in allowed:
            continue
        v = build_mon_view(p, resolver, side="player")
        if v is None:
            continue
        bench.append((v, _hp_frac_of(p)))
        bench_meta.append((i, p))
        obs = list(p.get("moves") or [])
        if len(obs) < 4 and resolver is not None:
            try:
                obs = complete_moves_from_registration(obs, registered_moves_of(p.get("species_ja"), resolver))
            except Exception:
                pass
        my_moves.setdefault(v.species_id, tuple(s.get("move_id") for s in obs
                                                if s.get("move_id") and dex.move(s.get("move_id"))))
    my_hz, opp_hz = _hazards(my_state), _hazards(opp_state)
    me = SimSide(active=my_view, active_hp=0.0 if my_fainted else _hp_frac_of(my_state["party"][my_idx]),
                 bench=bench, stealth_rock=my_hz["stealth_rock"], spikes=my_hz["spikes"],
                 sticky_web=my_hz["sticky_web"], mega_used=bool((state.get("mega_used") or {}).get("player")),
                 screens=_screens_of(my_state), tailwind=bool(my_state.get("tailwind")))

    # 相手の控え: 対戦に出たと分かっている個体だけ
    opp_moves = {opp_view.species_id: tuple(m for m, _w in pool if dex.move(m))}
    obench = []
    n_seen_alive = 1 if opp_hp > 0 else 0
    n_seen = 1
    for i, p in roster_slots(opp_state.get("party")):
        if i == opp_idx or not _seen(p):
            continue
        n_seen += 1
        if p.get("status") == "fainted":
            continue
        v = build_mon_view(p, resolver)
        if v is None:
            continue
        hp = _hp_frac_of(p)
        if hp <= 0:
            continue
        n_seen_alive += 1
        try:
            opool = opponent_move_pool(p, v, resolver)
        except Exception:
            opool = []
        opp_moves.setdefault(v.species_id, tuple(m for m, _w in opool if dex.move(m))[:6])
        obench.append((v, hp))
    # 相手の未判明の残り: 選出数が分かる (シミュレータの状態 team_size) なら 選出数 − 判明した数、無ければ残数 (画面の HUD) −
    # 判明した生存数、残数も無ければ VALUATION_TEAM_SIZE − 判明した数
    remaining = opp_state.get("remaining")
    if opp_state.get("team_size"):
        unknown = max(0, int(opp_state["team_size"]) - n_seen)
    elif remaining is None:
        unknown = max(0, int(C.VALUATION_TEAM_SIZE) - n_seen)
    else:
        unknown = max(0, int(remaining) - n_seen_alive)
    # 自分の状態に無い残り (選出の印が足りず控えを特定できないとき): 残数 − 分かっている生存数
    my_known_alive = (0 if my_fainted else 1) + len(bench)
    my_unknown = max(0, int(my_state["remaining"]) - my_known_alive) if my_state.get("remaining") is not None else 0
    opp = SimSide(active=opp_view, active_hp=opp_hp, bench=obench, stealth_rock=opp_hz["stealth_rock"],
                  spikes=opp_hz["spikes"], sticky_web=opp_hz["sticky_web"],
                  mega_forms=_opp_mega_forms(opp_p, state),
                  mega_used=bool((state.get("mega_used") or {}).get("opponent")),
                  screens=_screens_of(opp_state), tailwind=bool(opp_state.get("tailwind")))
    my_species = {my_view.species_id} | {v.species_id for v, _hp in bench}
    damage_fn = make_damage_fn(my_species, my_ctx, opp_ctx)
    vctx = ValuationContext(my_moves=my_moves, opp_moves=opp_moves, my_field=my_field, opp_field=opp_field,
                            opp_unknown=unknown, my_unknown=my_unknown, damage_fn=damage_fn)

    # 候補
    cands = []
    switch_mode = "replace" if my_fainted else "switch"
    if not my_fainted and not pivot_pending:
        for mid in my_moves[my_view.species_id]:
            mv = dex.move(mid) or {}
            leg = next((a for a in legacy_actions if a.get("kind") == "move" and a.get("id") == mid), None)
            name = (leg or {}).get("name") or mv.get("name") or mid
            cands.append(Candidate("move", mid, name, Action("move", move_id=mid, label=mid), "move"))
    for j, ((v, _hp), (i, p)) in enumerate(zip(bench, bench_meta)):
        sid = p.get("species_id") or f"slot{i}"
        name = p.get("species_ja") or p.get("display_name") or f"{i}番"
        cands.append(Candidate("switch", sid, name, Action("switch", bench_index=j, label=f"交代:{name}"),
                               switch_mode, party_index=i))
    notes = []
    responses = None
    if my_fainted:
        notes.append("ひんし後の繰り出し: 相手は行動しない (繰り出しの候補どうしを V で比べる)")
    elif pivot_pending:
        # 交代技の後の繰り出し先の選択: 自分が先に動いた (相手がまだ行動していない) なら相手の技を受ける (交代はしない)。
        # 相手が先に動いた後なら相手は行動しない
        if i_am_faster:
            responses = opponent_responses(opp, me, pool, damage_fn, opp_field, include_switch=False)
            notes.append("交代技の後の繰り出し: 相手はまだ行動していない見込み (技を受ける)")
        else:
            notes.append("交代技の後の繰り出し: 相手は行動済みの見込み (技を受けない)")
    else:
        responses = opponent_responses(opp, me, pool, damage_fn, opp_field)
    legacy_by_key = {(a.get("kind"), a.get("id")): a for a in legacy_actions}
    for (i, p) in bench_meta:
        if p.get("hp_uncertain"):
            k = ("switch", p.get("species_id") or f"slot{i}")
            legacy_by_key[k] = dict(legacy_by_key.get(k) or {}, hp_uncertain=True)
    inp = TransitionInputs(me=me, opp=opp, my_field=my_field, opp_field=opp_field, candidates=cands, responses=responses,
                           vctx=vctx, damage_fn=damage_fn, rl_probs=_rl_probs(rl_hint), legacy_by_key=legacy_by_key,
                           notes=notes)
    return inp, None


# ------------------------------------------------------------------ 退避率の集計 (advice 行から。純粋)
def summarize_fallbacks(advices) -> dict:
    """advice 行 (または evaluate の戻り値) の列から transition 方式の退避率を集計する (純粋)。
    分母は scoring_mode が transition の行、分子はそのうち scoring_fallback がある行。理由は「:」の前で分類する"""
    n = n_fb = 0
    by: dict = {}
    for a in advices or ():
        adv = a.get("advice") if isinstance(a, dict) and isinstance(a.get("advice"), dict) else a
        if not isinstance(adv, dict) or adv.get("scoring_mode") != "transition":
            continue
        n += 1
        fb = adv.get("scoring_fallback")
        if fb:
            n_fb += 1
            key = str(fb).split(":", 2)
            k = key[0] if key[0] != "unsupported" else ":".join(key[:3])
            by[k] = by.get(k, 0) + 1
    return {"n": n, "n_fallback": n_fb, "rate": (n_fb / n) if n else None,
            "by_reason": dict(sorted(by.items(), key=lambda kv: -kv[1]))}
