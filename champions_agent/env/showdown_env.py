"""
poke-env (0.10系) の SinglesEnv を利用した、シングルバトル専用の強化学習環境。

前提:
- ローカルで Pokémon Showdown サーバーを起動しておく必要がある。
    git clone https://github.com/smogon/pokemon-showdown.git
    cd pokemon-showdown
    npm install
    cp config/config-example.js config/config.js
    node pokemon-showdown start --no-security  # localhost:8000 で起動
  (champions_agent/scripts/setup_showdown.sh でも自動化できる)

- poke_env.player.player.Player.choose_move / ChampionsSinglesEnv を通じて
  自己対戦(self-play)を行う。相手は env/team_builder.py が生成したチームを使う
  ランダム/ヒューリスティックプレイヤーからスタートし、
  将来的には学習済みモデル同士の自己対戦(population-based selfplay)に置き換える。

性格(PlayStyle)対応:
- own_play_style: 学習対象エージェント自身の性格。team_builder(自チーム生成)と
  reward(報酬シェイピング)の両方に反映される。
- opp_play_style_pool: 対戦相手チーム生成時に、どの性格を使うかをランダムに
  選ぶことで、特定の戦術への過学習を防ぎ、多様な相手に強くなることを狙う。
  (population-based selfplay の簡易版。真の意味での「複数モデルの重み保持」は
   train/train_battle.py 側でスナップショットを蓄積して実現する想定)
"""
from __future__ import annotations

import random

import numpy as np
from gymnasium.spaces import Box
from poke_env.battle import AbstractBattle
from poke_env.environment.singles_env import SinglesEnv
from poke_env.environment.single_agent_wrapper import SingleAgentWrapper
from poke_env.player import RandomPlayer
from poke_env.ps_client import ServerConfiguration


from champions_agent.agent import encoders
from champions_agent.agent.spaces import BATTLE_OBS_DIM
from champions_agent.config import (
    DEFAULT_PLAY_STYLE, PLAY_STYLES, SHOWDOWN_PORT,
    TRAINING_BATTLE_FORMAT, TRAINING_TEAM_SIZE,
)
from champions_agent.env.reward import get_reward_config
from champions_agent.env.team_builder import ChampionsTeambuilder

# アドバイザーのバックエンド (8000) と併用するため、学習用Showdownは別ポートで動かす
TrainingServerConfiguration = ServerConfiguration(
    f"ws://localhost:{SHOWDOWN_PORT}/showdown/websocket",
    "https://play.pokemonshowdown.com/action.php?",
)

# poke-envの静的データへチャンピオンズの新フォーム/リバランス技を注入する
from champions_agent.env import champions_dex_patch
champions_dex_patch.apply()


class ChampionsSinglesEnv(SinglesEnv):
    """シングルバトル専用の観測/報酬をカスタマイズしたpoke-env環境。"""

    def __init__(self, *args, play_style: str = DEFAULT_PLAY_STYLE, **kwargs):
        super().__init__(*args, **kwargs)
        self.play_style = play_style
        self.reward_config = get_reward_config(play_style)
        # SinglesEnv(PokeEnv)は observation_spaces を自動定義しないため、
        # embed_battle() が返す固定長ベクトルに合わせて明示的に定義する。
        # (SingleAgentWrapper が __init__ 時に observation_spaces を参照するため必須)
        obs_space = Box(low=-np.inf, high=np.inf, shape=(BATTLE_OBS_DIM,), dtype=np.float32)
        self.observation_spaces = {agent: obs_space for agent in self.possible_agents}


    def embed_battle(self, battle: AbstractBattle) -> np.ndarray:
        """現在の盤面をエージェント用の固定長ベクトルへ変換する。

        技 (威力/相性/PP/STAB)・ランク・控え・設置技/壁・天候/フィールド・
        素早さ比較まで含む観測 (agent/encoders.py の encode_battle)。
        """
        try:
            return encoders.encode_battle(battle)
        except Exception:
            return np.zeros(BATTLE_OBS_DIM, dtype=np.float32)

    @staticmethod
    def action_to_order(action, battle, fake: bool = False, strict: bool = True):
        """poke-env 0.10 の SinglesEnv は6体チーム前提で、3体チームだと
        交代アクション (インデックス3-5) が IndexError になる。
        範囲外・不正アクションはランダムな合法手へフォールバックする。
        """
        from poke_env.player.player import Player
        try:
            return SinglesEnv.action_to_order(action, battle, fake=fake, strict=strict)
        except (IndexError, ValueError, AssertionError):
            return Player.choose_random_move(battle)

    def calc_reward(self, battle: AbstractBattle) -> float:
        """poke-envの reward_computing_helper を利用したシンプルな報酬計算。

        性格(play_style)ごとの RewardConfig(env/reward.py の REWARD_PRESETS)を反映する。
        """
        return self.reward_computing_helper(
            battle,
            fainted_value=self.reward_config.faint_bonus,
            hp_value=self.reward_config.hp_diff_weight,
            victory_value=self.reward_config.win_bonus,
        )


