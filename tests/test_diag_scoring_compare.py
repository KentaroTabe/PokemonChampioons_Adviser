"""tools/diag_scoring_compare (採点方式の比較の診断 段 1: 第一候補と実行行動の不一致の分類) の純粋な部分のテスト。

    python -m tests.test_diag_scoring_compare
"""
from __future__ import annotations

from tools.diag_scoring_compare import (base_id, classify_decision, fainted_before, render_markdown, same_mon,
                                        summarize_battles)

SEL = ["gyarados", "vanilluxe", "scolipede"]


def _dec(turn, best, executed, actions=None, me_species="gyarados", me_hp=1.0):
    acts = actions if actions is not None else [best]
    return {"turn": turn, "state_before": {"turn": turn, "me": {"species": me_species, "hp": me_hp, "status": ""}},
            "advisor_recommendation": {"best": best, "actions": acts, "ok": True},
            "executed_action": executed, "followed": True}


def _sw(i, score=1.0):
    return {"kind": "switch", "id": i, "score": score}


def _mv(i, score=1.0):
    return {"kind": "move", "id": i, "score": score}


def test_ids():
    assert base_id("scolipedemega") == "scolipede" and base_id("charizardmegax") == "charizard"
    assert same_mon("scolipedemega", "scolipede") and same_mon("floettemega", "floetteeternal")
    assert not same_mon("primarina", "gyarados")
    print("test_ids OK")


def test_classify():
    # 一致
    c = classify_decision(_dec(1, _mv("waterfall"), _mv("waterfall")), SEL, set())
    assert c["status"] == "match" and c["category"] is None
    # 未選出への交代 (レビュー記録の例: 第一候補 アシレーヌ、実行 ペンドラー)
    acts = [_sw("primarina"), _sw("archaludon"), _sw("blastoise"), _sw("scolipede")]
    c = classify_decision(_dec(1, acts[0], {"kind": "switch", "id": "scolipede"}, acts), SEL, set())
    assert c["status"] == "mismatch" and c["category"] == "unpicked_switch" and c["exec_rank"] == 4
    assert c["best_unpicked"] and c["unpicked_in_top4"]
    # メガ後の個体 (scolipedemega) は選出の scolipede と同じ個体
    c = classify_decision(_dec(2, _sw("scolipedemega"), _mv("icebeam")), SEL, set())
    assert c["category"] == "switch_blocked" and not c["best_unpicked"]
    # ひんしの個体への交代
    c = classify_decision(_dec(3, _sw("vanilluxe"), _mv("waterfall")), SEL, {"vanilluxe"})
    assert c["category"] == "fainted_switch"
    # 場の個体がひんしで技
    c = classify_decision(_dec(3, _mv("waterfall"), _sw("vanilluxe"), me_hp=0.0), SEL, set())
    assert c["category"] == "move_on_fainted"
    # 同じターンの 2 回目の決定 (交代技の後) で技
    c = classify_decision(_dec(3, _mv("uturn"), _sw("vanilluxe")), SEL, set(), same_turn_prior=True)
    assert c["category"] == "move_in_switch_scene"
    # 直前と同じ個体が同じ技 (固定の可能性)
    prev = _dec(2, _mv("outrage"), _mv("outrage"))
    c = classify_decision(_dec(3, _mv("earthquake"), _mv("outrage")), SEL, set(), prev=prev)
    assert c["category"] == "move_locked"
    c = classify_decision(_dec(3, _mv("earthquake"), _mv("protect")), SEL, set(), prev=prev)
    assert c["category"] == "move_restricted"
    # 実行行動なし = ランダム行動
    c = classify_decision(_dec(3, _sw("primarina"), None), SEL, set())
    assert c["status"] == "random" and c["category"] == "unpicked_switch"
    # 助言なし
    d = _dec(3, _mv("x"), None)
    d["advisor_recommendation"] = None
    assert classify_decision(d, SEL, set())["status"] == "no_advice"
    print("test_classify OK")


def test_fainted_before():
    rec = {"lead": {"ours": "gyarados", "theirs": "p2a: Gholdengo"},
           "ko_events": [{"turn": 2, "fainted": "p1a: Scolipede"}, {"turn": 2, "fainted": "p2a: Gholdengo"},
                         {"turn": 4, "fainted": "p1a: Vanilluxe"}]}
    assert fainted_before(rec, 2, False) == set()
    assert fainted_before(rec, 2, True) == {"scolipede"}            # 同じターンの交代の場面 (先の決定がある)
    assert fainted_before(rec, 5, False) == {"scolipede", "vanilluxe"}
    print("test_fainted_before OK")


def test_summarize_and_render():
    acts = [_sw("primarina"), _sw("archaludon"), _sw("blastoise"), _sw("scolipede")]
    rec = {"our_selection": SEL, "lead": {"theirs": "p2a: Gholdengo"}, "ko_events": [],
           "turns": [_dec(1, acts[0], {"kind": "switch", "id": "scolipede"}, acts),
                     _dec(2, _mv("irondefense"), _mv("irondefense"), me_species="scolipede"),
                     _dec(3, _mv("earthquake"), None, me_species="scolipede")]}
    s = summarize_battles([rec])
    assert (s["battles"], s["decisions"], s["executed"], s["mismatch"], s["random"], s["match"]) == (1, 3, 2, 1, 1, 1)
    assert s["categories_mismatch"] == {"unpicked_switch": 1} and s["exec_rank"] == {"4": 1}
    assert s["categories_random"] == {"move_restricted": 1}
    assert s["best_unpicked"] == 1 and s["battles_with_unpicked_best"] == 1 and s["unpicked_in_top4"] == 1
    md = render_markdown({"legacy": s}, {"legacy": {}})
    assert "| 不一致 (実行行動ありのうち第一候補と種類・id が違う) | 1 / 2 (50.0%) |" in md, md
    assert "未選出の個体への交代 | 1 (100.0%)" in md
    md2 = render_markdown({"transition": s}, {"transition": {"scoring_transition": 10, "scoring_fallback": 3}})
    assert "transition で採点した全決定: 7 (採点 10 − 退避 3)" in md2
    print("test_summarize_and_render OK")


if __name__ == "__main__":
    test_ids()
    test_classify()
    test_fainted_before()
    test_summarize_and_render()
