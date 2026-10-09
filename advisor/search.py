"""同時手番の探索エンジン (択の利得行列 + 2手読み)。

最上位帯の意思決定に必要な「択」を扱う:
- 自分の行動 (技+交代) × 相手の行動 (予測技+交代) の利得行列を構築
- 各ペアについて1ターンをシミュレート (優先度/素早さ順、ダメージロール群化)
- 結果盤面を「次ターンの1手読み」で評価する = 実質2手読み
- 行動ごとに 期待値 (相手の行動分布で加重) と 保証値 (相手最善=ミニマックス)
  を算出し、期待値と保証値が乖離する行動には択リスクを付ける

1 ターンの解決 (simulate_turn) は共通の遷移 advisor/transition.py (resolve_turn) に委譲する (2026-10-09 M1b)。
対応している効果と未対応の効果は transition.py の冒頭の表を参照。

簡略化 (v1):
- 追加効果 (怯み/能力低下/状態異常付与) は未シミュレート
- ダメージロールは {min, avg, max} の3群 (重み 0.25/0.5/0.25) — foul play方式
- ひんし時の後続は静的評価が最良の1体に固定
"""
from __future__ import annotations

import math
from dataclasses import replace
from typing import Optional

from advisor.damage import FieldView
# 1 ターンの解決は共通の遷移 (advisor/transition.py、docs/ADVICE_ACCURACY_PLAN_1009.md §2.3) に移した。
# 下の名前は既存の呼び出し側 (endgame / tools / tests) のための再公開
from advisor.transition import (  # noqa: F401
    HAZARD_MOVES, HEAL_MOVES, PROTECT_MOVES, SETUP_MOVES, STATUS_MOVES, SUCKER_MOVES, Action, SimSide, TurnResult,
    apply_switch as _apply_switch, best_bench as _best_bench, dmg_frac as _dmg_frac, hazard_frac as _hazard_frac,
    is_attack_action as _is_attack_action, mega_evolve as _mega_evolve, priority as _priority, resolve_turn,
    speed as _speed)
from champions_agent.config import SEARCH_OPP_MEGA_MIN_PROB

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


def opp_mega_split(acts: list, side: SimSide, min_prob: float = SEARCH_OPP_MEGA_MIN_PROB) -> list:
    """相手の技の候補を「メガシンカあり / なし」に分岐する。確率 p = メガ石を持つ事前分布の合計 (判明情報込み)。
    p < min_prob か、権利消費済み・姿が不明なら分岐しない。純粋"""
    if not side.mega_forms or side.mega_used:
        return acts
    p = min(1.0, sum(side.mega_forms.values()))
    if p < min_prob:
        return acts
    out = []
    for a in acts:
        if a.kind != "move":
            out.append(a)
            continue
        out.append(replace(a, prob=a.prob * (1.0 - p)))
        out.append(replace(a, prob=a.prob * p, mega=True, label=f"{a.label}+メガ"))
    return out


def simulate_turn(me: SimSide, opp: SimSide, my_act: Action, opp_act: Action,
                  my_field: Optional[FieldView], opp_field: Optional[FieldView],
                  roll: str) -> tuple:
    """1ターンを解決し (me', opp') を返す (副作用なし)。互換の入口: 解決は共通の遷移 advisor.transition.resolve_turn
    (結果の詳細 (ひんし・繰り出し要求・未対応の効果) が要る呼び出し側は resolve_turn を直接使う)"""
    r = resolve_turn(me, opp, my_act, opp_act, my_field, opp_field, roll)
    return r.me, r.opp


def _my_actions(me: SimSide, my_moves: list) -> list:
    acts = [Action("move", move_id=m, label=m) for m in my_moves]
    for i, (view, hp) in enumerate(me.bench):
        if hp > 0:
            acts.append(Action("switch", bench_index=i,
                               label=f"交代:{view.name_ja or view.species_id}"))
    return acts


def _opp_actions(opp: SimSide, opp_move_pool: list,
                 opp_prior: Optional[dict] = None,
                 prior_mix: float = 0.0) -> list:
    """相手の行動候補。技は予測プール、交代はベンチ全員 (重みは控えめ)。

    opp_prior (P6-b): {"move:<id>": p, "switch:<bench_index>": p} の事前分布
    (自己対戦方策が相手の立場で出す確率)。prior_mix=λ で
    p = (1-λ)·使用率由来 + λ·事前分布 に混ぜ、合計1に正規化する。
    事前分布に無い候補は λ 側が 0 (使用率側だけが残る)。
    """
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
    if opp_prior and prior_mix > 0:
        lam = max(0.0, min(1.0, prior_mix))
        for a in acts:
            key = (f"move:{a.move_id}" if a.kind == "move"
                   else f"switch:{a.bench_index}")
            a.prob = (1 - lam) * a.prob + lam * float(opp_prior.get(key, 0.0))
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
    opp_acts = opp_mega_split(_opp_actions(opp, opp_move_pool), opp)
    best = -9.9
    for ma in _my_actions(me, my_moves):
        v = 0.0
        for oa in opp_acts:
            m2, o2 = simulate_turn(me, opp, ma, oa, my_field, opp_field, "avg")
            v += oa.prob * static_eval(m2, o2)
        best = max(best, v)
    return best


