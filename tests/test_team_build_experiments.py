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
    # 同じ並びの実戦: 自分の 6 体がそろって一致し、勝敗が残っている対戦だけ (10/5: 自分の種が 1 体しか読めていない対戦を
    # 部分集合として数えていて、16 戦を 17 戦と数えていた)
    ref = ["l", "m", "n", "o", "p", "q"]
    bs = [{"our_species": ref, "won": True}, {"our_species": list(reversed(ref)), "won": False},
          {"our_species": ["l"], "won": False}, {"our_species": ["l", "m", "n", "o", "p", "zz"], "won": True},
          {"our_species": ref, "won": None}, {"our_species": [], "won": True}]
    same = EV.same_team_battles(bs, set(ref))
    assert [b["won"] for b in same] == [True, False]
    assert EV.same_team_battles(bs, {"l"}) == [] and EV.same_team_battles(bs, set()) == [] and EV.same_team_battles([], set(ref)) == []
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
    # 層化と帰無分布 (2026-10-05): 現行チームの系統が高く測定され、学習の代理もその系統を高く見るだけの世界。
    # 全部の並びでは相関が出るが、探索の並びだけでは出ない → 採否は探索の層で行い、不採用になる
    import random
    rng = random.Random(7)

    def run(n_inc: int, n_exp: int) -> list:
        out = []
        for i in range(n_inc):
            out.append({"candidate_id": f"L0{i}_INC", "tag": "incumbent_mut", "delta": 0.05 + 0.01 * rng.random(),
                        "learned": 0.70 + 0.01 * rng.random(), "surrogate": 0.80 + 0.01 * rng.random()})
        for i in range(n_exp):
            out.append({"candidate_id": f"L1{i}_C00{i}", "tag": "concept", "delta": -0.10 + 0.05 * rng.random(),
                        "learned": 0.60 + 0.02 * rng.random(), "surrogate": 0.90 + 0.02 * rng.random()})
        return out
    runs = [run(3, 6) for _ in range(5)]
    assert LS.is_incumbent_family({"candidate_id": "L01_INC-R1A1", "tag": "repair"}) and LS.is_incumbent_family({"candidate_id": "L00_INC"})
    assert not LS.is_incumbent_family({"candidate_id": "L05_C020", "tag": "concept"})
    assert [len(x) for x in LS.stratum_rows(runs, "exploration")] == [6] * 5 and [len(x) for x in LS.stratum_rows(runs, "all")] == [9] * 5
    s = LS.stratified_summary(runs, n_perm=300, seed=1)
    assert s["all"]["learned"]["mean"] > 0.5 and s["all"]["learned"]["p"] < 0.05           # 系統の対比だけで有意に見える
    assert s["all"]["surrogate"]["mean"] < 0 and s["exploration"]["n_rows_per_run"] == [6] * 5
    assert abs(s["exploration"]["learned"]["mean"]) < 0.5 and s["exploration"]["learned"]["p"] > 0.05
    d = LS.decide(s)
    assert d["adopt"] is False and d["stratum"] == "exploration" and "p =" in d["reason"]
    # 探索の並びの中でも学習の代理が Δ の順位を当てる世界 → 採用
    good = []
    for _ in range(5):
        rows2 = []
        for i in range(8):
            dlt = -0.2 + 0.05 * i
            rows2.append({"candidate_id": f"L2{i}_C01{i}", "tag": "concept", "delta": dlt, "learned": 0.5 + dlt, "surrogate": rng.random()})
        good.append(rows2)
    s2 = LS.stratified_summary(good, n_perm=300, seed=1)
    d2 = LS.decide(s2)
    assert s2["exploration"]["learned"]["median"] == 1.0 and s2["exploration"]["learned"]["p"] < 0.05 and d2["adopt"] is True
    # 並びが足りない run は数えない。使える run が無ければ p は None、採否は不採用
    few = [[{"candidate_id": "L00_INC", "tag": "incumbent", "delta": 0.0, "learned": 0.6, "surrogate": 0.5}]]
    s3 = LS.stratified_summary(few, n_perm=10, seed=1)
    assert s3["exploration"]["learned"]["p"] is None and LS.decide(s3)["adopt"] is False
    assert LS.pooled_rho([[{"delta": 0.1, "learned": 0.1}, {"delta": 0.2, "learned": 0.3}]], "learned")["n_runs"] == 0
    print("test_learned_surrogate OK")


