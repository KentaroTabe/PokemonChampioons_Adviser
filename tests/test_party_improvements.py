"""自パーティ改善案 (tools/party_improvements) の純粋部分のテスト。

    python -m tests.test_party_improvements
"""
from __future__ import annotations

from tools import party_improvements as PI


def test_parse_showdown_sets():
    text = ("Metagross @ metagrossite\nLevel: 50\nAbility: clearbody\nEVs: 2 HP / 32 Atk / 32 Spe\nAdamant Nature\n"
            "- bulletpunch\n- psychicfangs\n\nRotom-Wash @ choicescarf\nLevel: 50\nAbility: levitate\nModest Nature\n- hydropump\n")
    sets = PI.parse_showdown_sets(text)
    assert set(sets) == {"metagross", "rotomwash"}, sets
    m = sets["metagross"]
    assert m["item"] == "metagrossite" and m["ability"] == "clearbody" and m["nature"] == "adamant"
    assert m["evs"] == {"hp": 2, "atk": 32, "spe": 32} and m["moves"] == ["bulletpunch", "psychicfangs"]
    assert sets["rotomwash"]["evs"] == {} and sets["rotomwash"]["moves"] == ["hydropump"]
    print("test_parse_showdown_sets OK")


def test_pressure_and_difficulty():
    dec = [{"opp": "A", "best_kind": "move", "best_score": 120.0}, {"opp": "A", "best_kind": "switch", "best_score": 80.0},
           {"opp": "A", "best_kind": "move", "best_score": 30.0}, {"opp": "B", "best_kind": "move", "best_score": 150.0}]
    pm = PI.pressure_metrics(dec, low_score=60.0, min_n=2)
    assert pm["overall"]["n"] == 4 and pm["overall"]["pressure_share"] == 0.5
    assert "A" in pm["by_opponent"] and "B" not in pm["by_opponent"]          # B は決定数不足
    a = pm["by_opponent"]["A"]
    assert a["switch_share"] == round(1 / 3, 3) and a["low_share"] == round(1 / 3, 3) and a["pressure_share"] == round(2 / 3, 3)
    assert PI.difficulty_weight("loss", 0.5, loss_weight=1.0) == 2.5 and PI.difficulty_weight("win", None) == 1.0
    battles = [{"file": "x", "outcome": "loss", "opp_roster": ["a"], "decisions": dec},
               {"file": "y", "outcome": "win", "opp_roster": ["b"], "decisions": [dec[3]]},
               {"file": "z", "outcome": "win", "opp_roster": [], "decisions": []}]
    hard = PI.rank_hard_parties(battles, top=5)
    assert [h["file"] for h in hard] == ["x", "y"] and hard[0]["worst_opponent"] == "A"
    print("test_pressure_and_difficulty OK")


def test_team_concepts_psychic_terrain():
    members = {
        "armarouge": {"set": {"moves": ["armorcannon", "psychic", "psychicterrain", "destinybond"], "ability": "weakarmor"},
                      "base": {"hp": 85, "atk": 60, "def": 100, "spa": 125, "spd": 80, "spe": 75}},
        "raichu": {"set": {"moves": ["zapcannon", "focusblast", "grassknot", "nastyplot"], "ability": "lightningrod",
                           "item": "raichunitey"},
                   "base": {"hp": 60, "atk": 100, "def": 55, "spa": 160, "spd": 80, "spe": 130}},
        "garchomp": {"set": {"moves": ["earthquake", "stealthrock", "swordsdance", "scaleshot"], "ability": "roughskin"},
                     "base": {"hp": 108, "atk": 130, "def": 95, "spa": 80, "spd": 85, "spe": 102}},
    }
    tags = PI.team_concepts(members, ["move_opponent_psychicterrain"], move_info=None, name=lambda s: s.upper())
    by = {t["tag"]: t for t in tags}
    assert "psychic_terrain_support" in by, tags
    assert set(by["psychic_terrain_support"]["members"]) == {"armarouge", "raichu"}
    assert "ARMAROUGE" in by["psychic_terrain_support"]["evidence"] and "観測あり" in by["psychic_terrain_support"]["evidence"]
    assert "setup_sweep" in by and "hazard_stack" not in by        # 設置役は 1 体
    # 速くて脆い個体がいなければ地形だけでは概念にしない
    tags2 = PI.team_concepts({"armarouge": members["armarouge"], "garchomp": members["garchomp"]})
    assert all(t["tag"] != "psychic_terrain_support" for t in tags2), tags2
    print("test_team_concepts_psychic_terrain OK")


