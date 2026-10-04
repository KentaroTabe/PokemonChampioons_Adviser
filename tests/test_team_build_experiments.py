"""検証実験 (tools/team_build/experiments) の純粋関数のテスト。

    python -m tests.test_team_build_experiments
"""
from __future__ import annotations

from tools.team_build.experiments import best_delta_by_team
from tools.team_build.experiments import calibration as CAL
from tools.team_build.experiments import concept_origin as CO
from tools.team_build.experiments import env_validity as EV
from tools.team_build.experiments import llm_audit as LA


def test_calibration():
    # 2 項 (coverage 正、hole 負)。Δ は coverage − 2·hole で決まる世界 → 既定 (1, −1) より (1, −2) 寄りが良い
    def run(seed):
        import random
        rng = random.Random(seed)
        rows = []
        for _ in range(8):
            c, h = rng.random(), rng.random() * 0.3
            rows.append({"parts": {"coverage": c, "hole": h}, "delta": c - 2.0 * h + rng.gauss(0, 0.02)})
        return rows
    runs = [run(s) for s in range(6)]
    d = {"coverage": 1.0, "hole": -1.0}
    w = CAL.fit_weights(runs, d, grid=(0.0, 0.5, 1.0, 2.0, 4.0))
    assert w["coverage"] > 0 and w["hole"] < 0 and abs(w["hole"] / w["coverage"]) >= 1.5, w
    loo = CAL.leave_one_run_out(runs, d, grid=(0.0, 0.5, 1.0, 2.0, 4.0))
    assert len(loo) == 6 and all(r["rho_fitted"] is not None for r in loo)
    dec = CAL.decide(loo, min_gain=0.0, share=0.5, min_runs=5)
    assert dec["n_runs"] == 6 and dec["median_rho_fitted"] >= dec["median_rho_default"]
    assert CAL.decide(loo[:3], min_runs=5)["adopt"] is False
    assert CAL.within_run_rho(runs[0][:2], d) is None
    nf = CAL.noise_floor({"a+b": [0.10, 0.03], "c": [0.2]})
    assert nf["teams"][0]["sd"] == 0.035 and nf["median_sd"] == 0.035
    res = {"arms": [{"arm_id": "X@cheap", "result": {"mean": 0.1}, "n_done": 300, "eliminated_at": 300},
                    {"arm_id": "X@teampreview", "result": {"mean": -0.1}, "n_done": 100, "eliminated_at": 100},
                    {"arm_id": "Y", "result": {"mean": 0.05}, "n_done": 600, "state": "uncertain"}]}
    b = best_delta_by_team(res)
    assert b["X"]["delta"] == 0.1 and b["X"]["eliminated"] is True and b["Y"]["eliminated"] is False
    print("test_calibration OK")


def test_concept_origin():
    fam = {"C001": "llm:offense", "C002": "rule:balance", "C003": "archetype:tr"}
    rows = [{"candidate_id": "L00_INC", "tag": "incumbent", "ok": True, "score": 0.7},
            {"candidate_id": "L01_C001", "tag": "concept", "ok": True, "score": 0.9},
            {"candidate_id": "L02_C002", "tag": "best", "ok": True, "score": 0.8},
            {"candidate_id": "L03_C003", "tag": "fill", "ok": True, "score": 0.6},
            {"candidate_id": "L01_C001-R1A1", "tag": "repair", "ok": True, "origin": {"kind": "repair"}},
            {"candidate_id": "L09_C002", "tag": "concept", "ok": False}]
    deltas = {"L00_INC": {"delta": 0.1, "eliminated": False}, "L01_C001": {"delta": -0.2, "eliminated": True},
              "L02_C002": {"delta": 0.0, "eliminated": False}, "L01_C001-R1A1": {"delta": 0.01, "eliminated": False}}
    s10 = {"arms": [{"arm_id": "L00_INC", "state": "improved"}, {"arm_id": "L02_C002", "state": "degraded", "eliminated_at": 300}]}
    t = CO.reach_table(rows, fam, deltas, ["L00_INC", "L02_C002"], s10, "L00_INC", "PASS")
    tab = t["table"]
    assert tab["incumbent"]["holdout_pass"] == 1 and tab["incumbent"]["s10_contender"] == 1
    assert tab["llm"]["generated"] == 1 and tab["llm"]["measured"] == 1 and tab["llm"]["s8a_survived"] == 0
    assert tab["rule"]["s8b_contender"] == 1 and tab["rule"]["s10_contender"] == 0
    assert tab["archetype"]["generated"] == 1 and tab["archetype"]["measured"] == 0
    assert tab["repair"]["s8a_survived"] == 1
    agg = CO.aggregate([t, t])
    assert agg["llm"]["measured"] == 2 and agg["llm"]["survive_rate_s8a"] == 0.0 and agg["incumbent"]["s10_rate"] == 1.0
    print("test_concept_origin OK")


