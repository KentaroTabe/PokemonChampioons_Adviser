"""P8 の判定: 雑音下での aware−unaware (頑健性) と、雑音なしでの aware−unaware (ヘッジの代償)。

    python -m tools.p8_verdict logs/p8_verdict
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from tools.team_proposal import paired_verdict


def main(out_dir: str) -> None:
    d = Path(out_dir)
    arms = {}
    for name in ("noise_unaware", "noise_aware", "clean_unaware", "clean_aware"):
        f = d / f"p8_{name}.json"
        if f.exists():
            arms[name] = json.loads(f.read_text(encoding="utf-8"))
    for name, r in arms.items():
        print(f"{name:<14} noise={r['sensor_noise']} q={r['sensor_q']} 勝率 {r['win_rate']:.3f}  "
              f"p50 {r['latency_p50_ms']:.0f}ms")
    def diff(a, b):
        v = paired_verdict(arms[a]["outcomes"], arms[b]["outcomes"])
        se = v.get("se", 0.0)
        print(f"{a} − {b}: {v['mean']:+.3f} ± {se:.3f} (95%CI {v['mean']-1.96*se:+.3f}〜{v['mean']+1.96*se:+.3f})")
        return v["mean"], se
    ok = all(k in arms for k in ("noise_unaware", "noise_aware"))
    if ok:
        m1, s1 = diff("noise_aware", "noise_unaware")
        if all(k in arms for k in ("clean_unaware", "clean_aware")):
            m2, s2 = diff("clean_aware", "clean_unaware")
            print("判定:", "採用" if (m1 - 1.96 * s1 > 0 and m2 + 1.96 * s2 > -0.03)
                  else "棄却 (雑音下の利得が有意でないか、雑音なしの代償が大きい)")
        if all(k in arms for k in ("clean_unaware", "noise_unaware")):
            diff("noise_unaware", "clean_unaware")   # 雑音そのものの害 (参考)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "logs/p8_verdict")
