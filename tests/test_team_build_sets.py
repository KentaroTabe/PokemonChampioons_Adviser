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


def test_splice_registered_sets():
    from tools.team_build.sets import splice_registered_sets, team_blocks
    cand = ("metagross @ lifeorb\nLevel: 50\n- bulletpunch\n\nkingambit @ blackglasses\nLevel: 50\n- suckerpunch\n\n"
            "garchomp @ focussash\nLevel: 50\n- earthquake\n")
    reg = ("Metagross @ Metagrossite\nLevel: 50\nAbility: Clear Body\n- Bullet Punch\n\n"
           "Rotom-Wash @ Choice Scarf\nLevel: 50\n- Hydro Pump\n\nKingambit @ Black Glasses\nLevel: 50\n- Kowtow Cleave\n")
    assert [sid for sid, _ in team_blocks(reg)] == ["metagross", "rotomwash", "kingambit"]
    text, replaced = splice_registered_sets(cand, reg)
    assert replaced == ["metagross", "kingambit"], replaced
    assert "Metagross @ Metagrossite" in text and "Kowtow Cleave" in text     # 登録の型に差し替え
    assert "lifeorb" not in text and "garchomp @ focussash" in text          # 未登録の個体は候補の型のまま
    assert "Rotom-Wash" not in text                                           # 登録にいても候補にいない個体は足さない
    assert text.count("\n\n") == 2 and text.endswith("\n")
    print("test_splice_registered_sets OK")


def test_prefer_registered_keeps_registered_items():
    from tools.team_build.sets import prefer_registered, registered_items
    reg = "Rotomwash @ choicescarf\nLevel: 50\n- hydropump\n\nMetagross @ metagrossite\nLevel: 50\n- bulletpunch\n"
    items = registered_items(reg)
    assert items == {"rotomwash": "choicescarf", "metagross": "metagrossite"}, items
    team = [SetCandidate("hydreigon", "levitate", "choicescarf", "timid", "2/0/0/32/0/32", ["dracometeor"]),
            SetCandidate("rotomwash", "levitate", "leftovers", "modest", "32/0/0/32/0/2", ["hydropump"]),
            SetCandidate("metagross", "clearbody", "metagrossite", "adamant", "2/32/0/0/0/32", ["bulletpunch"])]
    ordered = prefer_registered(team, items)
    assert [c.species_id for c in ordered] == ["rotomwash", "metagross", "hydreigon"]
    assert ordered[0].item == "choicescarf"                                   # 登録の持ち物に合わせる
    fixed = resolve_item_clause(ordered, {"hydreigon": ["choicescarf", "choicespecs", "lifeorb"]})
    assert [c.item for c in fixed] == ["choicescarf", "metagrossite", "choicespecs"], [c.item for c in fixed]
    print("test_prefer_registered_keeps_registered_items OK")


if __name__ == "__main__":
    test_item_clause_and_text()
    test_splice_registered_sets()
    test_prefer_registered_keeps_registered_items()