def test_selection_compliance():
    from tools.team_build.real_eval import selection_compliance
    recs = [{"type": "scene", "t": 1.0, "state": {"scene": "selection", "selection_picked": 0, "player": {"party": [{"ja": "A"}, {"ja": "B"}, {"ja": "C"}]}}},
            {"type": "advice", "kind": "selection", "t": 2.0, "advice": {"primary": "model", "recommend": [{"index": 0, "name": "A", "lead": True}, {"index": 2, "name": "C"}, {"index": 1, "name": "B"}]}},
            {"type": "scene", "t": 5.0, "state": {"scene": "selection", "selection_picked": 3, "player": {"party": [{"ja": "A", "picked": True}, {"ja": "B", "picked": True}, {"ja": "C", "picked": True}]}}}]
    c = selection_compliance(recs)
    assert c["has_advice"] and c["primary"] == "model" and c["members_match"] is True and c["basis"] == "picked"
    assert c["timely"] is True and c["latency_s"] == 3.0
    late = [dict(recs[2], t=1.5)] + recs[:2]
    c2 = selection_compliance(late)
    assert c2["timely"] is False
    # picked が揃わないときは場に出た種で判定 (推奨外が出れば不一致)
    recs3 = recs[:2] + [{"type": "scene", "t": 9.0, "state": {"scene": "command", "player": {"active": 1, "party": [{"ja": "A"}, {"ja": "D"}, {"ja": "C"}]}}}]
    assert selection_compliance(recs3)["members_match"] is False and selection_compliance(recs3)["basis"] == "observed:1"
    assert selection_compliance([recs[0]])["has_advice"] is False
    print("test_selection_compliance OK")


def test_opponent_pick_validity_helpers():
    from tools.team_build.experiments import opponent_pick_validity as OV
    assert OV.consistent({"opp_full": True, "opp_seen": ["a", "b"], "opp_species": ["a", "b", "c", "d", "e", "f"], "our_species": list("uvwxyz")})
    assert not OV.consistent({"opp_full": True, "opp_seen": ["a", "q"], "opp_species": ["a", "b", "c", "d", "e", "f"], "our_species": list("uvwxyz")})
    assert not OV.consistent({"opp_full": False, "opp_seen": [], "opp_species": [], "our_species": list("uvwxyz")})
    rows = [{"precision": {"rule": 1.0, "matchup": 0.5}}, {"precision": {"rule": 0.5, "matchup": None}}]
    sm = OV.summarize(rows, ["rule", "matchup"])
    assert sm["rule"] == {"n": 2, "mean": 0.75, "median": 0.75} and sm["matchup"]["n"] == 1
    print("test_opponent_pick_validity_helpers OK")


def test_opponent_pilot_validity():
    import sys
    import types
    from pathlib import Path
    from tools.team_build import racing as R
    from tools.team_build.experiments import opponent_pilot_validity as OP
    rows = OP.gap_rows({"ref_heuristic_rule": [1, 1, 1, 0], "ref_rl_rule": [1, 0, 1, 0], "ref_rl_prior": []}, 0.4)
    assert [r["arm"] for r in rows] == ["ref_rl_rule", "ref_heuristic_rule", "ref_rl_prior"]
    assert rows[0] == {"arm": "ref_rl_rule", "n": 4, "win_rate": 0.5, "gap_to_real": 0.1}
    assert rows[2] == {"arm": "ref_rl_prior", "n": 0, "win_rate": None, "gap_to_real": None}
    rows = OP.gap_rows({"a": [1, 0], "b": [1, 1]}, None)       # 実戦の勝率が無ければ差は出さない (入力の順のまま)
    assert [r["arm"] for r in rows] == ["a", "b"] and all(r["gap_to_real"] is None for r in rows)

    # main の通し (対戦だけ差し替え): 測定のあとの集計と保存まで進む
    # (2026-10-05: 集計の行を消して rows が未定義になり、約 1 時間の測定が終わってから落ちる形になっていた)
    def fake_round(arms, n, *_args, **_kwargs):
        for i, a in enumerate(arms):
            a.outcomes.extend([1] * (n - i) + [0] * i)
    saved: dict = {}
    old = (OP.R, OP.write_result, sys.argv)
    OP.R = types.SimpleNamespace(Arm=R.Arm, measure_round=fake_round)
    OP.write_result = lambda name, doc: saved.update({name: doc}) or Path(name)
    sys.argv = ["opponent_pilot_validity", "--run-id", "nosuch_run", "--n", "4", "--combos", "heuristic:rule,rl:rule",
                "--real-win-rate", "0.75"]
    try:
        OP.main()
    finally:
        OP.R, OP.write_result, sys.argv = old
    res = saved["opponent_pilot_validity"]
    assert res["closest"] == "ref_rl_rule" and res["real_win_rate"] == 0.75 and res["real_n"] is None
    assert [(r["arm"], r["win_rate"], r["gap_to_real"]) for r in res["rows"]] == [("ref_rl_rule", 0.75, 0.0), ("ref_heuristic_rule", 1.0, 0.25)]
    print("test_opponent_pilot_validity OK")


def main() -> None:
    test_calibration()
    test_selection_compliance()
    test_opponent_pick_validity_helpers()
    test_opponent_pilot_validity()
    test_learned_surrogate()
    test_concept_origin()
    test_env_validity()
    test_llm_audit()
    print("ALL OK")


if __name__ == "__main__":
    main()
