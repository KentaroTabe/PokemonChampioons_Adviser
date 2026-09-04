"""advisor-as-player (助言エンジンをプレイヤー化) のテスト — poke-env 不要の純粋部分。

    scripts/run_test.sh test_advisor_player

Battle をダックタイピングの偽オブジェクトで与え、状態辞書への変換と
助言→行動の写像、チームテキストからの型登録を検証する。
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from champions_agent.env.advisor_player import (
    _SIM_BUILDS, battle_to_state, choose_from_advice, register_team_text)


def _move(mid, pp=10):
    return NS(id=mid, current_pp=pp, max_pp=pp)


def _mon(species, hp=1.0, fainted=False, moves=(), item="leftovers",
         ability="levitate", status=None, boosts=None, max_hp=200):
    return NS(species=species, current_hp_fraction=hp, fainted=fainted,
              moves={m: _move(m) for m in moves}, item=item, ability=ability,
              status=status, boosts=boosts or {}, max_hp=max_hp,
              current_hp=int(max_hp * hp), types=[])


def _battle():
    own = [_mon("garchomp", 0.8, moves=("earthquake", "scaleshot")),
           _mon("primarina", 1.0, moves=("moonblast",)),
           _mon("mimikyu", 0.0, fainted=True, moves=("playrough",))]
    opp = [_mon("archaludon", 0.6, moves=("dracometeor",), item="unknown_item")]
    return NS(active_pokemon=own[0], opponent_active_pokemon=opp[0],
              team={p.species: p for p in own},
              opponent_team={p.species: p for p in opp},
              available_moves=[_move("earthquake"), _move("scaleshot")],
              available_switches=[own[1]], can_mega_evolve=False,
              weather={}, fields={}, side_conditions={}, turn=4,
              opponent_side_conditions={"STEALTH_ROCK": 1})


def test_battle_to_state_schema():
    st = battle_to_state(_battle(), resolver=None)
    me, opp = st["player"], st["opponent"]
    assert st["turn"] == 4 and st["scene"] == "command"
    assert me["active_index"] == 0 and opp["active_index"] == 0
    assert [p["species_id"] for p in me["party"]] == ["garchomp", "primarina", "mimikyu"]
    assert me["party"][2]["status"] == "fainted"
    assert all(p["is_picked"] for p in me["party"])          # 3体が選出済み
    assert [m["move_id"] for m in me["party"][0]["moves"]] == ["earthquake", "scaleshot"]
    assert me["party"][0]["hp_max"] == 200 and me["party"][0]["hp_current"] == 160
    assert opp["party"][0]["item_id"] is None                 # unknown_item は不明扱い
    assert opp["party"][0]["revealed_moves"] == ["dracometeor"]
    assert opp["hazards"]["stealth_rock"] is True and me["hazards"]["stealth_rock"] is False
    assert me["remaining"] == 2
    print("test_battle_to_state_schema OK")


def test_choose_from_advice_maps_best_then_fallbacks():
    b = _battle()
    adv = {"ok": True, "best": {"kind": "move", "id": "scaleshot"},
           "actions": [{"kind": "move", "id": "scaleshot"},
                       {"kind": "switch", "id": "primarina"}]}
    d = choose_from_advice(b, adv)
    assert d["kind"] == "move" and d["move"].id == "scaleshot" and d["mega"] is False
    # best が選べない技なら次の候補 (交代) へ
    adv2 = {"ok": True, "best": {"kind": "move", "id": "dracometeor"},
            "actions": [{"kind": "switch", "id": "primarina"}]}
    d2 = choose_from_advice(b, adv2)
    assert d2["kind"] == "switch" and d2["pokemon"].species == "primarina"
    assert choose_from_advice(b, {"ok": False}) is None
    print("test_choose_from_advice_maps_best_then_fallbacks OK")


def test_register_team_text_points_to_evs():
    _SIM_BUILDS.clear()
    text = ("Garchomp @ focussash\nLevel: 50\nAbility: roughskin\n"
            "EVs: 2 HP / 32 Atk / 32 Spe\nJolly Nature\n- earthquake\n\n"
            "Primarina @ leftovers\nLevel: 50\nEVs: 32 HP / 32 SpA / 2 Spe\n"
            "Modest Nature\n- moonblast")
    builds = register_team_text(text)
    assert set(builds) == {"ガブリアス", "アシレーヌ"}
    g = builds["ガブリアス"]
    assert g["ev"]["atk"] == 252 and g["ev"]["spe"] == 252 and g["ev"]["hp"] == 16
    assert g["nature"].get("spe") == 1.1 and g["nature"].get("spa") == 0.9
    import advisor.my_team as mt
    assert mt.get_my_build("ガブリアス") is g       # フックが効いている
    _SIM_BUILDS.clear()
    print("test_register_team_text_points_to_evs OK")


if __name__ == "__main__":
    test_battle_to_state_schema()
    test_choose_from_advice_maps_best_then_fallbacks()
    test_register_team_text_points_to_evs()
