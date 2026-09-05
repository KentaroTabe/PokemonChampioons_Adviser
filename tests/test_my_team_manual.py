"""型登録の手入力ベース化 (2026-09-06) の検証。

    python -m tests.test_my_team_manual

- 画面からの自動登録 (update_build) は既定で無効 (AUTO_REGISTER_FROM_SCREEN=False)
- 手入力登録 (set_build) はエントリを丸ごと置き換え、他種は残す
- 「種族ID」があれば自分側の種族解決 (resolve_my_species) がそれを使う
- tools/register_my_team のチーム本文解析とエントリ化
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import advisor.my_team as mt


def _tmp_config(data: dict) -> Path:
    tmp = Path(tempfile.mkdtemp()) / "my_team.json"
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    mt.CONFIG_PATH = tmp
    mt._CACHE, mt._CACHE_MTIME = None, -1.0
    return tmp


def test_screen_auto_register_disabled_by_default():
    tmp = _tmp_config({"ドドゲザン": {"能力ポイント": {"h": 32, "a": 32, "s": 2}, "性格": "いじっぱり"}})
    assert mt.AUTO_REGISTER_FROM_SCREEN is False
    assert mt.update_build("ドドゲザン", {"持ち物": "くろいメガネ"}) is False
    assert "持ち物" not in json.loads(tmp.read_text(encoding="utf-8"))["ドドゲザン"]
    # 経路自体は残っている: 有効化すれば従来どおり書く
    mt.AUTO_REGISTER_FROM_SCREEN = True
    try:
        assert mt.update_build("ドドゲザン", {"持ち物": "くろいメガネ"}) is True
        assert json.loads(tmp.read_text(encoding="utf-8"))["ドドゲザン"]["持ち物"] == "くろいメガネ"
    finally:
        mt.AUTO_REGISTER_FROM_SCREEN = False
    print("test_screen_auto_register_disabled_by_default OK")


def test_set_build_replaces_entry_and_keeps_others():
    tmp = _tmp_config({
        "ミミッキュ": {"能力ポイント": {"h": 32, "a": 32, "b": 2}, "性格": "いじっぱり",
                   "技": ["じゃれつく", "かげうち", "つるぎのまい", "ドレインパンチ"]},
        "ペリッパー": {"能力ポイント": {"h": 32, "c": 32, "s": 2}, "性格": "ひかえめ"},
    })
    ok = mt.set_build("ミミッキュ", {
        "種族ID": "mimikyu", "能力ポイント": {"hp": 1, "atk": 32, "def": 1, "spe": 32},
        "性格": "いじっぱり", "持ち物": "いのちのたま", "特性": "ばけのかわ",
        "技": ["じゃれつく", "かげうち", "つるぎのまい", "シャドークロー"], "備考": ""})
    assert ok
    data = json.loads(tmp.read_text(encoding="utf-8"))
    assert data["ミミッキュ"]["能力ポイント"] == {"h": 1, "a": 32, "b": 1, "s": 32}, data["ミミッキュ"]
    assert data["ミミッキュ"]["技"][-1] == "シャドークロー"
    assert "備考" not in data["ミミッキュ"]            # 空値は落とす
    assert data["ペリッパー"]["性格"] == "ひかえめ"      # 他種は残る
    b = mt.get_my_build("ミミッキュ")
    assert b["ev"] == {"hp": 8, "atk": 252, "def": 8, "spe": 252}, b["ev"]
    assert mt.registered_species_id("ミミッキュ") == "mimikyu"
    assert mt.registered_species_id("ペリッパー") is None
    print("test_set_build_replaces_entry_and_keeps_others OK")


def test_resolve_my_species_uses_registered_form_id():
    from vision.extractors import resolve_my_species
    from vision.normalize import NameResolver
    resolver = NameResolver()
    _tmp_config({"ロトム": {"種族ID": "rotomwash", "能力ポイント": {"h": 2, "c": 32, "s": 32},
                          "性格": "ひかえめ", "持ち物": "こだわりスカーフ"}})
    r = resolve_my_species(resolver, "ロトム", cutoff=0.72)
    assert r is not None and r[0] == "ロトム" and r[1] == "rotomwash", r
    # 種族ID が無ければ従来どおり基本種
    _tmp_config({"ロトム": {"能力ポイント": {"h": 32, "b": 2, "c": 32}, "性格": "ひかえめ"}})
    r = resolve_my_species(resolver, "ロトム", cutoff=0.72)
    assert r is not None and r[1] == "rotom", r
    print("test_resolve_my_species_uses_registered_form_id OK")


def test_register_tool_parsing():
    from tools.register_my_team import parse_team_text, display_key, to_entry
    from vision.normalize import NameResolver
    text = """Rotom-Wash @ choicescarf
Level: 50
Ability: levitate
EVs: 2 HP / 32 SpA / 32 Spe
Modest Nature
- hydropump
- voltswitch
- willowisp
- thunderbolt

Kingambit @ blackglasses
Level: 50
Ability: supremeoverlord
EVs: 32 HP / 32 Atk / 2 Spe
Adamant Nature
- suckerpunch
- kowtowcleave
- ironhead
- swordsdance
"""
    parsed = parse_team_text(text)
    assert [p["species_id"] for p in parsed] == ["rotomwash", "kingambit"]
    assert parsed[0]["points"] == {"h": 2, "c": 32, "s": 32}, parsed[0]
    assert parsed[0]["nature"] == "modest" and parsed[0]["item"] == "choicescarf"
    assert parsed[1]["moves"] == ["suckerpunch", "kowtowcleave", "ironhead", "swordsdance"]
    dex = {"species": {"rotomwash": {"baseSpecies": "Rotom", "forme": "Wash"},
                       "kingambit": {"baseSpecies": "Kingambit", "forme": ""}}}
    assert display_key("rotomwash", dex) == "ロトム"      # HUD 表示名 (基本種)
    assert display_key("kingambit", dex) == "ドドゲザン"
    key, entry = to_entry(parsed[0], dex, NameResolver())
    assert key == "ロトム" and entry["種族ID"] == "rotomwash"
    assert entry["性格"] == "ひかえめ" and entry["持ち物"] == "こだわりスカーフ"
    assert entry["特性"] == "ふゆう" and entry["技"][0] == "ハイドロポンプ"
    key2, entry2 = to_entry(parsed[1], dex, NameResolver())
    assert key2 == "ドドゲザン" and entry2["技"] == ["ふいうち", "ドゲザン", "アイアンヘッド", "つるぎのまい"]
    print("test_register_tool_parsing OK")


if __name__ == "__main__":
    test_screen_auto_register_disabled_by_default()
    test_set_build_replaces_entry_and_keeps_others()
    test_resolve_my_species_uses_registered_form_id()
    test_register_tool_parsing()
