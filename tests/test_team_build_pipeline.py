"""測定段パイプラインの純粋関数 (screening 生存の選抜、選出方策 variant の選択) のテスト。

    python -m tests.test_team_build_pipeline
"""
from __future__ import annotations

from tools.team_build import pipeline as P


def _arm(arm_id, mean, state="uncertain", eliminated_at=None, model=None):
    return {"arm_id": arm_id, "state": state, "eliminated_at": eliminated_at, "selection_model": model,
            "result": {"mean": mean} if mean is not None else None}


def test_select_survivors_orders_and_caps():
    res = {"arms": [_arm("A", -0.16, "degraded"), _arm("B", -0.09), _arm("C", -0.04),
                    _arm("D", 0.01, "equivalent"), _arm("E", -0.03, eliminated_at=100), _arm("F", None)]}
    assert P.select_survivors(res, None) == ["D", "C", "B", "F"]        # Δ 降順、degraded / 脱落は除外、None は末尾
    assert P.select_survivors(res, 2) == ["D", "C"]
    assert P.select_survivors({"arms": []}, 3) == []
    print("test_select_survivors_orders_and_caps OK")


def test_choose_variants_picks_best_non_degraded():
    res = {"arms": [
        _arm(P.variant_arm_id("L00", "fresh"), -0.18, "degraded", model="f0"),
        _arm(P.variant_arm_id("L00", "generic"), -0.12, "uncertain", model="g"),
        _arm(P.variant_arm_id("L03", "fresh"), 0.01, "uncertain", model="f3"),
        _arm(P.variant_arm_id("L03", "generic"), -0.27, "degraded", model="g"),
        _arm(P.variant_arm_id("L05", "fresh"), -0.20, "degraded", model="f5"),
        _arm(P.variant_arm_id("L05", "generic"), -0.19, "degraded", model="g"),
    ]}
    chosen = P.choose_variants(res)
    assert set(chosen) == {"L00", "L03"}, chosen                       # L05 は両 variant とも degraded
    assert chosen["L00"]["variant"] == "generic" and chosen["L00"]["selection_model"] == "g"
    assert chosen["L03"]["variant"] == "fresh" and chosen["L03"]["selection_model"] == "f3"
    assert chosen["L03"]["delta"] == 0.01
    assert P.split_variant("L03@fresh") == ("L03", "fresh")
    assert P.split_variant("L03") == ("L03", "fresh")
    assert P.split_variant("L03_C027@generic") == ("L03_C027", "generic")
    print("test_choose_variants_picks_best_non_degraded OK")


