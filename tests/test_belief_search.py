"""多世界探索 (P7) のテスト — 純粋計算のみ (DB・対戦は使わない)。

    scripts/run_test.sh test_belief_search

相手型の仮説ごとの search() 結果を仮説重みで統合する aggregate_worlds と、
SpreadEstimator.top_k (事後確率の正規化・刈り込み) を検証する。
"""
from __future__ import annotations

import math

from advisor.ev_infer import SpreadEstimator
from advisor.search import _finalize, aggregate_worlds


def _world(actions):
    """[(label, expected, worst)] -> search() と同じ形の結果 (推奨順ソート済み)"""
    res = [{"label": l, "kind": "move", "move_id": l, "bench_index": None,
            "expected": e, "worst": w, "worst_reply": "x",
            "risky": (e - w) > 0.35} for l, e, w in actions]
    return _finalize(res, matrix=[{"my": l, "opp": "x", "v": e}
                                  for l, e, _ in actions])


def test_single_world_equals_search():
    """世界が1つなら統合結果は元の探索結果と同じ順位・値になる"""
    w = _world([("eq", 0.4, 0.1), ("sd", 0.3, 0.2)])
    out = aggregate_worlds([w], [1.0])
    assert [a["label"] for a in out["actions"]] == \
        [a["label"] for a in w["actions"]]
    assert out["actions"][0]["expected"] == w["actions"][0]["expected"]
    assert out["belief"] == {"k": 1, "coverage": 1.0, "stability": 1.0}
    print("test_single_world_equals_search OK")


def test_weighted_average_and_stability():
    """期待値は重み平均、安定度は統合後の最善が各世界でも最善だった重みの和"""
    w1 = _world([("eq", 0.6, 0.3), ("sd", 0.2, 0.1)])   # 世界1: eq が最善
    w2 = _world([("eq", 0.0, -0.2), ("sd", 0.5, 0.4)])  # 世界2: sd が最善
    out = aggregate_worlds([w1, w2], [0.75, 0.25])
    by = {a["label"]: a for a in out["actions"]}
    assert math.isclose(by["eq"]["expected"], 0.75 * 0.6 + 0.25 * 0.0, abs_tol=1e-3)
    assert math.isclose(by["sd"]["expected"], 0.75 * 0.2 + 0.25 * 0.5, abs_tol=1e-3)
    assert out["actions"][0]["label"] == "eq"
    assert math.isclose(out["belief"]["stability"], 0.75, abs_tol=1e-6)
    assert math.isclose(by["sd"]["support"], 0.25, abs_tol=1e-6)
    assert by["eq"]["expected_var"] > by["sd"]["expected_var"] * 0 and by["eq"]["expected_var"] > 0
    assert out["belief"]["k"] == 2
    print("test_weighted_average_and_stability OK")


def test_weights_normalized_and_coverage_kept():
    """重みは世界の和で正規化し、被覆率は呼び出し側の値を保持する"""
    w1 = _world([("a", 0.5, 0.5)])
    w2 = _world([("a", 0.1, 0.1)])
    out = aggregate_worlds([w1, w2], [0.6, 0.2], coverage=0.8)
    assert math.isclose(out["actions"][0]["expected"], 0.75 * 0.5 + 0.25 * 0.1, abs_tol=1e-3)
    assert out["belief"]["coverage"] == 0.8
    print("test_weights_normalized_and_coverage_kept OK")


def test_action_missing_in_some_world():
    """一部の世界にしか無い行動は、現れた世界の重みで正規化する"""
    w1 = _world([("a", 0.5, 0.5), ("b", 0.9, 0.9)])
    w2 = _world([("a", 0.5, 0.5)])
    out = aggregate_worlds([w1, w2], [0.5, 0.5])
    by = {a["label"]: a for a in out["actions"]}
    assert math.isclose(by["b"]["expected"], 0.9, abs_tol=1e-3)
    assert math.isclose(by["a"]["expected"], 0.5, abs_tol=1e-3)
    print("test_action_missing_in_some_world OK")


def test_empty_worlds_returns_none():
    assert aggregate_worlds([], []) is None
    assert aggregate_worlds([{"actions": []}], [1.0]) is None
    print("test_empty_worlds_returns_none OK")