def test_priority_dependence_and_proposals_with_dex():
    """実図鑑で: ふいうち持ちドドゲザンはサイコフィールド下 (先制技なし) で被覆が落ち、案の構造が正しい"""
    my = {"kingambit": {"item": "blackglasses", "ability": "supremeoverlord", "nature": "adamant",
                        "evs": {"hp": 32, "atk": 32, "spe": 2}, "moves": ["suckerpunch", "kowtowcleave", "ironhead", "swordsdance"]},
          "metagross": {"item": "metagrossite", "ability": "clearbody", "nature": "adamant",
                        "evs": {"hp": 2, "atk": 32, "spe": 32}, "moves": ["bulletpunch", "psychicfangs", "earthquake", "icepunch"]}}
    owned = dict(my)
    owned["dragonite"] = {"item": "heavydutyboots", "ability": "multiscale", "nature": "adamant",
                          "evs": {"hp": 2, "atk": 32, "spe": 32}, "moves": ["dragondance", "extremespeed", "earthquake", "icespinner"]}
    opp = {"armarouge": {"item": "focussash", "ability": "weakarmor", "nature": "modest", "evs": {"hp": 2, "spa": 32, "spe": 32},
                         "moves": ["armorcannon", "destinybond", "psychic", "psychicterrain"]},
           "raichu": {"item": "raichunitey", "ability": "lightningrod", "nature": "timid", "evs": {"hp": 2, "spa": 32, "spe": 32},
                      "moves": ["zapcannon", "focusblast", "grassknot", "nastyplot"]}}
    my_views, owned_views, opp_views = PI._views(my), PI._views(owned), PI._views(opp)
    assert set(opp_views) == {"armarouge", "raichu"} and opp_views["raichu"][0].species_id == "raichumegay"   # メガ後で評価
    pd = PI.priority_dependence(my_views, opp_views)
    k = pd["per_member"]["kingambit"]
    assert k["priority_moves"] == ["suckerpunch"] and k["drop"] >= 0.0
    assert 0.0 <= pd["team_coverage_no_priority"] <= pd["team_coverage"] <= 1.0
    pr = PI.counter_proposals(["kingambit", "metagross"], owned_views, opp_views, threat_order=["raichu", "armarouge"], top_threats=1)
    assert list(pr["per_threat"]) == ["raichu"]
    assert pr["per_threat"]["raichu"]["best_owned_outside"][0][1] == "dragonite"
    assert pr["swaps"] and all(s["in"] == "dragonite" for s in pr["swaps"]) and len(pr["swaps"]) == 1
    print("test_priority_dependence_and_proposals_with_dex OK")


