"""フォルム違いの基本種からメガシンカした個体 (フラエッテ えいえんのはな → メガフラエッテ) の登録参照のテスト (2026-10-09)。

予備確認 (p1_prelim_20261009_2217) で、送った個体 floetteeternal @ floettite がメガ後 floettemega になると、
engine.registration_names は基本種を gimmick._base_of で floette (通常のフラエッテ) に戻し、シムの登録 (送った本文の名前
「フラエッテ(えいえんのはな)」) に当たらず既定の攻撃系 252・性格なし・技なしで参照されていた。対応の確認 (text_set_of) も
本文の名前の前方一致だけで引くため、送った型が見つからず (None) 不一致に数えていた。

修正: register_team_text がメガ石を持つ個体をメガ後の形態の日本語名 (石 → メガ形態 id → 日本語名) でも登録し、
text_set_of は前方一致しないメガ後の形態をその石を持つ個体に当てる。

同じ予備確認の残り 1 戦: ばけのかわが剥がれたミミッキュ (mimikyubusted) も登録「ミミッキュ」に当たらなかった。戦闘中の形態変化を
変化前の種に戻す表 (advisor/data/battle_forms.json、tools.build_battle_forms) を作り、registration_names の最後に変化前の種の
日本語名を足す。地方フォルムは表に入れない (アローラライチュウは通常ライチュウの登録に当たらない)。

登録ファイル (config/my_team.json) と Showdown の対戦には依存しない (登録参照を環境変数で無効化し、シムの登録だけを使う)。

    python -m tests.test_mega_registration_forms
"""
from __future__ import annotations

import os
from contextlib import contextmanager

from advisor import engine as ENG
from advisor.engine import OFFENSIVE_EV, build_mon_view
from champions_agent import config as C
from champions_agent.env import advisor_player as AP
from vision.normalize import NameResolver

resolver = NameResolver()

FLOETTE_TEXT = ("Floette-Eternal @ Floettite\nLevel: 50\nAbility: Flower Veil\nEVs: 32 HP / 2 SpD / 32 SpA\n"
                "Modest Nature\n- Moonblast\n- Light of Ruin\n- Calm Mind\n- Protect\n\n"
                "Garchomp @ Choice Scarf\nLevel: 50\nAbility: Rough Skin\nEVs: 2 HP / 32 Atk / 32 Spe\nJolly Nature\n"
                "- Earthquake\n- Outrage")
ETERNAL_JA = "フラエッテ(えいえんのはな)"
MEGA_JA = "メガフラエッテ"


@contextmanager
def _sim_registration():
    """シムの登録だけで引く (本体の config/my_team.json の登録参照を無効化し、シムの登録を前後で空にする)"""
    prev = os.environ.get(C.MY_TEAM_DISABLED_ENV)
    os.environ[C.MY_TEAM_DISABLED_ENV] = "1"
    AP._SIM_BUILDS.clear()
    AP._SIM_MOVES.clear()
    try:
        yield
    finally:
        AP._SIM_BUILDS.clear()
        AP._SIM_MOVES.clear()
        if prev is None:
            os.environ.pop(C.MY_TEAM_DISABLED_ENV, None)
        else:
            os.environ[C.MY_TEAM_DISABLED_ENV] = prev


@contextmanager
def _no_battle_forms():
    """戦闘中の形態の表 (engine.battle_form_source) を空にする (修正前の登録参照の再現)"""
    orig = ENG.battle_form_source
    ENG.battle_form_source = lambda sid: None
    try:
        yield
    finally:
        ENG.battle_form_source = orig


def _mega_floette() -> dict:
    """メガ後のフラエッテ (シム: poke-env の species = floettemega → advisor_player._mon_entry の日本語名 メガフラエッテ)"""
    return {"species_id": "floettemega", "species_ja": MEGA_JA, "is_mega": True, "item_id": "floettite",
            "hp_percent": 100.0, "types": []}


