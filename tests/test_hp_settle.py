"""HP確定の時間安定条件 (気絶演出の遷移値対策) のテスト。

    scripts/run_test.sh test_hp_settle
"""
from __future__ import annotations

import time

from vision.state import BattleStateV2, PokemonState
from vision.extractors import _set_hp


def _mon(state):
    state.opponent.party = [PokemonState(species_ja="ガブリアス",
                                         species_id="garchomp")]
    state.opponent.switch_to(0)
    return state.opponent.party[0]


def test_drain_animation_not_committed():
    # 気絶演出: 100 -> 72 -> 45 -> 18 -> 0 と高速に変わる遷移値は確定しない
    state = BattleStateV2()
    mon = _mon(state)
    _set_hp(state, "opponent", mon, pct=100.0)   # 初回は即反映
    assert mon.hp_percent == 100.0
    for v in (72.0, 72.5, 45.0, 45.3, 18.0):     # 連続する遷移値 (600ms未満)
        _set_hp(state, "opponent", mon, pct=v)
    assert mon.hp_percent == 100.0, mon.hp_percent   # 遷移中は据え置き

    # 静止値 (同値が600ms以上継続) は確定する
    _set_hp(state, "opponent", mon, pct=42.0)
    time.sleep(0.65)
    _set_hp(state, "opponent", mon, pct=42.0)
    assert mon.hp_percent == 42.0, mon.hp_percent
    print("test_drain_animation_not_committed OK")


def test_normal_update_still_works():
    # 通常のダメージ更新 (ターン間隔) は従来どおり2読み+600msで確定
    state = BattleStateV2()
    mon = _mon(state)
    _set_hp(state, "opponent", mon, pct=100.0)
    _set_hp(state, "opponent", mon, pct=64.0)
    time.sleep(0.65)
    _set_hp(state, "opponent", mon, pct=64.0)
    assert mon.hp_percent == 64.0, mon.hp_percent
    print("test_normal_update_still_works OK")


if __name__ == "__main__":
    test_drain_animation_not_committed()
    test_normal_update_still_works()
    print("\nALL OK")