def test_choose_variants_team_x_pickvariant_and_survivors():
    res = {"arms": [
        {"arm_id": "L00@teampreview", "state": "degraded", "eliminated_at": None, "pick_policy": "teampreview",
         "selection_model": None, "n_done": 100, "result": {"mean": -0.16, "se": 0.05}},
        {"arm_id": "L00@generic", "state": "uncertain", "eliminated_at": None, "pick_policy": "advisor",
         "selection_model": "g", "n_done": 300, "result": {"mean": -0.08, "se": 0.03}},
        {"arm_id": "L00@cheap", "state": "degraded", "eliminated_at": 100, "pick_policy": "advisor",
         "selection_model": "c0", "n_done": 100, "result": {"mean": -0.20, "se": 0.05}},
        {"arm_id": "L03@teampreview", "state": "uncertain", "eliminated_at": None, "pick_policy": "teampreview",
         "selection_model": None, "n_done": 300, "result": {"mean": -0.05, "se": 0.03}},
        {"arm_id": "L03@cheap", "state": "equivalent", "eliminated_at": None, "pick_policy": "advisor",
         "selection_model": "c3", "n_done": 300, "result": {"mean": +0.01, "se": 0.03}},
        {"arm_id": "L09@teampreview", "state": "degraded", "eliminated_at": None, "pick_policy": "teampreview",
         "selection_model": None, "n_done": 100, "result": {"mean": -0.30, "se": 0.05}},
    ]}
    chosen = P.choose_variants(res)
    assert set(chosen) == {"L00", "L03"}, chosen                       # L09 は全 variant が degraded
    assert chosen["L00"]["variant"] == "generic" and chosen["L00"]["pick_policy"] == "advisor"
    assert chosen["L03"]["variant"] == "cheap" and chosen["L03"]["selection_model"] == "c3"
    assert P.team_survivors(chosen, None) == ["L03", "L00"] and P.team_survivors(chosen, 1) == ["L03"]
    assert P.best_by_win_rate({"teampreview": 0.85, "generic": 0.82, "cheap": 0.85}, ("teampreview", "generic", "cheap")) == "teampreview"
    assert P.best_by_win_rate({"teampreview": None, "cheap": 0.7}, ("teampreview", "generic", "cheap")) == "cheap"
    assert P.best_by_win_rate({}, ("teampreview",)) is None
    # S7b: 参照の fresh 変種 (S7 と同じ適応) が加わると、それが最善なら参照の variant になる (rule_0913 の対照実験の値)
    assert P.best_by_win_rate({"teampreview": 0.757, "generic": 0.713, "cheap": 0.72, "fresh": 0.83},
                              ("teampreview", "generic", "cheap", "fresh")) == "fresh"
    print("test_choose_variants_team_x_pickvariant_and_survivors OK")


def test_checkpoint_selection_helpers():
    from tools.team_build import adapt as AD
    ck = {5000: "a", 6000: "b", 7000: "c", 8000: "d", 9000: "e", 10000: "f", 11000: "g"}
    assert AD.pick_checkpoints_evenly(ck, 4) == [5000, 7000, 9000, 11000]
    assert AD.pick_checkpoints_evenly(ck, 2) == [5000, 11000]
    assert AD.pick_checkpoints_evenly(ck, 1) == [11000]
    assert AD.pick_checkpoints_evenly({5000: "a"}, 4) == [5000] and AD.pick_checkpoints_evenly({}, 4) == []
    assert AD.choose_best_checkpoint({5000: {"win_rate": 0.70}, 9000: {"win_rate": 0.64}, 11000: {"win_rate": 0.70}}) == 11000
    assert AD.choose_best_checkpoint({5000: {"win_rate": None}}) is None and AD.choose_best_checkpoint({}) is None
    hist = [{"n_battles": 5000, "checkpoint": "p5"}, {"n_battles": 6000}, {"n_battles": 7000, "checkpoint": "p7"}]
    assert AD.checkpoints_from_history(hist) == {5000: "p5", 7000: "p7"}
    print("test_checkpoint_selection_helpers OK")


def test_resolve_candidate_subset():
    from tools.team_build.run import resolve_candidate_subset
    rows = [{"candidate_id": "L00_INC", "ok": True, "tag": "incumbent", "score": 1.0},
            {"candidate_id": "L01_INC", "ok": True, "tag": "incumbent_mut", "score": 1.1},
            {"candidate_id": "L02_INC", "ok": True, "tag": "incumbent_mut", "score": 1.05},
            {"candidate_id": "L03_C001", "ok": True, "tag": "best", "score": 1.3},
            {"candidate_id": "L04_C002", "ok": False, "tag": "best", "score": 1.25},
            {"candidate_id": "L05_C003", "ok": True, "tag": "coverage", "score": 1.2},
            {"candidate_id": "L06_C004", "ok": True, "tag": "fill", "score": 0.9}]
    assert resolve_candidate_subset(rows, None, None, False, 2) is None
    assert resolve_candidate_subset(rows, "L05_C003,L03_C001", None, False, 2) == ["L05_C003", "L03_C001"]
    # strata は探索候補 (現行枝を除く、合法のみ) のスコア順: 1=L03, 2=L05, 3=L06。範囲外は無視
    assert resolve_candidate_subset(rows, None, "1,3,9", False, 2) == ["L03_C001", "L06_C004"]
    assert resolve_candidate_subset(rows, None, "2", True, 1) == ["L05_C003", "L00_INC", "L01_INC"]
    assert resolve_candidate_subset(rows, "L03_C001", "1", False, 0) == ["L03_C001"]      # 重複は除く
    # 修理モードの変種 (tag repair) は strata の順位に入れない (resume で測定対象が変わらない)
    rows2 = rows + [{"candidate_id": "L01_INC-R1A1", "ok": True, "tag": "repair", "score": 1.4}]
    assert resolve_candidate_subset(rows2, None, "1,3,9", False, 2) == ["L03_C001", "L06_C004"]
    print("test_resolve_candidate_subset OK")