def _pick_opponent_play_style(opp_play_style_pool: list[str] | None,
                               rng: random.Random) -> str:
    """対戦相手チーム生成に使う性格をプールからランダムに選ぶ。

    population-based selfplay の簡易版: 相手チームの傾向を毎回変えることで、
    特定の戦術のみへの過学習を防ぐ。
    """
    pool = opp_play_style_pool or list(PLAY_STYLES.keys())
    return rng.choice(pool)


def make_training_env(battle_format: str = TRAINING_BATTLE_FORMAT,
                       use_meta_team: bool = True,
                       own_play_style: str = DEFAULT_PLAY_STYLE,
                       opp_play_style_pool: list[str] | None = None,
                       team_size: int = TRAINING_TEAM_SIZE,
                       opponent_mode: str = "auto",
                       seed: int | None = None):
    """自己対戦(1体のRLエージェント vs ランダム/メタチームプレイヤー)用の環境を構築する。

    own_play_style: 学習対象エージェントの性格('offense'/'cycle'/'stall'/'balance')。
                    自チームの生成バイアスと報酬シェイピングの両方に反映される。
    opp_play_style_pool: 対戦相手チームを生成する際の性格候補リスト。
                    Noneの場合は全性格からランダムに選ぶ(多様な相手と対戦させるため)。

    戻り値は gymnasium.Env 互換(SingleAgentWrapper)。
    Stable-Baselines3 の PPO にそのまま渡せる。

    アカウント名はpoke-envの自動生成(AccountConfiguration.generate)に任せる。
    """
    rng = random.Random(seed)

    # バトルごとに使用率メタから新チームを生成する (過学習防止)
    own_teambuilder = ChampionsTeambuilder(size=team_size, play_style=own_play_style,
                                            rng=rng) if use_meta_team else None
    opp_teambuilder = ChampionsTeambuilder(size=team_size,
                                            style_pool=opp_play_style_pool,
                                            rng=rng) if use_meta_team else None

    env = ChampionsSinglesEnv(
        battle_format=battle_format,
        server_configuration=TrainingServerConfiguration,
        team=own_teambuilder,
        play_style=own_play_style,
        # strict=False: 学習初期はランダムに近い行動を大量に試すため、
        # ダイマックス/テラスタル等の不正な行動指定が起きても例外を投げず
        # デフォルト行動へフォールバックさせる(poke-env側の仕様)。
        strict=False,
    )

    # --- 対戦相手の決定 (population-based selfplay) ---
    # プールに「vs Random勝率ゲートを超えた過去チェックポイント」があれば
    # 自動でselfplay相手に切り替える。無ければRandomPlayer。
    from champions_agent.train.opponent_pool import OpponentPool, make_pool_opponent

    pool = OpponentPool()
    use_selfplay = opponent_mode == "selfplay" or \
        (opponent_mode == "auto" and pool.has_entries())
    if use_selfplay and pool.has_entries():
        opponent = make_pool_opponent(
            pool,
            battle_format=battle_format,
            server_configuration=TrainingServerConfiguration,
            team=opp_teambuilder,
        )
        print(f"[showdown_env] 対戦相手: selfplayプール {len(pool.entries())}件 "
              f"(+εランダム混合)")
    else:
        opponent = RandomPlayer(
            battle_format=battle_format,
            server_configuration=TrainingServerConfiguration,
            team=opp_teambuilder,
        )
        if opponent_mode != "random":
            print("[showdown_env] 対戦相手: RandomPlayer (プールが空。"
                  "勝率ゲート通過後に自動でselfplayへ移行)")

    return SingleAgentWrapper(env, opponent)


if __name__ == "__main__":
    print(
        "このモジュールは単体実行を想定していません。\n"
        "先にローカルでPokemon Showdownサーバーを起動してから、\n"
        "train/train_battle.py 経由で make_training_env() を利用してください。"
    )