def _estimator_with(logws):
    est = SpreadEstimator.__new__(SpreadEstimator)
    est.species_id = "garchomp"
    est.hyps = [{"nature": f"n{i}", "evs": {"atk": 252}, "item": f"i{i}",
                 "logw": lw} for i, lw in enumerate(logws)]
    est.n_obs = 0
    est.spe_lower = est.spe_upper = None
    est._choice_locked = False
    return est


def test_top_k_normalizes_over_all_hypotheses():
    """上位kの重みは全仮説で正規化した事後確率 (和 = 被覆率 ≤ 1)"""
    est = _estimator_with([math.log(0.5), math.log(0.3), math.log(0.2)])
    top = est.top_k(2)
    assert [h["nature"] for h in top] == ["n0", "n1"]
    assert math.isclose(sum(h["weight"] for h in top), 0.8, abs_tol=1e-3)
    assert est.top_k(1)[0]["item"] == est.best()["item"]   # k=1 は最尤仮説
    print("test_top_k_normalizes_over_all_hypotheses OK")


def test_top_k_prunes_light_hypotheses():
    """min_weight 未満は刈り込むが、最低1つは返す"""
    est = _estimator_with([math.log(0.9), math.log(0.09), math.log(0.01)])
    assert len(est.top_k(3, min_weight=0.05)) == 2
    assert len(est.top_k(3, min_weight=0.99)) == 1
    assert est.top_k(0) == []
    print("test_top_k_prunes_light_hypotheses OK")


if __name__ == "__main__":
    test_single_world_equals_search()
    test_weighted_average_and_stability()
    test_weights_normalized_and_coverage_kept()
    test_action_missing_in_some_world()
    test_empty_worlds_returns_none()
    test_top_k_normalizes_over_all_hypotheses()
    test_top_k_prunes_light_hypotheses()


# ---- P6-b: 相手行動の事前分布の混合 ----------------------------------------
def _opp_side():
    from advisor.dex import get_dex
    from advisor.damage import MonView
    from advisor.search import SimSide
    dex = get_dex()
    def view(sid):
        sp = dex.species(sid)
        return MonView(species_id=sid, types=sp["types"], base=sp["baseStats"],
                       ev={"atk": 252, "spa": 252, "spe": 252}, nature={})
    return SimSide(active=view("garchomp"), active_hp=1.0,
                   bench=[(view("primarina"), 1.0), (view("mimikyu"), 0.0)],
                   stealth_rock=False)


def test_opp_prior_mix_math():
    """λ=0 で使用率のみ、λ=1 で事前分布そのもの、中間は線形混合 (合計1)"""
    from advisor.search import _opp_actions
    opp = _opp_side()
    pool = [("earthquake", 60.0), ("scaleshot", 40.0)]
    base = {a.label: a.prob for a in _opp_actions(opp, pool)}
    prior = {"move:earthquake": 0.2, "move:scaleshot": 0.1, "switch:0": 0.7}
    same = {a.label: a.prob for a in _opp_actions(opp, pool, prior, 0.0)}
    assert same == base
    full = {(a.kind, a.move_id, a.bench_index): a.prob
            for a in _opp_actions(opp, pool, prior, 1.0)}
    assert math.isclose(full[("move", "earthquake", None)], 0.2, abs_tol=1e-6)
    assert math.isclose(full[("switch", None, 0)], 0.7, abs_tol=1e-6)
    half = _opp_actions(opp, pool, prior, 0.5)
    assert math.isclose(sum(a.prob for a in half), 1.0, abs_tol=1e-6)
    eq = next(a for a in half if a.move_id == "earthquake")
    assert math.isclose(eq.prob, 0.5 * base["earthquake"] + 0.5 * 0.2, abs_tol=1e-6)
    # ひんし (hp 0) のベンチは候補に出ない
    assert not any(a.bench_index == 1 for a in half)
    print("test_opp_prior_mix_math OK")


def test_opp_prior_missing_keys_keep_usage_side():
    """事前分布に無い候補は λ 側が 0 になるだけで消えない"""
    from advisor.search import _opp_actions
    opp = _opp_side()
    acts = _opp_actions(opp, [("earthquake", 1.0)], {"switch:0": 1.0}, 0.5)
    probs = {a.label: a.prob for a in acts}
    assert probs["earthquake"] > 0 and math.isclose(sum(probs.values()), 1.0, abs_tol=1e-6)
    print("test_opp_prior_missing_keys_keep_usage_side OK")


