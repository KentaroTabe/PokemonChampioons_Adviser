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

from champions_agent.config import (
    DEFAULT_PLAY_STYLE, PLAY_STYLES, MODELS_DIR,
    TRAINING_BATTLE_FORMAT, TRAINING_TEAM_SIZE,
)
from champions_agent.env.team_builder import build_random_team_text
from champions_agent.env.showdown_env import TrainingServerConfiguration
from poke_env.player import RandomPlayer
from poke_env.teambuilder import ConstantTeambuilder


class ModelPlayer(RandomPlayer):
    """学習済みPPOモデルで行動選択するPlayer(モデルが無ければRandomPlayer相当)。"""

    def __init__(self, *args, play_style: str = DEFAULT_PLAY_STYLE, **kwargs):
        super().__init__(*args, **kwargs)
        from champions_agent.agent.policy_battle import BattlePolicy
        self.policy = BattlePolicy(play_style=play_style)

    def choose_move(self, battle):
        return self.policy.choose_order(battle)


async def run_evaluation(play_style: str = DEFAULT_PLAY_STYLE,
                          opponent_play_style: str | None = None,
                          n_battles: int = 50,
                          battle_format: str = TRAINING_BATTLE_FORMAT) -> dict:
    """play_styleモデル vs (opponent_play_styleモデル or RandomPlayer) をn_battles戦させる。"""
    own_team = build_random_team_text(size=TRAINING_TEAM_SIZE, play_style=play_style)
    own_teambuilder = ConstantTeambuilder(own_team)

    player1 = ModelPlayer(
        battle_format=battle_format,
        server_configuration=TrainingServerConfiguration,
        team=own_teambuilder,
        play_style=play_style,
    )

    if opponent_play_style:
        opp_team = build_random_team_text(size=TRAINING_TEAM_SIZE, play_style=opponent_play_style)
        opp_teambuilder = ConstantTeambuilder(opp_team)
        player2 = ModelPlayer(
            battle_format=battle_format,
            server_configuration=TrainingServerConfiguration,
            team=opp_teambuilder,
            play_style=opponent_play_style,
        )
    else:
        opp_team = build_random_team_text(size=TRAINING_TEAM_SIZE, play_style="balance")
        opp_teambuilder = ConstantTeambuilder(opp_team)
        player2 = RandomPlayer(
            battle_format=battle_format,
            server_configuration=TrainingServerConfiguration,
            team=opp_teambuilder,
        )

    await player1.battle_against(player2, n_battles=n_battles)

    result = {
        "play_style": play_style,
        "opponent": opponent_play_style or "random",
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
    parser.add_argument("--battles", type=int, default=50)
    parser.add_argument("--timeout", type=int, default=0,
                         help="秒数を指定すると評価全体にタイムアウトをかける (ハング対策)")
    parser.add_argument("--format", type=str, default=TRAINING_BATTLE_FORMAT)
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
    ))
    print(f"[evaluate] {result}")

    # vs Random の結果は opponent_pool の勝率ゲート判定に使うため保存する
    if not args.opponent_play_style:
        import json
        from pathlib import Path
        log_dir = Path(__file__).resolve().parent / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / f"last_eval_{args.play_style}.json").write_text(
            json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
