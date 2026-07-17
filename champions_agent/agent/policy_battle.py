"""
戦闘中の6行動選択(技4つ+交代2つ)の推論ラッパー。

学習自体は train/train_battle.py で Stable-Baselines3 (PPO) を使って行い、
本モジュールは学習済みモデルをロードして
poke-env の AbstractBattle から実際の行動(BattleOrder)を決定する役割を持つ。

学習済みモデルが無い場合は poke-env 標準の RandomPlayer 相当の
ランダム行動にフォールバックする。
"""
from __future__ import annotations

import random

from poke_env.battle import AbstractBattle
from poke_env.environment.singles_env import SinglesEnv
from poke_env.player.battle_order import BattleOrder, DefaultBattleOrder

from champions_agent.config import MODELS_DIR, DEFAULT_PLAY_STYLE
from champions_agent.env.showdown_env import ChampionsSinglesEnv


class BattlePolicy:
    """学習済みPPOモデル(あれば)を使って行動を選択するクラス。

    play_style: 使用する性格別モデル('offense'/'cycle'/'stall'/'balance')。
                対応するcheckpoints/battle_policy_{play_style}.zip をロードする。
    """

    def __init__(self, model_path=None, play_style: str = DEFAULT_PLAY_STYLE):
        self.model = None
        self.play_style = play_style
        path = model_path or (MODELS_DIR / f"battle_policy_{play_style}.zip")
        if path.exists():
            from stable_baselines3 import PPO
            self.model = PPO.load(str(path))


    def choose_order(self, battle: AbstractBattle) -> BattleOrder:
        if self.model is None:
            return self._choose_random_legal(battle)

        obs = ChampionsSinglesEnv.embed_battle(self, battle)  # type: ignore[arg-type]
        action, _ = self.model.predict(obs, deterministic=True)
        try:
            return SinglesEnv.action_to_order(action, battle, strict=False)
        except Exception:
            return self._choose_random_legal(battle)

    @staticmethod
    def _choose_random_legal(battle: AbstractBattle) -> BattleOrder:
        if battle.available_moves:
            from poke_env.player.player import Player
            return Player.create_order(random.choice(battle.available_moves))
        if battle.available_switches:
            from poke_env.player.player import Player
            return Player.create_order(random.choice(battle.available_switches))
        return DefaultBattleOrder()
