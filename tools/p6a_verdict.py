"""P6-a の判定: RL価値の葉評価 ON/OFF の対応比較。

    python -m tools.p6a_verdict logs/p6a_verdict

事前登録: 差 (on−off) の95%CIが 0 を跨がず負なら「葉評価を無効化」、
正なら「維持」、跨ぐなら「維持 (較正課題として観察)」。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from tools.team_proposal import paired_verdict


def main(out_dir: str) -> None:
    d = Path(out_dir)
    arms = {}
    for name in ("value_off", "value_on"):
        f = d / f"p6a_{name}.json"
        if f.exists():
            arms[name] = json.loads(f.read_text(encoding="utf-8"))
    if len(arms) < 2:
        print("腕の結果が足りません:", list(arms))
        return
    for name, r in arms.items():
        print(f"{name:<10} 勝率 {r['win_rate']:.3f}  p50 {r['latency_p50_ms']:.0f}ms  "
              f"p95 {r['latency_p95_ms']:.0f}ms")
    v = paired_verdict(arms["value_on"]["outcomes"], arms["value_off"]["outcomes"])
    se = v.get("se", 0.0)
    lo, hi = v["mean"] - 1.96 * se, v["mean"] + 1.96 * se
    print(f"on − off: {v['mean']:+.3f} ± {se:.3f} (95%CI {lo:+.3f}〜{hi:+.3f}) → {v['verdict']}")
    if hi < 0:
        print("判定: 葉評価を無効化 (有害)")
    elif lo > 0:
        print("判定: 葉評価を維持 (有益)")
    else:
        print("判定: 維持 (効果は誤差圏。較正課題として観察)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "logs/p6a_verdict")