def test_screen_margin_analysis():
    from tools.team_build.screen_margin import analyze_margin, to_markdown
    def arm(aid, mean, se, state="uncertain", n=300):
        return {"arm_id": aid, "state": state, "eliminated_at": None, "n_done": n,
                "result": {"mean": mean, "se": se, "ci_low": mean - 1.96 * se, "ci_high": mean + 1.96 * se}}
    res8a = {"arms": [arm("A@teampreview", -0.16, 0.03, "degraded"), arm("A@cheap", -0.14, 0.03, "degraded"),
                      arm("B@teampreview", -0.09, 0.03, "degraded"), arm("B@cheap", -0.07, 0.03),
                      arm("C@cheap", -0.04, 0.03)]}
    res8b = {"arms": [arm("A@fresh", -0.18, 0.03, "degraded"), arm("A@generic", -0.12, 0.03, "degraded"),
                      arm("B@fresh", -0.02, 0.03), arm("C@fresh", +0.01, 0.03), arm("C@teampreview", -0.05, 0.03)]}
    m = analyze_margin(res8a, res8b)
    by = {r["team"]: r for r in m["teams"]}
    assert m["n_teams"] == 3 and by["A"]["full_degraded"] and not by["B"]["full_degraded"]
    assert abs(by["B"]["uplift"] - 0.05) < 1e-9 and by["B"]["variant_cheap"] == "cheap" and by["C"]["variant_full"] == "fresh"
    # margin 0 では B (cheap 段 CI 上端 -0.011 > -0.02 なので残る) は誤脱落にならず、A は full でも劣るので誤脱落ではない
    t0 = next(r for r in m["margin_table"] if r["margin"] == 0.0)
    assert t0["false_drop"] == [] and t0["kept_but_degraded"] == []
    assert m["order_cheap"] == ["C", "B", "A"] and m["order_full"] == ["C", "B", "A"] and m["reversals"] == []
    assert m["suggested_margin_q90"] is not None
    md = to_markdown(m)
    assert "margin の目安" in md and "順位反転なし" in md
    print("test_screen_margin_analysis OK")


def test_collect_run_timeout_returns_code():
    """収集サブプロセスの無応答は例外にせず RC_TIMEOUT を返す (run 全体を落とさない)"""
    import sys
    import tempfile
    from pathlib import Path
    from tools.team_build import adapt as AD
    with tempfile.TemporaryDirectory() as d:
        rc = AD._run([sys.executable, "-c", "import time; time.sleep(5)"], Path(d) / "x.log", timeout=1)
        assert rc == AD.RC_TIMEOUT, rc
        assert "timeout" in (Path(d) / "x.log").read_text(encoding="utf-8")
        rc_ok = AD._run([sys.executable, "-c", "print('ok')"], Path(d) / "y.log", timeout=30)
        assert rc_ok == 0
    print("test_collect_run_timeout_returns_code OK")