def test_measure_command_and_measured_report():
    import json
    import tempfile
    from pathlib import Path
    cmd = PI.measure_command("improve_x", Path("w.json"), 3, "medium", 5, 42)
    s = " ".join(cmd)
    assert s.startswith("bash scripts/team_build_nohup.sh improve_x --stages all --profile medium")
    assert "--only-incumbent" in s and "--incumbent-neighbors-s5 3" in s and "--max-candidates 4" in s
    assert "--threat-weights-file w.json" in s and "--seed 42" in s
    # 測定済みの候補だけを載せ、型つきの本文を日本語で出す
    with tempfile.TemporaryDirectory() as d:
        run = Path(d) / "runs" / "improve_t"
        (run / "evaluation").mkdir(parents=True)
        (run / "s06_sets").mkdir()
        (run / "s06_sets.json").write_text(json.dumps([
            {"candidate_id": "L00_INC", "ok": True, "tag": "incumbent", "members": ["kingambit", "metagross"]},
            {"candidate_id": "L01_INC", "ok": True, "tag": "incumbent_mut", "members": ["dragonite", "metagross"]},
            {"candidate_id": "L02_INC", "ok": True, "tag": "incumbent_mut", "members": ["garchomp", "metagross"]}]),
            encoding="utf-8")
        (run / "s06_sets" / "L01_INC.txt").write_text(
            "Dragonite @ heavydutyboots\nLevel: 50\nAbility: multiscale\nEVs: 2 HP / 32 Atk / 32 Spe\nAdamant Nature\n- dragondance\n- extremespeed\n",
            encoding="utf-8")
        def arm(aid, mean, state, n=300):
            return {"arm_id": aid, "state": state, "eliminated_at": None, "n_done": n,
                    "result": {"mean": mean, "se": 0.03, "ci_low": mean - 0.06, "ci_high": mean + 0.06}}
        (run / "evaluation" / "s08a_screen.json").write_text(json.dumps({"arms": [
            arm("L00_INC@cheap", -0.02, "equivalent"), arm("L01_INC@teampreview", -0.10, "uncertain"),
            arm("L01_INC@cheap", +0.01, "uncertain")]}), encoding="utf-8")
        (run / "evaluation" / "s08b_adapted.json").write_text(json.dumps({"arms": [
            arm("L01_INC@fresh", +0.03, "uncertain"), arm("L01_INC@generic", -0.05, "uncertain")]}), encoding="utf-8")
        (run / "evaluation" / "summary.json").write_text(json.dumps({
            "reference_variant": {"variant": "teampreview"}, "winner": "L01_INC", "winner_variant": "fresh",
            "holdout": {"verdict": "PASS_EQUIVALENT", "delta": 0.01, "ci": [-0.03, 0.05], "n": 600}, "result": "PASS_EQUIVALENT"}),
            encoding="utf-8")
        orig = PI.REPO
        try:
            PI.REPO = Path(d)
            (Path(d) / "logs" / "build_search").mkdir(parents=True)
            (Path(d) / "logs" / "build_search" / "runs").symlink_to(Path(d) / "runs")
            md = PI.measured_report("improve_t")
        finally:
            PI.REPO = orig
    assert "L01_INC" in md and "L00_INC" in md and "L02_INC" not in md          # 未測定の L02 は載せない
    assert "S8b: variant=fresh Δ=+0.030" in md and "S8a: variant=cheap Δ=+0.010" in md
    assert "PASS_EQUIVALENT" in md and "カイリュー" in md and "りゅうのまい" in md and "いじっぱり" in md
    assert md.index("L01_INC") < md.index("L00_INC")                               # Δ の高い順
    print("test_measure_command_and_measured_report OK")


def test_active_measurement_detection():
    """2026-10-07: pgrep に無い option (-m) を渡していて出力が常に空になり、run の実行中に終了処理が 2 本目の run を起動した
    (improve_20261007_0133)。引数が実際の pgrep に通ること、出力の読み方、pgrep が失敗したら起動を見送る側に倒すことを確かめる"""
    import subprocess
    argv = PI.pgrep_argv(PI.MEASUREMENT_PROCESS_PATTERN)
    assert argv[0] == "pgrep" and "-m" not in argv and argv[-1] == "tools.team_build.run", argv
    assert all(a.startswith("-") and set(a[1:]) <= set("fl") for a in argv[1:-1]), argv   # -f と -l 以外の option を渡さない
    res = subprocess.run(argv, capture_output=True, text=True)
    assert res.returncode in (0, 1), (res.returncode, res.stderr)                           # 2 = 使い方の誤り (illegal option)
    out = ("99419 /opt/python -m tools.team_build.run --run-id improve_x --stages all\n"
           "123 bash scripts/team_build_watch.sh improve_x\n"
           "456 /opt/python -m tools.check_advisor_player --battles 300\n")
    assert PI.measurement_lines(out) == [out.splitlines()[0]]
    assert PI.measurement_lines("") == []

    class Res:
        def __init__(self, rc, stdout="", stderr=""):
            self.returncode, self.stdout, self.stderr = rc, stdout, stderr

    assert PI.active_measurement(run=lambda argv: Res(1)) is None                           # 一致なし → 起動してよい
    assert PI.active_measurement(run=lambda argv: Res(0, out)) == out.splitlines()[0]       # run あり → その行
    bad = PI.active_measurement(run=lambda argv: Res(2, "", "pgrep: illegal option -- m"))
    assert bad and "pgrep" in bad and "rc=2" in bad                                         # pgrep が使えない → 見送る側
    print("test_active_measurement_detection OK")


