"""選出計画を選出モデルの初期値にする (tools/team_build/plan_prior) のテスト。純粋関数だけ。

    python -m tests.test_plan_prior
"""
from __future__ import annotations

import json
import random
import tempfile
from pathlib import Path

from tools.team_build import plan_prior as P


def test_plan_indices_and_prior():
    my = ["Dragonite", "tinkaton", "primarina", "typhlosion-hisui", "garchomp", "lopunny"]
    assert P.plan_indices(["tinkaton", "dragonite", "garchomp"], my) == (1, 0, 4)
    assert P.plan_indices(["tinkaton", "dragonite", "typhlosionhisui"], my) == (1, 0, 3)      # id は記号を落として比べる
    assert P.plan_indices(["tinkaton", "dragonite", "nosuch"], my) is None
    assert P.plan_indices(["tinkaton", "tinkaton", "dragonite"], my) is None
    assert P.plan_indices([], my) is None
    # 事前: 計画の 3 体と同じ集合の perm に mix を足し、降順に並べ直す。先発の順は変えない (集合だけを見る)
    scored = [((0, 1, 2), 0.60), ((1, 4, 0), 0.55), ((4, 0, 1), 0.50), ((2, 3, 5), 0.58)]
    out = P.apply_plan_prior(scored, (1, 0, 4), mix=0.3)
    assert out[0][0] == (1, 4, 0) and abs(out[0][1] - 0.85) < 1e-9 and out[1][0] == (4, 0, 1)
    assert out[2] == ((0, 1, 2), 0.60) and P.apply_plan_prior(scored, None) == scored and P.apply_plan_prior(scored, (1, 0, 4), 0.0) == scored
    # 収集の探索用: 先発の順を乱択
    rng = random.Random(3)
    perm = P.plan_perm((1, 0, 4), rng)
    assert sorted(perm) == [0, 1, 4] and P.plan_perm(None) is None and P.plan_perm((1, 0, 4)) == (1, 0, 4)
    print("test_plan_indices_and_prior OK")


def test_load_and_agreement():
    plan = {"candidate_id": "L01_C001", "members": ["a", "b", "c", "d", "e", "f"],
            "selection_plan": {"F1": ["a", "b", "c"], "F2": ["d", "e", "f"]}}
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "x.plan.json"
        p.write_text(json.dumps(plan), encoding="utf-8")
        assert P.load_plan(p)["selection_plan"]["F1"] == ["a", "b", "c"]
        assert P.load_plan(Path(td) / "none.json") == {}
        (Path(td) / "bad.json").write_text("{}", encoding="utf-8")
        assert P.load_plan(Path(td) / "bad.json") == {}
    assert P.plan_for_family(plan, "F2") == ["d", "e", "f"] and P.plan_for_family(plan, "F9") == [] and P.plan_for_family({}, "F1") == []
    recs = [{"opponent_family_id": "F1", "our_selection": ["c", "a", "b"]},
            {"opponent_family_id": "F1", "our_selection": ["a", "b", "e"]},
            {"opponent_family_id": "F2", "our_selection": ["a", "b", "c"]},
            {"opponent_family_id": "F9", "our_selection": ["a", "b", "c"]}]
    ag = P.plan_agreement(recs, plan)
    assert ag["n"] == 3 and ag["match3"] == 1 and ag["match3_rate"] == 0.333
    assert ag["planned_never_picked"] == ["d", "f"]
    assert P.plan_agreement([], plan)["match3_rate"] is None
    print("test_load_and_agreement OK")


def main() -> None:
    test_plan_indices_and_prior()
    test_load_and_agreement()
    print("ALL OK")


if __name__ == "__main__":
    main()