WINCON_PRESERVE_W = 0.15   # 勝ち筋個体のHP残存ボーナスの重み


def _mon_hp_of(side: SimSide, sid: str) -> float:
    """陣営内の指定種族の残りHP割合 (いなければ0)"""
    if side.active is not None and side.active.species_id == sid:
        return max(0.0, side.active_hp)
    for mv, hp in side.bench:
        if mv.species_id == sid:
            return max(0.0, hp)
    return 0.0


def search(me: SimSide, opp: SimSide, my_moves: list, opp_move_pool: list,
           my_field: Optional[FieldView] = None,
           opp_field: Optional[FieldView] = None,
           depth: int = 2,
           leaf_value_fn=None,
           wincon_sid: Optional[str] = None,
           opp_prior: Optional[dict] = None,
           prior_mix: float = 0.0) -> dict:
    """利得行列を構築し、行動ごとの期待値/保証値/択リスクを返す。

    my_moves: 自分の技ID列。opp_move_pool: [(move_id, weight)]。
    wincon_sid: 勝ち筋 (endgame検出) の種族ID。指定すると葉評価に
        その個体のHP残存ボーナスを加え、勝ち筋を消耗させる行動を
        相対的に下げる (定説「勝ち筋は大切に扱う」)。
    opp_prior / prior_mix (P6-b): 根の相手行動分布に混ぜる事前分布と混合率。
        1手読み (_position_value) の相手分布は使用率のみ (コスト優先)。
    戻り値 {"actions": [{label, kind, expected, worst, worst_reply,
                          recommended, risky}], "matrix": {...}}
    """
    my_acts = _my_actions(me, my_moves)
    opp_acts = opp_mega_split(_opp_actions(opp, opp_move_pool, opp_prior, prior_mix), opp)
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
                    if wincon_sid:
                        base += WINCON_PRESERVE_W * _mon_hp_of(m2, wincon_sid)
                    v += w * base
                else:
                    sv = static_eval(m2, o2)
                    if wincon_sid:
                        sv += WINCON_PRESERVE_W * _mon_hp_of(m2, wincon_sid)
                    v += w * sv
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

    return _finalize(results, matrix)


def _finalize(results: list, matrix) -> dict:
    """行動ごとの期待値/保証値から推奨値・順位・起点警告を決める。

    search() と aggregate_worlds() (多世界統合) が同じ規則を使うために分離。
    状況依存のリスク調整: 局面評価 (=最善手の期待値) で保証値の重みを
    変えてから推奨順を決める。優勢なら択を避け、劣勢なら賭ける
    """
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


_POOL = None
SEARCH_WORKERS = 1   # 世界の並列実行数 (1=逐次)。engine / search_expert が上書きする


def make_rl_leaf_fn(leaf_ctx: Optional[dict]):
    """leaf_ctx {"my_moves","field","turn"} から RL価値の葉評価関数を作る。
    モデルが無ければ None (ワーカー側でも呼べるようモジュール関数にしてある)"""
    if not leaf_ctx:
        return None
    try:
        from advisor.rl_bridge import value_of_sim, _load_model
        if _load_model() is None:
            return None
    except Exception:
        return None
    my_moves = leaf_ctx.get("my_moves") or []
    fieldv = leaf_ctx.get("field")
    turn = leaf_ctx.get("turn") or 5

    def leaf_fn(m2, o2):
        return value_of_sim(m2, o2, my_moves, fieldv, turn=turn)

    return leaf_fn


def _search_job(kwargs: dict) -> dict:
    """プロセスプール用のジョブ。葉評価は関数でなく leaf_ctx で受け取り、
    ワーカー側でモデルを読んで再構成する (各ワーカーは初回に一度だけ読む)"""
    kwargs = dict(kwargs)
    leaf_ctx = kwargs.pop("leaf_ctx", None)
    kwargs["leaf_value_fn"] = make_rl_leaf_fn(leaf_ctx)
    return search(**kwargs)


