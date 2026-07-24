"""human_battle の疎通確認: 人間の代わりに RandomPlayer が受けて1戦する。

チャレンジ送信→受諾→対戦完了→結果記録までの全経路をAI対AIで検証する
(実際の人間対戦はブラウザで行う。docstringは tools/human_battle.py 参照)。

    python -m tools.check_human_battle [--opponent search]
"""
from __future__ import annotations

import argparse
import asyncio
import os
from types import SimpleNamespace


async def run(opponent_kind: str) -> None:
    from poke_env import AccountConfiguration
    from poke_env.player import RandomPlayer
    from champions_agent.config import TRAINING_BATTLE_FORMAT
    from champions_agent.env.ranked_teams import RankedTeambuilder
    from champions_agent.env.showdown_env import TrainingServerConfiguration
    from tools.human_battle import run as hb_run

    stand_in_name = f"HBtest{os.getpid() % 10000}"
    stand_in = RandomPlayer(
        account_configuration=AccountConfiguration(stand_in_name, None),
        battle_format=TRAINING_BATTLE_FORMAT,
        server_configuration=TrainingServerConfiguration,
        team=RankedTeambuilder(),
    )
    args = SimpleNamespace(name=stand_in_name, opponent=opponent_kind,
                           style="balance", depth=1, battles=1,
                           mode="challenge", timer=False, log=False)
    await asyncio.gather(
        stand_in.accept_challenges(None, 1),
        hb_run(args),
    )
    print(f"[check_human_battle] OK: 代役({stand_in_name}) "
          f"{stand_in.n_won_battles}勝{1 - stand_in.n_won_battles}敗")


def main() -> None:
    ap = argparse.ArgumentParser(description="human_battleの疎通確認 (AI対AI)")
    ap.add_argument("--opponent", default="search",
                    choices=["model", "benchmark", "search"])
    args = ap.parse_args()
    asyncio.run(run(args.opponent))


if __name__ == "__main__":
    main()