def test_measure_is_confirmation_based_by_default():
    """2026-10-07 判断 1: 終了処理 (--session --measure) は測定 run を起動せず、目的・概算所要時間・起動コマンドを出す。
    config PARTY_IMPROVE_AUTO_LAUNCH=True か --launch で旧動作 (起動)"""
    import champions_agent.config as C
    calls = []
    saved = (PI.build_report, PI.load_battles, PI.propose_measurement, PI.launch_measurement)
    try:
        PI.build_report = lambda battles, hypothetical=None: {"threat_weights": {"a": 1.0}}
        PI.load_battles = lambda **kw: []
        PI.propose_measurement = lambda rep, n, prof, par: calls.append("propose") or {"proposed": True}
        PI.launch_measurement = lambda rep, n, prof, par: calls.append("launch") or {"launched": True}
        assert C.PARTY_IMPROVE_AUTO_LAUNCH is False
        PI.main(["--session", "--measure", "--json", "--no-save"])
        assert calls == ["propose"]
        PI.main(["--session", "--measure", "--launch", "--json", "--no-save"])
        assert calls == ["propose", "launch"]
        C.PARTY_IMPROVE_AUTO_LAUNCH = True
        PI.main(["--session", "--measure", "--json", "--no-save"])
        assert calls == ["propose", "launch", "launch"]
    finally:
        C.PARTY_IMPROVE_AUTO_LAUNCH = False
        PI.build_report, PI.load_battles, PI.propose_measurement, PI.launch_measurement = saved
    print("test_measure_is_confirmation_based_by_default OK")


def test_duration_estimate_and_proposal_text():
    import os
    import tempfile
    from pathlib import Path
    assert PI.duration_estimate([]) is None
    assert PI.duration_estimate([10.0, 2.0, 6.0]) == {"n": 3, "median_h": 6.0, "min_h": 2.0, "max_h": 10.0}
    assert PI.duration_estimate([4.0, 8.0])["median_h"] == 6.0
    with tempfile.TemporaryDirectory() as td:
        for name, start, end, done in (("improve_a", 1000.0, 1000.0 + 3600 * 5, True),
                                       ("improve_b", 2000.0, 2000.0 + 3600 * 11, True),
                                       ("improve_c", 3000.0, 3000.0 + 3600, False),    # 未完了 (summary なし) は使わない
                                       ("arch_x", 0.0, 3600.0, True)):                 # 改善 run 以外は使わない
            d = Path(td) / name
            (d / "evaluation").mkdir(parents=True)
            (d / "request.json").write_text("{}")
            os.utime(d / "request.json", (start, start))
            (d / "run.log").write_text("x")
            os.utime(d / "run.log", (end, end))
            if done:
                (d / "evaluation" / "summary.json").write_text("{}")
                os.utime(d / "evaluation" / "summary.json", (end - 60, end - 60))
        runs = PI.past_run_hours(Path(td), last=10)
        assert runs == [("improve_a", 5.0), ("improve_b", 11.0)], runs
        assert PI.past_run_hours(Path(td), last=1) == [("improve_b", 11.0)]
    cmd = PI.launch_command(Path("logs/build_search/session_threats_x.json"), 3, "medium", 5)
    assert cmd.startswith("python -m tools.party_improvements --launch-weights logs/build_search/session_threats_x.json")
    m = {"proposed": True, "purpose": PI.MEASURE_PURPOSE.format(n=3), "estimate": PI.duration_estimate([5.0, 11.0]),
         "command": cmd, "team_problem": None, "active": "123 python -m tools.team_build.run --run-id y"}
    rep = {"generated_at": "t", "current_ja": [], "n_battles": 0, "parties": [], "structural": {}, "ja": {}, "measure": m}
    md = PI.render(rep)
    assert "自動では起動していません" in md and "概算所要時間: 中央値 約 8.0 時間" in md and cmd in md
    assert "構築 run が実行中" in md and "目的:" in md
    print("test_duration_estimate_and_proposal_text OK")


