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

import os
import random

# 盤面シェイピングの縮小係数 (REWARD_SHAPE_SCALE で調整可)。
# 1.0=従来。勝敗ボーナスは縮小しない
_SHAPE_SCALE = float(os.environ.get("REWARD_SHAPE_SCALE", "0.3"))

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


def apply_matchup_teampreview(player) -> None:
    """プレイヤーの選出 (teampreview) を相性ベースに差し替える。

    poke-env の既定はランダム選出で、学習・評価の両方で「良いプレイをしたのに
    選出が悪くて負けた」というノイズが報酬とベンチに混入していた
    (実測: ベンチが1サイクル50戦で0.34-0.76まで振れる一因)。
    両陣営に同じ規則を適用するため対称で、構築の有利不利は歪めない。
    """
    import types

    def _teampreview(self, battle):
        try:
            from champions_agent.env.search_expert import teampreview_order
            return teampreview_order(battle)
        except Exception:
            return self.random_teampreview(battle)

    player.teampreview = types.MethodType(_teampreview, player)


def apply_model2_teampreview(player) -> None:
    """プレイヤーの選出を v2特徴量モデル (型情報+メタ事前分布) に差し替える。

    v1との並行比較用。モデルが読めなければ相性ベースに落ちる
    (v1へ落とすと比較が「v1 vs ほぼv1」になり差が消えるため落とさない)。
    """
    import types
    from champions_agent.agent.selection_features_v2 import predict_best_v2

    def _teampreview(self, battle):
        try:
            mons = list(battle.team.values())
            mine = [p.species for p in mons]
            opp = [p.species for p in battle.opponent_team.values()]
            best = predict_best_v2(mine, opp)
            if best is not None:
                perm = best[0]
                rest = [i for i in range(len(mons)) if i not in perm]
                return "/team " + "".join(str(i + 1)
                                          for i in list(perm) + rest)
        except Exception:
            pass
        from champions_agent.env.search_expert import teampreview_order
        return teampreview_order(battle)

    player.teampreview = types.MethodType(_teampreview, player)


def apply_matrix_teampreview(player) -> None:
    """プレイヤーの選出を「読み合いの均衡解」に差し替える。

    相手も6体からこちらを見て3体を選ぶ同時手番ゲームとして、
    自分120通り × 相手20通りの利得行列を条件付きモデルで作り、
    均衡混合戦略から選出する (selection_model.predict_maximin)。
    条件付きモデルが無い/相手が見えない場合は汎用モデルのargmax、
    それも無ければ相性ベースへ落ちる。

    実測 (2026-07-31, 各1,000戦・同一相手列・_best操縦):
        相性0.539 / argmax 0.611 / 均衡 0.597
    ベンチ相手の選出は決定的な相性ヒューリスティクスなので、読みを
    外される前提の均衡は argmax に対して期待値を上げられない (差は
    誤差の範囲)。均衡の価値は「相手がこちらの選出に適応してくる」
    対人戦で出る想定であり、ベンチではargmaxを既定にしておく。
    """
    import types
    from champions_agent.agent.selection_model import (
        GENERAL_MODEL_PATH, predict_best, predict_maximin,
    )

    def _teampreview(self, battle):
        try:
            mons = list(battle.team.values())
            mine = [p.species for p in mons]
            opp = [p.species for p in battle.opponent_team.values()]
            got = predict_maximin(mine, opp)
            if got is None:
                got = predict_best(mine, opp, GENERAL_MODEL_PATH)
            if got is not None:
                perm = got[0]
                rest = [i for i in range(len(mons)) if i not in perm]
                return "/team " + "".join(str(i + 1)
                                          for i in list(perm) + rest)
        except Exception:
            pass
        from champions_agent.env.search_expert import teampreview_order
        return teampreview_order(battle)

    player.teampreview = types.MethodType(_teampreview, player)


