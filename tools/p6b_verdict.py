"""P6-b の判定: 相手行動の事前分布 λ=0 vs λ=0.5 の対応比較。

    python -m tools.p6b_verdict logs/p6b_verdict
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from tools.team_proposal import paired_verdict


def main(out_dir: str) -> None:
    d = Path(out_dir)
    arms = {}
    for name in ("mix0", "mix05"):
        f = d / f"p6b_{name}.json"
        if f.exists():
            arms[name] = json.loads(f.read_text(encoding="utf-8"))
    if len(arms) < 2:
        print("腕の結果が足りません:", list(arms))
        return
    for name, r in arms.items():
        print(f"{name:<6} λ={r['opp_prior_mix']} 勝率 {r['win_rate']:.3f}  "
              f"p50 {r['latency_p50_ms']:.0f}ms  p95 {r['latency_p95_ms']:.0f}ms")
    v = paired_verdict(arms["mix05"]["outcomes"], arms["mix0"]["outcomes"])
    se = v.get("se", 0.0)
    lo = v["mean"] - 1.96 * se
    print(f"λ0.5 − λ0: {v['mean']:+.3f} ± {se:.3f} (95%CI下限 {lo:+.3f}) → {v['verdict']}")
    print("判定:", "採用 (λ=0.5)" if (v["mean"] >= 0.05 and lo > 0) else "棄却 (λ=0 維持)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "logs/p6b_verdict")