def test_surrogate_quality_metrics():
    from tools.team_build.review_run import spearman, surrogate_quality
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == 1.0
    assert spearman([1, 2, 3, 4], [40, 30, 20, 10]) == -1.0
    assert spearman([1, 2], [1, 2]) is None
    sets = [{"candidate_id": c, "ok": True, "score": s} for c, s in
            (("A", 1.3), ("B", 1.2), ("C", 1.1), ("D", 1.0), ("E", 0.9))] + [{"candidate_id": "X", "ok": False, "score": 2.0}]
    # 代理順位 A > B > C > D > E、実測は D > C > B > A (E は未測定)
    res = {"arms": [_arm("A", -0.26), _arm("B", -0.26), _arm("C", -0.12), _arm("D", -0.04), _arm("X", 0.5)]}
    q = surrogate_quality(sets, res, k=2)
    assert q["n"] == 4 and q["k"] == 2
    assert q["spearman"] is not None and q["spearman"] < 0, q
    assert q["precision_at_k"] == 0.0 and q["best_surrogate_rank"] == 4, q
    assert abs(q["regret_at_k"] - 0.22) < 1e-9, q          # -0.04 − (−0.26)
    assert surrogate_quality(sets, {"arms": []}) == {"n": 0}
    print("test_surrogate_quality_metrics OK")


def test_repro_gate():
    """再現性の門: S8b と S10 の両分割で Δ ≥ 0 のときだけ holdout に進める。欠損は不可"""
    assert P.repro_gate_ok(0.093, 0.078) and P.repro_gate_ok(0.0, 0.0)
    assert not P.repro_gate_ok(0.150, -0.043)          # rule_0909 の L21: S8b improved → S10 degraded
    assert not P.repro_gate_ok(-0.01, 0.05) and not P.repro_gate_ok(None, 0.05) and not P.repro_gate_ok(0.05, None)
    print("test_repro_gate OK")


def test_repair_race_reusable():
    """修理モードの racing の再利用 (resume): 今回の腕を全部含み、各腕が確定か上限まで測っているときだけ"""
    res = {"max_battles": 300, "arms": [
        {"arm_id": "L01-R1A1@cheap", "state": "improved", "n_done": 300, "eliminated_at": None},
        {"arm_id": "L01-R1A1@generic", "state": "degraded", "n_done": 100, "eliminated_at": 100},
        {"arm_id": "L01-R1A2@cheap", "state": "uncertain", "n_done": 300, "eliminated_at": None},
        {"arm_id": "L01-R1A3@cheap", "state": "uncertain", "n_done": 100, "eliminated_at": None}]}
    assert P.repair_race_reusable(res, ["L01-R1A1@cheap", "L01-R1A1@generic", "L01-R1A2@cheap"])
    assert not P.repair_race_reusable(res, ["L01-R1A1@cheap", "L01-R1A3@cheap"])      # 測り切っていない腕
    assert not P.repair_race_reusable(res, ["L01-R1A1@cheap", "L01-R1B1@cheap"])      # 無い腕
    assert not P.repair_race_reusable({}, ["x"]) and not P.repair_race_reusable(res, [])
    print("test_repair_race_reusable OK")