def test_session_record_in_end_report():
    """終了レポートに fps と隠れたページの比率 (frames 行)、勝敗と根拠、選出候補と実際の選出を出す。古いログは fps を埋めない"""
    new = {"file": "battle_1.jsonl", "outcome": "win", "inferred": True, "corrected": False, "by_rate": False,
           "outcome_basis": {"inferred": True, "basis": "rate", "basis_text": "レート 1500.0 → 1512.0"},
           "frames": {"received": 600, "hidden": 60, "span_sec": 100.0, "recv_fps": 6.0, "proc_fps": 4.0, "hidden_ratio": 0.1},
           "selection": {"recommend": ["A", "B", "C"], "candidates": {"rule": ["A", "D", "E"], "registered": None}},
           "my_picked": ["A", "B", "C"]}
    old = {"file": "battle_0.jsonl", "outcome": "loss", "inferred": False, "frames": None, "selection": None, "my_picked": []}
    sr = PI.session_record([old, new])
    assert sr["n_frames_rows"] == 1 and sr["recv_fps"] == 6.0 and sr["hidden_ratio"] == 0.1
    assert sr["battles"][0]["recv_fps"] is None
    lines = "\n".join(PI.render_session_record(sr))
    assert "受信 6.0 fps" in lines and "隠れたページからの受信 10%" in lines
    assert "推定: レート 1500.0 → 1512.0" in lines and "◎ A/B/C" in lines and "規則 A/D/E" in lines
    assert "実際の自分の選出: A/B/C" in lines and "(読めず)" in lines
    assert PI.session_record([old])["recv_fps"] is None
    assert "frames 行のある対戦なし" in "\n".join(PI.render_session_record(PI.session_record([old])))
    # 選出の候補: candidates 欄が無い古い advice 行は advice の中から作る
    row = {"advice": {"ok": True, "primary": "model", "recommend": [{"name": "A"}], "rule_recommend": [{"name": "D"}],
                      "model_pick": {"names": ["A", "B", "C"]}}}
    ss = PI.selection_summary(row)
    assert ss["candidates"] == {"rule": ["D"], "model_pick": ["A", "B", "C"], "model_pick_real": None}
    # selection_record 行 (advice_id で結ぶ) があればその candidates を使う。計算に失敗した記録 (error) は古いログと同じ扱い
    record = {"type": "selection_record", "advice_id": "x-0001",
              "candidates": {"rule": {"names": ["D", "E", "F"]}, "registered": None, "reasons": {}, "primary": "model"}}
    assert PI.selection_summary(row, record)["candidates"] == {"rule": ["D", "E", "F"], "registered": None}
    assert PI.selection_summary(row, {"candidates": {"error": "x"}})["candidates"] == ss["candidates"]
    import json as _json
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "b.jsonl"
        lines = [{"type": "advice", "kind": "selection", "advice_id": "x-0001", **row},
                 {"type": "selection_record", "advice_id": "x-0000", "candidates": {"rule": {"names": ["Z"]}}},
                 record]
        p.write_text("\n".join(_json.dumps(r, ensure_ascii=False) for r in lines) + "\n", encoding="utf-8")
        assert PI.parse_battle(str(p))["selection"]["candidates"]["rule"] == ["D", "E", "F"]
    assert PI.outcome_basis_of({"outcome": "win", "basis": "fainted", "inferred": True})["basis"] == "fainted"
    assert PI.outcome_basis_of(None) is None
    print("test_session_record_in_end_report OK")


