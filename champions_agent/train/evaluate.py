"""
学習済み戦闘方策(性格別)の評価スクリプト。

固定ベースライン(RandomPlayer)に対してNバトル対戦させ、勝率を測定する。
性格ごとのモデルを相互対戦させ、相性(offense vs stall 等)を見ることも想定。

前提: ローカルでPokemon Showdownサーバーが起動していること。

使い方:
    python -m champions_agent.train.evaluate --play-style offense --battles 50
    python -m champions_agent.train.evaluate --play-style offense --opponent-play-style stall --battles 50
"""
from __future__ import annotations

import argparse
import asyncio
import random

from champions_agent.config import (
    DEFAULT_PLAY_STYLE, PLAY_STYLES, MODELS_DIR, RANDOM_SEED,
    TRAINING_BATTLE_FORMAT, TRAINING_TEAM_SIZE,
)
from champions_agent.env.team_builder import build_random_team_text
from champions_agent.env.showdown_env import TrainingServerConfiguration
from poke_env.player import RandomPlayer
from poke_env.teambuilder import ConstantTeambuilder


_eval_seq = 0


def _uniq_accounts():
    """評価1回ぶんの一意なアカウント名ペア (PID+連番+乱数、18文字以内)。

    同一プロセスで連続評価しても、別プロセスの評価と同時に走っても
    衝突しないようにする (衝突するとチャレンジが成立せずハングする)。
    """
    import os
    import random
    from poke_env import AccountConfiguration
    global _eval_seq
    _eval_seq += 1
    tag = f"{os.getpid() % 10000}x{_eval_seq}{random.randint(10, 99)}"
    return (AccountConfiguration(f"EvA{tag}"[:18], None),
            AccountConfiguration(f"EvB{tag}"[:18], None))


class ModelPlayer(RandomPlayer):
    """学習済みPPOモデルで行動選択するPlayer(モデルが無ければRandomPlayer相当)。

    checkpoint="current": 学習中の最新チェックポイントを評価する (既定)。
    checkpoint="best":    配布用の最良スナップショット (_best) を使う。
    ⚠ 2026-07-26まで既定がBattlePolicy任せ (=_best優先) だったため、
    _best作成以降の夜間ベンチは「凍結された_best」を測り続けていた
    (どの施策でもベンチが不動だった頭打ちの正体)。学習進捗の評価は
    必ず current を測ること。
    """

    def __init__(self, *args, play_style: str = DEFAULT_PLAY_STYLE,
                 checkpoint: str = "current", **kwargs):
        super().__init__(*args, **kwargs)
        from champions_agent.agent.policy_battle import BattlePolicy
        if checkpoint == "current":
            path = MODELS_DIR / f"battle_policy_{play_style}.zip"
            self.policy = BattlePolicy(model_path=path if path.exists()
                                       else None, play_style=play_style)
        else:
            self.policy = BattlePolicy(play_style=play_style)

    def choose_move(self, battle):
        return self.policy.choose_order(battle)


class HybridPlayer(ModelPlayer):
    """探索 (深さ2) + RL価値の葉評価で行動選択するPlayer。

    従来のベンチはRL方策単体を測っていたが、配布アドバイザーは探索との
    複合体を提示する。複合体を測るためのベンチ経路。

    葉評価のRL価値は advisor.rl_bridge 経由 (配布と同じ _best を読む)。
    探索が行動を返せない局面 (情報不足など) はRL方策へフォールバック。

    ⚠ 実測 (2026-07-31, 各1,000戦・同一相手列・_best):
        policy 0.560 / hybrid 0.320 / search(葉評価なし) 0.326
    「探索を主・方策を従」にするこの構成は方策単体より大幅に弱い。
    原因は葉評価ではなく探索エンジン自体 (手書き評価の深さ2) が
    方策に大きく劣ること。方策はBC後の自己対戦で教師 (探索) を
    とうに超えている。複合で方策を超えたい場合は、方策を主にして
    探索は限定的な役割 (確定勝ち筋の検出など) に絞る設計が必要。
    """

    def __init__(self, *args, depth: int = 2, use_value: bool = True,
                 **kwargs):
        super().__init__(*args, **kwargs)
        self._depth = depth
        self._use_value = use_value

    def choose_move(self, battle):
        from champions_agent.env.search_expert import decide
        try:
            d = decide(battle, depth=self._depth, use_value=self._use_value)
        except Exception:
            d = None
        if d is None:
            return self.policy.choose_order(battle)
        if d["kind"] == "move":
            return self.create_order(d["move"], mega=d["mega"])
        return self.create_order(d["pokemon"])


