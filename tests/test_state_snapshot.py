"""対戦状態スナップショットの保存・復元テスト。

    scripts/run_test.sh test_state_snapshot
"""
from __future__ import annotations

from vision.state import BattleStateV2, PokemonState, MoveSlot


def test_restore_roundtrip():
    st = BattleStateV2()
    st.opponent.party = [
        PokemonState(species_ja="ガブリアス", species_id="garchomp",
                     types=["ドラゴン", "じめん"], hp_percent=70.0,
                     revealed_moves=["じしん"], aliases=["カフリアス"]),
        PokemonState(types=["みず", "あく"]),
    ]
    st.opponent.switch_to(0)
    st.player.party = [
        PokemonState(species_ja="ラグラージ", species_id="swampert",
                     is_picked=True, pick_order=1,
                     moves=[MoveSlot(name_ja="じしん", move_id="earthquake",
                                     pp=14, max_pp=16)]),
    ]
    st.player.switch_to(0)
    st.player.stealth_rock = True
    st.field.weather = "rain"
    st.field.weather_turns = 3
    st.turn = 7
    st.mega_used = {"player": True, "opponent": False}
    st.protect_streak = {"player": 2, "opponent": 0}
    st.battle_active = True

    snap = st.to_dict()

    fresh = BattleStateV2()
    fresh.restore_from_dict(snap)
    opp0 = fresh.opponent.party[0]
    assert opp0.species_ja == "ガブリアス" and opp0.hp_percent == 70.0
    assert opp0.revealed_moves == ["じしん"]
    assert opp0.aliases == ["カフリアス"]          # 個体名キャッシュも復元
    assert fresh.opponent.party[1].types == ["みず", "あく"]
    assert fresh.opponent.active_index == 0
    me = fresh.player.party[0]
    assert me.is_picked and me.pick_order == 1
    assert me.moves[0].move_id == "earthquake" and me.moves[0].pp == 14
    assert fresh.player.stealth_rock
    assert fresh.field.weather == "rain" and fresh.field.weather_turns == 3
    assert fresh.turn == 7
    assert fresh.mega_used["player"] is True
    assert fresh.protect_streak["player"] == 2
    assert fresh.battle_active
    print("test_restore_roundtrip OK")


if __name__ == "__main__":
    test_restore_roundtrip()
    print("\nALL OK")