def test_env_validity():
    teams = {"t1": ["a", "b", "c", "d", "e", "f"], "t2": ["a", "b", "x", "y", "z", "w"]}
    fam = {"t1": "F1", "t2": "F2"}
    assert EV.overlap({"a", "b", "c", "d", "e", "f"}, set(teams["t1"])) == 1.0
    assert EV.overlap({"a", "b", "c"}, set(teams["t1"])) == 1.0 and abs(EV.overlap({"a", "b", "x"}, set(teams["t1"])) - 2 / 3) < 1e-9
    m = EV.match_family({"a", "b", "x", "y"}, teams, fam)
    assert m["family_id"] == "F2" and m["overlap"] == 1.0          # 4 体しか読めていない → 含有率
    cov = EV.coverage_summary([{"overlap": 0.75}, {"overlap": 0.2}, {"overlap": 0.5}], 0.5)
    assert cov["covered"] == 2 and abs(cov["covered_share"] - 2 / 3) < 1e-3
    recs = [{"family_id": "F1", "won": True}] * 4 + [{"family_id": "F1", "won": False}] + [{"family_id": "F2", "won": False}] * 3 + [{"family_id": "F3", "won": True}]
    wr = EV.family_win_rates(recs, min_n=3)
    assert wr["F1"] == {"n": 5, "win_rate": 0.8} and wr["F2"]["win_rate"] == 0.0 and "F3" not in wr
    cmp = EV.compare_families({"F1": {"n": 5, "win_rate": 0.8}, "F2": {"n": 3, "win_rate": 0.0}, "F3": {"n": 3, "win_rate": 0.5}},
                              {"F1": {"n": 50, "win_rate": 0.7}, "F2": {"n": 50, "win_rate": 0.3}, "F3": {"n": 50, "win_rate": 0.5}, "F4": {"n": 9, "win_rate": 0.9}})
    assert cmp["n_common"] == 3 and cmp["spearman"] == 1.0
    print("test_env_validity OK")