def apply_model_teampreview(player, path=None) -> None:
    """プレイヤーの選出を学習済み選出モデルに差し替える。

    相性ベースとの違いは実測で大きい (同一チーム・同一相手・同一操縦で
    相性0.24-0.30 → モデル0.51)。配布アドバイザーはモデルの選出を提示して
    いるため、配布実態に沿った測定にはこちらを使う。

    path 未指定なら汎用モデル (微調整前)。ベンチマークは毎戦チームが
    変わるので、my_team に寄せた配布版を使うと偏る。
    モデルが読めないときは相性ベースに落ちる (無選出で壊れないように)。
    """
    import types
    from champions_agent.agent.selection_model import (
        GENERAL_MODEL_PATH, predict_best,
    )
    model_path = path or GENERAL_MODEL_PATH

    def _teampreview(self, battle):
        try:
            mons = list(battle.team.values())
            mine = [p.species for p in mons]
            opp = [p.species for p in battle.opponent_team.values()]
            best = predict_best(mine, opp, model_path)
            if best is not None:
                perm = best[0]
                # Showdown は1始まりの並びで6体すべてを並べる (先頭3体が選出)
                rest = [i for i in range(len(mons)) if i not in perm]
                return "/team " + "".join(str(i + 1)
                                          for i in list(perm) + rest)
        except Exception:
            pass
        from champions_agent.env.search_expert import teampreview_order
        return teampreview_order(battle)

    player.teampreview = types.MethodType(_teampreview, player)


# 既定は matchup。model を試したが効果がなく costs だけ残った (2026-07-30):
#   配布条件 (certify model) 0.620 → 0.617 で横ばい
#   相性条件                 0.572 → 0.518 と悪化 (学習と評価の分布ずれ)
#   fps 250 → 212 (選出モデルの推論で学習量が約15%減)
# 「学習時の選出を配布時に揃える」という仮説は棄却。TRAIN_SELECTION=model で再試行可。
TRAIN_SELECTION = os.environ.get("TRAIN_SELECTION", "matchup")


