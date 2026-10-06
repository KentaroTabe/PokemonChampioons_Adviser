"""テーマの検査 (tools/team_build/theme_check) と再構築の引き金 (tools/team_build/triggers) の純粋関数テスト。

    python -m tests.test_theme_check
"""
from __future__ import annotations

from tools.team_build import theme_check as T
from tools.team_build import triggers as G


def _recs(sel_list):
    return [{"our_selection": s, "won": True} for s in sel_list]


def test_theme_check():
    theme = T.theme_of_spec({"ace": "Lopunny", "favorites": ["lopunny", "garchomp"], "required_moves": {"garchomp": ["swordsdance"]}})
    assert theme == {"ace": "lopunny", "favorites": ["garchomp"], "required_moves": {"garchomp": ["swordsdance"]}}
    assert T.active(theme) and not T.active(T.theme_of_spec({}))
    row = {"members": ["lopunny", "garchomp", "a", "b", "c", "d"],
           "sets": [{"species": "garchomp", "moves": ["swordsdance", "earthquake", "scaleshot", "protect"]}]}
    recs = _recs([["lopunny", "a", "b"], ["garchomp", "a", "b"], ["lopunny", "garchomp", "c"], ["a", "b", "c"]])
    pr = T.pick_rates(recs + [{"our_selection": []}], ["lopunny", "garchomp", "zzz"])
    assert pr["lopunny"] == {"n": 4, "picked": 2, "rate": 0.5} and pr["zzz"]["picked"] == 0
    r = T.check_team(theme, row, recs, ace_min=0.5)
    assert r["pass"] is True and r["ace"]["pick_rate"] == 0.5 and r["favorites"]["garchomp"]["pick_rate"] == 0.5
    assert r["required_moves"]["garchomp"]["present"] is True
    r = T.check_team(theme, row, recs, ace_min=0.6)
    assert r["pass"] is False and r["reasons"] and "選出率" in r["reasons"][0]
    # 記録が無ければ判定なし、エースが並びに居なければ False、技が無ければ False
    assert T.check_team(theme, row, [], 0.5)["pass"] is None
    assert T.check_team(theme, {"members": ["garchomp"], "sets": []}, recs, 0.5)["pass"] is False
    row2 = dict(row, sets=[{"species": "garchomp", "moves": ["earthquake"]}])
    r2 = T.check_team(theme, row2, recs, 0.5)
    assert r2["pass"] is False and r2["required_moves"]["garchomp"]["missing"] == ["swordsdance"]
    # run 全体と門
    run = T.check_run(theme, {"A": row, "B": row2, "C": row}, {"A": recs, "B": recs, "C": []}, 0.5)
    assert run["n_pass"] == 1 and run["n_fail"] == 1 and run["n_checked"] == 3 and not run["none_pass"]
    assert T.apply_gate(["B", "A", "C"], run["teams"], gate=False) == (["B", "A", "C"], [])
    assert T.apply_gate(["B", "A", "C"], run["teams"], gate=True) == (["A", "C"], ["B"])
    run2 = T.check_run(theme, {"B": row2}, {"B": recs}, 0.5)
    assert run2["none_pass"] and T.apply_gate(["B"], run2["teams"], True) == (["B"], [])
    # Δ を渡すと「同等のときだけ入れ替える」(判断 #3): 1 位が満たさず、満たす最上位の Δ が 1 位 − eps 以上なら入替、そうでなければそのまま
    teams = {"X": {"pass": False}, "Y": {"pass": True}, "Z": {"pass": True}}
    assert T.apply_gate(["X", "Y", "Z"], teams, True, deltas={"X": 0.10, "Y": 0.085, "Z": 0.0}, eps=0.02) == (["Y", "X", "Z"], ["X"])
    assert T.apply_gate(["X", "Y", "Z"], teams, True, deltas={"X": 0.10, "Y": 0.07, "Z": 0.0}, eps=0.02) == (["X", "Y", "Z"], [])
    assert T.apply_gate(["Y", "X"], teams, True, deltas={"X": 0.10, "Y": 0.2}, eps=0.02) == (["Y", "X"], [])      # 1 位が満たすなら何もしない
    assert T.apply_gate(["X"], {"X": {"pass": False}}, True, deltas={"X": 0.1}) == (["X"], [])                        # 満たす並びが無い
    assert T.apply_gate(["X", "Y"], teams, False, deltas={"X": 0.1, "Y": 0.1}) == (["X", "Y"], [])
    assert "満たす 1" in T.format_line(run)
    print("test_theme_check OK")


def test_triggers():
    base = {"season": "regmc", "real_win_rate": 0.45, "real_n": 40, "sim_reference_win_rate": 0.65, "pool_match_share": 0.3}
    cur = dict(base)
    r = G.evaluate(cur, base)
    assert not r["fire"] and r["checks"]["real_gap"]["gap"] == -0.2 and not r["checks"]["pool_match"]["fire"]
    # 実戦の差が基準線より 0.2 (判断 #8) 以上悪化 → 引き金。試合数が足りなければ (現在・基準線のどちらでも) 引き金にしない
    r = G.evaluate(dict(base, real_win_rate=0.24), base)
    assert r["fire"] and r["checks"]["real_gap"]["fire"] and "悪化" in r["reasons"][0]
    assert not G.evaluate(dict(base, real_win_rate=0.28), base)["fire"]                       # 0.17 の悪化では引かない
    r = G.evaluate(dict(base, real_win_rate=0.24, real_n=10), base)
    assert not r["fire"] and r["checks"]["real_gap"]["note"] == "few_battles"
    r = G.evaluate(dict(base, real_win_rate=0.24), dict(base, real_n=10))
    assert not r["fire"] and r["checks"]["real_gap"]["note"] == "baseline_few_battles"
    # 一致率は記録だけ (BUILD_TRIGGER_POOL_MATCH_FIRE = False): 床を割っても引かない。pool_fire=True なら引く
    r = G.evaluate(dict(base, pool_match_share=0.14), base)
    assert not r["fire"] and r["checks"]["pool_match"]["below_floor"] and r["checks"]["pool_match"]["note"] == "record_only"
    assert r["checks"]["pool_match"]["floor"] == 0.15
    r = G.evaluate(dict(base, pool_match_share=0.14), base, pool_fire=True)
    assert r["fire"] and r["checks"]["pool_match"]["fire"]
    r = G.evaluate(dict(base, pool_match_share=0.05), None)
    assert not r["fire"] and r["checks"]["pool_match"]["note"] == "no_baseline" and r["checks"]["real_gap"]["note"] == "no_baseline"
    # 季節 (規制) の切替
    r = G.evaluate(dict(base, season="regmd"), base)
    assert r["fire"] and r["checks"]["season"]["fire"]
    print("test_triggers OK")


def main() -> None:
    test_theme_check()
    test_triggers()
    print("ALL OK")


if __name__ == "__main__":
    main()