def test_session_record_visibility_and_display():
    """2026-10-07 実機確認: 隠れ % に加えて 可視 % / 不明 % と表示通知の hidden 件数 / 全件、ページの版 (client 行)。
    unknown が 100% なら「可視状態の通知なし (ページの版が古い可能性)」と添える"""
    import json as _json
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "b.jsonl"
        lines = [{"type": "client", "sid": "a", "hello": True, "stale": False},
                 {"type": "display", "advice_id": "x-1", "hidden": True}, {"type": "display", "advice_id": "x-2", "hidden": False},
                 {"type": "display", "advice_id": "x-3"},
                 {"type": "frames", "received": 100, "hidden": 10, "visible": 30, "unknown": 60, "hidden_ratio": 0.25,
                  "span_sec": 20.0, "recv_fps": 5.0, "proc_fps": 4.0}]
        p.write_text("\n".join(_json.dumps(r) for r in lines) + "\n", encoding="utf-8")
        b = PI.parse_battle(str(p))
    assert b["display"] == {"n": 3, "hidden": 1} and b["client_stale"] is False
    assert PI.client_stale_of([]) is None and PI.client_stale_of([{"hello": False, "stale": None}]) is True
    assert PI.client_stale_of([{"stale": False}, {"stale": True}]) is True and PI.client_stale_of([{"stale": None}]) is None
    assert PI.visibility_ratios(b["frames"]) == {"hidden_ratio": 0.25, "visible_ratio": 0.75, "unknown_ratio": 0.6}
    assert PI.visibility_ratios({"received": 40, "hidden": 4, "hidden_ratio": 0.1}) == \
        {"hidden_ratio": 0.1, "visible_ratio": None, "unknown_ratio": None}                     # 古い frames 行
    allu = {"file": "battle_2.jsonl", "outcome": "loss", "client_stale": True, "display": {"n": 43, "hidden": 43},
            "frames": {"received": 50, "hidden": 0, "visible": 0, "unknown": 50, "hidden_ratio": None,
                       "span_sec": 10.0, "recv_fps": 5.0}}
    sr = PI.session_record([dict(b, file="battle_1.jsonl", outcome="win"), allu])
    assert sr["hidden_ratio"] == 0.25 and sr["visible_ratio"] == 0.75 and sr["unknown_ratio"] == round(110 / 150, 3)
    assert sr["n_display"] == 46 and sr["n_display_hidden"] == 44
    assert (sr["n_client_stale"], sr["n_client_ok"], sr["n_client_unknown"]) == (1, 1, 0)
    lines = "\n".join(PI.render_session_record(sr))
    assert "隠れたページからの受信 25%" in lines and "可視 75%" in lines and "不明 73%" in lines
    assert "表示通知の hidden: 44/46 件" in lines and "古い 1 戦 / 一致 1 戦" in lines
    assert "可視状態の通知なし (ページの版が古い可能性)" in lines                          # 2 戦目は全部 unknown
    assert "表示通知の hidden 43/43" in lines and "⚠ ページの版が古い" in lines
    only = PI.session_record([allu])
    assert only["unknown_ratio"] == 1.0 and only["hidden_ratio"] is None and only["visible_ratio"] is None
    text = "\n".join(PI.render_session_record(only))
    assert "⚠ 可視状態の通知なし (ページの版が古い可能性" in text and "隠れたページからの受信 ?%" in text
    print("test_session_record_visibility_and_display OK")


def main():
    test_parse_showdown_sets()
    test_pressure_and_difficulty()
    test_team_concepts_psychic_terrain()
    test_priority_dependence_and_proposals_with_dex()
    test_measure_command_and_measured_report()
    test_active_measurement_detection()
    test_measure_is_confirmation_based_by_default()
    test_duration_estimate_and_proposal_text()
    test_session_record_in_end_report()
    test_session_record_visibility_and_display()


if __name__ == "__main__":
    main()
