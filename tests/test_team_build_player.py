"""助言操縦プレイヤーの M0 追加分 (純粋ヘルパー) のテスト。

    python -m tests.test_team_build_player
"""
from __future__ import annotations

import random

from champions_agent.env.advisor_player import apply_action_noise, pick_order_from_perm


def test_pick_order_from_perm():
    assert pick_order_from_perm((2, 0, 5), 6) == "/team 316245"
    assert pick_order_from_perm((0, 1, 2), 6) == "/team 123456"
    print("test_pick_order_from_perm OK")


def test_apply_action_noise():
    """遵守モデルは廃止 (2026-10-05)。残るのは STRESS 用の行動ノイズ (確率 p で 2 位の手) だけ"""
    adv = {"ok": True, "best": {"kind": "move", "id": "a", "score": 20.0},
           "actions": [{"kind": "move", "id": "a", "score": 20.0},
                       {"kind": "switch", "id": "b", "score": 19.5},
                       {"kind": "move", "id": "c", "score": 3.0}]}
    rng = random.Random(0)
    out, followed = apply_action_noise(adv, 0.0, rng)
    assert followed and out is adv
    out, followed = apply_action_noise(adv, 1.0, rng)
    assert not followed and out["best"]["id"] == "b" and [a["id"] for a in out["actions"]] == ["b", "a", "c"]
    assert apply_action_noise({"ok": False}, 1.0, rng) == ({"ok": False}, True)
    one = {"ok": True, "actions": [{"kind": "move", "id": "a", "score": 1.0}]}
    assert apply_action_noise(one, 1.0, rng)[1] is True
    print("test_apply_action_noise OK")


if __name__ == "__main__":
    test_pick_order_from_perm()
    test_apply_action_noise()
