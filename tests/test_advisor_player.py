"""advisor-as-player (助言エンジンをプレイヤー化) のテスト — poke-env 不要の純粋部分。

    scripts/run_test.sh test_advisor_player

Battle をダックタイピングの偽オブジェクトで与え、状態辞書への変換と
助言→行動の写像、チームテキストからの型登録を検証する。
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from advisor.infer import species_ja_name
from champions_agent.env.advisor_player import (
    _SIM_BUILDS, battle_to_state, choose_from_advice, register_team_for_battle,
    register_team_text, team_matches_species, team_text_for_battle)


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


def _team_text(species: list) -> str:
    return "\n\n".join(f"{sp} @ leftovers\nLevel: 50\nEVs: 32 HP / 32 Atk / 2 Spe\nAdamant Nature\n- tackle"
                         for sp in species)


_TEAMS = [["Garchomp", "Primarina", "Mimikyu"],
          ["Gengar", "Scizor", "Gyarados"],
          ["Kingambit", "Dragapult", "Floette-Eternal"]]


class _PerBattleSource:
    """check_advisor_player._remembering_teambuilder を模した teambuilder (poke-env 不要): 出した順に本文を覚える"""

    def __init__(self, teams):
        self._teams = [_team_text(t) for t in teams]
        self.texts, self.last_text = [], None

    def yield_team(self):
        text = self._teams[len(self.texts) % len(self._teams)]
        self.texts.append(text)
        self.last_text = text
        return text

    def text_for_battle(self, k):
        return self.texts[k] if 0 <= k < len(self.texts) else None


class _LastTextOnly:
    """修正前の形 (last_text しか持たない teambuilder)"""

    def __init__(self, src):
        self.src = src

    @property
    def last_text(self):
        return self.src.last_text


def _ids(names):
    import re
    return [re.sub(r"[^a-z0-9]", "", n.lower()) for n in names]


def _ja(names):
    return {species_ja_name(s) or s for s in _ids(names)}


def test_registration_follows_battle_not_last_yield():
    """poke-env の順序 (対戦 k が作られた直後に対戦 k+1 の challenge を送り yield_team を呼ぶ) を模して、
    対戦 k の選出・行動の時点の登録が対戦 k のチームになることを確かめる。last_text だけだと 1 戦ずれる (修正前の再現)"""
    def simulate(wrap, stats):
        _SIM_BUILDS.clear()
        src = _PerBattleSource(_TEAMS)
        n, seen = len(_TEAMS), []
        src.yield_team()                               # 対戦 0 の challenge
        for k in range(n):
            # 対戦 k が作られた (semaphore 解放) → 最後の対戦でなければ対戦 k+1 の challenge を先に送る
            if k + 1 < n:
                src.yield_team()
            species = _ids(_TEAMS[k])                  # battle.team の種族 id (選出画面)
            register_team_for_battle(wrap(src), k, species, None, stats)
            seen.append(set(_SIM_BUILDS))
        return seen

    stats = {}
    seen_new = simulate(lambda src: src, stats)
    seen_old = simulate(_LastTextOnly, {})
    assert seen_new == [_ja(t) for t in _TEAMS], seen_new
    # 修正前の経路: 対戦 0 と 1 は次の対戦のチーム、最後の対戦だけ一致
    assert seen_old == [_ja(_TEAMS[1]), _ja(_TEAMS[2]), _ja(_TEAMS[2])], seen_old
    assert not any(k.startswith("team_reg_") for k in stats), stats
    _SIM_BUILDS.clear()
    print("test_registration_follows_battle_not_last_yield OK")


def test_team_text_for_battle_species_check_and_fallbacks():
    src = _PerBattleSource(_TEAMS)
    for _ in range(3):
        src.yield_team()
    t0, t1 = src.texts[0], src.texts[1]
    assert team_text_for_battle(src, 0, _ids(_TEAMS[0])) == (t0, "index")
    # k がずれていても (対戦の数え方が合わない) 種族の合う本文を k に近い順に探す
    assert team_text_for_battle(src, 0, _ids(_TEAMS[1])) == (t1, "species")
    # どれとも合わなければ k 番目を使い、決め方に印を残す
    assert team_text_for_battle(src, 0, ["pikachu"]) == (t0, "unverified")
    # 種族が分からなければ k 番目
    assert team_text_for_battle(src, 2, []) == (src.texts[2], "index")
    # text_for_battle を持たない teambuilder は従来どおり last_text
    assert team_text_for_battle(_LastTextOnly(src), 0, _ids(_TEAMS[0])) == (src.last_text, "last_text")
    assert team_text_for_battle(_PerBattleSource(_TEAMS), 0, []) == (None, "none")
    stats = {}
    register_team_for_battle(src, 0, ["pikachu"], None, stats)
    assert stats.get("team_reg_unverified") == 1 and stats["_registered"] == t0
    _SIM_BUILDS.clear()
    print("test_team_text_for_battle_species_check_and_fallbacks OK")


def test_team_matches_species_forms():
    text = _team_text(["Gyarados", "Aegislash", "Floette-Eternal"])
    assert team_matches_species(text, ["gyaradosmega", "aegislashblade", "floetteeternal"])  # メガ・フォルム違い
    assert team_matches_species(text, ["gyarados"])                                          # 選出後の一部だけでも合う
    assert not team_matches_species(text, ["gyarados", "garchomp"])
    assert not team_matches_species(text, []) and not team_matches_species("", ["gyarados"])
    print("test_team_matches_species_forms OK")


def test_constant_source_registers_same_team_every_battle():
    """登録チーム (--team-file、定数) の経路: どの対戦も同じ本文で登録する"""
    _SIM_BUILDS.clear()
    text = _team_text(_TEAMS[0])

    class _Const:
        texts = [text]
        last_text = text

        def text_for_battle(self, k):
            return text

    stats = {}
    for k in range(3):
        assert register_team_for_battle(_Const(), k, _ids(_TEAMS[0]), None, stats) == text
        assert set(_SIM_BUILDS) == _ja(_TEAMS[0])
    assert not any(k.startswith("team_reg_") for k in stats), stats
    _SIM_BUILDS.clear()
    print("test_constant_source_registers_same_team_every_battle OK")


if __name__ == "__main__":
    test_battle_to_state_schema()
    test_choose_from_advice_maps_best_then_fallbacks()
    test_register_team_text_points_to_evs()
    test_registration_follows_battle_not_last_yield()
    test_team_text_for_battle_species_check_and_fallbacks()
    test_team_matches_species_forms()
    test_constant_source_registers_same_team_every_battle()
