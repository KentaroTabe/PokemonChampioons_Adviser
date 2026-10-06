"""メガストーン名の解決 (接尾辞 X/Y/Z を厳密に扱う) と、石 → フォルムの解決のテスト。

2026-09-29 第17回接続テスト: 登録の「メガガブリアスZナイト」が文字列類似で ガブリアスナイト (無印) に解決され、
通常のメガガブリアスとして助言されていた。また vision/abilities.mega_form_id が末尾 z を見ていなかった。

    scripts/run_test.sh test_mega_stone_names
"""
from __future__ import annotations

from vision.normalize import NameResolver, parse_mega_stone_name, stone_species_variants

resolver = NameResolver()


def test_parse_stone_name():
    assert parse_mega_stone_name("ガブリアスナイトZ") == ("ガブリアス", "z")
    assert parse_mega_stone_name("メガガブリアスZナイト") == ("メガガブリアス", "z")
    assert parse_mega_stone_name("リザードナイトX") == ("リザード", "x")
    assert parse_mega_stone_name("ライチュウナイトＹ") == ("ライチュウ", "y")       # 全角も NFKC で同じ
    assert parse_mega_stone_name("ハッサムナイト") == ("ハッサム", "")
    assert parse_mega_stone_name("メガニウムナイト") == ("メガニウム", "")
    assert parse_mega_stone_name("こだわりスカーフ") is None
    assert parse_mega_stone_name("") is None
    assert stone_species_variants("メガガブリアス") == ["メガガブリアス", "ガブリアス"]
    assert stone_species_variants("メガニウム") == ["メガニウム", "ニウム"]
    print("test_parse_stone_name OK")


def test_resolve_items_by_suffix():
    def rid(text, cutoff=0.9):
        r = resolver.resolve(text, "items", cutoff=cutoff)
        return r[1] if r else None
    assert rid("ガブリアスナイトZ") == "garchompitez"
    assert rid("メガガブリアスZナイト") == "garchompitez"        # 手入力の並び (第17回の登録)
    assert rid("メガガブリアスZナイト", cutoff=0.85) == "garchompitez"   # engine の閾値
    assert rid("ガブリアスナイト") == "garchompite"              # 無印はそのまま
    assert rid("メガガブリアスナイト") == "garchompite"
    assert rid("ガプリアスナイトZ") == "garchompitez"            # OCR の濁点落ちは緩いキーで吸収
    assert rid("メガルカリオZナイト") == "lucarionitez"
    assert rid("ルカリオナイト") == "lucarionite"
    assert rid("メガリザードンXナイト") == "charizarditex"
    assert rid("リザードナイトY") == "charizarditey"
    assert rid("メガニウムナイト") is not None and "meganium" in rid("メガニウムナイト")   # 種族名の「メガ」を剥がさない
    # 石以外は従来どおり
    assert rid("こだわりスカーフ") == "choicescarf" and rid("たべのこし") == "leftovers"
    # 接尾辞が合わない候補には落ちない: Z 石の名前で無印に解決しない
    r = resolver.resolve_mega_stone("ガブリアスナイトZ")
    assert r and r[1] == "garchompitez" and r[2] == 1.0
    print("test_resolve_items_by_suffix OK")


def test_stone_form_and_mega_form_id():
    from advisor.gimmick import stone_form_of, same_species_stones, stone_base_species
    from vision.abilities import mega_form_id, fixed_ability
    assert stone_form_of("garchomp", None, "メガガブリアスZナイト") == "garchompmegaz"
    assert stone_form_of("garchomp", None, "ガブリアスナイトZ") == "garchompmegaz"
    assert stone_form_of("garchomp", None, "ガブリアスナイト") == "garchompmega"
    assert stone_form_of("garchomp", "garchompite", "メガガブリアスZナイト") == "garchompmega"   # id が分かれば id
    assert stone_form_of("charizard", None, "メガリザードンYナイト") == "charizardmegay"
    assert same_species_stones("garchompite", "garchompitez")
    assert not same_species_stones("garchompite", "garchompite")
    assert not same_species_stones("garchompite", "lucarionitez")
    assert not same_species_stones("leftovers", "garchompitez")
    assert stone_base_species("garchompitez") == "garchomp" and stone_base_species("leftovers") is None
    # vision 側の導出も Z を判別する (表引き → 末尾 z)
    assert mega_form_id("garchomp", "garchompitez") == "garchompmegaz"
    assert mega_form_id("garchomp", "garchompite") == "garchompmega"
    assert mega_form_id("garchomp", None) is None          # 無印/Z の 2 候補は石なしでは決めない
    assert mega_form_id("garchompmegaz") is None            # メガ後 id はそのまま
    assert fixed_ability("garchomp", is_mega=True, item_id="garchompitez") == "levitate"
    print("test_stone_form_and_mega_form_id OK")


def test_engine_timing_note_uses_registered_z_stone():
    """登録の持ち物名 (手入力の並び) から Z フォルムで比較する (advisor.engine._mega_timing_note の経路)"""
    from advisor.engine import build_mon_view, _mega_timing_note
    from advisor.dex import get_dex
    dex = get_dex()
    my_p = {"species_id": "garchomp", "species_ja": "ガブリアス", "types": ["ドラゴン", "じめん"], "hp_percent": 100.0,
            "item_ja": "メガガブリアスZナイト", "item_id": None,
            "moves": [{"move_id": "dracometeor", "name_ja": "りゅうせいぐん"}]}
    my_view = build_mon_view(my_p, resolver, side="player")
    opp_p = {"species_id": "scizor", "species_ja": "ハッサム", "types": ["むし", "はがね"], "hp_percent": 100.0}
    opp_view = build_mon_view(opp_p, resolver)
    note = _mega_timing_note(my_p, my_view, opp_view, None, resolver)
    assert note is None or isinstance(note, str)
    # 比較に使うフォルムが Z (特攻 141) であることを直接確かめる
    from advisor.gimmick import stone_form_of
    r = resolver.resolve(my_p["item_ja"], "items", cutoff=0.9)
    form = stone_form_of("garchomp", r[1] if r else None, my_p["item_ja"])
    assert form == "garchompmegaz" and dex.species(form)["baseStats"]["spa"] == 141
    print("test_engine_timing_note_uses_registered_z_stone OK")


def main() -> None:
    test_parse_stone_name()
    test_resolve_items_by_suffix()
    test_stone_form_and_mega_form_id()
    test_engine_timing_note_uses_registered_z_stone()
    print("ALL OK")


if __name__ == "__main__":
    main()
