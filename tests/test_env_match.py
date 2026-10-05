"""環境の整合 (tools/team_build/env_match)、段ごとの所要 (review_run.durations_from_marks)、修理の変更枠数 (pipeline.change_counts) の
純粋関数テスト。

    python -m tests.test_env_match
"""
from __future__ import annotations

from tools.team_build import env_match as E
from tools.team_build import pipeline as P
from tools.team_build import review_run as RV


def test_match_rate_and_real_record():
    teams = {"t1": ["a", "b", "c", "d", "e", "f"], "t2": ["a", "b", "c", "x", "y", "z"]}
    b = [{"file": "b1", "opp_species": ["a", "b", "c", "d", "e", "f"], "opp_full": True, "consistent": True},
         {"file": "b2", "opp_species": ["a", "b", "c", "d", "e", "q"], "opp_full": True, "consistent": True},
         {"file": "b3", "opp_species": ["p", "q", "r", "s", "t", "u"], "opp_full": True, "consistent": True},
         {"file": "b4", "opp_species": ["a", "b", "c"], "opp_full": False, "consistent": False}]
    r = E.match_rate(teams, b)
    assert r["n_real"] == 4 and r["n_consistent"] == 3 and r["n_matched"] == 2 and r["covered_share"] == 0.667
    # プールに入れた対戦 (b1) を除くと、後から来た対戦だけで測る (判断 #1: 入れた対戦で測ると 0.92 に見える)
    r2 = E.match_rate(teams, b, exclude_files=["b1"])
    assert r2["n_consistent"] == 2 and r2["n_matched"] == 1 and r2["covered_share"] == 0.5 and r2["n_excluded_used_in_pool"] == 1
    assert E.match_rate(teams, [])["covered_share"] is None
    # 登録チームと同じ 6 体の実戦だけを数える (引き金と実験 13 の入力)
    reg = ["l", "m", "n", "o", "p", "q"]
    bb = [{"our_species": reg, "won": True}, {"our_species": list(reversed(reg)), "won": False},
          {"our_species": ["l", "m", "n", "o", "p", "zz"], "won": True}, {"our_species": reg, "won": None}]
    assert E.real_record(bb, reg) == {"n": 2, "wins": 1, "win_rate": 0.5}
    assert E.real_record(bb, ["l"])["n"] == 0 and E.real_record([], reg)["win_rate"] is None
    print("test_match_rate_and_real_record OK")


def test_stage_durations_and_change_counts():
    marks = [(0, "S0 spec"), (60, "S7-13 measurement start"), (120, "S8a cheap adaptation"), (180, "[racing:s08a_screen] looks=3"),
             (600, "S8a survivors"), (660, "S9 repair 1: 親"), (900, "S7 adapt"), (1500, "S8b: x"), (1800, "S10 winner"),
             (1900, "[holdout] PASS"), (2000, "[stress] start"), (2300, "S13 package"), (100, "S13 registry")]     # 日付をまたぐ
    d = RV.durations_from_marks(marks)
    assert d["S8a"] == 540 and d["S9 repair"] == 240 and d["S7"] == 600 and d["S12 holdout"] == 100 and d["STRESS"] == 300
    assert d["S13"] == 86400 - 2200 and d["total"] == sum(v for k, v in d.items() if k != "total")
    assert RV.stage_of("S9 repair 2: 親") == "S9 repair" and RV.stage_of("[racing:x] y") is None and RV.stage_of("最終候補 (方向性)") == "S10"
    rows = [{"candidate_id": "L01-R1A1", "origin": {"kind": "repair", "variant": "A", "changes": [{"out": "a"}, {"out": "b"}]}},
            {"candidate_id": "L01-R1B1", "origin": {"kind": "repair", "variant": "B", "changes": [{"species": "a"}]}},
            {"candidate_id": "L02", "origin": {}}]
    assert P.change_counts(rows, ["L01-R1A1", "L01-R1B1", "L02", "nosuch"]) == {"L01-R1A1": {"kind": "A", "n_changes": 2},
                                                                              "L01-R1B1": {"kind": "B", "n_changes": 1}}
    print("test_stage_durations_and_change_counts OK")


def main() -> None:
    test_match_rate_and_real_record()
    test_stage_durations_and_change_counts()
    print("ALL OK")


if __name__ == "__main__":
    main()
