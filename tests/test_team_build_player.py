"""助言操縦プレイヤーの M0 追加分 (純粋ヘルパー) のテスト。

    python -m tests.test_team_build_player
"""
from __future__ import annotations

import random

from champions_agent.env.advisor_player import apply_user_policy, pick_order_from_perm


def test_pick_order_from_perm():
    assert pick_order_from_perm((2, 0, 5), 6) == "/team 316245"
    assert pick_order_from_perm((0, 1, 2), 6) == "/team 123456"
    print("test_pick_order_from_perm OK")


def test_apply_user_policy():
    adv = {"ok": True, "best": {"kind": "move", "id": "a", "score": 20.0},
           "actions": [{"kind": "move", "id": "a", "score": 20.0},
                       {"kind": "switch", "id": "b", "score": 19.5},
                       {"kind": "move", "id": "c", "score": 3.0}]}
    rng = random.Random(0)
    out, followed = apply_user_policy(adv, "full", 0.0, rng)
    assert followed and out is adv
    # 行動ノイズ 100%: 常に 2 位
    out, followed = apply_user_policy(adv, "full", 1.0, rng)
    assert not followed and out["best"]["id"] == "b" and out["actions"][0]["id"] == "b"
    assert [a["id"] for a in out["actions"]] == ["b", "a", "c"]
    # 遵守モデル mixed: 迷い局面 (差 2.5%) なので一定割合で離反
    dev = sum(1 for _ in range(500) if not apply_user_policy(adv, "mixed", 0.0, rng)[1])
    assert 20 < dev < 200, dev
    # ok でない / 候補 1 つは素通し
    assert apply_user_policy({"ok": False}, "expert", 1.0, rng) == ({"ok": False}, True)
    one = {"ok": True, "actions": [{"kind": "move", "id": "a", "score": 1.0}]}
    assert apply_user_policy(one, "expert", 1.0, rng)[1] is True
    print("test_apply_user_policy OK")


if __name__ == "__main__":
    test_pick_order_from_perm()
    test_apply_user_policy()