def test_llm_audit():
    assert LA.cohen_kappa([1, 1, 0, 0], [1, 1, 0, 0]) == 1.0 and LA.cohen_kappa([1, 1, 0, 0], [0, 0, 1, 1]) == -1.0
    assert LA.cohen_kappa([1, 0, 1, 0], [1, 0, 0, 1]) == 0.0 and LA.cohen_kappa([0, 0], [0, 0]) == 1.0
    v = LA.parse_verdicts({"verdicts": [{"id": 0, "flags": ["item_mismatch", "bogus"]}, {"id": "2", "flags": []}, {"id": 9, "flags": ["role_mismatch"]}]}, [0, 1, 2])
    assert v == {0: {"item_mismatch"}, 1: set(), 2: set()}
    assert LA.majority([{"a"}, {"a", "b"}, {"b"}]) == {"a", "b"} and LA.majority([{"a"}, {"b"}]) == set() and LA.majority([]) == set()
    runs = {"s#1": {0: {"item_mismatch"}, 1: set(), 2: {"role_mismatch"}}, "s#2": {0: {"item_mismatch"}, 1: set(), 2: set()}}
    ag = LA.agreement(runs, [0, 1, 2], categories=("item_mismatch", "role_mismatch"))
    assert len(ag["pairs"]) == 1 and ag["per_category"]["item_mismatch"] == 1.0 and ag["per_category"]["role_mismatch"] == 0.0
    fr = LA.flag_rates(runs["s#1"], [0, 1, 2], categories=("item_mismatch", "role_mismatch"))
    assert fr["any"] == 0.667 and fr["item_mismatch"] == 0.333
    # 配線: mock provider で 1 周
    from tools.team_build.llm.provider import MockProvider
    import json
    sets = [{"species": "x", "ability": "a", "item": "leftovers", "nature": "jolly", "evs": "2/32/0/0/0/32", "moves": ["tackle"], "role": "breaker"}] * 3
    prov = MockProvider([json.dumps({"authoritative": {"verdicts": [{"id": 0, "flags": ["ev_misallocation"]}, {"id": 1, "flags": []}, {"id": 2, "flags": []}]}})])
    labels = LA.audit_once(prov, "sonnet", sets, 10, "audit_test")
    assert labels == {0: {"ev_misallocation"}, 1: set(), 2: set()}
    # 呼び出しの失敗は「判定なし」(None)。「指摘なし」(空集合) と区別し、指摘率と κ の分母から外す
    # (2026-10-04: Haiku の 2・3 周目が全部 429 で失敗し、指摘なしと数えて κ 0.33・tier 間 κ 0.0 が出た)
    bad = MockProvider([""])                                   # JSON が返らない → 再試行しても ok にならない
    failed = LA.audit_once(bad, "haiku", sets, 10, "audit_fail")
    assert failed == {0: None, 1: None, 2: None}
    mixed = {"h#1": labels, "h#2": failed, "h#3": {0: {"ev_misallocation"}, 1: None, 2: set()}}
    ag2 = LA.agreement(mixed, [0, 1, 2])
    by = {(p["a"], p["b"]): p for p in ag2["pairs"]}
    assert by[("h#1", "h#2")]["n"] == 0 and by[("h#1", "h#2")]["kappa_any"] is None     # 失敗した周との対は数えない
    assert by[("h#1", "h#3")]["n"] == 2 and by[("h#1", "h#3")]["kappa_any"] == 1.0 and ag2["mean_kappa_any"] == 1.0
    fr2 = LA.flag_rates(mixed["h#3"], [0, 1, 2])
    assert fr2["n"] == 2 and fr2["any"] == 0.5 and LA.flag_rates(failed, [0, 1, 2])["n"] == 0
    assert LA.majority([{"a"}, None, {"a", "b"}]) == {"a"} and LA.majority([None, None]) is None
    assert LA.judged(mixed["h#3"], [0, 1, 2]) == [0, 2]
    # 再開: 成功した記録 (同じ段・同じ入力・同じ system) は再利用して呼び出さない。失敗の記録は使わない
    prompt = json.dumps({"sets": [dict(s, id=i) for i, s in enumerate(sets)], "categories": list(LA.CATEGORIES)}, ensure_ascii=False, indent=1)
    ok_text = json.dumps({"authoritative": {"verdicts": [{"id": 1, "flags": ["role_mismatch"]}]}})
    recs = [{"stage": "audit_x", "system": LA.SYSTEM, "prompt": prompt, "error": None, "problems": [], "raw_text": ok_text},
            {"stage": "audit_y", "system": LA.SYSTEM, "prompt": prompt, "error": "RuntimeError('429')", "problems": ["出力に JSON オブジェクトが見つからない"], "raw_text": ""},
            {"stage": "audit_z", "system": "別の system", "prompt": prompt, "error": None, "problems": [], "raw_text": ok_text}]
    cache = LA.cached_verdicts(recs, LA.SYSTEM)
    assert list(cache) == [("audit_x", prompt)]
    idle = MockProvider([""])
    assert LA.audit_once(idle, "haiku", sets, 10, "audit_x", cache) == {0: set(), 1: {"role_mismatch"}, 2: set()} and idle.i == 0
    assert LA.audit_once(idle, "haiku", sets, 10, "audit_y", cache) == {0: None, 1: None, 2: None} and idle.i > 0
    retried = dict(recs[0], stage="audit_r", prompt=prompt + LA.RETRY_MARK + " (直して再出力):\n- x")     # 再試行で成功した記録
    assert ("audit_r", prompt) in LA.cached_verdicts([retried], LA.SYSTEM)
    print("test_llm_audit OK")


def test_learned_surrogate():
    from tools.team_build.experiments import learned_surrogate as LS
    def score(team, opp):
        if opp == ["x"]:
            return []
        return [((0, 1, 2), 0.1 * len(set(team) & set(opp)))]
    assert LS.learned_value(["a", "b", "c"], [["a", "b"], ["c"], ["x"]], score) == 0.15
    assert LS.learned_value(["a"], [["x"]], score) is None
    rows = [{"delta": 0.1, "learned": 0.6, "surrogate": 0.2}, {"delta": 0.0, "learned": 0.5, "surrogate": 0.9},
            {"delta": -0.1, "learned": 0.4, "surrogate": 0.5}, {"delta": 0.2, "learned": None, "surrogate": 0.1}]
    c = LS.compare_surrogates(rows)
    assert c["n"] == 3 and c["spearman_learned"] == 1.0 and c["spearman_surrogate"] == -0.5
    print("test_learned_surrogate OK")


def main() -> None:
    test_calibration()
    test_learned_surrogate()
    test_concept_origin()
    test_env_validity()
    test_llm_audit()
    print("ALL OK")


if __name__ == "__main__":
    main()
