"""人間 vs AI の対戦 (学習進捗の体感チェック用)。

ローカルShowdown (ポート8100) にAIプレイヤーを接続し、指定した人間の
ユーザー名へチャレンジを送る (--mode accept なら人間からの申請を待つ)。

人間側の手順:
  1. ブラウザで https://play.pokemonshowdown.com/~~localhost:8100/ を開く
  2. 右上 Choose name で --name に渡した名前を入力 (パスワード不要)
  3. チームビルダーでフォーマット [Gen 9] Champions BSS Reg MB を選び、
     `python -m tools.export_my_team_showdown` の出力を Import から貼り付け
  4. AIからのチャレンジを Accept する (対戦はShowdownの通常UI)

    python -m tools.human_battle --name <自分の名前>                    # vs 学習モデル(balance)
    python -m tools.human_battle --name <名前> --opponent benchmark    # vs ベンチマーク
    python -m tools.human_battle --name <名前> --opponent search       # vs 探索エキスパート
    python -m tools.human_battle --name <名前> --style offense --battles 3
    python -m tools.human_battle --name <名前> --mode accept           # 人間から申請する

結果は logs/human_battles.jsonl に記録される (人間相手の勝率=進捗の物差し)。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path

from champions_agent.config import TRAINING_BATTLE_FORMAT

REPO = Path(__file__).resolve().parent.parent
LOG_PATH = REPO / "logs" / "human_battles.jsonl"


def _make_ai(kind: str, style: str, depth: int, timer: bool):
    from poke_env import AccountConfiguration
    from champions_agent.env.ranked_teams import RankedTeambuilder
    from champions_agent.env.showdown_env import (
        TrainingServerConfiguration, make_benchmark_player,
    )
    ai_name = f"AI-{kind}-{os.getpid() % 100}"[:18]
    common = dict(
        account_configuration=AccountConfiguration(ai_name, None),
        battle_format=TRAINING_BATTLE_FORMAT,
        server_configuration=TrainingServerConfiguration,
        start_timer_on_battle_start=timer,
    )
    if kind == "benchmark":
        # make_benchmark_player は format/server/チームを内部で設定する
        return make_benchmark_player(
            account_configuration=common["account_configuration"],
            start_timer_on_battle_start=timer), ai_name
    if kind == "search":
        from champions_agent.env.search_expert import make_search_expert_player
        return make_search_expert_player(
            depth=depth, team=RankedTeambuilder(), **common), ai_name
    from champions_agent.train.evaluate import ModelPlayer
    return ModelPlayer(play_style=style, team=RankedTeambuilder(),
                       **common), ai_name


async def run(args) -> None:
    ai, ai_name = _make_ai(args.opponent, args.style, args.depth, args.timer)
    label = args.opponent + (f"({args.style})" if args.opponent == "model" else "")
    print(f"[human_battle] AI: {ai_name} ({label}) / 相手: {args.name} / "
          f"{args.battles}戦 / format={TRAINING_BATTLE_FORMAT}", flush=True)
    print("[human_battle] ブラウザ: https://play.pokemonshowdown.com/~~localhost:8100/",
          flush=True)
    if args.mode == "accept":
        print(f"[human_battle] {args.name} からのチャレンジを待っています...",
              flush=True)
        await ai.accept_challenges(args.name, args.battles)
    else:
        print(f"[human_battle] {args.name} へチャレンジを送ります "
              "(ブラウザ側で Accept してください)", flush=True)
        await ai.send_challenges(args.name, args.battles)

    ai_wins = ai.n_won_battles
    n = ai.n_finished_battles or args.battles
    print(f"[human_battle] 結果: あなた {n - ai_wins}勝 - AI {ai_wins}勝",
          flush=True)
    if not getattr(args, "log", True):   # 疎通確認 (check_human_battle) は記録しない
        return
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "t": time.time(), "human": args.name, "opponent": args.opponent,
            "style": args.style if args.opponent == "model" else None,
            "n": n, "human_wins": n - ai_wins, "ai_wins": ai_wins,
        }, ensure_ascii=False) + "\n")
    print(f"[human_battle] 記録: {LOG_PATH}")


def main() -> None:
    ap = argparse.ArgumentParser(description="人間 vs AI 対戦 (ローカルShowdown)")
    ap.add_argument("--name", required=True,
                    help="人間側のShowdownユーザー名 (ブラウザで入力する名前)")
    ap.add_argument("--opponent", default="model",
                    choices=["model", "benchmark", "search"],
                    help="model=学習済み方策 / benchmark=上位構築ヒューリスティクス"
                         " / search=探索エキスパート")
    ap.add_argument("--style", default="balance",
                    help="modelの性格 (balance/offense/cycle)")
    ap.add_argument("--depth", type=int, default=2, help="searchの読み深さ")
    ap.add_argument("--battles", type=int, default=1)
    ap.add_argument("--mode", default="challenge", choices=["challenge", "accept"],
                    help="challenge=AIから申請 / accept=人間からの申請を待つ")
    ap.add_argument("--timer", action="store_true", help="対戦タイマーを有効化")
    args = ap.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
