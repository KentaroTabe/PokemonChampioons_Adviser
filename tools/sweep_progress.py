"""報酬スイープ各条件の到達ステップ数を出す。

    python -m tools.sweep_progress

途中で止まった場合、条件間でステップ数が揃っているかを確認する。
揃っていれば --eval-only で比較を続行できる (揃っていないと学習量の差が
報酬設計の差に見えてしまう)。
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SWEEP_ROOT = REPO / "logs" / "reward_sweep"
STEPS_RE = re.compile(r"\|\s*total_timesteps\s*\|\s*(\d+)\s*\|")


def main() -> None:
    rows = []
    for log in sorted(SWEEP_ROOT.glob("*/train_*.log")):
        steps = [int(x) for x in STEPS_RE.findall(log.read_text(errors="ignore"))]
        rows.append((log.parent.name, log.stem.replace("train_", ""),
                     max(steps) if steps else 0))
    if not rows:
        print("スイープのログがありません")
        return
    print("■ 到達ステップ")
    for name, style, steps in rows:
        print(f"  {name:8s} {style:8s} {steps:,}")
    vals = [s for _, _, s in rows]
    spread = (max(vals) - min(vals)) / max(vals) if max(vals) else 0
    print(f"\n  最大との差: {spread * 100:.1f}%")
    if spread > 0.05:
        print("  ⚠ 条件間でステップ数が5%以上ずれている。"
              "学習量の差が報酬設計の差に見えるため、揃えてから比較すること")
    else:
        print("  条件間のステップ数は揃っている (比較可能)")


if __name__ == "__main__":
    main()
