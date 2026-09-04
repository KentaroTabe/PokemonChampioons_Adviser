"""P9 の判定: advisor-as-player の基準 (A) と統合腕 (B/C) の対応比較。

    python -m tools.p9_verdict <A.json> <B.json> [C.json]

事前登録 (2026-09-04 20:30): (B)−(A) または (C)−(A) ≥ +0.05 かつ 95%CI下限 > 0 で採用。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from tools.team_proposal import paired_verdict


def _load(p: str) -> dict:
    return json.loads(Path(p).read_text(encoding="utf-8"))


def main(paths: list) -> None:
    arms = {name: _load(p) for name, p in zip("ABC", paths)}
    for name, r in arms.items():
        print(f"{name}: 勝率 {r['win_rate']:.3f} (K={r['belief_k']}, blend={r.get('search_blend', 0)}, "
              f"q={r.get('sensor_q', 0)}) p50 {r['latency_p50_ms']:.0f}ms / p95 {r['latency_p95_ms']:.0f}ms")
    a = arms["A"]
    for name in ("B", "C"):
        if name not in arms:
            continue
        v = paired_verdict(arms[name]["outcomes"], a["outcomes"])
        se = v.get("se", 0.0)
        lo = v["mean"] - 1.96 * se
        gate = v["mean"] >= 0.05 and lo > 0
        print(f"{name} − A: {v['mean']:+.3f} ± {se:.3f} (95%CI下限 {lo:+.3f}) → "
              f"{v['verdict']} / ゲート{'通過' if gate else '未達'}")
    if "B" in arms and "C" in arms:
        v = paired_verdict(arms["C"]["outcomes"], arms["B"]["outcomes"])
        print(f"C − B: {v['mean']:+.3f} ± {v.get('se', 0):.3f} → {v['verdict']}")


if __name__ == "__main__":
    main(sys.argv[1:])