def test_import_lineups():
    """--extra-lineups: 別の run の並びを s06_sets に写し、この run のレギュレーションで検査して候補に加える"""
    import json
    import tempfile
    from pathlib import Path
    from tools.team_build.run import import_lineups, imported_candidate_id, parse_extra_lineups
    assert parse_extra_lineups("rule_0910:L26_C003, rule_0909:L07_C026") == [("rule_0910", "L26_C003"), ("rule_0909", "L07_C026")]
    assert parse_extra_lineups(None) == [] and parse_extra_lineups(" ") == []
    assert imported_candidate_id("rule_0910", "L26_C003") == "L26_C003_from_rule_0910"
    try:
        parse_extra_lineups("L26_C003")
        assert False, "run_id 無しは受け付けない"
    except SystemExit:
        pass
    with tempfile.TemporaryDirectory() as d:
        runs = Path(d)
        src = runs / "rule_0910"
        (src / "s06_sets").mkdir(parents=True)
        (src / "s06_sets" / "L26_C003.txt").write_text("delphox @ delphoxite\nLevel: 50\n- psychic\n")
        (src / "s06_sets.json").write_text(json.dumps([{"candidate_id": "L26_C003", "members": ["delphox"], "ok": True,
                                                        "sets": [{"species": "delphox"}], "score": 1.2, "tag": "concept"}]))
        dst = runs / "new_run"
        (dst / "s06_sets").mkdir(parents=True)
        rows = [{"candidate_id": "L00_C001", "members": ["a"], "ok": True}]
        logs = []
        ids = import_lineups(dst, rows, [("rule_0910", "L26_C003")], "gen9championsbssregmc",
                             validate=lambda text: (True, []), runs_dir=runs, log=logs.append)
        assert ids == ["L26_C003_from_rule_0910"] and len(rows) == 2
        row = rows[1]
        assert row["candidate_id"] == "L26_C003_from_rule_0910" and row["ok"] and row["tag"] == "imported"
        assert row["members"] == ["delphox"] and row["score"] == 1.2 and row["imported_from"] == {"run_id": "rule_0910", "candidate_id": "L26_C003"}
        assert (dst / "s06_sets" / "L26_C003_from_rule_0910.txt").read_text().startswith("delphox @ delphoxite")
        assert logs and "合法" in logs[0]
        # 2 回目は再利用 (行を増やさない)。不合法なら ok=False で errors を残す (候補には入らない)
        assert import_lineups(dst, rows, [("rule_0910", "L26_C003")], "x", validate=lambda t: (True, []), runs_dir=runs,
                              log=logs.append) == ["L26_C003_from_rule_0910"] and len(rows) == 2
        rows2 = []
        import_lineups(dst / "other", rows2, [("rule_0910", "L26_C003")], "x", validate=lambda t: (False, ["bad item"]),
                       runs_dir=runs, log=logs.append)
        assert rows2[0]["ok"] is False and rows2[0]["errors"] == ["bad item"]
        try:
            import_lineups(dst, rows, [("rule_0910", "L99_C999")], "x", validate=lambda t: (True, []), runs_dir=runs, log=logs.append)
            assert False, "無い並びは止まる"
        except SystemExit:
            pass
        # 使わないポケモン (config/banned_species.txt) を含む並びは持ち込めない (2026-09-25)
        try:
            import_lineups(dst / "b", [], [("rule_0910", "L26_C003")], "x", validate=lambda t: (True, []), runs_dir=runs,
                           log=logs.append, banned={"delphox"})
            assert False, "使わないポケモンを含む並びは止まる"
        except SystemExit as e:
            assert "delphox" in str(e) and "使わない" in str(e), e
    print("test_import_lineups OK")


