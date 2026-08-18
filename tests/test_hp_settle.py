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


def test_zero_read_faint_corroboration():
    """0/xxx 読みのひんし裏付け (2026-08-18 欠陥#6)。

    - faintイベントがあれば1回で受理 (従来どおり)
    - faintイベントを取り逃しても、安定した連続ゼロ読みで受理
      (実測: ミミッキュの「たおれた」が未イベント化しHP43%が固着した)
    - 1〜2回のゼロ読みでは受理しない (2026-08-04 の偽ひんし対策を維持)
    - 非ゼロ読みでカウンタはリセットされる
    """
    from vision.extractors import ZERO_READ_FAINT_COUNT, _accept_zero_hp_read

    # faint裏付けあり → 即受理
    state = BattleStateV2()
    state.last_faint = {"side": "player", "ts": time.time()}
    mon = PokemonState(species_ja="ミミッキュ", species_id="mimikyu")
    assert _accept_zero_hp_read(state, mon) is True

    # faint裏付けなし → 規定回数までは拒否、達したら受理
    state2 = BattleStateV2()
    mon2 = PokemonState(species_ja="ミミッキュ", species_id="mimikyu")
    results = [_accept_zero_hp_read(state2, mon2)
               for _ in range(ZERO_READ_FAINT_COUNT)]
    assert results[:-1] == [False] * (ZERO_READ_FAINT_COUNT - 1), results
    assert results[-1] is True, results

    # 非ゼロ読みが挟まればリセット (呼び出し側が0に戻す想定の検証)
    mon3 = PokemonState(species_ja="サザンドラ", species_id="hydreigon")
    state3 = BattleStateV2()
    assert _accept_zero_hp_read(state3, mon3) is False
    mon3._zero_read_count = 0   # 非ゼロ読み時に呼び出し側が行うリセット
    assert _accept_zero_hp_read(state3, mon3) is False
    print("test_zero_read_faint_corroboration OK")


if __name__ == "__main__":
    test_drain_animation_not_committed()
    test_normal_update_still_works()
    test_zero_read_faint_corroboration()
    print("\nALL OK")
