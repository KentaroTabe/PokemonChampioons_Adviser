"""型ライブラリ (sets) の純粋部分のテスト。DB を使う列挙は scratchpad の煙試験で確認する。

    python -m tests.test_team_build_sets
"""
from __future__ import annotations

from tools.team_build.sets import SetCandidate, resolve_item_clause, to_showdown_text


def test_item_clause_and_text():
    team = [SetCandidate("metagross", "clearbody", "metagrossite", "adamant", "2/32/0/0/0/32",
                         ["bulletpunch", "psychicfangs", "earthquake", "icepunch"]),
            SetCandidate("kingambit", "supremeoverlord", "blackglasses", "adamant", "32/32/0/0/0/2",
                         ["suckerpunch", "kowtowcleave", "ironhead", "swordsdance"]),
            SetCandidate("garchomp", "roughskin", "blackglasses", "jolly", "2/32/0/0/0/32",
                         ["earthquake", "stealthrock", "scaleshot", "swordsdance"])]
    fixed = resolve_item_clause(team, {"garchomp": ["blackglasses", "focussash", "lifeorb"]})
    assert [c.item for c in fixed] == ["metagrossite", "blackglasses", "focussash"], [c.item for c in fixed]
    assert fixed[2].notes == ["clause:blackglasses->focussash"]
    text = to_showdown_text(fixed)
    assert "garchomp @ focussash" in text and "EVs: 2 HP / 32 Atk / 32 Spe" in text
    assert "Jolly Nature" in text and "- stealthrock" in text and "Level: 50" in text
    assert text.count("\n\n") == 2
    print("test_item_clause_and_text OK")


if __name__ == "__main__":
    test_item_clause_and_text()
