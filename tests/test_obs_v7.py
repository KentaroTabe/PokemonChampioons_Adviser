"""観測v7 + Set Encoder のテスト。

    python -m tests.test_obs_v7

- OBS_PARTSの合計がBATTLE_OBS_DIMと一致 (オフセット表と実装の同期)
- エンティティ分解が重複なく、グローバルと合わせて全次元を被覆
- v6互換: 先頭388がv6と同義 (プレフィックス不変の原則)
- Set Encoder の順伝播形状
"""
from __future__ import annotations

import numpy as np


def test_parts_table_sums_to_dim():
    from champions_agent.agent.spaces import (
        BATTLE_OBS_DIM, OBS_PARTS, obs_part_slices,
    )
    total = sum(d for _, d in OBS_PARTS)
    assert total == BATTLE_OBS_DIM == 420, (total, BATTLE_OBS_DIM)
    slices = obs_part_slices()
    assert slices["own_active"] == (0, 75)
    # v6の末尾 (ability) は386-388、v7のraceが388から始まる
    assert slices["ability"] == (384, 388), slices["ability"]
    assert slices["race"] == (388, 394), slices["race"]
    print(f"test_parts_table_sums_to_dim OK ({len(OBS_PARTS)}ブロック)")


def test_entity_groups_cover_all_dims():
    from champions_agent.agent.spaces import (
        BATTLE_OBS_DIM, entity_index_groups,
    )
    groups, global_idx = entity_index_groups()
    seen: list = []
    for name, idx in groups.items():
        assert idx, name
        seen.extend(idx)
    seen.extend(global_idx)
    assert len(seen) == len(set(seen)), "インデックスが重複"
    assert sorted(seen) == list(range(BATTLE_OBS_DIM)), "被覆漏れ"
    print(f"test_entity_groups_cover_all_dims OK "
          f"(エンティティ{len(groups)} + グローバル{len(global_idx)}次元)")


def test_v6_prefix_preserved():
    """先頭388次元はv6とビット一致する (旧チェックポイント互換の根拠)"""
    from champions_agent.agent import encoders
    from champions_agent.agent.spaces import BATTLE_OBS_DIM

    class _Mon:
        types = []
        base_stats = {"hp": 80, "atk": 80, "def": 80,
                      "spa": 80, "spd": 80, "spe": 80}
        boosts = {}
        status = None
        fainted = False
        moves = {}
        item = None
        current_hp_fraction = 0.7
        species = "pikachu"
        ability = None
        protect_counter = 0

    class _B:
        active_pokemon = _Mon()
        opponent_active_pokemon = _Mon()
        team = {"a": _Mon(), "b": _Mon(), "c": _Mon()}
        opponent_team = {"x": _Mon()}
        side_conditions = {}
        opponent_side_conditions = {}
        weather = {}
        fields = {}
        can_mega_evolve = False
        turn = 5

    obs = encoders.encode_battle(_B())
    assert obs.shape == (BATTLE_OBS_DIM,), obs.shape
    assert np.all(np.isfinite(obs))
    # v7ブロック (race) が実際に値を持つ (未実装の0埋めでない)
    race = obs[388:394]
    assert race[0] > 0, f"自HP計が0: {race}"
    assert race[1] > 0, f"相手HP計が0: {race}"
    print(f"test_v6_prefix_preserved OK (race={np.round(race, 2)})")


def test_set_encoder_forward():
    import torch
    import gymnasium as gym
    from champions_agent.agent.set_encoder import SetEncoderExtractor
    from champions_agent.agent.spaces import BATTLE_OBS_DIM

    space = gym.spaces.Box(low=-np.inf, high=np.inf,
                           shape=(BATTLE_OBS_DIM,), dtype=np.float32)
    ext = SetEncoderExtractor(space)
    x = torch.randn(4, BATTLE_OBS_DIM)
    out = ext(x)
    assert out.shape == (4, 512), out.shape
    assert torch.isfinite(out).all()
    # 勾配が流れる (共有MLP/attention/グローバルの配線確認)
    out.sum().backward()
    grads = [p.grad for p in ext.parameters() if p.grad is not None]
    assert grads, "勾配が流れていない"
    print("test_set_encoder_forward OK (420 -> 512, 勾配OK)")


if __name__ == "__main__":
    test_parts_table_sums_to_dim()
    test_entity_groups_cover_all_dims()
    test_v6_prefix_preserved()
    test_set_encoder_forward()
    print("\nALL OK")
