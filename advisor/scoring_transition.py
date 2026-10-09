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
  2026-10-09 M2c: 相手の応手の解決に出た未対応の効果も退避する (scoring_fallback = "opp_unsupported:<効果>:<技>"。M2b までは
  opp_unmodeled に記録して続行していた。opp_unmodeled の欄は互換のため空の列で残す)。HP の範囲を作れない個体 ("hp_range") も退避。

2026-10-09 M2c で足したこと: V の終了は遷移の勝者 (TurnResult.winner) を使う。HP が不確かな個体は範囲の悲観側で T と V を
評価して順位を決め、楽観側の順位は感度 (hp_range、候補の q_hp_opt) に残す。相手の場の個体の持ち物 (スカーフ / タスキ) の有無を
入れ替えた順位も感度 (sensitivity) に残す (どちらも順位には使わない。計算予算の残りで計算し、超えたら省略して理由を残す)。
判明情報と矛盾する仮説 (消費済みの持ち物・相手の技の固定) は build_inputs で除く。退避率は決定単位でも集計できる (summarize_fallbacks)。

2026-10-09 M2d で変えたこと (レビュー 1・3):
- 未評価の確率の区間: 相手の応手の未対応の効果が**使用率の推定技**から出たときは退避せず、その応手を「未評価」に分ける (判明技なら
  従来どおり opp_unsupported で退避)。未評価の応手の確率の合計 ε は全候補で共通、候補の q は残りの応手で正規化した期待値、
  Q の区間は [(1 − ε) q − ε, (1 − ε) q + ε] (V ∈ [−1, 1])。第一候補の下限が他の全候補の上限を上回るときだけ順位を確定し、
  そうでなければ退避 ("opp_unevaluated_ambiguous")。未評価の応手を外して正規化するだけの案は不採用 (ε を残す)。
- 本採点と感度の分離: 感度 (楽観側の HP・持ち物) は config.TRANSITION_SENSITIVITY_ENABLED で切れる。感度の予算超過は感度を省略する
  だけで退避ではない (退避は本採点の予算超過だけ)。transition_stats の main / sens / elapsed_total_ms に分けて残す。

2026-10-09 M2e で足したこと: 遷移の分岐のうち状態を作れない分岐 (ふきとばし・ほえる で相手の未判明の個体を引き出した分岐。
TurnResult.unevaluated) は V を評価せず、その確率を候補ごとの未評価の質量 δ_a に回す。候補 a の未評価の質量は
m_a = ε + (1 − ε) δ_a、q は評価した分岐だけで正規化した期待値、区間は [(1 − m_a) q + m_a V_MIN, (1 − m_a) q + m_a V_MAX]
(δ_a = 0 なら M2d と同じ)。第一候補の下限が他の全候補の上限を上回らなければ退避 (ε > 0 なら "opp_unevaluated_ambiguous"、
分岐の未評価だけなら "unevaluated_ambiguous")。

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


def _ctx_key(ctx: Optional[dict]) -> Optional[tuple]:
    if not ctx:
        return None
    return tuple(sorted((str(k), repr(v)) for k, v in ctx.items()))


def make_damage_fn(my_species: set, my_ctx: Optional[dict], opp_ctx: Optional[dict]) -> Callable:
    """calc_damage を盤面の文脈 (fainted_allies) 込みで呼び、結果をキャッシュする関数 (純粋な計算の再利用)。

    キャッシュのキー (2026-10-09 M2c で修正): 攻撃側・防御側の MonView のダメージに効く部分 (valuation.view_key)、技、場、
    使った文脈 (陣営の fainted_allies 等) と、ダメージが残り HP で変わり得る側 (damage.hp_dependence: ふんか・しおふき・
    きしかいせい・じたばた・しぼりとる・しおみず、もうか系・マルチスケイル 等) の残り HP そのもの (9 桁に丸めた値)。
    M2b までは HP を「満タン / 1/3 超 / 以下」の区分でしかキーに入れず、ふんかを HP 90% で計算した後に 40% で計算すると 90% の
    結果が返っていた (評価の順で結果が変わる)。HP で変わらない技はキーに HP を入れない (乱数の分岐で HP が細かく分かれても
    再計算しない)。
    文脈: 呼び出し側が陣営 side ("me" / "opp") を渡せばその陣営の文脈、渡さなければ攻撃側の種族が自分の選出にあれば自分側
    (同じ種族どうしの対面では自分側の文脈を使う近似。M2b までと同じ)"""
    from advisor.damage import hp_dependence
    cache: dict = {}
    dep_cache: dict = {}
    vk_cache: dict = {}

    def vk(v):
        hit = vk_cache.get(id(v))
        if hit is not None and hit[0] is v:
            return hit[1]
        out = view_key(v)
        vk_cache[id(v)] = (v, out)
        return out

    def fn(att_view: MonView, att_hp: float, def_view: MonView, def_hp: float, move_id: str,
           fieldv: Optional[FieldView], side: Optional[str] = None) -> Optional[dict]:
        if side == "me":
            ctx = my_ctx
        elif side == "opp":
            ctx = opp_ctx
        else:
            ctx = my_ctx if att_view.species_id in my_species else opp_ctx
        ak, dk = vk(att_view), vk(def_view)
        dkey = (ak, dk, move_id)
        dep = dep_cache.get(dkey)
        if dep is None:
            dep = hp_dependence(att_view, def_view, move_id)
            dep_cache[dkey] = dep
        key = (ak, round(float(att_hp), 9) if dep[0] else None, dk, round(float(def_hp), 9) if dep[1] else None,
               move_id, _field_key(fieldv), _ctx_key(ctx))
        if key in cache:
            return cache[key]
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
            d = damage_fn(opp.active, opp.active_hp, me.active, me.active_hp, mid, field_against(opp_field, me), side="opp")
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
    # 以下は 2026-10-09 M2c。応手を別の仮説の状態で作り直すための材料 (pool = engine.opponent_move_pool の結果を技の固定で
    # 絞ったもの、response_mode = "full" (技 + 交代) / "no_switch" (交代技の後の繰り出し) / None (相手は行動しない))
    pool: list = field(default_factory=list)
    response_mode: Optional[str] = None
    # HP の範囲 (段 3): 不確かな個体ごとの {"side", "slot", "name", "hp", "lo", "hi", "source", "age_s"}。me / opp は悲観側の仮説
    # (自分の個体 = 下限、相手の個体 = 上限)、hp_alt は楽観側の仮説の (me, opp)。範囲が無ければ空 / None
    hp_ranges: list = field(default_factory=list)
    hp_alt: Optional[tuple] = None
    # 持ち物の感度 (段 5): [(名前, 相手の陣営 or None, 省略の理由 or None)]。名前は "scarf" / "sash"
    item_alts: list = field(default_factory=list)
    # 相手の技の候補のうち使用率で推定した技 (判明技でない。pool の重み < 100) の id (2026-10-09 M2d)。これらの応手の未対応の効果は
    # 退避せず「未評価」に回す (_score_core)。空なら従来どおり全部退避
    opp_estimated: frozenset = frozenset()


