"""学習ループのスモークテスト (タイムアウト付き)。

    python -m tools.smoke_train [--timesteps 2048] [--play-style balance]
                                [--format <id>] [--resume] [--timeout 300]

事前にShowdownサーバー (既定: ポート8100) を起動しておくこと。
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
import time

from champions_agent.config import TRAINING_BATTLE_FORMAT, DEFAULT_PLAY_STYLE


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timesteps", type=int, default=2048)
    parser.add_argument("--play-style", type=str, default=DEFAULT_PLAY_STYLE)
    parser.add_argument("--format", type=str, default=TRAINING_BATTLE_FORMAT)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--n-envs", type=int,
                        default=int(os.environ.get("N_ENVS", "1")),
                        help="並列環境数 (Showdown通信律速の高速化)")
    args = parser.parse_args()

    def handler(sig, frame):
        print(f"[smoke_train] TIMEOUT: {args.timeout}秒で打ち切り")
        sys.exit(1)

    signal.signal(signal.SIGALRM, handler)
    signal.alarm(args.timeout)

    t0 = time.time()
    from champions_agent.train.train_battle import train
    train(total_timesteps=args.timesteps, battle_format=args.format,
          play_style=args.play_style, resume=args.resume, n_envs=args.n_envs)
    print(f"[smoke_train] 完了: {time.time() - t0:.1f}秒")


if __name__ == "__main__":
    main()