def test_mega_alias_from_stone():
    """石 → メガ形態 → 日本語名 の別名。その種の石でなければ別名を作らない"""
    assert AP._mega_stone_form("floetteeternal", "Floettite") == "floettemega"     # フォルム違いの基本種
    assert AP._mega_alias_ja("floetteeternal", "Floettite") == MEGA_JA
    assert AP._mega_stone_form("lopunny", "Lopunnite") == "lopunnymega"
    assert AP._mega_stone_form("charizard", "Charizardite X") == "charizardmegax"
    assert AP._mega_stone_form("garchomp", "Floettite") is None                    # 別の種の石
    assert AP._mega_stone_form("garchomp", "Choice Scarf") is None                 # 石でない
    assert AP._mega_stone_form("garchomp", "") is None and AP._mega_stone_form("garchomp", None) is None
    # 基本種の日本語名が本文の名前と同じなら (登録参照が基本種の名前で当たる) 別名は作らない (従来の登録のまま)
    assert AP._mega_alias_ja("lopunny", "Lopunnite") is None
    assert AP._mega_alias_ja("charizard", "Charizardite X") is None
    print("test_mega_alias_from_stone OK")


def test_register_team_text_registers_mega_alias():
    """えいえんのはな + フラエッテナイト: 本文の名前とメガ後の名前の両方で同じ型・技を引ける。戻り値は本文の個体の名前だけ"""
    with _sim_registration():
        out = AP.register_team_text(FLOETTE_TEXT, resolver)
        assert set(out) == {ETERNAL_JA, "ガブリアス"}, set(out)
        assert AP._SIM_BUILDS[MEGA_JA] is AP._SIM_BUILDS[ETERNAL_JA]
        assert AP._SIM_MOVES[MEGA_JA] == AP._SIM_MOVES[ETERNAL_JA] and len(AP._SIM_MOVES[MEGA_JA]) == 4
        assert "メガガブリアス" not in AP._SIM_BUILDS                               # 石を持たない個体は別名なし
        # 基本種の名前で引ける個体 (ミミロップ) は別名を足さない
        out2 = AP.register_team_text("Lopunny @ Lopunnite\nEVs: 32 HP / 32 Spe\nJolly Nature\n- Fake Out", resolver)
        assert set(out2) == {"ミミロップ"} and set(AP._SIM_BUILDS) == {"ミミロップ"}, set(AP._SIM_BUILDS)
    print("test_register_team_text_registers_mega_alias OK")


def test_mega_floette_referenced_with_registered_set():
    """メガ後の個体が登録の配分・性格・技で参照され、種族値はメガのまま。対応の確認も一致"""
    from advisor.dex import get_dex
    with _sim_registration():
        AP.register_team_text(FLOETTE_TEXT, resolver)
        p = _mega_floette()
        assert MEGA_JA in ENG.registration_names(p)
        v = build_mon_view(p, resolver, side="player")
        assert v.ev == {"hp": 252, "atk": 0, "def": 0, "spa": 252, "spd": 16, "spe": 0}, v.ev
        assert v.nature == {"spa": 1.1, "atk": 0.9}, v.nature
        assert v.species_id == "floettemega" and v.base == get_dex().species("floettemega")["baseStats"]
        mv = sorted(m for _ja, m in ENG.registered_moves_of_mon(p, resolver))
        assert mv == ["calmmind", "lightofruin", "moonblast", "protect"], mv
        # 送った型 (本文の Floette-Eternal の個体) を引ける
        exp = AP.text_set_of(FLOETTE_TEXT, "floettemega")
        assert exp is not None and exp["item"] == "floettite" and exp["ev"] == {"hp": 252, "spa": 252, "spd": 16}, exp
        corr: dict = {}
        assert AP.check_reference(corr, p, FLOETTE_TEXT, resolver) is True
        assert corr["ref_checked"] == 1 and "ref_mismatch" not in corr
        # 別名が無くても、戦闘中の形態の表 (Showdown の battleOnly: floettemega → floetteeternal) の名前で引ける (二重の対応)
        AP._SIM_BUILDS.pop(MEGA_JA)
        AP._SIM_MOVES.pop(MEGA_JA)
        assert ENG.registration_names(p)[-1] == ETERNAL_JA
        assert build_mon_view(p, resolver, side="player").ev == v.ev
        # 別名も表も無いとき (修正前) は既定の攻撃系 252・性格なし・技なしで参照される (再現)
        with _no_battle_forms():
            old = build_mon_view(p, resolver, side="player")
            assert old.ev == OFFENSIVE_EV and old.nature == {}, old.ev
            assert ENG.registered_moves_of_mon(p, resolver) == []
            corr2: dict = {}
            assert AP.check_reference(corr2, p, FLOETTE_TEXT, resolver) is False and corr2["ref_mismatch"] == 1
    print("test_mega_floette_referenced_with_registered_set OK")


