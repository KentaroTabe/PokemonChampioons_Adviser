"""行動方策 adapter の採否ロジック (純粋関数) と学習入口の引数のテスト。

    python -m tests.test_team_build_action_adapt
"""
from tools.team_build.adapt import decide_action_adapt


def test_decide_action_adapt():
    assert decide_action_adapt([])["use_adapted"] is False
    h = [{"n_steps": 100000, "delta": 0.01, "state": "uncertain"},
         {"n_steps": 200000, "delta": 0.06, "state": "improved"},
         {"n_steps": 300000, "delta": 0.03, "state": "uncertain"}]
    d = decide_action_adapt(h)
    assert d["use_adapted"] and d["best_index"] == 1, d
    h2 = [{"n_steps": 100000, "delta": -0.02, "state": "degraded"}, {"n_steps": 200000, "delta": 0.0, "state": "equivalent"}]
    assert decide_action_adapt(h2)["use_adapted"] is False
    print("test_decide_action_adapt OK")


def test_smoke_train_accepts_own_team_file():
    import argparse
    import importlib
    src = open("tools/smoke_train.py", encoding="utf-8").read()
    assert "--own-team-file" in src and "own_team_text=own_text" in src
    src2 = open("champions_agent/train/train_battle.py", encoding="utf-8").read()
    assert "TRAIN_TARGET_KL" in src2 and "own_team_text" in src2
    src3 = open("champions_agent/env/showdown_env.py", encoding="utf-8").read()
    assert "ConstantTeambuilder(own_team_text)" in src3
    print("test_smoke_train_accepts_own_team_file OK")


if __name__ == "__main__":
    test_decide_action_adapt()
    test_smoke_train_accepts_own_team_file()