def apply_train_teampreview(player) -> None:
    """学習ループ用の選出。TRAIN_SELECTION=model で選出モデルに切り替わる。

    学習と評価で選出方式が食い違うとベンチの解釈ができなくなるので、
    切替は必ず training_changes.json に記録すること。
    """
    if TRAIN_SELECTION == "model":
        apply_model_teampreview(player)
    else:
        apply_matchup_teampreview(player)


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
        # 選出はRLの行動空間 (26次元) の外で決まるため、環境側の両プレイヤーに
        # 明示的な選出を入れる (既定のランダム選出はノイズ源)。
        # TRAIN_SELECTION=model で学習済み選出モデルを使う。配布アドバイザーは
        # モデルの選出を提示しているので、そこで実際に生じる3体構成を操縦する
        # 経験を積ませる狙い (認定測定 400戦: 相性0.572 → モデル0.620)。
        for agent in (getattr(self, "agent1", None), getattr(self, "agent2", None)):
            if agent is not None:
                apply_train_teampreview(agent)


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
        # シェイピング縮小: HP差分報酬が支配的だと「貪欲なダメージ交換」に
        # 収束し、ヒューリスティクス相手に0.55前後で頭打ちした (観測拡張・
        # ネット容量拡大でも天井が変わらないことから報酬設計が原因と特定)。
        # 盤面シェイピングを縮小し、勝敗ボーナスへ信用を寄せる
        value *= _SHAPE_SCALE
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

    # バトルごとに使用率メタから新チームを生成する (過学習防止)。
    # 自分チームも一定確率で上位構築の実物にする: 生成チームだけでは
    # 「上位構築を操縦する」経験が積めず、アドバイザーの実用途
    # (ユーザーの実チームでの助言) やベンチマーク評価と分布がズレる
    own_teambuilder = ChampionsTeambuilder(size=team_size, play_style=own_play_style,
                                            rng=rng) if use_meta_team else None
    if own_teambuilder is not None:
        try:
            from champions_agent.env.ranked_teams import RankedTeambuilder
            from champions_agent.train.opponent_pool import OWN_RANKED_TEAM_PROB
            # 学習の条件を動かさないため上位60構築に固定 (選出モデル側の
            # 実験と同時に学習分布が変わると、ベンチの解釈ができなくなる)
            _own_ranked = RankedTeambuilder(top_n=60, rng=rng,
                                            include_external=False)

            class _OwnMixedTeambuilder(ChampionsTeambuilder):
                def yield_team(self) -> str:
                    if self.rng.random() < OWN_RANKED_TEAM_PROB:
                        return _own_ranked.yield_team()
                    return super().yield_team()

            own_teambuilder = _OwnMixedTeambuilder(
                size=team_size, play_style=own_play_style, rng=rng)
        except Exception as e:
            print(f"[showdown_env] 自分側の上位構築チームは無効 (メタ生成のみ): {e}")
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
    from poke_env import AccountConfiguration

    # 並列環境 (SubprocVecEnv) では各プロセスの対戦相手が同じサーバーに接続する。
    # poke-envの自動生成はエージェント側だけなので、相手のアカウント名を
    # プロセスPID+seedで一意化してユーザー名衝突 (nametaken) を防ぐ。
    # PID併用でプロセス再起動をまたいだ残存接続との衝突も避ける
    import os as _os
    _uid = (seed if seed is not None else rng.randint(0, 9999)) % 10000
    opp_account = AccountConfiguration(f"CO{_os.getpid() % 100000}x{_uid}", None)

    if opponent_mode == "random":
        opponent = RandomPlayer(
            account_configuration=opp_account,
            battle_format=battle_format,
            server_configuration=TrainingServerConfiguration,
            team=opp_teambuilder,
        )
        return MaskedSingleAgentWrapper(env, opponent)

    opp_team = opp_teambuilder
    try:
        from champions_agent.env.ranked_teams import RankedTeambuilder
        # 上と同じ理由で上位60構築に固定
        ranked_tb = RankedTeambuilder(top_n=60, rng=rng, include_external=False)

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
        account_configuration=opp_account,
        battle_format=battle_format,
        server_configuration=TrainingServerConfiguration,
        team=opp_team,
    )
    print(f"[showdown_env] 対戦相手: selfplayプール{len(pool.entries())}件 + "
          f"ヒューリスティクス強敵 + ランダム (混合)")

    return MaskedSingleAgentWrapper(env, opponent)


def make_benchmark_player(battle_format: str = TRAINING_BATTLE_FORMAT,
                          top_n: int = 60, team=None, **kwargs):
    """評価用の固定ベンチマーク相手: 上位構築 x SimpleHeuristicsPlayer

    ⚠ 既定のチームプールは上位60構築に固定し、取り込んだ外部構築も混ぜない。
    ここが増えると過去のベンチ履歴と比較できなくなる (評価基準が動く)。
    広げる場合は training_changes.json に記録し、compare_periods で
    前後を切って読むこと。

    team を渡すと差し替えられる。評価以外の用途 (選出データ収集など、
    相手構築の種類を増やしたい場面) はこちらを使うこと。
    """
    from poke_env.player import SimpleHeuristicsPlayer
    from champions_agent.env.ranked_teams import RankedTeambuilder

    if team is None:
        team = RankedTeambuilder(top_n=top_n, include_external=False)
    return SimpleHeuristicsPlayer(
        battle_format=battle_format,
        server_configuration=TrainingServerConfiguration,
        team=team,
        **kwargs,
    )


if __name__ == "__main__":
    print(
        "このモジュールは単体実行を想定していません。\n"
        "先にローカルでPokemon Showdownサーバーを起動してから、\n"
        "train/train_battle.py 経由で make_training_env() を利用してください。"
    )