def test_identical_reference_and_repair_parents():
    """参照と同一の候補は腕にしない、修理の親 (同一 → 参照の記録 / 生存の上位 / 探索の並びの Δ 上位)、本番モデルの弱さ (純粋)"""
    import tempfile
    from pathlib import Path
    from tools.team_build import pipeline as PL
    from tools.team_build import racing as R
    a = "Dragonite @ Choice Scarf\nAbility: Multiscale\nLevel: 50\nAdamant Nature\nEVs: 4 HP / 252 Atk / 252 Spe\n- Outrage\n- Earthquake\n\n" \
        "Tinkaton @ Leftovers\nAbility: Mold Breaker\nLevel: 50\nCareful Nature\nEVs: 252 HP / 4 Atk / 252 SpD\n- Gigaton Hammer\n- Stealth Rock\n"
    b = "Tinkaton @ Leftovers\nAbility: Mold Breaker\nLevel: 50\nCareful Nature\nEVs: 252 HP / 4 Atk / 252 SpD\n- Stealth Rock\n- Gigaton Hammer\n\n" \
        "Dragonite @ Choice Scarf\nAbility: Multiscale\nLevel: 50\nAdamant Nature\nEVs: 4 HP / 252 Atk / 252 Spe\n- Earthquake\n- Outrage\n"
    c = a.replace("Choice Scarf", "Lum Berry")
    assert PL.same_team(a, a) and not PL.same_team(a, c) and not PL.same_team("", "") and not PL.same_team(a, "")
    with tempfile.TemporaryDirectory() as td:
        pa, pc = Path(td) / "a.txt", Path(td) / "c.txt"
        pa.write_text(a, encoding="utf-8")
        pc.write_text(c, encoding="utf-8")
        arms = [R.Arm("L00_INC", pa), R.Arm("L01_C001", pc), R.Arm("L02_X", Path(td) / "missing.txt")]
        keep, same = PL.split_identical(arms, a)
        assert [x.arm_id for x in same] == ["L00_INC"] and [x.arm_id for x in keep] == ["L01_C001", "L02_X"]
    # 修理の親
    res = {"arms": [
        {"arm_id": "L00_INC@teampreview", "result": {"mean": 0.10}}, {"arm_id": "L00_INC@cheap", "result": {"mean": 0.14}},
        {"arm_id": "L01_INC@cheap", "result": {"mean": 0.01}},
        {"arm_id": "L05_C005@teampreview", "result": {"mean": -0.20}, "eliminated_at": 300},
        {"arm_id": "L05_C005@cheap", "result": {"mean": -0.08}, "eliminated_at": 300},
        {"arm_id": "L76_C066@cheap", "result": {"mean": -0.15}, "eliminated_at": 300},
        {"arm_id": "L03_C001-R1A1@cheap", "result": {"mean": -0.05}, "eliminated_at": 300},
        {"arm_id": "L09_old@cheap", "result": {"mean": 0.3}}]}
    rows_by = {"L00_INC": {"roles": {"x": "breaker"}, "tag": "incumbent"}, "L01_INC": {"roles": {"x": "breaker"}, "tag": "incumbent_mut"},
               "L05_C005": {"roles": {"x": "breaker"}, "tag": "concept"}, "L76_C066": {"roles": {"x": "breaker"}, "tag": "best"},
               "L03_C001-R1A1": {"roles": {"x": "breaker"}, "tag": "repair"}, "L09_old": {"tag": "concept"}}
    chosen = {"L01_INC": {"arm_id": "L01_INC@cheap"}}
    parents = PL.repair_parents(res, ["L01_INC"], rows_by, identical=["L00_INC"], n_main=2, n_explore=1, chosen=chosen)
    assert parents == [("L00_INC", ["reference"]), ("L01_INC", ["L01_INC@cheap"]), ("L05_C005", ["L05_C005@teampreview", "L05_C005@cheap"])], parents
    # 探索の並びは脱落していても Δ 上位 (L05 −0.08 > L76 −0.15)、修理の変種と役割の無い行は親にしない、variant を束ねない設定なら選んだ腕だけ
    parents2 = PL.repair_parents(res, ["L01_INC"], rows_by, identical=[], n_main=1, n_explore=2, pool_variants=False, chosen=chosen)
    assert parents2 == [("L01_INC", ["L01_INC@cheap"]), ("L05_C005", ["L05_C005@teampreview"]), ("L76_C066", ["L76_C066@cheap"])], parents2
    assert PL.best_delta_by_team(res)["L05_C005"] == -0.08 and PL.arms_of_team(res, "L00_INC") == ["L00_INC@teampreview", "L00_INC@cheap"]
    # 本番の選出モデルが現行チームで弱い
    gap = PL.reference_production_gap({"teampreview": 0.6, "production": 0.417, "fresh": 0.73}, threshold=0.1)
    assert gap and abs(gap["gap"] - 0.313) < 1e-6
    assert PL.reference_production_gap({"production": 0.7, "fresh": 0.73}, threshold=0.1) is None
    assert PL.reference_production_gap({"fresh": 0.73}, threshold=0.1) is None
    # 系統ごとの較正
    from tools.team_build.review_run import family_calibration
    recs = [{"opponent_family_id": "F1", "won": i < 8} for i in range(10)] + [{"opponent_family_id": "F2", "won": i < 2} for i in range(10)] \
        + [{"opponent_family_id": "F3", "won": i < 5} for i in range(10)] + [{"opponent_family_id": "F4", "won": True} for _ in range(2)]
    cal = family_calibration({"F1": 0.9, "F2": 0.3, "F3": 0.6, "F4": 0.1}, recs, min_n=5)
    assert cal["n_families"] == 3 and cal["spearman"] == 1.0 and cal["rows"][0]["family"] in ("F1", "F2", "F3")
    assert family_calibration({}, recs)["spearman"] is None
    print("test_identical_reference_and_repair_parents OK")