def run_world_searches(jobs: list, workers: int = 1) -> list:
    """世界ごとの search(**kwargs) をまとめて実行する (P7 レイテンシ条項)。

    ジョブは search の引数辞書。葉評価は "leaf_value_fn" (関数、逐次のみ) か
    "leaf_ctx" ({"my_moves","field","turn"}、並列可) のどちらかで渡す。
    workers>1 かつ全ジョブが leaf_value_fn を持たなければプロセスプールで
    並列実行し、それ以外は逐次。並列化は各世界の結果を変えない (決定的)。
    プールは初回に生成して使い回す (spawn の起動コストを毎回払わない)。
    """
    def _seq(j):
        j = dict(j)
        ctx = j.pop("leaf_ctx", None)
        if j.get("leaf_value_fn") is None and ctx:
            j["leaf_value_fn"] = make_rl_leaf_fn(ctx)
        return search(**j)

    if workers <= 1 or len(jobs) <= 1 or any(j.get("leaf_value_fn") for j in jobs):
        return [_seq(j) for j in jobs]
    global _POOL
    from concurrent.futures import ProcessPoolExecutor
    if _POOL is None:
        _POOL = ProcessPoolExecutor(max_workers=workers)
    try:
        return list(_POOL.map(_search_job, jobs))
    except Exception:
        # プールが壊れた場合 (子プロセス死亡等) は逐次に戻す
        _POOL = None
        return [_seq(j) for j in jobs]


def sensor_worlds(me: SimSide, q: float, delta: float) -> list:
    """自分の表示HPが固着している可能性を世界に分ける (P8)。

    [(1-q, そのまま), (q, 自分アクティブのHPを delta だけ低く見た世界)]。
    表示が古い (実際はもっと削られている) 側だけを持つ: 決定再生の感度表で
    最大の反転要因 (自分HP固着 52.9%) は「表示より実HPが低い」向きのため。
    q<=0 なら [(1.0, me)] (無効)。
    """
    if q <= 0 or me.active_hp <= 0:
        return [(1.0, me)]
    import copy as _copy
    low = max(0.02, me.active_hp - delta)
    view = _copy.copy(me.active)
    view.hp_frac = low
    hedged = replace(me, active=view, active_hp=low)
    return [(1.0 - q, me), (q, hedged)]


def aggregate_worlds(world_results: list, weights: list,
                     coverage: Optional[float] = None) -> Optional[dict]:
    """相手型の仮説 (世界) ごとの search() 結果を仮説重みで統合する (P7)。

    - 期待値/保証値: 各世界の値の重み平均 (その行動が現れた世界で正規化)
    - 推奨値・順位: 統合後の値に _finalize と同じ規則を適用
    - support: その行動が世界内で最善 (recommended 首位) だった重みの和
    - expected_var: 期待値の重み付き分散 (仮説間のばらつき = 型依存の度合い)
    - belief.stability: 統合後の最善が各世界でも最善だった重みの和
    - matrix/最悪応手: 最も重い世界のもの / 全世界で最も低い応手
    coverage は呼び出し側が渡す「採用した仮説の重みの総和」(刈り込み前の
    分布に対する被覆率)。None なら重みの和。
    """
    pairs = [(r, w) for r, w in zip(world_results, weights)
             if r and r.get("actions")]
    if not pairs:
        return None
    total = sum(w for _, w in pairs) or 1.0
    pairs = [(r, w / total) for r, w in pairs]
    merged: dict = {}
    for r, w in pairs:
        top_label = r["actions"][0]["label"]
        for a in r["actions"]:
            m = merged.setdefault(a["label"], {
                "label": a["label"], "kind": a["kind"],
                "move_id": a.get("move_id"),
                "bench_index": a.get("bench_index"),
                "_exp": 0.0, "_wor": 0.0, "_sq": 0.0, "_w": 0.0,
                "support": 0.0, "worst_reply": None, "_worst_val": 9.9})
            m["_exp"] += w * a["expected"]
            m["_wor"] += w * a["worst"]
            m["_sq"] += w * a["expected"] ** 2
            m["_w"] += w
            if a["label"] == top_label:
                m["support"] += w
            if a["worst"] < m["_worst_val"]:
                m["_worst_val"] = a["worst"]
                m["worst_reply"] = a.get("worst_reply")
    results = []
    for m in merged.values():
        wsum = m.pop("_w") or 1.0
        exp = m.pop("_exp") / wsum
        wor = m.pop("_wor") / wsum
        var = max(0.0, m.pop("_sq") / wsum - exp ** 2)
        m.pop("_worst_val")
        m.update({"expected": round(exp, 3), "worst": round(wor, 3),
                  "expected_var": round(var, 4),
                  "support": round(m["support"], 3),
                  "risky": (exp - wor) > 0.35})
        results.append(m)
    heaviest = max(pairs, key=lambda pr: pr[1])[0]
    out = _finalize(results, heaviest.get("matrix"))
    best_label = out["actions"][0]["label"]
    out["belief"] = {
        "k": len(pairs),
        "coverage": round(coverage if coverage is not None else total, 3),
        "stability": round(merged[best_label]["support"], 3),
    }
    return out