if __name__ == "__main__":
    test_opp_prior_mix_math()
    test_opp_prior_missing_keys_keep_usage_side()


# ---- P8: 自分HPのセンサ世界 -------------------------------------------------
def test_sensor_worlds_shape():
    """q=0 で無効、q>0 で (1-q, そのまま) と (q, HPを delta 下げた世界)"""
    from advisor.search import sensor_worlds
    from advisor.dex import get_dex
    from advisor.damage import MonView
    from advisor.search import SimSide
    sp = get_dex().species("garchomp")
    v = MonView(species_id="garchomp", types=sp["types"], base=sp["baseStats"],
                ev={"atk": 252}, nature={}, hp_frac=0.6)
    me = SimSide(active=v, active_hp=0.6, bench=[], stealth_rock=False)
    assert sensor_worlds(me, 0.0, 0.25) == [(1.0, me)]
    ws = sensor_worlds(me, 0.3, 0.25)
    assert len(ws) == 2 and math.isclose(ws[0][0], 0.7) and ws[1][0] == 0.3
    assert ws[0][1] is me
    assert math.isclose(ws[1][1].active_hp, 0.35, abs_tol=1e-6)
    assert math.isclose(ws[1][1].active.hp_frac, 0.35, abs_tol=1e-6)
    assert me.active.hp_frac == 0.6        # 元の世界は不変
    # 低HPの下限
    me2 = SimSide(active=v, active_hp=0.1, bench=[], stealth_rock=False)
    assert math.isclose(sensor_worlds(me2, 0.5, 0.25)[1][1].active_hp, 0.02)
    print("test_sensor_worlds_shape OK")


def test_displayed_hp_noise_is_sticky_and_seeded():
    """雑音注入: noise=0 で真値、noise=1 で初回以降は固着、同じタグで再現"""
    from champions_agent.env.search_expert import displayed_hp, _shown_hp

    class _B:
        battle_tag = "battle-test-p8"

    _shown_hp.clear()
    assert displayed_hp(_B(), 0.9, 0.0) == 0.9
    _shown_hp.clear()
    assert displayed_hp(_B(), 0.9, 1.0) == 0.9      # 初回は真値
    assert displayed_hp(_B(), 0.5, 1.0) == 0.9      # 以後は固着
    _shown_hp.clear()
    seq1 = [displayed_hp(_B(), hp, 0.5) for hp in (1.0, 0.8, 0.6, 0.4, 0.2)]
    _shown_hp.clear()
    seq2 = [displayed_hp(_B(), hp, 0.5) for hp in (1.0, 0.8, 0.6, 0.4, 0.2)]
    assert seq1 == seq2                              # 種付けで再現
    _shown_hp.clear()
    print("test_displayed_hp_noise_is_sticky_and_seeded OK")


if __name__ == "__main__":
    test_sensor_worlds_shape()
    test_displayed_hp_noise_is_sticky_and_seeded()


# ---- 世界の並列実行 ---------------------------------------------------------
def test_run_world_searches_parallel_matches_sequential():
    """並列実行は逐次と同じ結果を返す (決定的)。葉評価つきは逐次に落ちる"""
    from advisor.search import run_world_searches
    from advisor.dex import get_dex
    from advisor.damage import MonView
    from advisor.search import SimSide
    dex = get_dex()
    def view(sid, hp=1.0):
        sp = dex.species(sid)
        return MonView(species_id=sid, types=sp["types"], base=sp["baseStats"],
                       ev={"atk": 252, "spa": 252, "spe": 252}, nature={},
                       hp_frac=hp)
    me = SimSide(active=view("garchomp"), active_hp=1.0,
                 bench=[(view("primarina"), 1.0)], stealth_rock=False)
    jobs = []
    for hp in (1.0, 0.6):
        opp = SimSide(active=view("archaludon", hp), active_hp=hp,
                      bench=[(view("scizor"), 1.0)], stealth_rock=False)
        jobs.append(dict(me=me, opp=opp, my_moves=["earthquake", "scaleshot"],
                         opp_move_pool=[("dracometeor", 1.0), ("flashcannon", 1.0)],
                         depth=1))
    seq = run_world_searches(jobs, workers=1)
    par = run_world_searches(jobs, workers=2)
    assert [[(a["label"], a["expected"]) for a in r["actions"]] for r in seq] == \
        [[(a["label"], a["expected"]) for a in r["actions"]] for r in par]
    # 葉評価つきのジョブは逐次で処理される (例外なく結果が返る)
    jobs2 = [dict(j, leaf_value_fn=lambda m, o: 0.0) for j in jobs]
    assert len(run_world_searches(jobs2, workers=2)) == 2
    print("test_run_world_searches_parallel_matches_sequential OK")


