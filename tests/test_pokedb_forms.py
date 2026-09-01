"""pokedbフォルム名解決のテスト (2026-09-02 フォルム丸めインシデント)。

    scripts/run_test.sh test_pokedb_forms

かつて _species_id が form 引数を無視して全フォルムをベース種に丸めていたため、
基本ロトム0構築の環境で「rotom 8.5%」と誤集計されていた。対戦上の実体が
異なるフォルムが専用IDへ写ることを検証する。DB・ネットワークは使わない。
"""
from __future__ import annotations

from champions_agent.data.sources.pokedb_opendata import _species_id


def test_rotom_appliance_forms_resolve():
    """家電ロトムは専用IDへ (フォルム欄が完全な種族名のパターン)"""
    assert _species_id("ロトム", "ウォッシュロトム") == "rotomwash"
    assert _species_id("ロトム", "ヒートロトム") == "rotomheat"
    assert _species_id("ロトム", "フロストロトム") == "rotomfrost"
    assert _species_id("ロトム", "カットロトム") == "rotommow"
    assert _species_id("ロトム", "スピンロトム") == "rotomfan"
    print("test_rotom_appliance_forms_resolve OK")


def test_base_rotom_stays_base():
    """フォルム欄が空ならベース種のまま"""
    assert _species_id("ロトム", "") == "rotom"
    assert _species_id("ロトム") == "rotom"
    print("test_base_rotom_stays_base OK")


def test_regional_suffix_forms():
    """『◯◯のすがた』はベースID+接尾辞へ"""
    assert _species_id("キュウコン", "アローラのすがた") == "ninetalesalola"
    assert _species_id("ヤドキング", "ガラルのすがた") == "slowkinggalar"
    assert _species_id("ダイケンキ", "ヒスイのすがた") == "samurotthisui"
    assert _species_id("ヌメルゴン", "ヒスイのすがた") == "goodrahisui"
    assert _species_id("ゾロアーク", "ヒスイのすがた") == "zoroarkhisui"
    print("test_regional_suffix_forms OK")


def test_species_specific_forms():
    """種族固有フォルム (複合キーが接尾辞規則より優先)"""
    assert _species_id("フラエッテ", "えいえんのはな") == "floetteeternal"
    assert _species_id("イダイトウ", "メスのすがた") == "basculegionf"
    assert (_species_id("ケンタロス", "パルデアのすがた・コンバットしゅ")
            == "taurospaldeacombat")
    print("test_species_specific_forms OK")


def test_cosmetic_and_default_forms_round_to_base():
    """見た目/デフォルトのフォルムはベース種に丸めて正しい"""
    assert _species_id("ミミッキュ", "ばけたすがた") == "mimikyu"
    assert _species_id("ギルガルド", "シールドフォルム") == "aegislash"
    assert _species_id("イダイトウ", "オスのすがた") == "basculegion"
    assert _species_id("イルカマン", "ナイーブフォルム") == "palafin"
    print("test_cosmetic_and_default_forms_round_to_base OK")


def test_unknown_form_rounds_to_base():
    """表に無いフォルムはベース種に丸める (安全側)"""
    assert _species_id("ビビヨン", "はなぞののもよう") == "vivillon"
    print("test_unknown_form_rounds_to_base OK")


def test_set_fallback_species():
    """型データの無いフォルムはベース種名義の型を参照する
    (cbdがフォルムを分けない種族への対応。相手プールの構築破棄を防ぐ)"""
    from champions_agent.data.sources.pokedb_opendata import set_fallback_species
    assert set_fallback_species("floetteeternal") == "floette"
    assert set_fallback_species("basculegionf") == "basculegion"
    assert set_fallback_species("rotomwash") is None   # 専用ページがある種は不要
    print("test_set_fallback_species OK")


if __name__ == "__main__":
    test_rotom_appliance_forms_resolve()
    test_base_rotom_stays_base()
    test_regional_suffix_forms()
    test_species_specific_forms()
    test_cosmetic_and_default_forms_round_to_base()
    test_unknown_form_rounds_to_base()
    test_set_fallback_species()
