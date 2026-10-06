"""メガシンカ文言の解釈と、名前で帰属できた相手を場の個体にする処理のテスト。

2026-09-29 第17回接続テスト:
- 「ガブリアスはメガガブリアスZにメガシンカした」の末尾 Z が OCR で落ち、Z 石の個体が無印のメガ (別の種族値・タイプ・
  特性) になった → 石が分かっていれば石でフォルムを決める
- ニックネームの相手 (まちょぐろす = メタグロス) は HUD 名から種族を引けず、7 ターン相手不明のまま助言が計算された →
  「相手の<名前>の<技>」「…はメガ<種族>にメガシンカした」で帰属できた個体を場の個体にする

    scripts/run_test.sh test_mega_evolve_attribution
"""
from __future__ import annotations

from vision.events import EventParser
from vision.normalize import NameResolver
from vision.state import BattleStateV2, PokemonState

resolver = NameResolver()


def _state(opp_full: bool = False):
    st = BattleStateV2()
    st.player.party = [PokemonState(species_ja="ガブリアス", species_id="garchomp", types=["ドラゴン", "じめん"],
                                    item_ja="ガブリアスナイトZ", item_id="garchompitez")]
    st.player.active_index = 0
    rows = [("エースバーン", "cinderace", ["ほのお"]), ("メタグロス", "metagross", ["はがね", "エスパー"])]
    if opp_full:
        rows += [("ガブリアス", "garchomp", ["ドラゴン", "じめん"]), ("アローラキュウコン", "ninetalesalola", ["こおり", "フェアリー"]),
                 (None, None, ["くさ"]), (None, None, ["かくとう", "はがね"])]
    st.opponent.party = [PokemonState(species_ja=ja, species_id=sid, types=list(t)) for ja, sid, t in rows]
    st.opponent.active_index = None
    st.battle_active = True
    return st, EventParser(st, resolver)


def test_player_mega_uses_stone_form():
    st, p = _state()
    fired = p.parse("ガプリアスはメガガプリアスにメガシンカした")   # OCR: 末尾の Z が落ち、濁点も崩れる
    assert "mega_evolve" in fired, fired
    me = st.player.party[0]
    assert me.species_id == "garchompmegaz", me.species_id
    assert me.is_mega and me.species_ja == "ガブリアス" and not me.species_guess
    assert "メガガブリアス" in me.aliases
    assert me.ability_id == "levitate", me.ability_id          # Z フォルムの固定特性
    assert st.mega_used["player"] and not st.mega_used["opponent"]
    print("test_player_mega_uses_stone_form OK")


def test_text_form_used_when_stone_unknown():
    st, p = _state()
    st.player.party[0].item_id = None
    st.player.party[0].item_ja = None
    p.parse("ガブリアスはメガガブリアスZにメガシンカした")
    assert st.player.party[0].species_id == "garchompmegaz"
    st2, p2 = _state()
    st2.player.party[0].item_id = None
    p2.parse("ガブリアスはメガガブリアスにメガシンカした")
    assert st2.player.party[0].species_id == "garchompmega"          # 文言どおり (石が不明なら無印)
    print("test_text_form_used_when_stone_unknown OK")


def test_stone_of_other_species_is_ignored():
    st, p = _state()
    st.player.party[0].item_id = "lucarionitez"      # 誤読の石は無視して文言を使う
    p.parse("ガブリアスはメガガブリアスZにメガシンカした")
    assert st.player.party[0].species_id == "garchompmegaz"
    print("test_stone_of_other_species_is_ignored OK")


def test_nicknamed_opponent_mega_links_roster_slot():
    """満枠 (6) の相手: HUD 名がニックネームで active が無い状態でも、メガ文言の種族名でロスターの枠を場の個体にする"""
    st, p = _state(opp_full=True)
    fired = p.parse("三相手のまちょぐろすはメガメタグロスにメガシンカした")
    assert "mega_evolve" in fired, fired
    opp = st.opponent.active()
    assert opp is not None and opp is st.opponent.party[1], st.opponent.active_index
    assert opp.species_id == "metagrossmega" and opp.is_mega and opp.species_ja == "メタグロス"
    assert st.mega_used["opponent"] and not st.mega_used["player"]
    assert len(st.opponent.party) == 6
    print("test_nicknamed_opponent_mega_links_roster_slot OK")


def test_nicknamed_opponent_mega_with_placeholder_then_link():
    """ロスターが 6 未満: 使い捨てでない placeholder が active になり、link_active_to_party が同種の枠へ寄せる"""
    from vision import extractors
    st, p = _state(opp_full=False)
    p.parse("三相手のまちょぐろすはメガメタグロスにメガシンカした")
    assert st.opponent.active() is not None and st.opponent.active().species_ja == "メタグロス"
    extractors.link_active_to_party(st, "opponent")
    assert len(st.opponent.party) == 2 and st.opponent.active() is st.opponent.party[1]
    assert st.opponent.party[1].is_mega and st.opponent.party[1].species_id == "metagrossmega"
    print("test_nicknamed_opponent_mega_with_placeholder_then_link OK")


def test_named_move_activates_unknown_active():
    """「相手の<ニックネーム>の<技>」: 名前 (HUD で別名として蓄積されたニックネーム) で帰属できた個体を場の個体にする"""
    st, p = _state(opp_full=True)
    st.opponent.party[1].aliases.append("まちょぐろす")   # extract_battle_hud が HUD 名から蓄積する別名
    fired = p.parse("相手のまちょぐろすのじしん")
    assert any(f.startswith("move_opponent_") for f in fired), fired
    assert st.opponent.active() is st.opponent.party[1], st.opponent.active_index
    # 既に場の個体が決まっているときは変えない
    st.opponent.switch_to(0)
    p.parse("相手のまちょぐろすのバレットパンチ")
    assert st.opponent.active() is st.opponent.party[0]
    print("test_named_move_activates_unknown_active OK")


def main() -> None:
    test_player_mega_uses_stone_form()
    test_text_form_used_when_stone_unknown()
    test_stone_of_other_species_is_ignored()
    test_nicknamed_opponent_mega_links_roster_slot()
    test_nicknamed_opponent_mega_with_placeholder_then_link()
    test_named_move_activates_unknown_active()
    print("ALL OK")


if __name__ == "__main__":
    main()