def _fallback(reason: str, t0: float, **extra) -> dict:
    return {"ok": False, "fallback": reason, "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1), **extra}


def variant_inputs(inp: TransitionInputs, me: SimSide, opp: SimSide) -> TransitionInputs:
    """別の仮説の状態 (me, opp) で同じ候補を比べる入力 (応手はその状態で作り直す。全候補で同じ応手を使う規則は変えない)"""
    from dataclasses import replace as _r
    responses = _responses_for(me, opp, inp.pool, inp.damage_fn, inp.opp_field, inp.response_mode)
    vctx = _r(inp.vctx, _cache={})
    return _r(inp, me=me, opp=opp, responses=responses, vctx=vctx, hp_ranges=[], hp_alt=None, item_alts=[])


def _responses_for(me: SimSide, opp: SimSide, pool: list, damage_fn: Callable, opp_field: Optional[FieldView],
                   mode: Optional[str]) -> Optional[list]:
    if mode == "full":
        return opponent_responses(opp, me, pool, damage_fn, opp_field)
    if mode == "no_switch":
        return opponent_responses(opp, me, pool, damage_fn, opp_field, include_switch=False)
    return None


# 局面評価 V の値域 (advisor.valuation: 終了 ±1、終了していない局面は ±VALUATION_NONTERMINAL_MAX)。未評価の応手の寄与の上下限に使う
V_MIN, V_MAX = -1.0, 1.0


def _unevaluated_ok(inp: "TransitionInputs", oa: Action) -> bool:
    """相手の応手 oa の未対応の効果を「未評価」に回してよいか (2026-10-09 M2d、レビュー 1)。
    使用率で推定した技 (inp.opp_estimated に入っている技) だけ。判明技・交代・推定の集合が無い入力は従来どおり退避"""
    return oa.kind == "move" and bool(oa.move_id) and oa.move_id in (inp.opp_estimated or ())


def _score_core(inp: TransitionInputs, t0: float, deadline: float, budget: float) -> dict:
    """候補ごとの Q / Q_worst / 分岐の要約 (score の本体。退避の判定もここ)。

    未評価の応手 (2026-10-09 M2d): 相手の応手の解決に未対応の効果が出たとき、その応手が使用率の推定技なら退避せずに「未評価」に
    分ける。未評価の応手の集合 U は全候補で共通 (どれかの候補の解決で出たら全候補で U に入れる)、確率の合計を ε とする。
    各候補の q は U を除いた応手で正規化した期待値、候補の Q の区間は [(1 − ε) q + ε V_MIN, (1 − ε) q + ε V_MAX]。
    第一候補の下限が他の全候補の上限を上回るときだけ順位を確定し、そうでなければ決定全体を退避する
    ("opp_unevaluated_ambiguous")。ε = 0 なら従来どおり (q は全応手の期待値、区間の欄は付けない)。
    判明技の未対応・自分の候補の未対応は従来どおり即座に退避"""
    rows = []          # (cand, [(応手の番号, 確率, 期待値, ラベル, 統計)], 分岐数)
    n_total = 0
    reserves = {"me": int(inp.vctx.my_unknown or 0), "opp": int(inp.vctx.opp_unknown or 0)}
    unevaluated: dict = {}     # 応手の番号 → (効果, 技)
    branch_unevaluated: dict = {}   # 未評価の分岐 (M2e): "効果:技" → 技
    for cand in inp.candidates:
        per_resp = []
        n_br = 0
        if cand.mode == "replace" or inp.responses is None:
            me2 = apply_switch(inp.me, cand.action.bench_index) if cand.action.kind == "switch" else inp.me
            v = value(me2, inp.opp, inp.vctx)
            st = {"skip": 0.0, "ko": 0.0, "faint": 1.0 if me2.active_hp <= 0 else 0.0, "win": 0.0, "lose": 0.0}
            per_resp.append((None, 1.0, v, None, st, 0.0))
            n_br = 1
        else:
            for ri, (p_o, oa) in enumerate(inp.responses):
                brs = resolve_turn_branches(inp.me, inp.opp, cand.action, oa, inp.my_field, inp.opp_field,
                                            damage_fn=inp.damage_fn, reserves=reserves)
                e = 0.0
                u = 0.0            # 未評価の分岐の確率 (M2e: 未判明の個体を引き出した分岐 等)
                st = {"skip": 0.0, "ko": 0.0, "faint": 0.0, "win": 0.0, "lose": 0.0}
                skip_resp = False
                for p_b, res in brs:
                    if res.unsupported_by.get("me"):
                        w, eff, src = res.unsupported_by["me"][0]
                        return _fallback(f"unsupported:{eff}:{src}", t0, candidate=cand.id)
                    if res.unsupported_by.get("opp"):
                        w, eff, src = res.unsupported_by["opp"][0]
                        if _unevaluated_ok(inp, oa):
                            # 推定技の未対応の効果: この応手を未評価に回す (全候補で共通の集合)
                            unevaluated.setdefault(ri, (eff, oa.move_id))
                            skip_resp = True
                            break
                        # 判明技の未対応の効果 (2026-10-09 M2c): 記録だけで続行せず、その決定を legacy に退避する
                        return _fallback(f"opp_unsupported:{eff}:{src}", t0, candidate=cand.id, response=oa.label)
                    if res.unevaluated:
                        # 状態を作れない分岐 (M2e): V を評価せず、候補の未評価の質量に回す
                        u += p_b
                        for _w, eff, src in res.unevaluated:
                            branch_unevaluated.setdefault(f"{eff}:{src}", src)
                        continue
                    e += p_b * value(res.me, res.opp, inp.vctx, res.winner)
                    if cand.kind == "move" and any(w == "me" and k == "skip" and d == "fainted_before_acting"
                                                   for w, k, d in res.events):
                        st["skip"] += p_b
                    if res.fainted.get("opp"):
                        st["ko"] += p_b
                    if res.fainted.get("me"):
                        st["faint"] += p_b
                    if res.winner == "me":
                        st["win"] += p_b
                    elif res.winner == "opp":
                        st["lose"] += p_b
                n_br += len(brs)
                if not skip_resp:
                    per_resp.append((ri, p_o, e, oa.label, st, u))
                if time.perf_counter() > deadline:
                    return _fallback("budget", t0, candidate=cand.id, budget_ms=budget)
        n_total += n_br
        rows.append((cand, per_resp, n_br))
    eps = sum(inp.responses[ri][0] for ri in unevaluated) if (unevaluated and inp.responses) else 0.0
    une_info = None
    if unevaluated or branch_unevaluated:
        une_info = {"eps": round(eps, 6),
                    "moves": sorted({m for _e, m in unevaluated.values()} | set(branch_unevaluated.values())),
                    "effects": sorted({f"{e}:{m}" for e, m in unevaluated.values()} | set(branch_unevaluated))}
    amb_reason = "opp_unevaluated_ambiguous" if unevaluated else "unevaluated_ambiguous"
    out = []
    deltas = {}
    for cand, per_resp, n_br in rows:
        kept = [r for r in per_resp if r[0] is None or r[0] not in unevaluated]
        if not kept:
            return _fallback("opp_unevaluated_ambiguous", t0, candidate=cand.id, unevaluated=une_info)
        tot = sum(p for _i, p, _e, _l, _s, _u in kept)
        norm = 1.0 if not unevaluated else (tot or 1.0)
        # 未評価の分岐の質量 δ (M2e): 評価した分岐だけで q を正規化する。δ = 0 なら M2d までと同じ計算
        delta = min(1.0, max(0.0, sum(p * u for _i, p, _e, _l, _s, u in kept) / norm))
        ev_mass = 1.0 - delta
        if ev_mass <= 1e-12:
            return _fallback(amb_reason, t0, candidate=cand.id, unevaluated=une_info)
        deltas[(cand.kind, cand.id)] = delta
        q = sum(p * e for _i, p, e, _l, _s, _u in kept) / norm / ev_mass
        # 最悪の応手: 応手ごとの評価した分岐の条件つき期待値 (未評価だけの応手は除く。u = 0 なら M2d までと同じ)
        ev_resp = [t for t in kept if 1.0 - t[5] > 1e-12] or kept
        worst = min(ev_resp, key=lambda t: t[2] / max(1e-12, 1.0 - t[5]))
        agg = {k: sum(p * s[k] for _i, p, _e, _l, s, _u in kept) / norm / ev_mass
               for k in ("skip", "ko", "faint", "win", "lose")}
        br = {"n": n_br, "p_ko_before_act": round(agg["skip"], 4), "p_ko": round(agg["ko"], 4),
              "p_faint": round(agg["faint"], 4)}
        if agg["win"] > 0 or agg["lose"] > 0:
            br["p_win_now"] = round(agg["win"], 4)
            br["p_lose_now"] = round(agg["lose"], 4)
        if delta > 0:
            br["p_unevaluated"] = round(delta, 4)
        out.append((cand, q, worst[2] / max(1e-12, 1.0 - worst[5]), worst[3], br))
    # 並べ替えは丸める前の Q (同じ Q は候補の順 = 技の枠の順 → 交代の順のまま)
    out.sort(key=lambda t: -round(t[1], 9))      # 浮動小数の誤差で同じ Q の順が入れ替わらないように 9 桁で比べる
    intervals = None
    if unevaluated or any(d > 0 for d in deltas.values()):
        intervals = {}
        for c, q, *_r in out:
            m = eps + (1.0 - eps) * deltas.get((c.kind, c.id), 0.0)
            intervals[(c.kind, c.id)] = ((1.0 - m) * q + m * V_MIN, (1.0 - m) * q + m * V_MAX)
        if len(out) > 1:
            lo_best = intervals[(out[0][0].kind, out[0][0].id)][0]
            hi_other = max(intervals[(c.kind, c.id)][1] for c, *_r in out[1:])
            if not lo_best > hi_other:
                return _fallback(amb_reason, t0, unevaluated=dict(
                    une_info, best=out[0][0].id, best_lo=round(lo_best, 4), other_hi=round(hi_other, 4)))
    return {"ok": True, "ranked": out, "n_branches": n_total, "unevaluated": une_info, "intervals": intervals,
            "deltas": deltas}


def _best_id(core: dict) -> Optional[tuple]:
    if not core.get("ok") or not core.get("ranked"):
        return None
    c = core["ranked"][0][0]
    return (c.kind, c.id)


def sensitivity_enabled() -> bool:
    """感度 (楽観側の HP・スカーフ / タスキの有無) を計算するか (config.TRANSITION_SENSITIVITY_ENABLED を呼ぶたびに読む)"""
    return bool(C.TRANSITION_SENSITIVITY_ENABLED)


def score(inp: TransitionInputs, budget_ms: Optional[float] = None, sensitivity: Optional[bool] = None) -> dict:
    """候補ごとの Q / Q_worst / 分岐の要約を計算する (純粋。入力は書き換えない)。

    戻り値: 成功 {"ok": True, "actions": [...] (スコア順), "elapsed_ms", "elapsed_total_ms", "n_branches", "responses",
    "opp_unmodeled", "hp_range", "sensitivity", "unevaluated", "main", "sens"} / 退避 {"ok": False, "fallback": 理由, "elapsed_ms", ...}。

    順位は inp の状態 (HP が不確かな個体は悲観側の仮説: 自分 = 下限、相手 = 上限) の期待値で決める (2026-10-09 M2c。この悲観側を
    T と V に渡す処理は感度の有無に関係なく常に行う)。

    本採点と感度の分離 (2026-10-09 M2d、レビュー 3):
    - 本採点 ("main"): 順位を決める計算。elapsed_ms はその時間。計算予算 (TRANSITION_BUDGET_MS) を超えたらその決定は legacy に
      退避する (fallback "budget"。退避はこの場合だけ)。
    - 感度 ("sens"): 楽観側の HP の仮説 (hp_alt) と、相手の場の個体のスカーフ / タスキの有無を入れ替えた仮説 (item_alts) での
      順位。順位には使わない。sensitivity (既定 config.TRANSITION_SENSITIVITY_ENABLED) が偽なら計算しない (status "disabled")。
      本採点の残りの予算で計算し、予算を超えた仮説は省略して理由を残す (**省略しても本採点の結果を返す。退避ではない**)。
    - 全処理時間 (elapsed_total_ms): 感度込みの時間"""
    t0 = time.perf_counter()
    budget = C.TRANSITION_BUDGET_MS if budget_ms is None else float(budget_ms)
    deadline = t0 + budget / 1000.0
    sens_on = sensitivity_enabled() if sensitivity is None else bool(sensitivity)
    if not inp.candidates:
        return _fallback("no_candidates", t0)
    core = _score_core(inp, t0, deadline, budget)
    if not core.get("ok"):
        core["main"] = {"elapsed_ms": core.get("elapsed_ms"), "budget_ms": budget,
                        "budget_fallback": core.get("fallback") == "budget"}
        return core
    elapsed_main = round((time.perf_counter() - t0) * 1000, 1)
    best = _best_id(core)
    sens_run: list = []
    sens_omitted: dict = {}

    def alt_eval(name: str, me: SimSide, opp: SimSide) -> dict:
        if time.perf_counter() > deadline:
            sens_omitted[name] = "budget"
            return {"omitted": "budget"}
        alt = variant_inputs(inp, me, opp)
        r = _score_core(alt, t0, deadline, budget)
        if not r.get("ok"):
            why = r.get("fallback") or "unknown"
            sens_omitted[name] = why
            return {"omitted": why}
        sens_run.append(name)
        b = _best_id(r)
        return {"best": b[1] if b else None, "best_kind": b[0] if b else None, "changes": b != best,
                "q": {(c.kind, c.id): q for c, q, _qw, _wl, _br in r["ranked"]}}

    hp_info = None
    q_opt = {}
    if inp.hp_ranges:
        hp_info = {"individuals": [dict(h) for h in inp.hp_ranges], "best": best[1] if best else None}
        if inp.hp_alt is not None:
            if sens_on:
                r = alt_eval("hp_optimistic", *inp.hp_alt)
                q_opt = r.pop("q", {}) or {}
                hp_info["optimistic"] = r
            else:
                hp_info["optimistic"] = {"omitted": "disabled"}
    sens = {}
    if sens_on:
        for name, alt_opp, why in inp.item_alts:
            if alt_opp is None:
                sens[name] = {"omitted": why}
                sens_omitted[name] = why
                continue
            r = alt_eval(name, inp.me, alt_opp)
            r.pop("q", None)
            r["flip"] = why
            sens[name] = r
    actions = []
    intervals = core.get("intervals") or {}
    deltas = core.get("deltas") or {}
    une = core.get("unevaluated")
    for cand, q, qw, wl, br in core["ranked"]:
        a = _action_dict(inp, cand, q, qw, wl, br)
        if (cand.kind, cand.id) in q_opt:
            a["q_hp_opt"] = round(q_opt[(cand.kind, cand.id)], 4)
        if (cand.kind, cand.id) in intervals:
            lo, hi = intervals[(cand.kind, cand.id)]
            m = (hi - lo) / (V_MAX - V_MIN)        # この候補の未評価の質量 (応手の ε + 分岐の未評価。M2e)
            a["q_lo"], a["q_hi"], a["eps_unevaluated"] = round(lo, 4), round(hi, 4), une["eps"]
            if deltas.get((cand.kind, cand.id), 0.0) > 0:
                a["unevaluated_mass"] = round(m, 4)
                a["reason"] += f" / 未評価 {m:.0%} (応手 {une['eps']:.0%} + 分岐、{', '.join(une['moves'])}): " \
                               f"Q の区間 {lo:+.3f}〜{hi:+.3f}"
            else:
                a["reason"] += f" / 未評価の応手 {une['eps']:.0%} ({', '.join(une['moves'])}): Q の区間 {lo:+.3f}〜{hi:+.3f}"
        actions.append(a)
    elapsed_total = round((time.perf_counter() - t0) * 1000, 1)
    has_alts = bool(inp.item_alts) or inp.hp_alt is not None
    if not sens_on:
        sens_status = "disabled"
    elif not has_alts:
        sens_status = "none"
    elif sens_omitted and not sens_run:
        sens_status = "omitted"
    elif sens_omitted:
        sens_status = "partial"
    else:
        sens_status = "run"
    return {"ok": True, "actions": actions, "elapsed_ms": elapsed_main,
            "elapsed_total_ms": elapsed_total,
            "n_branches": core["n_branches"],
            "responses": [{"label": a.label + ("+メガ" if a.mega and not a.label.endswith("+メガ") else ""),
                           "p": round(p, 4)} for p, a in (inp.responses or [])],
            "opp_unmodeled": [],
            "hp_range": hp_info,
            "sensitivity": sens or None,
            "unevaluated": une,
            "main": {"elapsed_ms": elapsed_main, "budget_ms": budget, "budget_fallback": False},
            "sens": {"enabled": sens_on, "status": sens_status, "run": list(sens_run), "omitted": dict(sens_omitted),
                     "elapsed_ms": round(elapsed_total - elapsed_main, 1)}}


def _action_dict(inp: TransitionInputs, cand: Candidate, q: float, q_worst: float, worst_label, br: dict) -> dict:
    legacy = inp.legacy_by_key.get((cand.kind, cand.id)) or {}
    parts = []
    if cand.kind == "move":
        d = inp.damage_fn(inp.me.active, inp.me.active_hp, inp.opp.active, inp.opp.active_hp, cand.action.move_id,
                          field_against(inp.my_field, inp.opp), side="me") if inp.opp.active_hp > 0 else None
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
    if cand.party_index is not None:
        # HP の範囲 (2026-10-09 M2c): この交代先の HP が不確かなら、順位に使った悲観側 (下限) と範囲を残す
        rng = next((h for h in inp.hp_ranges if h.get("side") == "me" and h.get("party_index") == cand.party_index), None)
        if rng:
            a["hp_range"] = [rng["lo"], rng["hi"]]
            a["reason"] += f" / HP は範囲 {rng['lo']:.0%}〜{rng['hi']:.0%} の下限で評価"
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


def hp_range_of(p: dict, *, side: str, is_active: bool, now: Optional[float]) -> Optional[dict]:
    """個体の HP の範囲 (純粋。2026-10-09 M2c、規則と係数は config の HP_RANGE_*)。

    戻り値: 不確かでなければ None。不確かなら {"hp", "lo", "hi", "source", "age_s"}、範囲を作れなければ {"error": 理由}
    (直近値が無い "no_recent_value" / 自分の個体の最大 HP が分からない "max_hp_unknown")。
    不確かさの出所: バー推定 (hp_source == "bar") → ERR_BAR、交代の見逃し等 (hp_uncertain、出所なし) → ERR_UNCERTAIN、
    技イベント由来の推定 (hp_estimated) → ERR_ESTIMATED、場の個体の観測が古い (now − hp_read_ts > STALE_SEC) → 経過時間で下限を下げる"""
    if p.get("status") == "fainted":
        return None
    src = None
    err = 0.0
    if p.get("hp_source") == "bar":
        src, err = "bar", float(C.HP_RANGE_ERR_BAR)
    elif p.get("hp_uncertain"):
        src, err = "uncertain", float(C.HP_RANGE_ERR_UNCERTAIN)
    elif p.get("hp_estimated"):
        src, err = "estimated", float(C.HP_RANGE_ERR_ESTIMATED)
    age = None
    drop = 0.0
    ts = p.get("hp_read_ts")
    if is_active and ts and now:
        age = max(0.0, float(now) - float(ts))
        if age > float(C.HP_RANGE_STALE_SEC):
            drop = (age - float(C.HP_RANGE_STALE_SEC)) * float(C.HP_RANGE_STALE_DROP_PER_SEC)
            src = src or "stale"
    if src is None:
        return None
    if p.get("hp_percent") is None and not (p.get("hp_current") is not None and p.get("hp_max")):
        return {"error": "no_recent_value"}
    if side == "me":
        mhp = p.get("hp_max")
        if not mhp:
            return {"error": "max_hp_unknown"}
        floor = 1.0 / max(1, int(mhp))
    else:
        floor = 0.01
    from advisor.engine import _hp_frac_of
    h0 = _hp_frac_of(p)
    lo = max(floor, min(h0, h0 - err - drop))
    hi = max(lo, min(1.0, h0 + err))
    return {"hp": round(h0, 4), "lo": round(lo, 4), "hi": round(hi, 4), "source": src,
            "age_s": None if age is None else round(age, 1)}


def _with_hp(side: SimSide, slot, hp: float) -> SimSide:
    """陣営の 1 体の HP を差し替えた新しい陣営 (slot = "active" または控えの位置)"""
    from dataclasses import replace as _r
    if slot == "active":
        return _r(side, active=_r(side.active, hp_frac=hp), active_hp=hp)
    bench = list(side.bench)
    v, _hp = bench[slot]
    bench[slot] = (_r(v, hp_frac=hp), hp)
    return _r(side, bench=bench)


def restrict_opp_pool(pool: list, opp_p: dict, state: dict, opp_item_observed: Optional[str]) -> tuple:
    """相手の技の候補を、選べない技と矛盾しないように絞る (純粋。2026-10-09 M2c、レビュー 4)。戻り値 (pool, 注記の列)。

    アンコール (固定技だけ。evaluate_common でも絞っている)、ちょうはつ (変化技を除く)、かなしばり ("disable_<技>" を除く)、
    観測で確定したこだわり系の持ち物 (直前の技 last_move.opponent が候補にあればそれだけ。直前の技は交代・ひんしで消える)。
    相手の PP は観測していないので PP 0 は扱わない。全部除くと候補が無くなるときは絞らない (注記だけ残す)"""
    from advisor.engine import CHOICE_ITEM_IDS
    dex = get_dex()
    vols = {str(v).lower() for v in (opp_p.get("volatiles") or [])}
    notes = []
    out = list(pool or [])
    last = (state.get("last_move") or {}).get("opponent")
    if "encore" in vols:
        locked = opp_p.get("encore_move") or last
        if locked and any(m == locked for m, _w in out):
            out = [(locked, 100.0)]
            notes.append(f"相手はアンコール中: {locked} だけ")
    if "taunt" in vols:
        kept = [(m, w) for m, w in out if (dex.move(m) or {}).get("category") != "Status"]
        if kept and len(kept) < len(out):
            notes.append("相手はちょうはつ中: 変化技を除く")
            out = kept
    disabled = {v.split("_", 1)[1] for v in vols if v.startswith("disable_")}
    if disabled:
        kept = [(m, w) for m, w in out if m not in disabled]
        if kept and len(kept) < len(out):
            notes.append(f"相手のかなしばり: {', '.join(sorted(disabled))} を除く")
            out = kept
    if opp_item_observed in CHOICE_ITEM_IDS and not opp_p.get("item_consumed") and not opp_p.get("item_removed") \
            and last and any(m == last for m, _w in out) and len(out) > 1:
        out = [(last, 100.0)]
        notes.append(f"相手はこだわり中: {last} だけ")
    return out, notes


def _drop_contradicted_item(view: MonView, p: dict, observed: Optional[str], resolver) -> tuple:
    """判明情報と矛盾する持ち物の仮定を外す (純粋。2026-10-09 M2c)。戻り値 (view, 注記 or None)。
    消費済み (item_consumed: きあいのタスキ・きのみ 等)・はたきおとされた (item_removed) なら持ち物なし。
    観測で確定していない (使用率・型推定の) とつげきチョッキは、変化技を使ったのを見たら外す"""
    from dataclasses import replace as _r
    if view.item and (p.get("item_consumed") or p.get("item_removed")):
        return _r(view, item=None), f"{view.item} は消費済み / 失った"
    if view.item == "assaultvest" and observed != "assaultvest" and resolver is not None:
        dex = get_dex()
        for ja in p.get("revealed_moves") or []:
            r = resolver.resolve(ja, "moves", cutoff=0.8)
            if r and (dex.move(r[1]) or {}).get("category") == "Status":
                return _r(view, item=None), "変化技を使ったので とつげきチョッキ の仮定を外す"
    return view, None


def build_inputs(state: dict, resolver, *, my_view: MonView, opp_view: Optional[MonView], opp_p: Optional[dict],
                 pool: list, legal_move_ids: list, my_field: FieldView, opp_field: FieldView, my_ctx: dict,
                 opp_ctx: dict, my_fainted: bool, pivot_pending: bool, i_am_faster: Optional[bool],
                 legacy_actions: list, rl_hint: Optional[dict], opp_pending: bool,
                 encore_locked: Optional[str] = None) -> tuple:
    """engine.evaluate_common の共有入力 (MonView・合法手・補完した技・相手の型の仮定・控え) から TransitionInputs を作る。

    戻り値 (TransitionInputs, None) / 作れなければ (None, 退避の理由)。自分の控えは legacy の交代候補と同じ規則 (選出済み・
    ひんしでない・場でない)。相手の控えは対戦に出たと分かっている個体 (_seen) だけ (登録 6 体を残存戦力にしない。計画書 §2.1)。
    相手の未判明の残り = 選出数 (team_size があれば) − 判明した数、無ければ残数 − 判明した生存数 (残数も無ければ選出数 − 判明した数)。

    2026-10-09 M2c で足したこと:
    - 判明情報との矛盾の除外 (段 5): 消費済み・失った持ち物は外す (両陣営)、観測で確定していない持ち物の仮定のうち判明した技と矛盾
      するもの (とつげきチョッキ と変化技) を外す、相手の技の候補を技の固定 (アンコール・ちょうはつ・かなしばり・観測したこだわり)
      で絞る (restrict_opp_pool)、自分の控えの PP 0 の技を後続の対面の技から外す。
    - 揮発状態の持ち越し: 状態のアンコール ("encore:<技>")・ちょうはつ・みがわり (残り HP は分からないので最大 HP の 1/4 と仮定) と
      直前の技 (last_move) を SimSide に入れる。
    - HP の範囲 (段 3、hp_range_of): 不確かな個体は悲観側の仮説 (自分 = 下限、相手 = 上限) を me / opp に入れ、楽観側を hp_alt に
      残す。範囲を作れない個体があればその決定は legacy に退避 ("hp_range")。
    - 持ち物の感度 (段 5): 相手の場の個体の持ち物が観測で確定していなければ、スカーフ / タスキの有無を入れ替えた仮説を item_alts に"""
    from dataclasses import replace as _r

    from advisor.engine import (_hp_frac_of, _opp_mega_forms, _rl_probs, build_mon_view, complete_moves_from_registration,
                                opponent_move_pool, registered_moves_of_mon)
    from advisor.party import battle_party_indices
    from vision.state import roster_slots
    if opp_view is None or opp_p is None:
        return None, "opp_unknown"
    if opp_pending:
        return None, "opp_pending"
    dex = get_dex()
    my_state, opp_state = state["player"], state["opponent"]
    my_idx, opp_idx = my_state.get("active_index"), opp_state.get("active_index")
    my_p = my_state["party"][my_idx]
    opp_hp = _hp_frac_of(opp_p)
    if opp_p.get("status") == "fainted" or opp_hp <= 0:
        if not my_fainted:
            return None, "opp_fainted"
    now = state.get("now") or time.time()
    notes = []
    last = state.get("last_move") or {}

    # 判明情報と矛盾する持ち物を外す (自分の場の個体・相手の場の個体)
    my_view, n1 = _drop_contradicted_item(my_view, my_p, my_p.get("item_id"), None)
    opp_view, n2 = _drop_contradicted_item(opp_view, opp_p, opp_p.get("item_id"), resolver)
    notes.extend(n for n in (n1, n2) if n)
    pool, pool_notes = restrict_opp_pool(pool, opp_p, state, opp_p.get("item_id"))
    notes.extend(pool_notes)

    # HP の範囲 (不確かな個体)
    ranges = []
    errors = []

    def note_range(p, side, slot, is_active, party_index=None):
        r = hp_range_of(p, side=side, is_active=is_active, now=now)
        if r is None:
            return
        if "error" in r:
            errors.append(f"{side}:{p.get('species_id') or p.get('species_ja')}:{r['error']}")
            return
        ranges.append(dict(r, side=side, slot=slot, party_index=party_index,
                           name=p.get("species_ja") or p.get("species_id")))

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
        v, _n = _drop_contradicted_item(v, p, p.get("item_id"), None)
        note_range(p, "me", len(bench), False, party_index=i)
        bench.append((v, _hp_frac_of(p)))
        bench_meta.append((i, p))
        obs = list(p.get("moves") or [])
        if len(obs) < 4 and resolver is not None:
            try:
                obs = complete_moves_from_registration(obs, registered_moves_of_mon(p, resolver))
            except Exception:
                pass
        my_moves.setdefault(v.species_id, tuple(s.get("move_id") for s in obs
                                                if s.get("move_id") and dex.move(s.get("move_id")) and s.get("pp") != 0))
    if not my_fainted:
        note_range(my_p, "me", "active", True, party_index=my_idx)
    my_hz, opp_hz = _hazards(my_state), _hazards(opp_state)
    my_vols = {str(v).lower() for v in (my_p.get("volatiles") or [])}
    me = SimSide(active=my_view, active_hp=0.0 if my_fainted else _hp_frac_of(my_p),
                 bench=bench, stealth_rock=my_hz["stealth_rock"], spikes=my_hz["spikes"],
                 sticky_web=my_hz["sticky_web"], mega_used=bool((state.get("mega_used") or {}).get("player")),
                 screens=_screens_of(my_state), tailwind=bool(my_state.get("tailwind")),
                 volatiles=_side_volatiles(my_vols, encore_locked), last_move=last.get("player"),
                 sub_hp=_sub_hp_of(my_vols, my_view))

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
        v, _n = _drop_contradicted_item(v, p, p.get("item_id"), resolver)
        note_range(p, "opp", len(obench), False)
        opp_moves.setdefault(v.species_id, tuple(m for m, _w in opool if dex.move(m))[:6])
        obench.append((v, hp))
    if opp_hp > 0:
        note_range(opp_p, "opp", "active", True)
    if errors:
        return None, "hp_range"
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
    opp_vols = {str(v).lower() for v in (opp_p.get("volatiles") or [])}
    opp_locked = None
    if "encore" in opp_vols:
        cand = opp_p.get("encore_move") or last.get("opponent")
        opp_locked = cand if cand and any(m == cand for m, _w in pool) else None
    opp = SimSide(active=opp_view, active_hp=opp_hp, bench=obench, stealth_rock=opp_hz["stealth_rock"],
                  spikes=opp_hz["spikes"], sticky_web=opp_hz["sticky_web"],
                  mega_forms=_opp_mega_forms(opp_p, state),
                  mega_used=bool((state.get("mega_used") or {}).get("opponent")),
                  screens=_screens_of(opp_state), tailwind=bool(opp_state.get("tailwind")),
                  volatiles=_side_volatiles(opp_vols, opp_locked), last_move=last.get("opponent"),
                  sub_hp=_sub_hp_of(opp_vols, opp_view))
    # HP の仮説: 悲観側 (自分 = 下限、相手 = 上限) を順位に、楽観側を感度に
    hp_alt = None
    if ranges:
        pess_me, pess_opp, opt_me, opt_opp = me, opp, me, opp
        for r in ranges:
            if r["side"] == "me":
                pess_me = _with_hp(pess_me, r["slot"], r["lo"])
                opt_me = _with_hp(opt_me, r["slot"], r["hi"])
            else:
                pess_opp = _with_hp(pess_opp, r["slot"], r["hi"])
                opt_opp = _with_hp(opt_opp, r["slot"], r["lo"])
        me, opp = pess_me, pess_opp
        hp_alt = (opt_me, opt_opp)
        notes.append("HP が不確かな個体は範囲の悲観側 (自分 = 下限、相手 = 上限) で評価: " +
                     ", ".join(f"{r['name']} {r['lo']:.0%}〜{r['hi']:.0%}" for r in ranges))
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
    mode = None
    if my_fainted:
        notes.append("ひんし後の繰り出し: 相手は行動しない (繰り出しの候補どうしを V で比べる)")
    elif pivot_pending:
        # 交代技の後の繰り出し先の選択: 自分が先に動いた (相手がまだ行動していない) なら相手の技を受ける (交代はしない)。
        # 相手が先に動いた後なら相手は行動しない
        if i_am_faster:
            mode = "no_switch"
            notes.append("交代技の後の繰り出し: 相手はまだ行動していない見込み (技を受ける)")
        else:
            notes.append("交代技の後の繰り出し: 相手は行動済みの見込み (技を受けない)")
    else:
        mode = "full"
    responses = _responses_for(me, opp, pool, damage_fn, opp_field, mode)
    # 持ち物の感度: 相手の場の個体の持ち物が観測で確定していなければ、スカーフ / タスキの有無を入れ替えた仮説
    item_alts = []
    if mode is not None:
        observed = opp_p.get("item_id")
        for name, item in C.TRANSITION_SENSITIVITY_ITEMS:
            if observed or opp_p.get("item_consumed") or opp_p.get("item_removed"):
                item_alts.append((name, None, "item_known"))
                continue
            if item == "focussash" and opp.active_hp < 1.0 - 1e-6:
                item_alts.append((name, None, "not_full_hp"))
                continue
            has = opp.active.item == item
            alt_view = _r(opp.active, item=None if has else item)
            item_alts.append((name, _r(opp, active=alt_view), "remove" if has else "add"))
    legacy_by_key = {(a.get("kind"), a.get("id")): a for a in legacy_actions}
    for (i, p) in bench_meta:
        if p.get("hp_uncertain"):
            k = ("switch", p.get("species_id") or f"slot{i}")
            legacy_by_key[k] = dict(legacy_by_key.get(k) or {}, hp_uncertain=True)
    inp = TransitionInputs(me=me, opp=opp, my_field=my_field, opp_field=opp_field, candidates=cands, responses=responses,
                           vctx=vctx, damage_fn=damage_fn, rl_probs=_rl_probs(rl_hint), legacy_by_key=legacy_by_key,
                           notes=notes, pool=list(pool), response_mode=mode, hp_ranges=ranges, hp_alt=hp_alt,
                           item_alts=item_alts, opp_estimated=estimated_moves(pool, opp_p, resolver))
    return inp, None


def estimated_moves(pool: list, opp_p: Optional[dict], resolver) -> frozenset:
    """相手の技の候補のうち「使用率で推定した技」の id (純粋。2026-10-09 M2d)。
    pool の重みが 100 未満 (engine.opponent_move_pool: 判明技 = 100、推定技 = 使用率 × 減衰、型の代表技 = 50) で、
    相手の個体の判明技 (revealed_moves を resolver で引いたもの) に入っていない技"""
    revealed = set()
    for ja in (opp_p or {}).get("revealed_moves") or []:
        r = resolver.resolve(ja, "moves", cutoff=0.8) if resolver is not None else None
        if r:
            revealed.add(r[1])
    return frozenset(m for m, w in (pool or []) if float(w) < 100.0 and m not in revealed)


def _side_volatiles(vols: set, encore_locked: Optional[str]) -> frozenset:
    """状態の揮発状態 → SimSide.volatiles (アンコールは固定技が分かるときだけ "encore:<技>"、ちょうはつ)"""
    out = set()
    if "encore" in vols and encore_locked:
        out.add(f"encore:{encore_locked}")
    if "taunt" in vols:
        out.add("taunt")
    return frozenset(out)


def _sub_hp_of(vols: set, view: MonView) -> float:
    """状態にみがわりがあれば、その残り HP (分からないので最大 HP の 1/4 = 張った直後と仮定)"""
    if "substitute" not in vols:
        return 0.0
    from advisor.transition import _frac_of, floor_hp_frac
    return floor_hp_frac(view, *_frac_of("substitute_frac", (1, 4)))


# ------------------------------------------------------------------ 退避率の集計 (advice 行から。純粋)
def _advice_of(a) -> Optional[dict]:
    adv = a.get("advice") if isinstance(a, dict) and isinstance(a.get("advice"), dict) else a
    return adv if isinstance(adv, dict) else None


def _reason_key(fb) -> str:
    """退避の理由の分類: 未対応の効果 (unsupported / opp_unsupported) は「理由:効果:技」まで、ほかは「:」の前"""
    key = str(fb).split(":", 2)
    return ":".join(key[:3]) if key[0] in ("unsupported", "opp_unsupported") else key[0]


def _pct(vals: list, q: float) -> Optional[float]:
    """分位点 (最近傍。vals が空なら None)"""
    if not vals:
        return None
    s = sorted(vals)
    return s[min(len(s) - 1, int(len(s) * q))]


def _rate_of(advs: list) -> dict:
    n = n_fb = 0
    by: dict = {}
    opp_by: dict = {}
    # 未評価の応手 (2026-10-09 M2d): 未評価が出た決定の数 (続行 / 区間で順位が決まらず退避) と技の頻度
    une_n = une_cont = une_amb = 0
    une_by: dict = {}
    # 本採点 / 感度 / 全処理時間 (2026-10-09 M2d)
    main_ms: list = []
    total_ms: list = []
    n_budget = 0
    sens_status: dict = {}
    sens_omit: dict = {}
    for adv in advs:
        if adv.get("scoring_mode") != "transition":
            continue
        n += 1
        fb = adv.get("scoring_fallback")
        if fb:
            n_fb += 1
            k = _reason_key(fb)
            by[k] = by.get(k, 0) + 1
            if k.startswith("opp_unsupported:"):
                eff = k.split(":", 2)[1]
                opp_by[eff] = opp_by.get(eff, 0) + 1
            if k == "budget":
                n_budget += 1
        ts = adv.get("transition_stats") or {}
        une = ts.get("unevaluated")
        if une:
            une_n += 1
            if fb:
                une_amb += 1
            else:
                une_cont += 1
            for m in une.get("moves") or []:
                une_by[m] = une_by.get(m, 0) + 1
        main = ts.get("main") or {}
        em = main.get("elapsed_ms", ts.get("elapsed_ms") if ts else None)
        if em is not None and not fb:
            main_ms.append(float(em))
        if ts.get("elapsed_total_ms") is not None and not fb:
            total_ms.append(float(ts["elapsed_total_ms"]))
        sens = ts.get("sens")
        if sens and not fb:
            st = sens.get("status") or "unknown"
            sens_status[st] = sens_status.get(st, 0) + 1
            for name, why in (sens.get("omitted") or {}).items():
                key = f"{name}:{why}"
                sens_omit[key] = sens_omit.get(key, 0) + 1
    n_opp = sum(opp_by.values())
    return {"n": n, "n_fallback": n_fb, "rate": (n_fb / n) if n else None,
            "by_reason": dict(sorted(by.items(), key=lambda kv: (-kv[1], kv[0]))),
            "opp_unsupported": {"n": n_opp, "rate": (n_opp / n) if n else None,
                                "by_effect": dict(sorted(opp_by.items(), key=lambda kv: (-kv[1], kv[0])))},
            "unevaluated": {"n": une_n, "rate": (une_n / n) if n else None, "continued": une_cont, "ambiguous": une_amb,
                            "by_move": dict(sorted(une_by.items(), key=lambda kv: (-kv[1], kv[0])))},
            # 3 つを分ける: 本採点 (時間と予算超過の退避) / 感度 (実行・省略) / 全処理時間 (感度込み)。時間は採点できた決定だけ
            "main": {"n": len(main_ms), "p50_ms": _pct(main_ms, 0.5), "p95_ms": _pct(main_ms, 0.95),
                     "budget_fallback": n_budget, "budget_fallback_rate": (n_budget / n) if n else None},
            "sens": {"status": dict(sorted(sens_status.items())),
                     "omitted": dict(sorted(sens_omit.items(), key=lambda kv: (-kv[1], kv[0])))},
            "total": {"n": len(total_ms), "p50_ms": _pct(total_ms, 0.5), "p95_ms": _pct(total_ms, 0.95)}}


def summarize_fallbacks(advices, decisions=None) -> dict:
    """advice 行 (または evaluate の戻り値) の列から transition 方式の退避率を集計する (純粋)。

    行の単位: 分母は scoring_mode が transition の行、分子はそのうち scoring_fallback がある行。理由は「:」の前で分類し、
    未対応の効果 (自分の候補 unsupported / 相手の応手 opp_unsupported) は効果と技まで分ける。
    決定の単位 (2026-10-09 M2c、"decisions"): 同じ決定の advice 行 (画面の更新ごとに何度も出る) を 1 つに数える。
    decisions (battle_logger の decision 行の列) を渡せば、各 decision 行の advice_id の advice (決定の時点に出ていた助言) を
    その決定の代表にする (advice_id が advice の列に無い決定は数えない)。渡さなければ、advice の列の並びで position_key
    (engine.position_key_of: 場の個体・技の固定・交代しか選べないか) と turn が続けて同じ行を 1 つの決定とみなし、その最後の行を
    代表にする。opp_unsupported = 相手の応手の未対応の効果で退避した割合と効果の内訳。
    2026-10-09 M2d: unevaluated = 未評価の応手が出た決定 (区間で順位が決まって続行 continued / 決まらず退避 ambiguous) と技の頻度
    (頻出する未対応の技を実装の優先順位の材料にする)。main = 本採点の時間 (p50 / p95、採点できた行だけ) と予算超過の退避、
    sens = 感度の状態 (disabled / none / run / partial / omitted) と省略の理由、total = 感度込みの全処理時間"""
    advs = [x for x in (_advice_of(a) for a in (advices or ())) if x is not None]
    out = _rate_of(advs)
    reps: list = []
    unit = "position"
    if decisions is not None:
        unit = "decision"
        by_id = {}
        for a, adv in zip(advices or (), (_advice_of(a) for a in (advices or ()))):
            if adv is None:
                continue
            aid = (a.get("advice_id") if isinstance(a, dict) else None) or adv.get("advice_id")
            if aid:
                by_id[str(aid)] = adv
        for d in decisions:
            adv = by_id.get(str(d.get("advice_id")))
            if adv is not None:
                reps.append(adv)
    else:
        prev_key = object()
        for a in advices or ():
            adv = _advice_of(a)
            if adv is None:
                continue
            turn = (a.get("turn") if isinstance(a, dict) and a is not adv else None) or adv.get("turn")
            k = (repr(adv.get("position_key")), turn)
            if reps and k == prev_key:
                reps[-1] = adv
            else:
                reps.append(adv)
            prev_key = k
    out["decisions"] = dict(_rate_of(reps), unit=unit)
    return out
