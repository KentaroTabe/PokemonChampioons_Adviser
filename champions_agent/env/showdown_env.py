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


def compute_action_mask(battle) -> np.ndarray:
    """現在のバトル状態から合法アクションのマスク (26次元bool) を作る。

    poke-env 0.10 SinglesEnv のアクション対応:
      0-5:   交代 (battle.team の並び順)
      6-9:   技 (active.moves の並び順)
      10-13: 技+メガシンカ
      14-25: Zワザ/ダイマックス/テラスタル (チャンピオンズには存在しない -> 常に無効)
    無効アクションはサーバーのデフォルト行動に丸められ、学習のクレジット割当を
    壊すため、MaskablePPO にこのマスクを与えて合法手のみをサンプリングさせる。
    """
    mask = np.zeros(26, dtype=bool)
    try:
        if battle is None or battle.finished:
            mask[:] = True
            return mask

        active = battle.active_pokemon
        if active is not None:
            move_list = list(active.moves.values())
            available_ids = {m.id for m in battle.available_moves}
            for i, mv in enumerate(move_list[:4]):
                if mv.id in available_ids:
                    mask[6 + i] = True
                    if battle.can_mega_evolve:
                        mask[10 + i] = True

        switchable = {p.species for p in battle.available_switches}
        for i, p in enumerate(list(battle.team.values())[:6]):
            if p.species in switchable:
                mask[i] = True

        if not mask.any():
            mask[:] = True
    except Exception:
        mask[:] = True
    return mask


class MaskedSingleAgentWrapper(SingleAgentWrapper):
    """SingleAgentWrapper + MaskablePPO用の action_masks() 提供"""

    def action_masks(self) -> np.ndarray:
        battle = getattr(self.env, "battle1", None)
        return compute_action_mask(battle)


class ChampionsSinglesEnv(SinglesEnv):
    """シングルバトル専用の観測/報酬をカスタマイズしたpoke-env環境。"""

    def __init__(self, *args, play_style: str = DEFAULT_PLAY_STYLE, **kwargs):
        super().__init__(*args, **kwargs)
        self.play_style = play_style
        self.reward_config = get_reward_config(play_style)
        self._reward_state: dict = {}   # battle_tag -> 前ステップの盤面価値
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
        """RewardConfig (性格別プリセット) を完全に反映したステップ報酬。

        以前は poke-env の reward_computing_helper に委譲しており、
        step_penalty / 非対称きぜつペナルティ / 状態異常シェイピングが
        すべて未使用だった (=性格間で報酬がほぼ同一だった) ため、
        盤面価値の差分を自前で計算する方式に変更した。
        """
        cfg = self.reward_config

        # --- 現在の盤面価値 ---
        my_hp = sum(p.current_hp_fraction for p in battle.team.values())
        opp_hp = sum(p.current_hp_fraction for p in battle.opponent_team.values())
        # 未視認の相手は満タン扱い (視認時にHPが判明して差分が入る)
        opp_hp += max(0, 3 - len(battle.opponent_team))
        my_fainted = sum(1 for p in battle.team.values() if p.fainted)
        opp_fainted = sum(1 for p in battle.opponent_team.values() if p.fainted)
        my_status = sum(1 for p in battle.team.values()
                        if p.status is not None and not p.fainted)
        opp_status = sum(1 for p in battle.opponent_team.values()
                         if p.status is not None and not p.fainted)

        value = (cfg.hp_diff_weight * (my_hp - opp_hp)
                 + cfg.faint_bonus * opp_fainted
                 - cfg.fainted_penalty * my_fainted
                 + 0.3 * (opp_status - my_status))
        if battle.won is True:
            value += cfg.win_bonus
        elif battle.won is False:
            value -= cfg.lose_penalty

        # --- 前ステップとの差分 + 毎ターンの微小ペナルティ ---
        tag = battle.battle_tag
        prev = self._reward_state.get(tag, 0.0)
        self._reward_state[tag] = value
        if len(self._reward_state) > 60:
            for k in list(self._reward_state.keys())[:-30]:
                del self._reward_state[k]
        return (value - prev) - cfg.step_penalty


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

    # --- 対戦相手の決定 ---
    # 混合相手: selfplayプールの過去世代 + 上位構築ヒューリスティクス (常設の強敵)
    # + ランダム (忘却防止)。チームも30%の確率でラダー上位の実構築になる。
    from champions_agent.train.opponent_pool import (
        OpponentPool, make_pool_opponent, RANKED_TEAM_PROB,
    )

    if opponent_mode == "random":
        opponent = RandomPlayer(
            battle_format=battle_format,
            server_configuration=TrainingServerConfiguration,
            team=opp_teambuilder,
        )
        return MaskedSingleAgentWrapper(env, opponent)

    opp_team = opp_teambuilder
    try:
        from champions_agent.env.ranked_teams import RankedTeambuilder
        ranked_tb = RankedTeambuilder(rng=rng)

        class _MixedTeambuilder(ChampionsTeambuilder):
            def yield_team(self) -> str:
                if self.rng.random() < RANKED_TEAM_PROB:
                    return ranked_tb.yield_team()
                return super().yield_team()

        opp_team = _MixedTeambuilder(size=team_size,
                                      style_pool=opp_play_style_pool, rng=rng)
    except Exception as e:
        print(f"[showdown_env] 上位構築チームは無効 (メタ生成のみ): {e}")

    pool = OpponentPool()
    opponent = make_pool_opponent(
        pool,
        battle_format=battle_format,
        server_configuration=TrainingServerConfiguration,
        team=opp_team,
    )
    print(f"[showdown_env] 対戦相手: selfplayプール{len(pool.entries())}件 + "
          f"ヒューリスティクス強敵 + ランダム (混合)")

    return MaskedSingleAgentWrapper(env, opponent)


def make_benchmark_player(battle_format: str = TRAINING_BATTLE_FORMAT,
                          top_n: int = 60, **kwargs):
    """評価用の固定ベンチマーク相手: 上位構築 x SimpleHeuristicsPlayer"""
    from poke_env.player import SimpleHeuristicsPlayer
    from champions_agent.env.ranked_teams import RankedTeambuilder

    return SimpleHeuristicsPlayer(
        battle_format=battle_format,
        server_configuration=TrainingServerConfiguration,
        team=RankedTeambuilder(top_n=top_n),
        **kwargs,
    )


if __name__ == "__main__":
    print(
        "このモジュールは単体実行を想定していません。\n"
        "先にローカルでPokemon Showdownサーバーを起動してから、\n"
        "train/train_battle.py 経由で make_training_env() を利用してください。"
    )
