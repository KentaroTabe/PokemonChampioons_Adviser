"""poke_env バトル→探索エンジンブリッジ (search_expert) の検証。

poke_env の実バトルはサーバーが必要なため、バトル状態をduck-typingの
モックで再現してブリッジの変換と行動選択を検証する。

使い方: python -m tests.test_search_expert
"""
from types import SimpleNamespace

from advisor.dex import get_dex
from champions_agent.env.search_expert import (
    _mon_view, _opp_move_pool, decide,
)


def _t(name: str):
    return SimpleNamespace(name=name.upper())


def _mock_mon(species: str, types: list, moves: list,
              hp: float = 1.0, status=None, fainted: bool = False):
    sp = get_dex().species(species)
    return SimpleNamespace(
        species=species,
        types=[_t(t) for t in types],
        base_stats=dict(sp["baseStats"]),
        current_hp_fraction=hp,
        status=status,
        boosts={},
        ability=None,
        item=None,
        level=50,
        moves={m: SimpleNamespace(id=m) for m in moves},
        fainted=fainted,
    )


def _mock_battle(active, opp_active, team=None, opp_team=None,
                 available_moves=None, available_switches=None):
    team = team or [active]
    opp_team = opp_team or [opp_active]
    return SimpleNamespace(
        active_pokemon=active,
        opponent_active_pokemon=opp_active,
        team={f"p1: {i}": p for i, p in enumerate(team)},
        opponent_team={f"p2: {i}": p for i, p in enumerate(opp_team)},
        available_moves=(available_moves if available_moves is not None
                         else list(active.moves.values())),
        available_switches=(available_switches if available_switches is not None
                            else [p for p in team
                                  if p is not active and not p.fainted]),
        can_mega_evolve=False,
        side_conditions={},
        opponent_side_conditions={},
        weather={},
        fields={},
    )


def test_mon_view_conversion():
    mon = _mock_mon("garchomp", ["dragon", "ground"], ["earthquake"],
                    hp=0.6, status=_t("PAR"))
    v = _mon_view(mon)
    assert v.species_id == "garchomp"
    assert v.types == ["Dragon", "Ground"], v.types
    assert abs(v.hp_frac - 0.6) < 1e-6
    assert v.status == "paralysis", v.status
    assert v.base.get("atk") == 130, v.base
    print("test_mon_view_conversion OK")


def test_opp_pool_fallback():
    from champions_agent.env import search_expert

    # DBに実セットがある種族: 視認済み技(w=1.0) + 実セット補完(w=0.6)
    search_expert._meta_moves_cache = {
        "heatran": ["magmastorm", "earthpower", "flashcannon", "protect"]}
    opp = _mock_mon("heatran", ["fire", "steel"], ["magmastorm"])
    pool = _opp_move_pool(opp)
    assert pool[0] == ("magmastorm", 1.0), pool
    assert ("earthpower", 0.6) in pool, pool

    # DBに無い種族で技未視認 -> タイプ代表技で代用される
    search_expert._meta_moves_cache = {}
    opp2 = _mock_mon("heatran", ["fire", "steel"], [])
    ids = [m for m, _ in _opp_move_pool(opp2)]
    assert "flamethrower" in ids and "ironhead" in ids, ids

    search_expert._meta_moves_cache = None   # 実キャッシュに戻す
    print("test_opp_pool_fallback OK")


def test_decide_prefers_super_effective():
    # ガブリアス (じしん/りゅうのはどう) vs ヒードラン -> じしん (4倍) を選ぶ
    me = _mock_mon("garchomp", ["dragon", "ground"],
                   ["dragonpulse", "earthquake"])
    opp = _mock_mon("heatran", ["fire", "steel"], [])
    battle = _mock_battle(me, opp)
    d = decide(battle, depth=1)
    assert d is not None
    assert d["kind"] == "move", d
    assert d["move"].id == "earthquake", d
    assert d["action_index"] == 6 + 1, d   # movesの並びで2番目
    print(f"test_decide_prefers_super_effective OK: {d['move'].id} "
          f"(action={d['action_index']})")


def test_decide_forced_switch():
    # 技が選べない (ひんし後の入れ替え) -> ベンチから交代を選ぶ
    me = _mock_mon("garchomp", ["dragon", "ground"], ["earthquake"],
                   hp=0.0, fainted=True)
    bench1 = _mock_mon("rotomwash", ["electric", "water"], ["hydropump"])
    bench2 = _mock_mon("ferrothorn", ["grass", "steel"], ["powerwhip"])
    opp = _mock_mon("charizard", ["fire", "flying"], ["flamethrower"])
    battle = _mock_battle(me, opp, team=[me, bench1, bench2],
                          available_moves=[])
    d = decide(battle, depth=1)
    assert d is not None
    assert d["kind"] == "switch", d
    # 炎に強いロトム (index 1) がナットレイより優先されるはず
    assert d["pokemon"].species == "rotomwash", d["pokemon"].species
    assert d["action_index"] == 1, d
    print(f"test_decide_forced_switch OK: -> {d['pokemon'].species}")


def test_decision_speed():
    # 学習相手として使うため、フル盤面 (3v3・4技) でも1手が十分速いこと
    import time
    me = _mock_mon("garchomp", ["dragon", "ground"],
                   ["earthquake", "dragonclaw", "stoneedge", "swordsdance"])
    b1 = _mock_mon("rotomwash", ["electric", "water"],
                   ["hydropump", "voltswitch", "willowisp", "protect"])
    b2 = _mock_mon("ferrothorn", ["grass", "steel"],
                   ["powerwhip", "gyroball", "stealthrock", "leechseed"])
    opp = _mock_mon("charizard", ["fire", "flying"],
                    ["flamethrower", "airslash"])
    o1 = _mock_mon("heatran", ["fire", "steel"], [])
    o2 = _mock_mon("garganacl", ["rock"], [])
    battle = _mock_battle(me, opp, team=[me, b1, b2],
                          opp_team=[opp, o1, o2])
    n = 20
    t0 = time.time()
    for _ in range(n):
        d = decide(battle, depth=1)
    per = (time.time() - t0) / n * 1000
    assert d is not None
    assert per < 200, f"1手 {per:.0f}ms は学習相手として遅すぎる"
    t0 = time.time()
    for _ in range(5):
        decide(battle, depth=2)
    per2 = (time.time() - t0) / 5 * 1000
    print(f"test_decision_speed OK: depth=1 {per:.1f}ms / depth=2 {per2:.0f}ms")


def main() -> None:
    test_mon_view_conversion()
    test_opp_pool_fallback()
    test_decide_prefers_super_effective()
    test_decide_forced_switch()
    test_decision_speed()
    print("all OK")


if __name__ == "__main__":
    main()
