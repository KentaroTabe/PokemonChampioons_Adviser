"""アークの判定の再測定 (tools/arc_remeasure.py・tools/arc_remeasure_summary.py) の純粋な部分のテスト。合成データだけで閉じる。

    python -m tests.test_arc_remeasure
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from champions_agent.config import (
    ARC_REMEASURE_FORMATS, ARC_REMEASURE_META_SNAPSHOT, ARC_REMEASURE_SEC_PER_BATTLE, MY_TEAM_DISABLED_ENV)
from tools import arc_remeasure as R
from tools import arc_remeasure_summary as S


def _raises(fn, *a) -> bool:
    try:
        fn(*a)
    except ValueError:
        return True
    return False


def test_select():
    assert R.select("all") == [("p9_300", ("A", "B")), ("p9_600", ("A", "B")), ("p9b_1500", ("A", "B"))]
    assert R.select("baseline") == [("p9_300", ("A",))]
    assert R.select("baseline,p9_300") == [("p9_300", ("A", "B"))]
    assert R.select("p9b_1500, p9_300") == [("p9_300", ("A", "B")), ("p9b_1500", ("A", "B"))]   # 並びは VERDICT_ORDER
    for bad in ("p6a", "p10", "nope", "", " , "):
        assert _raises(R.select, bad), bad
    print("test_select OK")


def test_catalog_consistency():
    for v in R.VERDICT_ORDER:
        e = R.CATALOG[v]
        assert set(e["arms"]) == {"A", "B"}
        assert e["arms"]["A"]["search_blend"] == 0.0 and e["arms"]["B"]["search_blend"] == 40.0
        assert all(arm["old"].endswith(".json") for arm in e["arms"].values())
        assert e["mde"] in (0.05, 0.03) and isinstance(e["final"], bool)
    assert R.CATALOG["p9_300"]["final"] is False and R.CATALOG["p9b_1500"]["mde"] == 0.03
    assert not set(R.EXCLUDED) & set(R.CATALOG)
    print("test_catalog_consistency OK")


def test_jobs_and_command():
    jobs = R.jobs_for(R.select("p9_600"))
    assert [(j["arm"], j["battles"], j["search_blend"]) for j in jobs] == [("A", 600, 0.0), ("B", 600, 40.0)]
    smoke = R.jobs_for(R.select("p9_600"), battles_override=2)
    assert all(j["battles"] == 2 and j["arc_battles"] == 600 for j in smoke)
    out = Path("/tmp/x")
    cmd = R.build_command(jobs[1], out, ARC_REMEASURE_FORMATS["mb"], python="py")
    s = " ".join(cmd)
    assert cmd[:3] == ["py", "-m", "tools.check_advisor_player"]
    for frag in ("--battles 600", "--opp-seed 20260904", "--skip-random", "--belief-k 0", "--sensor-q 0.0",
                 "--workers 1", "--rl-blend 25", "--pick-policy teampreview", "--opp-pilot heuristic",
                 "--opp-pick-policy heuristic", "--search-blend 40", "--battle-format gen9championsbssregmb",
                 "--json /tmp/x/p9_600/B.json", "--battle-log /tmp/x/p9_600/B.battles.jsonl",
                 "--candidate-id arc:p9_600:B"):
        assert frag in s, frag
    assert "--team-file" not in s and "--opp-split" not in s     # 当時と同じ: 自チームは対戦ごとに引く経路
    assert "--search-blend 0 " in " ".join(R.build_command(jobs[0], out, "f", python="py")) + " "
    print("test_jobs_and_command OK")


def test_child_environment():
    base = {"PATH": "/bin", "RL_BLEND_WEIGHT": "5", MY_TEAM_DISABLED_ENV: "1"}
    env = R.child_environment("/pin", base=base)
    assert env["CHAMPIONS_MODELS_DIR"] == "/pin" and env["ADVICE_SCORING_MODE"] == "legacy"
    assert env[MY_TEAM_DISABLED_ENV] == "1" and "RL_BLEND_WEIGHT" not in env
    assert env["OMP_NUM_THREADS"] == "1" and env["PATH"] == "/bin"
    env2 = R.child_environment("/pin", base=base, use_my_team=True)
    assert MY_TEAM_DISABLED_ENV not in env2
    assert R.child_environment("/pin", base={"OMP_NUM_THREADS": "4"})["OMP_NUM_THREADS"] == "4"   # 呼び出し側の指定を優先
    assert "RL_BLEND_WEIGHT" in base                                                                 # 元の dict は変えない
    print("test_child_environment OK")


def test_schedule_and_estimate():
    jobs = R.jobs_for(R.select("all"))
    lanes = R.schedule(jobs, 3)
    assert sorted(f"{j['verdict']}/{j['arm']}" for lane in lanes for j in lane) == \
        sorted(f"{j['verdict']}/{j['arm']}" for j in jobs)
    assert R.makespan_battles(lanes) == 1800                  # 1500 + 300 / 1500 / 600 + 600 + 300
    assert R.makespan_battles(R.schedule(jobs, 1)) == 4800
    assert R.makespan_battles(R.schedule(jobs, 10)) == 1500    # 枠が余っても最長の腕より短くならない
    assert R.schedule([], 3) == []
    est = R.estimate(jobs, 3)
    assert est["total_battles"] == 4800 and est["makespan_battles"] == 1800
    assert abs(est["total_sec"] - 1800 * ARC_REMEASURE_SEC_PER_BATTLE) < 1e-6
    assert abs(est["sequential_sec"] - 4800 * ARC_REMEASURE_SEC_PER_BATTLE) < 1e-6
    assert est["per_verdict"]["p9b_1500"]["makespan_battles"] == 1500      # 2 腕を 2 枠で同時に
    assert est["per_verdict"]["p9_300"]["battles"] == 600
    one = R.estimate(R.jobs_for(R.select("baseline")), 3, sec_per_battle=10.0)
    assert one["slots"] == 3 and one["total_sec"] == 3000.0
    print("test_schedule_and_estimate OK")


def test_finish_check():
    start = dt.datetime(2026, 10, 9, 22, 30)
    fc = R.finish_check(start, 1800 * 9.7, "06:30")
    assert fc["end"] == "2026-10-10 03:21" and fc["deadline"] == "2026-10-10 06:30" and fc["ok"]
    late = R.finish_check(dt.datetime(2026, 10, 10, 2, 0), 5 * 3600, "06:30")
    assert late["end"] == "2026-10-10 07:00" and not late["ok"] and late["margin_min"] == -30.0
    morning = R.finish_check(dt.datetime(2026, 10, 10, 7, 0), 60, "06:30")
    assert morning["deadline"] == "2026-10-11 06:30" and morning["ok"]     # 期限を過ぎていれば翌日の期限
    edge = R.finish_check(dt.datetime(2026, 10, 10, 6, 0), 1800, "06:30")
    assert edge["ok"] and edge["margin_min"] == 0.0
    print("test_finish_check OK")


def test_preflight():
    good = {"meta_pin": ARC_REMEASURE_META_SNAPSHOT, "pin_has_ckpt": True, "showdown_listening": True,
            "format_known": True, "pin_dir": "/p"}
    assert R.preflight_errors(good, "f", False) == []
    assert R.preflight_errors(dict(good, format_known=None), "f", False) == []      # 読めない = 未確認は止めない
    for k, v in (("meta_pin", 25), ("meta_pin", None), ("pin_has_ckpt", False), ("showdown_listening", False),
                 ("format_known", False)):
        assert len(R.preflight_errors(dict(good, **{k: v}), "f", False)) == 1, (k, v)
    assert len(R.preflight_errors(good, "f", True)) == 1
    print("test_preflight OK")


def _doc(outcomes, stats=None, sb=0.0):
    return {"n_battles": len(outcomes), "wins": sum(outcomes), "win_rate": sum(outcomes) / len(outcomes),
            "outcomes": list(outcomes), "stats": stats or {}, "search_blend": sb}


def test_decide_boundaries():
    assert S.decide(0.05, 0.001, 0.05, True) == S.ADOPT
    assert S.decide(0.05, 0.0, 0.05, True) == S.REJECT            # 下限 0 ちょうどは採用しない
    assert S.decide(0.05, -0.01, 0.05, False) == S.INCONCLUSIVE   # 中間: 推定はゲート以上、CI が 0 を跨ぐ
    assert S.decide(0.049, -0.01, 0.05, False) == S.REJECT
    assert S.decide(0.049, 0.01, 0.05, True) == S.REJECT
    assert S.decide(0.03, 0.001, 0.03, True) == S.ADOPT
    assert S.decide(None, None, 0.05, True) == S.UNTESTABLE
    print("test_decide_boundaries OK")


def test_paired_and_rows():
    a = [1, 0] * 50                     # 0.50
    b = [1, 1] * 25 + [1, 0] * 25       # 0.75 (対戦 i が対応)
    p = S.paired(_doc(b), _doc(a), 0.05, True)
    assert p["n"] == 100 and abs(p["mean"] - 0.25) < 1e-9 and p["lo"] > 0 and p["verdict"] == S.ADOPT
    assert S.paired(None, _doc(a), 0.05, True)["verdict"] == S.UNTESTABLE
    row = S.arm_row("p9_600", "A", _doc([1, 0, 0, 0]), _doc([1, 1, 0, 0], stats={"team_reg_species": 2, "fallback": 1}))
    assert row["old_wr"] == 0.25 and row["new_wr"] == 0.5 and abs(row["diff"] - 0.25) < 1e-9 and row["se"] > 0
    assert row["reg_signals"] == {"team_reg_species": 2, "fallback": 1}
    assert S.arm_row("p9_600", "A", None, _doc([1]))["diff"] is None
    assert S.reg_signals({"stats": {"decide": 3, "error": 0, "team_reg_unverified": 1}}) == {"team_reg_unverified": 1}
    print("test_paired_and_rows OK")


def test_verdict_row_and_summary():
    a_old, b_old = [1, 0] * 150, [1, 0] * 150                      # 当時: 差 0 → 棄却
    a_new, b_new = [1, 0] * 150, [1, 1] * 75 + [1, 0] * 75         # 再測定: +0.25 → 採用
    old = {"p9_600": {"A": _doc(a_old), "B": _doc(b_old, sb=40.0)}}
    new = {"p9_600": {"A": _doc(a_new), "B": _doc(b_new, sb=40.0)}}
    s = S.build_summary(new, old, smoke=False)
    v = s["verdicts"][0]
    assert v["old"]["verdict"] == S.REJECT and v["new"]["verdict"] == S.ADOPT and v["changed"] is True
    assert s["registration_ok"] and s["baseline"] is None and len(s["arms"]) == 2
    assert S.build_summary(new, old, smoke=True)["verdicts"][0]["changed"] is None     # 煙試験は判定に使わない
    same = S.build_summary(old, old, smoke=False)["verdicts"][0]
    assert same["changed"] is False
    # 片腕だけなら腕の行は出るが判定の行は出ない。baseline は p9_300/A
    part = S.build_summary({"p9_300": {"A": _doc([1, 0], stats={"team_reg_species": 1})}}, {}, smoke=False)
    assert part["verdicts"] == [] and part["baseline"]["arm"] == "A" and not part["registration_ok"]
    text = S.render(s) + S.render(part) + S.render(S.build_summary(new, old, smoke=True))
    assert "変わった" in text and "煙試験" in text and "サインあり" in text
    print("test_verdict_row_and_summary OK")


def main():
    test_select()
    test_catalog_consistency()
    test_jobs_and_command()
    test_child_environment()
    test_schedule_and_estimate()
    test_finish_check()
    test_preflight()
    test_decide_boundaries()
    test_paired_and_rows()
    test_verdict_row_and_summary()


if __name__ == "__main__":
    main()