MIMIKYU_TEXT = ("Mimikyu @ Life Orb\nLevel: 50\nAbility: Disguise\nEVs: 1 HP / 1 Def / 32 Atk / 32 Spe\nAdamant Nature\n"
                "- Play Rough\n- Shadow Claw\n- Shadow Sneak\n- Swords Dance\n\n"
                "Raichu @ Focus Sash\nLevel: 50\nEVs: 32 SpA / 32 Spe\nTimid Nature\n- Thunderbolt")


def test_busted_mimikyu_referenced_with_registered_set():
    """ばけのかわが剥がれたミミッキュ (シム: species = mimikyubusted) が、登録「ミミッキュ」の配分・性格・技で参照される"""
    p = {"species_id": "mimikyubusted", "species_ja": "mimikyubusted", "is_mega": False, "item_id": "lifeorb",
         "hp_percent": 100.0, "types": []}
    assert ENG.battle_form_source("mimikyubusted") == "mimikyu"
    assert ENG.battle_form_source("aegislashblade") == "aegislash"
    with _sim_registration():
        AP.register_team_text(MIMIKYU_TEXT, resolver)
        assert ENG.registration_names(p) == ["mimikyubusted", "ミミッキュ"]
        v = build_mon_view(p, resolver, side="player")
        assert v.ev == {"hp": 8, "atk": 252, "def": 8, "spa": 0, "spd": 0, "spe": 252}, v.ev
        assert v.nature == {"atk": 1.1, "spa": 0.9} and v.species_id == "mimikyubusted"
        mv = sorted(m for _ja, m in ENG.registered_moves_of_mon(p, resolver))
        assert mv == ["playrough", "shadowclaw", "shadowsneak", "swordsdance"], mv
        corr: dict = {}
        assert AP.check_reference(corr, p, MIMIKYU_TEXT, resolver) is True
        with _no_battle_forms():                                                    # 表が無いとき (修正前) は既定の型
            assert build_mon_view(p, resolver, side="player").ev == OFFENSIVE_EV
    print("test_busted_mimikyu_referenced_with_registered_set OK")


