"""P7' の判定: 観測更新つき多世界 (K=8) − 現行 (K=0) の対応比較。

    python -m tools.p7b_verdict logs/p7b_verdict
採用条件 (事前登録): 差 ≥ +0.05 かつ 95%CI下限 > 0。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from tools.team_proposal import paired_verdict


def main(out_dir: str) -> None:
    d = Path(out_dir)
    arms = {n: json.loads((d / f"p7b_{n}.json").read_text(encoding="utf-8"))
            for n in ("current", "updated") if (d / f"p7b_{n}.json").exists()}
    if len(arms) < 2:
        print("腕の結果が足りません:", list(arms))
        return
    for n, r in arms.items():
        st = r["stats"]
        print(f"{n:<8} K={r['belief_k']} updates={r.get('belief_updates')} 勝率 {r['win_rate']:.3f}  "
              f"p50 {r['latency_p50_ms']:.0f}ms p95 {r['latency_p95_ms']:.0f}ms  "
              f"探索/FB/例外 {st['decide']}/{st['fallback']}/{st['error']}")
    v = paired_verdict(arms["updated"]["outcomes"], arms["current"]["outcomes"])
    se = v.get("se", 0.0)
    lo = v["mean"] - 1.96 * se
    print(f"updated − current: {v['mean']:+.3f} ± {se:.3f} (95%CI下限 {lo:+.3f}) → {v['verdict']}")
    print("判定:", "採用" if (v["mean"] >= 0.05 and lo > 0) else "棄却")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "logs/p7b_verdict")
