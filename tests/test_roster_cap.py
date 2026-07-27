"""相手ロスターの6匹上限とひんし後HP矛盾ガードの検証。

監査2026-07-24で発見:
- ラフレシアと視覚誤同定した枠の実体がフシギバナで、切り出しメッセージの
  appendによりルール上あり得ない7匹構成になった
- 「たおれた!」確定後のリザードンにHP34%が記録された (蘇生は存在しない)

使い方: scripts/run_test.sh test_roster_cap
"""
from vision.state import BattleStateV2, PokemonState


def _full_side():
    st = BattleStateV2()
    side = st.opponent
    names = [("メタグロス", "metagross", ["はがね", "エスパー"]),
             ("ラフレシア", "vileplume", ["くさ", "どく"]),   # 実体はフシギバナ
             ("アシレーヌ", "primarina", ["みず", "フェアリー"]),
             ("ラウドボーン", "skeledirge", ["ほのお", "ゴースト"]),
             ("ガブリアス", "garchomp", ["ドラゴン", "じめん"]),
             ("ムクホーク", "staraptor", ["ノーマル", "ひこう"])]
    for ja, sid, types in names:
        side.party.append(PokemonState(species_ja=ja, species_id=sid,
                                       types=list(types)))
    side.active_index = 0
    return st, side


def test_full_roster_replaces_misidentified():
    st, side = _full_side()
    mon = side.switch_to_species("フシギバナ", "venusaur")
    assert len(side.party) == 6, f"7匹化: {[p.species_ja for p in side.party]}"
    assert mon.species_ja == "フシギバナ"
    # 同タイプ (くさ/どく) の視覚同定枠 (ラフレシア) が置き換わる
    names = [p.species_ja for p in side.party]
    assert "ラフレシア" not in names, names
    assert side.active().species_ja == "フシギバナ"
    print("test_full_roster_replaces_misidentified OK:", names)


def test_revealed_moves_slot_not_replaced():
    st, side = _full_side()
    side.party[1].revealed_moves = ["gigadrain"]   # 技判明枠は保護される
    side.switch_to_species("フシギバナ", "venusaur")
    assert len(side.party) == 6
    names = [p.species_ja for p in side.party]
    assert "ラフレシア" in names, names           # 保護された
    assert "フシギバナ" in names, names           # 別の枠が置き換わった
    print("test_revealed_moves_slot_not_replaced OK:", names)


def test_known_species_still_matches():
    st, side = _full_side()
    mon = side.switch_to_species("ガブリアス", "garchomp")
    assert len(side.party) == 6
    assert side.active().species_ja == "ガブリアス"
    print("test_known_species_still_matches OK")


def test_fainted_hp_not_resurrected():
    from vision.extractors import _set_hp
    st = BattleStateV2()
    side = st.opponent
    mon = PokemonState(species_ja="リザードン", species_id="charizard")
    side.party.append(mon)
    mon.hp_percent = 0.0
    mon.status = "fainted"
    _set_hp(st, "opponent", mon, pct=34.0)
    assert mon.hp_percent == 0.0, mon.hp_percent
    assert mon.status == "fainted"
    print("test_fainted_hp_not_resurrected OK")


def main() -> None:
    test_full_roster_replaces_misidentified()
    test_revealed_moves_slot_not_replaced()
    test_known_species_still_matches()
    test_fainted_hp_not_resurrected()
    print("ALL OK")


if __name__ == "__main__":
    main()
