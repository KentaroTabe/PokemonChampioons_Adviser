"""探索エキスパート (search_expert) の実戦診断。

vs ベンチマークで対戦させ、意思決定の内訳 (探索成功/フォールバック率、
技/交代比率、平均ターン数) と勝率を測る。RandomPlayer の基準線も併記して
「探索がどれだけ上積みしているか」を見る。

    python -m tools.check_search_expert --battles 20 --depth 1
"""
from __future__ import annotations

import argparse
import asyncio
import os
import time

from champions_agent.config import TRAINING_BATTLE_FORMAT


async def run(n_battles: int, depth: int, by: str = "recommended") -> None:
    from poke_env import AccountConfiguration
    from poke_env.player import Player, RandomPlayer
    from champions_agent.env.ranked_teams import RankedTeambuilder
    from champions_agent.env.search_expert import decide, teampreview_order
    from champions_agent.env.showdown_env import (
        TrainingServerConfiguration, make_benchmark_player,
    )

    stats = {"decide": 0, "fallback": 0, "error": 0,
             "move": 0, "switch": 0, "mega": 0}

    class _DiagExpert(Player):
        def choose_move(self, battle):
            try:
                d = decide(battle, depth=depth, by=by)
            except Exception as e:
                stats["error"] += 1
                stats.setdefault("last_error", repr(e))
                d = None
            if d is None:
                stats["fallback"] += 1
                return self.choose_random_move(battle)
            stats["decide"] += 1
            stats[d["kind"]] += 1
            if d["mega"]:
                stats["mega"] += 1
            if d["kind"] == "move":
                return self.create_order(d["move"], mega=d["mega"])
            return self.create_order(d["pokemon"])

        def teampreview(self, battle):
            try:
                return teampreview_order(battle)
            except Exception:
                return self.random_teampreview(battle)

    uid = os.getpid() % 100000
    expert = _DiagExpert(
        account_configuration=AccountConfiguration(f"DXex{uid}", None),
        battle_format=TRAINING_BATTLE_FORMAT,
        server_configuration=TrainingServerConfiguration,
        team=RankedTeambuilder(),
    )
    bench1 = make_benchmark_player(
        battle_format=TRAINING_BATTLE_FORMAT,
        account_configuration=AccountConfiguration(f"DXop{uid}", None))
    t0 = time.time()
    await expert.battle_against(bench1, n_battles=n_battles)
    dt = time.time() - t0
    turns = [b.turn for b in expert.battles.values()]
    n_dec = stats["decide"] + stats["fallback"]
    print(f"=== 探索エキスパート (depth={depth} by={by}) vs ベンチマーク "
          f"{n_battles}戦 ({dt:.0f}s) ===")
    print(f"勝率: {expert.n_won_battles / n_battles:.2f}")
    print(f"平均ターン数: {sum(turns) / max(1, len(turns)):.1f}")
    print(f"意思決定: 探索{stats['decide']} / フォールバック{stats['fallback']} "
          f"({stats['fallback'] / max(1, n_dec):.0%}) / 例外{stats['error']}")
    print(f"内訳: 技{stats['move']} (うちメガ{stats['mega']}) "
          f"交代{stats['switch']}")
    if stats.get("last_error"):
        print(f"直近の例外: {stats['last_error']}")

    rand = RandomPlayer(
        account_configuration=AccountConfiguration(f"DXrd{uid}", None),
        battle_format=TRAINING_BATTLE_FORMAT,
        server_configuration=TrainingServerConfiguration,
        team=RankedTeambuilder(),
    )
    bench2 = make_benchmark_player(
        battle_format=TRAINING_BATTLE_FORMAT,
        account_configuration=AccountConfiguration(f"DXo2{uid}", None))
    await rand.battle_against(bench2, n_battles=n_battles)
    rturns = [b.turn for b in rand.battles.values()]
    print(f"--- 基準線: RandomPlayer 勝率 "
          f"{rand.n_won_battles / n_battles:.2f} "
          f"(平均{sum(rturns) / max(1, len(rturns)):.1f}ターン)")


def main() -> None:
    ap = argparse.ArgumentParser(description="探索エキスパートの実戦診断")
    ap.add_argument("--battles", type=int, default=20)
    ap.add_argument("--depth", type=int, default=1)
    ap.add_argument("--by", default="recommended",
                    choices=["recommended", "expected"])
    args = ap.parse_args()
    asyncio.run(run(args.battles, args.depth, args.by))


if __name__ == "__main__":
    main()