def test_regional_form_not_mapped_to_base():
    """地方フォルムは表に入れない: アローラライチュウは通常ライチュウの登録に当たらない (既定の型のまま)"""
    p = {"species_id": "raichualola", "species_ja": "アローラライチュウ", "is_mega": False, "item_id": None,
         "hp_percent": 100.0, "types": []}
    assert ENG.battle_form_source("raichualola") is None
    with _sim_registration():
        AP.register_team_text(MIMIKYU_TEXT, resolver)                             # 通常ライチュウを登録
        assert "ライチュウ" in AP._SIM_BUILDS
        assert ENG.registration_names(p) == ["アローラライチュウ"]
        assert build_mon_view(p, resolver, side="player").ev == OFFENSIVE_EV
    # 表に地方フォルムをまたぐ行が無い (生成の規則)
    import json
    from tools.build_battle_forms import OUT_PATH, REGIONAL_SUFFIXES, build_table, battle_form_sources_from_text
    forms = json.loads(OUT_PATH.read_text(encoding="utf-8"))["forms"]
    doc = json.loads(OUT_PATH.read_text(encoding="utf-8"))
    for f, b in forms.items():
        assert {s for s in REGIONAL_SUFFIXES if s in f} == {s for s in REGIONAL_SUFFIXES if s in b}, (f, b)
    # changesFrom 由来の行が無い (戦闘外の形態変化: 登録の無いヒートロトムが「ロトム」の登録を引かない)
    assert doc["_source"]["include_changes_from"] is False
    for f in ("rotomheat", "rotomwash", "landorustherian", "giratinaorigin", "keldeoresolute", "charizardgmax", "pikachugmax"):
        assert f not in forms, f
    for f, b in (("floettemega", "floetteeternal"), ("mimikyubusted", "mimikyu"), ("aegislashblade", "aegislash")):
        assert forms.get(f) == b, (f, forms.get(f))
    # 生成の解析 (合成の pokedex.ts): battleOnly 優先・複数は先頭・changesFrom・図鑑に無い / 地方をまたぐ行は落とす
    text = ('\tmimikyubusted: {\n\t\tname: "Mimikyu-Busted",\n\t\tbattleOnly: "Mimikyu",\n\t},\n'
            '\tzygardecomplete: {\n\t\tbattleOnly: ["Zygarde", "Zygarde-10%"],\n\t},\n'
            '\trotomheat: {\n\t\tchangesFrom: "Rotom",\n\t},\n'
            '\tfoogalarzen: {\n\t\tbattleOnly: "Foo",\n\t},\n'
            '\traichu: {\n\t\tname: "Raichu",\n\t},\n')
    src = battle_form_sources_from_text(text)                                     # 既定: battleOnly だけ
    assert src == {"mimikyubusted": "mimikyu", "zygardecomplete": "zygarde", "foogalarzen": "foo"}, src
    src_cf = battle_form_sources_from_text(text, include_changes_from=True)
    assert src_cf == dict(src, rotomheat="rotom"), src_cf
    ids = {"mimikyubusted", "mimikyu", "zygardecomplete", "zygarde", "rotomheat", "rotom", "foogalarzen", "foo"}
    assert build_table(src, ids) == {"mimikyubusted": "mimikyu", "zygardecomplete": "zygarde"}
    assert build_table(src, ids - {"zygarde"}) == {"mimikyubusted": "mimikyu"}
    print("test_regional_form_not_mapped_to_base OK")


def test_text_set_of_stone_match_scope():
    """石による照合は前方一致しないメガ後の形態だけ。通常名の前方一致 (lopunnymega) と、メガでない種はこれまでどおり"""
    text = ("Lopunny @ Lopunnite\nEVs: 252 HP / 252 Spe\nJolly Nature\n- Fake Out\n\n" + FLOETTE_TEXT)
    assert AP.text_set_of(text, "lopunnymega")["item"] == "lopunnite"
    assert AP.text_set_of(text, "floetteeternal")["item"] == "floettite"
    assert AP.text_set_of(text, "garchomp")["item"] == "choicescarf"
    assert AP.text_set_of(text, "charizardmegax") is None                          # 石を持つ個体が無い
    assert AP.text_set_of("Garchomp @ Floettite\nEVs: 32 Atk\n- Outrage", "floettemega") is None   # 別の種の石は当てない
    print("test_text_set_of_stone_match_scope OK")


def main():
    test_mega_alias_from_stone()
    test_register_team_text_registers_mega_alias()
    test_mega_floette_referenced_with_registered_set()
    test_text_set_of_stone_match_scope()
    test_busted_mimikyu_referenced_with_registered_set()
    test_regional_form_not_mapped_to_base()
    print("ALL OK")


if __name__ == "__main__":
    main()
