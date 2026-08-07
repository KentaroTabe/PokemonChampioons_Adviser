"""苦手カリキュラム (H5) の重み作成: 相手60構築それぞれへの負率を実測する。

    python -m tools.team_weights --per-team 50

各構築を相手に固定して対戦し、負率から抽選重みを作る (事前登録の式):

    w_i = clip(1 + 3 * (loss_i - mean_loss), 0.5, 2.5)

負率が平均より高い構築ほど学習で多く当たる (上限2.5倍・下限0.5倍)。
出力: logs/curriculum/team_weights.json (build_ranked_teamsの順に対応)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from pathlib import Path

logging.getLogger("poke-env").setLevel(logging.ERROR)

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "logs" / "curriculum" / "team_weights.json"


async def measure(per_team: int, style: str) -> list:
    from poke_env import AccountConfiguration
    from champions_agent.config import TRAINING_BATTLE_FORMAT
    from champions_agent.env.ranked_teams import (
        RankedTeambuilder, build_ranked_teams,
    )
    from champions_agent.env.showdown_env import (
        TrainingServerConfiguration, apply_matchup_teampreview,
        make_benchmark_player,
    )
    from champions_agent.train.evaluate import ModelPlayer, _uniq_accounts
    from tools.collect_selection_data import _make_switchable

    teams = build_ranked_teams(top_n=60, include_external=False)
    loss_rates = []
    for i, text in enumerate(teams):
        acc1, acc2 = _uniq_accounts()
        opp_tb = _make_switchable()
        opp_tb.set_text(text)
        me = ModelPlayer(
            account_configuration=acc1,
            battle_format=TRAINING_BATTLE_FORMAT,
            server_configuration=TrainingServerConfiguration,
            team=RankedTeambuilder(top_n=60, include_external=False),
            play_style=style, checkpoint="best")
        opp = make_benchmark_player(
            battle_format=TRAINING_BATTLE_FORMAT, team=opp_tb,
            account_configuration=acc2)
        apply_matchup_teampreview(me)
        apply_matchup_teampreview(opp)
        await me.battle_against(opp, n_battles=per_team)
        loss = 1.0 - me.n_won_battles / per_team
        loss_rates.append(round(loss, 3))
        if (i + 1) % 10 == 0:
            print(f"  {i + 1}/{len(teams)}構築 測定済み", flush=True)
    return loss_rates


def to_weights(loss_rates: list) -> list:
    mean = sum(loss_rates) / len(loss_rates)
    return [round(min(2.5, max(0.5, 1 + 3 * (l - mean))), 3)
            for l in loss_rates]


def main() -> None:
    ap = argparse.ArgumentParser(description="苦手カリキュラムの重み作成")
    ap.add_argument("--per-team", type=int, default=50)
    ap.add_argument("--style", default="balance")
    args = ap.parse_args()

    t0 = time.time()
    loss_rates = asyncio.run(measure(args.per_team, args.style))
    weights = to_weights(loss_rates)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "created": time.strftime("%Y-%m-%d %H:%M"),
        "per_team": args.per_team,
        "loss_rates": loss_rates,
        "weights": weights,
    }, ensure_ascii=False), encoding="utf-8")
    hi = sorted(range(len(weights)), key=lambda i: -weights[i])[:5]
    print(f"完了 ({time.time() - t0:.0f}s) → {OUT}")
    print(f"平均負率 {sum(loss_rates) / len(loss_rates):.3f} / "
          f"重み上位: {[round(weights[i], 2) for i in hi]} "
          f"(負率 {[loss_rates[i] for i in hi]})")


if __name__ == "__main__":
    main()
