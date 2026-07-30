"""学習ログから fps (学習速度) の推移を出す。

    python -m tools.train_speed [表示するログ数]

選出モデルの推論を学習ループに入れる等、1対戦あたりのコストが増える変更を
入れたときに、ステップ数が落ちていないかを確認するのに使う
(勝率が下がった原因が「方策の劣化」なのか「学習量の減少」なのかを切り分ける)。
"""
from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "champions_agent" / "train" / "logs"
FPS_RE = re.compile(r"\|\s*fps\s*\|\s*(\d+)\s*\|")
STEPS_RE = re.compile(r"\|\s*total_timesteps\s*\|\s*(\d+)\s*\|")


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    logs = sorted(LOG_DIR.glob("nightly_*.log"))[-n:]
    print(f"■ 学習速度 (直近{len(logs)}ログ)")
    for p in logs:
        text = p.read_text(errors="ignore")
        fps = [int(x) for x in FPS_RE.findall(text)]
        steps = [int(x) for x in STEPS_RE.findall(text)]
        if not fps:
            continue
        stamp = p.stem.replace("nightly_", "")
        try:
            when = datetime.strptime(stamp, "%Y-%m-%d_%H%M").strftime("%m-%d %H:%M")
        except ValueError:
            when = stamp
        print(f"  {when}  fps 中央値{sorted(fps)[len(fps) // 2]:>4} "
              f"(最小{min(fps)}/最大{max(fps)})  "
              f"総ステップ{max(steps) if steps else '?'}")


if __name__ == "__main__":
    main()