def test_plan_prior_variants_and_promotion_key():
    """plan prior は既定 off: fresh の腕に計画を付けない。ab では fresh_plan の腕だけが計画を持ち、対応差を plan_ab_pairs で出す。
    昇格の PASS 回数は candidate_id でなく 6 体の種で数える (判断 #24)"""
    from pathlib import Path
    from tools.team_build import pipeline as PL
    from tools.team_build import racing as R
    from tools.team_build.promote import pass_runs_for_team, team_key
    base = R.Arm("L01_C001", Path("/tmp/x.txt"), None, "models", plan_file="/tmp/x.plan.json")
    models = {"L01_C001": "/tmp/m.pt"}
    off = PL._variant_arm(base, "fresh", models, None, plan_prior="off")
    assert off is not None and off.plan_file is None and off.selection_model == "/tmp/m.pt"
    on = PL._variant_arm(base, "fresh", models, None, plan_prior="on")
    assert on.plan_file == "/tmp/x.plan.json"
    assert PL._variant_arm(base, "fresh_plan", models, None, plan_prior="off") is None
    ab = PL._variant_arm(base, "fresh_plan", models, None, plan_prior="ab")
    assert ab is not None and ab.arm_id == "L01_C001@fresh_plan" and ab.plan_file == "/tmp/x.plan.json"
    assert PL._variant_arm(base, "fresh", models, None, plan_prior="ab").plan_file is None
    assert PL._variant_arm(base, "teampreview", models, None, plan_prior="on").plan_file is None
    rule = PL._variant_arm(base, "rule", models, None)
    assert rule is not None and rule.pick_policy == "rule" and rule.selection_model is None and rule.plan_file is None
    ch = PL.choose_variants({"arms": [{"arm_id": "L01_C001@rule", "result": {"mean": 0.05}}]})
    assert ch["L01_C001"]["pick_policy"] == "rule" and ch["L01_C001"]["variant"] == "rule"
    res = {"arms": [{"arm_id": "L01_C001@fresh", "result": {"mean": 0.10}}, {"arm_id": "L01_C001@fresh_plan", "result": {"mean": 0.14}},
                    {"arm_id": "L02_C002@fresh", "result": {"mean": 0.0}}, {"arm_id": "L01_C001@teampreview", "result": {"mean": -0.1}}]}
    pairs = PL.plan_ab_pairs(res)
    assert pairs == {"L01_C001": {"delta_fresh": 0.10, "delta_fresh_plan": 0.14, "diff": 0.04}}
    chosen = PL.choose_variants(res)
    assert chosen["L01_C001"]["variant"] == "fresh_plan" and "plan_file" in chosen["L01_C001"]
    # 昇格: 同じ 6 体 (candidate_id が違っても) の PASS を数える
    pk = [{"run_id": "r1", "meta": {"candidate_id": "L00_INC", "species": ["a", "b", "c", "d", "e", "f"], "holdout": {"verdict": "PASS"}}},
          {"run_id": "r2", "meta": {"candidate_id": "L03_C009", "species": ["f", "e", "d", "c", "b", "a"], "holdout": {"verdict": "PASS_EQUIVALENT"}}},
          {"run_id": "r3", "meta": {"candidate_id": "L00_INC", "species": ["a", "b", "c", "d", "e", "g"], "holdout": {"verdict": "PASS"}}},
          {"run_id": "r4", "meta": {"candidate_id": "L00_INC", "species": ["a", "b", "c", "d", "e", "f"], "holdout": {"verdict": "INCONCLUSIVE"}}},
          {"run_id": "r5", "meta": {"candidate_id": "L00_INC", "holdout": {"verdict": "PASS"}}}]
    assert pass_runs_for_team(pk, pk[0]["meta"]) == {"r1", "r2"}
    assert pass_runs_for_team(pk, {"candidate_id": "L00_INC"}) == {"r1", "r3", "r5"}      # species の無い古い Package は id で
    assert team_key({"species": ["b", "a"]}) == ("a", "b")
    print("test_plan_prior_variants_and_promotion_key OK")


