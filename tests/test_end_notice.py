"""対戦終了の通知 (vision.end_notice.battle_end_notice) のテスト。

2026-09-29 第17回接続テスト: 終了の検知から表示までに遅れがあるように見えた (助言欄が前のターンのまま)。
検知した瞬間に根拠つきで助言欄へ出す。

    scripts/run_test.sh test_end_notice
"""
from __future__ import annotations

from champions_agent.config import BATTLE_END_FAINT_CONFIRM_SEC
from vision.end_notice import battle_end_notice


def test_hint_and_cancel():
    n = battle_end_notice({"end_hint": {"side": "opponent"}}, ["faint", "battle_end_faint"])
    assert n and n["end_hint"] is True and n["ok"] is False and n["kind"] == "battle"
    assert "相手" in n["reason"] and f"{BATTLE_END_FAINT_CONFIRM_SEC:.0f} 秒" in n["reason"], n
    n = battle_end_notice({"end_hint": {"side": "player"}}, ["battle_end_faint"])
    assert n and n["reason"].startswith("自分")
    n = battle_end_notice({}, ["battle_end_faint_cancel"])
    assert n and n["end_hint"] is False and "取り消し" in n["reason"]
    print("test_hint_and_cancel OK")


def test_end_basis_and_result():
    assert battle_end_notice({"outcome": "win"}, ["battle_win"])["reason"] == "対戦終了: 勝ち (勝負の文言)"
    assert battle_end_notice({"outcome": "loss"}, ["battle_lose"])["reason"] == "対戦終了: 負け (勝負の文言)"
    n = battle_end_notice({"outcome": "loss"}, ["faint", "battle_end_faint_confirmed"])
    assert n["battle_end"] is True and n["reason"] == "対戦終了: 負け (3 体目のひんしから確定)"
    assert "リザルト画面" in battle_end_notice({"outcome": None}, ["battle_end_result"])["reason"]
    n = battle_end_notice({"outcome": None}, ["battle_end_rank"])
    assert "ランク画面" in n["reason"] and "未確定" in n["reason"], n
    # 複数同時なら勝負文言を根拠に出す
    assert "勝負の文言" in battle_end_notice({"outcome": "win"}, ["battle_end_rank", "battle_win"])["reason"]
    print("test_end_basis_and_result OK")


def test_no_notice_for_other_events():
    assert battle_end_notice({"outcome": "win"}, []) is None
    assert battle_end_notice({"outcome": "win"}, None) is None
    assert battle_end_notice({}, ["faint", "switch_opponent", "move_player_surf"]) is None
    print("test_no_notice_for_other_events OK")


def main() -> None:
    test_hint_and_cancel()
    test_end_basis_and_result()
    test_no_notice_for_other_events()
    print("ALL OK")


if __name__ == "__main__":
    main()
