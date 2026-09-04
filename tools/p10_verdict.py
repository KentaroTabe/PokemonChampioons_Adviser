"""P10 の判定: 雑音下 estimate − unaware の対応比較。

    python -m tools.p10_verdict logs/p10_verdict
採用条件 (事前登録): 差 ≥ +0.03 かつ 95%CI下限 > 0。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from tools.team_proposal import paired_verdict


def main(out_dir: str) -> None:
    d = Path(out_dir)
    arms = {n: json.loads((d / f"p10_{n}.json").read_text(encoding="utf-8"))
            for n in ("unaware", "estimate") if (d / f"p10_{n}.json").exists()}
    if len(arms) < 2:
        print("腕の結果が足りません:", list(arms))
        return
    for n, r in arms.items():
        print(f"{n:<9} 勝率 {r['win_rate']:.3f}  p50 {r['latency_p50_ms']:.0f}ms  "
              f"p95 {r['latency_p95_ms']:.0f}ms  ({r['n_battles']}戦)")
    v = paired_verdict(arms["estimate"]["outcomes"], arms["unaware"]["outcomes"])
    se = v.get("se", 0.0)
    lo = v["mean"] - 1.96 * se
    print(f"estimate − unaware: {v['mean']:+.3f} ± {se:.3f} (95%CI下限 {lo:+.3f}) → {v['verdict']}")
    print("判定:", "採用 (推定を有効のまま)" if (v["mean"] >= 0.03 and lo > 0)
          else "棄却 (推定は無効化を検討)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "logs/p10_verdict")
