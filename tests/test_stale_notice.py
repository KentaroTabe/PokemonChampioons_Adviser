"""場のポケモンが替わったあとの通知 (vision.stale_notice) のテスト。

2026-10-06 第18回接続テスト (15 戦で通知 33 回):
- 助言の第一推奨が交代で、そのとおりに交代したのに「前の助言は無効です」と出た (14 回)
- メガシンカで種族 id が基本種とメガ後の間で揺れ、交代していないのに通知が出た
- 次の対戦の先発に対して、前の対戦の最後の個体と比べた通知が出た

    scripts/run_test.sh test_stale_notice
"""
from __future__ import annotations

from vision.stale_notice import advice_target, stale_advice_notice

PARTY = [("blastoise", "カメックス"), ("indeedee", "イエッサン"), ("sneasler", "オオニューラ"),
         ("armarouge", "グレンアルマ"), ("salamence", "ボーマンダ"), ("archaludon", "ブリジュラス")]


def _state(active, seq=3, overrides=None):
    party = [{"species_id": sid, "species_ja": ja} for sid, ja in PARTY]
    for i, sid in (overrides or {}).items():
        party[i]["species_id"] = sid
    return {"battle_seq": seq, "player": {"active_index": active, "party": party}}


def _switch(sid, ja):
    return {"ok": True, "best": {"kind": "switch", "id": sid, "name": ja, "score": 40.0}}


MOVE = {"ok": True, "best": {"kind": "move", "id": "expandingforce", "name": "ワイドフォース", "score": 90.0}}


def test_followed_switch_is_not_called_invalid():
    # イエッサン が場にいるときの第一推奨が「オオニューラ に交代」→ そのとおり オオニューラ が出た
    target = advice_target(_state(1), _switch("sneasler", "オオニューラ"))
    assert target["index"] == 1 and target["best_kind"] == "switch" and target["best_id"] == "sneasler"
    n = stale_advice_notice(target, _state(2))
    assert n and n["stale"] is True and n["followed"] is True and n["ok"] is False and n["kind"] == "battle", n
    assert "助言どおり オオニューラ に交代" in n["reason"] and "無効" not in n["reason"], n
    # 交代先がすぐメガシンカして種族 id がメガ後になっていても、助言どおり
    target = advice_target(_state(2), _switch("salamence", "ボーマンダ"))
    n = stale_advice_notice(target, _state(4, overrides={4: "salamencemega"}))
    assert n and n["followed"] is True and "助言どおり ボーマンダ に交代" in n["reason"], n
    print("test_followed_switch_is_not_called_invalid OK")


def test_other_changes_keep_the_invalid_notice():
    # 第一推奨が技だったのに場が替わった (自分で交代した / ひんしで交代した)
    n = stale_advice_notice(advice_target(_state(1), MOVE), _state(0))
    assert n and n["followed"] is False, n
    assert n["reason"] == "場のポケモンが カメックス に代わりました。前の助言は無効です (次の決定画面で更新します)", n
    # 第一推奨は別の個体への交代だった
    n = stale_advice_notice(advice_target(_state(1), _switch("blastoise", "カメックス")), _state(2))
    assert n and n["followed"] is False and "オオニューラ" in n["reason"] and "無効" in n["reason"], n
    # 推奨が無い助言 (保留) のあとに替わった
    n = stale_advice_notice(advice_target(_state(1), {"ok": False, "reason": "情報不足"}), _state(2))
    assert n and n["followed"] is False
    print("test_other_changes_keep_the_invalid_notice OK")


def test_mega_evolution_is_not_a_switch():
    # カメックス がメガシンカして種族 id が blastoise → blastoisemega に替わっただけ (同じ枠)
    target = advice_target(_state(0), MOVE)
    assert stale_advice_notice(target, _state(0, overrides={0: "blastoisemega"})) is None
    # メガ後の id で助言を出したあと、id が基本種に戻って見えても同じ (第18回: blastoisemega → blastoise の揺れ)
    target = advice_target(_state(0, overrides={0: "blastoisemega"}), MOVE)
    assert stale_advice_notice(target, _state(0)) is None
    print("test_mega_evolution_is_not_a_switch OK")


def test_no_notice_across_battles_or_without_active():
    target = advice_target(_state(1, seq=3), MOVE)
    # 次の対戦 (battle_seq が違う) の先発には、前の対戦の助言の対象と比べた通知を出さない
    assert stale_advice_notice(target, _state(2, seq=4)) is None
    # 場の個体が未確定 / 助言がまだ無い / 助言の時点で場の個体が未確定
    assert stale_advice_notice(target, _state(None)) is None
    assert stale_advice_notice(None, _state(2)) is None
    assert stale_advice_notice(advice_target(_state(None), MOVE), _state(2)) is None
    # 場の個体の種族が読めていない
    st = _state(2)
    st["player"]["party"][2]["species_id"] = None
    assert stale_advice_notice(target, st) is None
    print("test_no_notice_across_battles_or_without_active OK")


def main() -> None:
    test_followed_switch_is_not_called_invalid()
    test_other_changes_keep_the_invalid_notice()
    test_mega_evolution_is_not_a_switch()
    test_no_notice_across_battles_or_without_active()
    print("ALL OK")


if __name__ == "__main__":
    main()
