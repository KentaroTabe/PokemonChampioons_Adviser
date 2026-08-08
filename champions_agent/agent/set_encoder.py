"""Set Encoder: パーティをエンティティ (ポケモン) の集合として扱う特徴抽出器。

平坦MLPは6体分の「ポケモンの評価方法」を別々の重みで学び直すが、
本抽出器は共有MLPで6体を同じ座標系に埋め込み、self-attentionで
関係 (対面相性・交代先の価値) を混ぜる。サンプル効率の改善が狙い
(docs/RL_V7_SET_ENCODER_DESIGN.md)。

エンティティ分解は spaces.OBS_PARTS / ENTITY_PARTS から機械的に導出する。
手書きのインデックスは使わない (観測v8で必ず壊れるため)。

使い方: TRAIN_ARCH=set で train_battle が policy_kwargs に載せる。
既定は従来MLP (本番は移行判定まで不変)。
"""
from __future__ import annotations

import numpy as np
import torch
import torch.nn as nn
from stable_baselines3.common.torch_layers import BaseFeaturesExtractor

from champions_agent.agent.spaces import entity_index_groups

ENTITY_ORDER = ("own_active", "opp_active",
                "own_bench0", "own_bench1", "opp_bench0", "opp_bench1")


class SetEncoderExtractor(BaseFeaturesExtractor):
    """観測 -> [エンティティ埋め込み + attention + グローバル] -> 特徴ベクトル"""

    def __init__(self, observation_space, entity_dim: int = 96,
                 global_dim: int = 128, features_dim: int = 512,
                 n_heads: int = 4):
        super().__init__(observation_space, features_dim=features_dim)
        groups, global_idx = entity_index_groups()

        # エンティティごとの生特徴インデックス (長さは種別で異なる ->
        # 最大長へゼロパディングし、種別one-hotを添える)
        self._entity_indices = [torch.tensor(groups[name], dtype=torch.long)
                                for name in ENTITY_ORDER]
        self._global_indices = torch.tensor(global_idx, dtype=torch.long)
        self._max_raw = max(len(ix) for ix in self._entity_indices)
        n_ent = len(ENTITY_ORDER)

        self.entity_mlp = nn.Sequential(
            nn.Linear(self._max_raw + n_ent, entity_dim), nn.ReLU(),
            nn.Linear(entity_dim, entity_dim), nn.ReLU(),
        )
        self.attn = nn.MultiheadAttention(entity_dim, n_heads,
                                          batch_first=True)
        self.global_mlp = nn.Sequential(
            nn.Linear(len(global_idx), global_dim), nn.ReLU(),
        )
        # [自アクティブ埋め込み, attention後の平均, グローバル]
        self.head = nn.Sequential(
            nn.Linear(entity_dim * 2 + global_dim, features_dim), nn.ReLU(),
        )
        # 種別one-hot (固定)
        eye = torch.eye(n_ent)
        self.register_buffer("_type_onehot", eye)

    def forward(self, obs: torch.Tensor) -> torch.Tensor:
        b = obs.shape[0]
        ents = []
        for k, idx in enumerate(self._entity_indices):
            raw = obs[:, idx.to(obs.device)]
            if raw.shape[1] < self._max_raw:
                pad = torch.zeros(b, self._max_raw - raw.shape[1],
                                  device=obs.device, dtype=obs.dtype)
                raw = torch.cat([raw, pad], dim=1)
            onehot = self._type_onehot[k].to(obs.device).expand(b, -1)
            ents.append(torch.cat([raw, onehot], dim=1))
        x = torch.stack([self.entity_mlp(e) for e in ents], dim=1)  # (b,6,d)
        attn_out, _ = self.attn(x, x, x, need_weights=False)
        pooled = attn_out.mean(dim=1)
        own_active = x[:, 0, :]
        g = self.global_mlp(obs[:, self._global_indices.to(obs.device)])
        return self.head(torch.cat([own_active, pooled, g], dim=1))


def set_policy_kwargs(features_dim: int = 512) -> dict:
    """train_battle 用の policy_kwargs (TRAIN_ARCH=set のとき)"""
    return {
        "features_extractor_class": SetEncoderExtractor,
        "features_extractor_kwargs": {"features_dim": features_dim},
        # 抽出器が512次元へ集約済みなので、トランクは薄くてよい
        "net_arch": [256],
    }


def _self_test() -> None:
    from champions_agent.agent.spaces import BATTLE_OBS_DIM
    import gymnasium as gym
    space = gym.spaces.Box(low=-1, high=2, shape=(BATTLE_OBS_DIM,),
                           dtype=np.float32)
    ext = SetEncoderExtractor(space)
    out = ext(torch.zeros(3, BATTLE_OBS_DIM))
    assert out.shape == (3, 512), out.shape
    print(f"SetEncoderExtractor OK (obs {BATTLE_OBS_DIM} -> {out.shape})")


if __name__ == "__main__":
    _self_test()
