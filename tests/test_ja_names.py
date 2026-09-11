"""日本語名の表示の検証: フォルム id の種族名 (advisor.infer.species_ja_name) と、
技/特性/持ち物/性格/タイプの逆引き・Showdown 本文の日本語化 (advisor.ja_names、表は vision/data/jp_names.json)。

使い方: python -m tests.test_ja_names
"""
from __future__ import annotations

from advisor import ja_names as J
from advisor.infer import species_ja_name

TEXT = """sneasler @ focussash
Level: 50
Ability: unburden
EVs: 2 HP / 32 Atk / 32 Spe
Adamant Nature
- closecombat
- direclaw
- fakeout
- throatchop

delphox @ delphoxite
Level: 50
Ability: blaze
EVs: 2 HP / 32 SpA / 32 Spe
Timid Nature
- flamethrower
- psychicterrain
"""


def test_form_names():
    cases = {
        "rotomwash": "ウォッシュロトム",
        "rotomheat": "ヒートロトム",
        "typhlosionhisui": "ヒスイバクフーン",
        "slowkinggalar": "ガラルヤドキング",
        "raichualola": "アローラライチュウ",
        "ogerponwellspringmask": "オーガポン(いどのめん)",
        "urshifusinglestrike": "ウーラオス(いちげき)",
        "basculegionmale": "イダイトウ(オス)",
        "landorustherian": "ランドロス(れいじゅう)",
        "swampertmega": "メガラグラージ",
        "charizardmegay": "メガリザードンY",
        "garchomp": "ガブリアス",
    }
    for sid, want in cases.items():
        got = species_ja_name(sid)
        assert got == want, f"{sid}: {got} != {want}"
    print(f"test_form_names OK ({len(cases)}件)")


def test_lookups():
    """種族 / 技 / 特性 / 持ち物 / 性格 / タイプは表から引く (手書きの翻訳をしない)。表に無い id はそのまま"""
    assert J.species_ja("sneasler") == "オオニューラ" and J.species_ja("charizardmegay") == "メガリザードンY"
    assert J.move_ja("direclaw") == "フェイタルクロー" and J.move_ja("lastrespects") == "おはかまいり"
    assert J.move_ja("Dire Claw") == "フェイタルクロー"                      # 表記ゆれは id に正規化
    assert J.ability_ja("unburden") == "かるわざ" and J.item_ja("normalgem") == "ノーマルジュエル"
    assert J.item_ja("focussash") == "きあいのタスキ" and J.item_ja(None) == ""
    assert J.nature_ja("adamant") == "いじっぱり" and J.nature_ja("timid") == "おくびょう" and J.nature_ja("calm") == "おだやか"
    assert J.type_ja("Fire") == "ほのお" and J.move_ja("nosuchmove") == "nosuchmove"
    assert J.evs_ja("2/32/0/0/0/32") == "H2 A32 S32" and J.evs_ja({"hp": 32, "def": 32, "spd": 2}) == "H32 B32 D2"
    assert J.evs_ja("") == "" and J.evs_ja("bad") == "bad"
    print("test_lookups OK")


def test_set_and_team_rendering():
    rows = J.parse_showdown_text(TEXT)
    assert len(rows) == 2 and rows[0]["species"] == "sneasler" and rows[0]["item"] == "focussash"
    assert rows[0]["ability"] == "unburden" and rows[0]["nature"] == "adamant" and rows[0]["evs"] == "2/32/0/0/0/32"
    assert rows[0]["moves"] == ["closecombat", "direclaw", "fakeout", "throatchop"]
    line = J.set_line_ja(rows[0])
    assert line.startswith("オオニューラ @ きあいのタスキ / かるわざ / いじっぱり H2 A32 S32 / インファイト / フェイタルクロー")
    lines = J.team_text_ja(TEXT)
    assert len(lines) == 2 and lines[1].startswith("マフォクシー @ ")
    table = J.team_table_ja(rows)
    assert "| オオニューラ | きあいのタスキ | かるわざ | いじっぱり H2 A32 S32 |" in table and "| ポケモン |" in table
    j = J.set_ja({"species_id": "garchomp", "item": None, "ability": None, "nature": None, "evs": None, "moves": []})
    assert j["species"] == "ガブリアス" and j["item"] == "" and j["moves"] == []
    print("test_set_and_team_rendering OK")


if __name__ == "__main__":
    test_form_names()
    test_lookups()
    test_set_and_team_rendering()
    print("ALL OK")
