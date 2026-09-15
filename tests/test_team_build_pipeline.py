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
    print("test_import_lineups OK")


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
    print("test_variant_arm_production OK")


if __name__ == "__main__":
    test_variant_arm_production()
    test_import_lineups()
    test_repro_gate()
    test_select_survivors_orders_and_caps()
    test_choose_variants_picks_best_non_degraded()
    test_choose_variants_team_x_pickvariant_and_survivors()
    test_checkpoint_selection_helpers()
    test_resolve_candidate_subset()
    test_screen_margin_analysis()
    test_collect_run_timeout_returns_code()
    test_surrogate_quality_metrics()
