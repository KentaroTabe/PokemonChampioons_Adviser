"""
自己対戦マネージャ。

3つの意思決定(選出/戦闘/パーティ編集)を一連のエピソードとして繋げ、
- 選出方策(policy_selection)の学習データ(観測・行動・報酬)
- 戦闘方策(policy_battle, poke-env経由でPPOが直接学習)
- パーティ編集方策(policy_teambuild)
を生成するための橋渡し役。

現時点ではPokemon Showdownサーバーへの依存を避け、
train_selection.py の学習ループを検証できるよう、
env/team_builder.py のダミーメタデータを使った簡易シミュレーションを提供する。

TODO: showdown_env.py の実バトル環境と接続し、
      「選出→バトル(PPO推論)→勝敗→選出方策への報酬」の一連を自動化する。
"""
from __future__ import annotations

import random

import numpy as np

from champions_agent.agent.policy_selection import build_selection_observation
from champions_agent.agent.spaces import SELECTION_PERMUTATIONS, N_SELECTION_ACTIONS
from champions_agent.env.team_builder import build_random_party


def _dummy_party(size: int = 6) -> list[dict]:
    sets = build_random_party(size=size)
    return [
        {"species": s.species, "hp_percent": 1.0, "status": "none"}
        for s in sets
    ]


def generate_selection_episode() -> tuple[np.ndarray, int, float]:
    """選出方策の学習用に1エピソード分の(観測, 行動インデックス, 報酬)を生成する。

    現時点ではPokemon Showdownでの実対戦を行わないため、
    「種族値合計が高い3体を選ぶほど勝ちやすい」という仮の報酬モデルで代用する
    (パイプライン検証用のプレースホルダ)。
    """
    own_party = _dummy_party(6)
    opponent_party = [{"species": p["species"]} for p in _dummy_party(6)]

    obs = build_selection_observation(own_party, opponent_party)

    action_idx = random.randrange(N_SELECTION_ACTIONS)
    chosen_indices = SELECTION_PERMUTATIONS[action_idx]

    from champions_agent.agent.encoders import get_pokemon_static

    def bst(p: dict) -> float:
        static = get_pokemon_static(p["species"]) or {}
        return sum(
            static.get(k) or 0
            for k in ("hp", "attack", "defense", "sp_attack", "sp_defense", "speed")
        )

    reward = sum(bst(own_party[i]) for i in chosen_indices) / 300.0 - 3.0  # 適当な正規化

    return obs, action_idx, reward


if __name__ == "__main__":
    obs, action_idx, reward = generate_selection_episode()
    print(f"obs.shape={obs.shape}, action_idx={action_idx}, reward={reward:.3f}")
