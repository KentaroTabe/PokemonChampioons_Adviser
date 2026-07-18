"""selfplay経路のスモークテスト。

現在のチェックポイントを一時的に相手プールへ投入し、
「対戦相手がselfplayプールから供給される状態」で短い学習を回して検証する。
テスト後にプールを元の状態へ戻す。

    python -m tools.smoke_selfplay [--timesteps 2048] [--play-style balance]
"""
from __future__ import annotations

import argparse
import signal
import sys
import time

from champions_agent.config import MODELS_DIR


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timesteps", type=int, default=2048)
    parser.add_argument("--play-style", type=str, default="balance")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()

    signal.signal(signal.SIGALRM,
                  lambda s, f: (print("[smoke_selfplay] TIMEOUT"), sys.exit(1)))
    signal.alarm(args.timeout)

    from champions_agent.train.opponent_pool import OpponentPool

    ckpt = MODELS_DIR / f"battle_policy_{args.play_style}.zip"
    if not ckpt.exists():
        print(f"[smoke_selfplay] チェックポイントがありません: {ckpt}")
        sys.exit(1)

    pool = OpponentPool()
    before = len(pool.entries())
    added = pool.add(args.play_style, ckpt, win_rate=-1.0)  # テスト用の仮投入
    print(f"[smoke_selfplay] テスト用にプールへ仮投入: {added.name}")

    try:
        t0 = time.time()
        from champions_agent.train.train_battle import train
        train(total_timesteps=args.timesteps, play_style=args.play_style, resume=True)
        print(f"[smoke_selfplay] 学習完了: {time.time() - t0:.1f}秒")
    finally:
        # 仮投入したエントリを掃除して元の状態へ戻す
        pool2 = OpponentPool()
        for e in list(pool2.entries()):
            if e["file"] == added.name:
                (added).unlink(missing_ok=True)
                pool2.state["entries"].remove(e)
        pool2._save()
        print(f"[smoke_selfplay] プールを復元 ({before}件)")


if __name__ == "__main__":
    main()