def test_variant_arm_production():
    """参照の variant「production」(配布版の選出モデルを強制) は path があるときだけ作られ、advisor 方策になる"""
    from pathlib import Path
    from tools.team_build import racing as R
    base = R.Arm("reference", Path("/r.txt"), None, "/pins")
    arm = P._variant_arm(base, "production", {}, None, production_path="/deploy/selection_model.pt")
    assert arm is not None and arm.arm_id == "reference@production"
    assert arm.selection_model == "/deploy/selection_model.pt" and arm.pick_policy == "advisor" and arm.models_dir == "/pins"
    assert P._variant_arm(base, "production", {}, None) is None                     # 配布版が無ければ variant も無い
    assert P._variant_arm(base, "teampreview", {}, None).pick_policy == "teampreview"  # 既存 variant は不変
    assert P.best_by_win_rate({"teampreview": 0.757, "cheap": 0.72, "production": 0.848, "fresh": 0.83},
                              ("teampreview", "generic", "cheap", "production", "fresh")) == "production"
    # 選出計画は plan_prior=on のときだけ advisor 方策の variant に引き継ぐ (既定 off: 2026-10-05 判断 #25。teampreview は相性順だけ
    # なので渡さない)。計画の無い腕 (参照) は None のまま
    cand = R.Arm("L05_C005", Path("/c.txt"), None, "/pins", plan_file="/runs/x/s06_sets/L05_C005.plan.json")
    assert P._variant_arm(cand, "cheap", {"L05_C005": "/m.pt"}, None, plan_prior="on").plan_file == cand.plan_file
    assert P._variant_arm(cand, "generic", {}, "/g.pt", plan_prior="on").plan_file == cand.plan_file
    assert P._variant_arm(cand, "fresh", {"L05_C005": "/f.pt"}, None, plan_prior="on").plan_file == cand.plan_file
    assert P._variant_arm(cand, "cheap", {"L05_C005": "/m.pt"}, None).plan_file is None          # 既定 off
    assert P._variant_arm(cand, "teampreview", {}, None, plan_prior="on").plan_file is None
    assert P._variant_arm(base, "production", {}, None, production_path="/p.pt", plan_prior="on").plan_file is None
    print("test_variant_arm_production OK")


if __name__ == "__main__":
    test_variant_arm_production()
    test_import_lineups()
    test_repro_gate()
    test_repair_race_reusable()
    test_select_survivors_orders_and_caps()
    test_choose_variants_picks_best_non_degraded()
    test_choose_variants_team_x_pickvariant_and_survivors()
    test_checkpoint_selection_helpers()
    test_resolve_candidate_subset()
    test_screen_margin_analysis()
    test_collect_run_timeout_returns_code()
    test_surrogate_quality_metrics()
    test_identical_reference_and_repair_parents()
    test_plan_prior_variants_and_promotion_key()