async def run_evaluation(play_style: str = DEFAULT_PLAY_STYLE,
                          opponent_play_style: str | None = None,
                          n_battles: int = 50,
                          battle_format: str = TRAINING_BATTLE_FORMAT,
                          opponent_kind: str = "random",
                          checkpoint: str = "current",
                          selection: str = "matchup",
                          own_teams: str = "train",
                          opp_seed: int | None = None,
                          agent: str = "policy") -> dict:
    """play_styleモデル vs (opponent_play_styleモデル or RandomPlayer) をn_battles戦させる。"""
    # 自分チーム: 以前は「生成チーム1個を全戦使い回し」(ConstantTeambuilder)
    # だったため、勝率がチームドローの当たり外れで±0.2以上振動し、方策の
    # 進歩が読めなかった。ベンチマーク評価では相手と同じ上位構築を毎戦
    # 引き直して等条件にする (方策の強さだけを測る。アドバイザーの実用途
    # =ユーザーの実チームを操縦する、とも整合)
    if opponent_kind == "benchmark":
        from champions_agent.env.ranked_teams import RankedTeambuilder
        if own_teams == "holdout":
            # 学習に使っていない構築だけで測る。ベンチの上昇が
            # 「相手ヒューリスティクスへの過学習」でないかの検証用
            # (学習側は上位60構築に固定してあるので、それ以外が未学習)
            from champions_agent.env.ranked_teams import build_ranked_teams
            all_teams = build_ranked_teams(include_external=False)
            trained = set(build_ranked_teams(top_n=60, include_external=False))
            held = [t for t in all_teams if t not in trained]
            from poke_env.teambuilder import Teambuilder as _TB

            class _Held(_TB):
                def __init__(self, texts):
                    self.texts = texts
                    self.rng = random.Random(RANDOM_SEED)

                def yield_team(self):
                    return self.join_team(self.parse_showdown_team(
                        self.rng.choice(self.texts)))

            own_teambuilder = _Held(held)
        else:
            # 評価基準を動かさないため上位60構築に固定 (make_benchmark_player と対)
            own_teambuilder = RankedTeambuilder(top_n=60,
                                                include_external=False)
    else:
        own_team = build_random_team_text(size=TRAINING_TEAM_SIZE,
                                          play_style=play_style)
        own_teambuilder = ConstantTeambuilder(own_team)

    # アカウント名を一意にする。poke-envの既定名 ("ModelPlayer 1" 等) のままだと
    # 別プロセスの評価と名前が衝突し、サーバーに残った古いチャレンジが
    # 「There's already a challenge between you and RandomPlayer 1」を返して
    # battle_against が返らなくなる (2026-07-27に夜間評価が5時間空転した実績。
    # 手動評価と学習ループの評価が同名だったのが原因)
    acc1, acc2 = _uniq_accounts()
    # agent="hybrid": 探索+RL価値の複合体 (配布アドバイザー相当) を測る。
    # agent="search": 葉評価なしの純探索 (価値関数の寄与の切り分け用)。
    # 従来の "policy" はRL方策単体
    extra = {}
    if agent in ("hybrid", "search"):
        cls = HybridPlayer
        extra["use_value"] = (agent == "hybrid")
    else:
        cls = ModelPlayer
    player1 = cls(
        account_configuration=acc1,
        battle_format=battle_format,
        server_configuration=TrainingServerConfiguration,
        team=own_teambuilder,
        play_style=play_style,
        checkpoint=checkpoint,
        **extra,
    )

    if opponent_kind == "benchmark":
        from champions_agent.env.showdown_env import make_benchmark_player
        # opp_seed を渡すと相手チームの並びが再現される。A/B比較で両条件に
        # 同じ相手列を当てると、差が相手の引き運に埋もれなくなる
        team = None
        if opp_seed is not None:
            from champions_agent.env.ranked_teams import RankedTeambuilder
            team = RankedTeambuilder(top_n=60, include_external=False,
                                     rng=random.Random(opp_seed))
        player2 = make_benchmark_player(battle_format=battle_format,
                                        team=team,
                                        account_configuration=acc2)
    elif opponent_play_style:
        opp_team = build_random_team_text(size=TRAINING_TEAM_SIZE, play_style=opponent_play_style)
        opp_teambuilder = ConstantTeambuilder(opp_team)
        player2 = ModelPlayer(
            account_configuration=acc2,
            battle_format=battle_format,
            server_configuration=TrainingServerConfiguration,
            team=opp_teambuilder,
            play_style=opponent_play_style,
        )
    else:
        opp_team = build_random_team_text(size=TRAINING_TEAM_SIZE, play_style="balance")
        opp_teambuilder = ConstantTeambuilder(opp_team)
        player2 = RandomPlayer(
            account_configuration=acc2,
            battle_format=battle_format,
            server_configuration=TrainingServerConfiguration,
            team=opp_teambuilder,
        )

    # 選出を学習環境と同じ相性ベースに揃える (両陣営に適用するので対称)。
    # 既定のランダム選出は勝敗にノイズを乗せ、ベンチの分解能を下げていた
    from champions_agent.env.showdown_env import (
        apply_matchup_teampreview, apply_matrix_teampreview,
        apply_model_teampreview,
    )
    if selection == "model":
        # 自分側だけモデル選出にする (配布アドバイザーと同じ条件)。
        # 相手は相性のままにして、既存ベンチとの差が選出モデルの寄与だけに
        # なるようにする
        apply_model_teampreview(player1)
    elif selection == "matrix":
        # 読み合いの均衡解 (条件付きモデル + 利得行列)
        apply_matrix_teampreview(player1)
    else:
        apply_matchup_teampreview(player1)
    apply_matchup_teampreview(player2)

    await player1.battle_against(player2, n_battles=n_battles)

    result = {
        "play_style": play_style,
        "opponent": opponent_play_style or opponent_kind,
        "agent": agent,
        "n_battles": n_battles,
        "wins": player1.n_won_battles,
        "win_rate": player1.n_won_battles / n_battles if n_battles else 0.0,
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="学習済み戦闘方策の勝率評価")
    parser.add_argument("--play-style", type=str, default=DEFAULT_PLAY_STYLE,
                         choices=list(PLAY_STYLES.keys()))
    parser.add_argument("--opponent-play-style", type=str, default=None,
                         choices=list(PLAY_STYLES.keys()) + [None],
                         help="省略時はRandomPlayerと対戦")
    parser.add_argument("--no-save", action="store_true",
                         help="評価結果を last_eval_*.json に書かない。"
                              "報酬スイープ等、本番の昇格判定 (best_checkpoint) や "
                              "プール抽選に混ぜたくない測定で使う")
    parser.add_argument("--battles", type=int, default=50)
    parser.add_argument("--opp-seed", type=int, default=None,
                         help="相手チームの並びを固定する。A/B比較で両条件に"
                              "同じ相手列を当てると差が読みやすくなる")
    parser.add_argument("--opponent", type=str, default="random",
                         choices=["random", "benchmark"],
                         help="benchmark=上位構築xヒューリスティクスの固定強敵")
    parser.add_argument("--timeout", type=int, default=0,
                         help="秒数を指定すると評価全体にタイムアウトをかける (ハング対策)")
    parser.add_argument("--format", type=str, default=TRAINING_BATTLE_FORMAT)
    parser.add_argument("--checkpoint", type=str, default="current",
                         choices=["current", "best"],
                         help="current=学習中の最新 (進捗測定) / best=_best (配布版の実力測定)")
    parser.add_argument("--agent", type=str, default="policy",
                         choices=["policy", "hybrid", "search"],
                         help="policy=RL方策単体 (既定・従来のベンチ) / "
                              "hybrid=探索+RL価値の複合体 (配布相当) / "
                              "search=葉評価なしの純探索 (切り分け用)")
    parser.add_argument("--selection", type=str, default="matchup",
                         choices=["matchup", "model", "matrix"],
                         help="自分側の選出: matchup=相性 (既定) / "
                              "model=選出モデルargmax / "
                              "matrix=読み合いの均衡解 (条件付きモデル)")
    args = parser.parse_args()

    if args.timeout > 0:
        import signal
        import sys

        def _timeout_handler(sig, frame):
            print(f"[evaluate] TIMEOUT: {args.timeout}秒で打ち切り")
            sys.exit(1)

        signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(args.timeout)

    result = asyncio.run(run_evaluation(
        play_style=args.play_style,
        opponent_play_style=args.opponent_play_style,
        n_battles=args.battles,
        battle_format=args.format,
        opponent_kind=args.opponent,
        checkpoint=args.checkpoint,
        opp_seed=args.opp_seed,
        agent=args.agent,
        selection=args.selection,
    ))
    print(f"[evaluate] {result}")

    # 評価結果の保存: vs Random は opponent_pool の勝率ゲート判定、
    # vs benchmark は最良チェックポイント保持とプール抽選の重み付けに使う
    # (currentの測定のみ保存する。bestの再測定で昇格判定を汚さない。
    #  hybrid等の複合体や選出モデルの測定は方策の学習進捗ではないので保存しない)
    if (not args.opponent_play_style and args.checkpoint == "current"
            and args.agent == "policy" and args.selection == "matchup"
            and not args.no_save):
        import json
        from pathlib import Path
        log_dir = Path(__file__).resolve().parent / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        suffix = "_benchmark" if args.opponent == "benchmark" else ""
        (log_dir / f"last_eval_{args.play_style}{suffix}.json").write_text(
            json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
