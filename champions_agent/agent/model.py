"""
共通のニューラルネットワーク定義(PyTorch)。

戦闘方策(policy_battle)は Stable-Baselines3 の MlpPolicy を利用するため
ここでの独自定義は不要だが、選出方策(policy_selection)・パーティ編集方策
(policy_teambuild)は行動空間が特殊(組み合わせ選択)なため、
シンプルなMLPスコアリングモデルをここで定義し、両方策から共有する。
"""
from __future__ import annotations

import torch
import torch.nn as nn


class MLP(nn.Module):
    """全結合層のみのシンプルなMLP。選出/パーティ編集方策の共通バックボーン。"""

    def __init__(self, input_dim: int, output_dim: int,
                 hidden_dims: tuple[int, ...] = (256, 256)) -> None:
        super().__init__()
        layers: list[nn.Module] = []
        prev_dim = input_dim
        for h in hidden_dims:
            layers.append(nn.Linear(prev_dim, h))
            layers.append(nn.ReLU())
            prev_dim = h
        layers.append(nn.Linear(prev_dim, output_dim))
        self.net = nn.Sequential(*layers)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


class SelectionPolicyNet(nn.Module):
    """選出フェーズ(6→3体+順序)用の方策ネットワーク。

    入力: SELECTION_OBS_DIM次元の観測ベクトル
    出力: N_SELECTION_ACTIONS(120通りの並び)のロジット
    """

    def __init__(self, obs_dim: int, n_actions: int) -> None:
        super().__init__()
        self.backbone = MLP(obs_dim, n_actions)

    def forward(self, obs: torch.Tensor) -> torch.Tensor:
        return self.backbone(obs)


class TeamEditPolicyNet(nn.Module):
    """対戦後のパーティ編集用の方策ネットワーク。

    入力: 現在の6体編成の観測ベクトル + 交換候補プールの観測ベクトル
    出力: 「入れ替えなし」を含む編集アクションのロジット
    """

    def __init__(self, obs_dim: int, n_actions: int) -> None:
        super().__init__()
        self.backbone = MLP(obs_dim, n_actions)

    def forward(self, obs: torch.Tensor) -> torch.Tensor:
        return self.backbone(obs)
