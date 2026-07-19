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
        try:
            order = self._choose_masked(battle, obs)
            if order is not None:
                return order
        except Exception:
            pass
        # フォールバック: 素のargmax (無効ならさらにランダム合法手)
        action, _ = self.model.predict(obs, deterministic=True)
        try:
            return ChampionsSinglesEnv.action_to_order(action, battle, strict=False)
        except Exception:
            return self._choose_random_legal(battle)

    def _choose_masked(self, battle: AbstractBattle, obs):
        """方策の行動分布から「合法手のみ」で最大確率の行動を選ぶ。

        素のargmaxは無効アクション (テラスタル等) を高頻度で出し、
        サーバーのデフォルト選択に丸められて意図が失われるため、
        アドバイス用途では必ずこちらを使う。
        """
        import numpy as np
        from poke_env.player.player import Player

        obs_t, _ = self.model.policy.obs_to_tensor(np.array(obs)[None, :])
        dist = self.model.policy.get_distribution(obs_t)
        probs = dist.distribution.probs.detach().cpu().numpy()[0]

        candidates = []  # (prob, order)
        active = battle.active_pokemon

        # 技: アクション 6+i / メガ付き 10+i (iは active.moves 内のインデックス)
        if active is not None:
            move_list = list(active.moves.values())
            available_ids = {m.id for m in battle.available_moves}
            for i, mv in enumerate(move_list[:4]):
                if mv.id not in available_ids:
                    continue
                candidates.append((probs[6 + i], Player.create_order(mv)))
                if battle.can_mega_evolve:
                    candidates.append(
                        (probs[10 + i], Player.create_order(mv, mega=True)))

        # 交代: アクション 0-5 (battle.team の並び順)
        team_list = list(battle.team.values())
        switchable = {p.species for p in battle.available_switches}
        for i, p in enumerate(team_list[:6]):
            if p.species in switchable:
                candidates.append((probs[i], Player.create_order(p)))

        if not candidates:
            return None
        return max(candidates, key=lambda c: c[0])[1]

    @staticmethod
    def _choose_random_legal(battle: AbstractBattle) -> BattleOrder:
        if battle.available_moves:
            from poke_env.player.player import Player
            return Player.create_order(random.choice(battle.available_moves))
        if battle.available_switches:
            from poke_env.player.player import Player
            return Player.create_order(random.choice(battle.available_switches))
        return DefaultBattleOrder()