if __name__ == "__main__":
    test_run_world_searches_parallel_matches_sequential()


# ---- P9: 探索値の助言スコア統合 --------------------------------------------
def test_apply_search_blend_relative_to_best():
    """探索の最善からの差を重みづけして加点 (最善は0、劣る行動は減点)。
    探索に無い行動と選べない行動 (score<=-90) は不変"""
    from advisor.engine import _apply_search_blend
    actions = [
        {"kind": "move", "id": "earthquake", "name": "じしん", "score": 50.0, "reason": ""},
        {"kind": "move", "id": "protect", "name": "まもる", "score": 48.0, "reason": ""},
        {"kind": "switch", "id": "primarina", "name": "アシレーヌ", "score": 40.0, "reason": ""},
        {"kind": "move", "id": "sealed", "name": "封印", "score": -99.0, "reason": ""},
        {"kind": "move", "id": "unknown", "name": "?", "score": 10.0, "reason": ""},
    ]
    search = [
        {"kind": "move", "move_id": "earthquake", "label": "earthquake", "recommended": 0.2},
        {"kind": "move", "move_id": "protect", "label": "protect", "recommended": 0.7},
        {"kind": "switch", "bench_index": 0, "label": "交代:アシレーヌ", "recommended": 0.5},
        {"kind": "move", "move_id": "sealed", "label": "sealed", "recommended": 0.9},
    ]
    _apply_search_blend(actions, search, 40.0)
    by = {a["id"]: a for a in actions}
    assert by["protect"]["score"] == 48.0 + 40 * (0.7 - 0.9)     # -8
    assert by["earthquake"]["score"] == 50.0 + 40 * (0.2 - 0.9)  # -28
    assert by["primarina"]["score"] == 40.0 + 40 * (0.5 - 0.9)   # -16
    assert by["sealed"]["score"] == -99.0                        # 選べない行動は不変
    assert by["unknown"]["score"] == 10.0                        # 探索に無い行動は不変
    assert "探索-8" in by["protect"]["reason"]
    print("test_apply_search_blend_relative_to_best OK")


if __name__ == "__main__":
    test_apply_search_blend_relative_to_best()


def test_run_world_searches_with_leaf_ctx():
    """leaf_ctx (葉評価の文脈) つきジョブは並列でも実行でき、逐次と一致する。
    RLモデルが無い環境 (CI) では葉評価なしとして両者とも同じ結果になる"""
    from advisor.search import run_world_searches, make_rl_leaf_fn
    from advisor.dex import get_dex
    from advisor.damage import MonView
    from advisor.search import SimSide
    dex = get_dex()
    def view(sid, hp=1.0):
        sp = dex.species(sid)
        return MonView(species_id=sid, types=sp["types"], base=sp["baseStats"],
                       ev={"atk": 252, "spa": 252, "spe": 252}, nature={},
                       hp_frac=hp)
    me = SimSide(active=view("garchomp"), active_hp=1.0,
                 bench=[(view("primarina"), 1.0)], stealth_rock=False)
    ctx = {"my_moves": ["earthquake", "scaleshot"], "field": None, "turn": 3}
    jobs = []
    for hp in (1.0, 0.6):
        opp = SimSide(active=view("archaludon", hp), active_hp=hp,
                      bench=[(view("scizor"), 1.0)], stealth_rock=False)
        jobs.append(dict(me=me, opp=opp, my_moves=ctx["my_moves"],
                         opp_move_pool=[("dracometeor", 1.0), ("flashcannon", 1.0)],
                         depth=2, leaf_ctx=ctx))
    seq = run_world_searches(jobs, workers=1)
    par = run_world_searches(jobs, workers=2)
    key = lambda r: [(a["label"], a["expected"]) for a in r["actions"]]
    assert [key(r) for r in seq] == [key(r) for r in par]
    assert make_rl_leaf_fn(None) is None
    print("test_run_world_searches_with_leaf_ctx OK")


if __name__ == "__main__":
    test_run_world_searches_with_leaf_ctx()
