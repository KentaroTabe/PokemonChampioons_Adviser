"""P7 の判定: 3腕 (current / map / belief) の対応比較を出す。

    python -m tools.p7_verdict logs/p7_verdict

事前登録 (2026-09-04 19:40): (c)belief−(a)current ≥ +0.05 かつ
(c)belief−(b)map の95%CI下限 > 0 で採用。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from tools.team_proposal import paired_verdict


def main(out_dir: str) -> None:
    d = Path(out_dir)
    arms = {}
    for name in ("current", "map", "belief"):
        f = d / f"p7_{name}.json"
        if f.exists():
            arms[name] = json.loads(f.read_text(encoding="utf-8"))
    if len(arms) < 2:
        print("腕の結果が足りません:", list(arms))
        return
    print("腕        勝率    p50ms  p95ms  探索/FB/例外")
    for name, r in arms.items():
        st = r["stats"]
        print(f"{name:<9} {r['win_rate']:.3f}  {r['latency_p50_ms']:>5.0f}  "
              f"{r['latency_p95_ms']:>5.0f}  {st['decide']}/{st['fallback']}/{st['error']}")
    print()
    for a, b in (("belief", "current"), ("belief", "map"), ("map", "current")):
        if a in arms and b in arms:
            v = paired_verdict(arms[a]["outcomes"], arms[b]["outcomes"])
            lo = v["mean"] - 1.96 * v.get("se", 0.0)
            print(f"{a} − {b}: {v['mean']:+.3f} ± {v.get('se', 0):.3f} "
                  f"(95%CI下限 {lo:+.3f}) → {v['verdict']}")
    if all(k in arms for k in ("current", "map", "belief")):
        v1 = paired_verdict(arms["belief"]["outcomes"], arms["current"]["outcomes"])
        v2 = paired_verdict(arms["belief"]["outcomes"], arms["map"]["outcomes"])
        gate1 = v1["mean"] >= 0.05
        gate2 = (v2["mean"] - 1.96 * v2.get("se", 0.0)) > 0
        print(f"\n事前登録ゲート: belief−current ≥ +0.05 → {gate1}; "
              f"belief−map CI下限 > 0 → {gate2}")
        print("判定:", "採用" if (gate1 and gate2) else
              ("MAP採用・多世界は棄却" if (v1["mean"] >= 0.05 and not gate2
                                       and paired_verdict(arms["map"]["outcomes"],
                                                          arms["current"]["outcomes"])["mean"] >= 0.05)
               else "棄却"))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "logs/p7_verdict")
