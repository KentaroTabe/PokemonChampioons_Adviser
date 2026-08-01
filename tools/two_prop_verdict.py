"""2条件の勝率比較の事前登録判定 (2標本比率)。

    python -m tools.two_prop_verdict <winsA> <nA> <winsB> <nB> [--delta 0.02]

A = 新条件 / B = 基準。ab_decision と同じ思想:
  採用     : 差 >= delta かつ 95%CI下限 > 0
  棄却     : 95%CI上限 < delta
  判定不能 : どちらでもない → 戦数を倍増して1回だけ再測定。なお不能なら棄却
判定規則は測定の前に決め、結果を見てから動かさない。
"""
from __future__ import annotations

import argparse
import math


def decide(wins_a: int, n_a: int, wins_b: int, n_b: int,
           delta: float = 0.02) -> dict:
    pa, pb = wins_a / n_a, wins_b / n_b
    diff = pa - pb
    se = math.sqrt(pa * (1 - pa) / n_a + pb * (1 - pb) / n_b)
    lo, hi = diff - 1.96 * se, diff + 1.96 * se
    if diff >= delta and lo > 0:
        verdict = "採用"
    elif hi < delta:
        verdict = "棄却"
    else:
        verdict = "判定不能"
    return {"p_new": pa, "p_base": pb, "diff": diff,
            "ci": (lo, hi), "verdict": verdict, "delta": delta}


def main() -> None:
    ap = argparse.ArgumentParser(description="2条件の勝率比較の事前登録判定")
    ap.add_argument("wins_a", type=int)
    ap.add_argument("n_a", type=int)
    ap.add_argument("wins_b", type=int)
    ap.add_argument("n_b", type=int)
    ap.add_argument("--delta", type=float, default=0.02,
                    help="採用に必要な最小効果量")
    args = ap.parse_args()
    r = decide(args.wins_a, args.n_a, args.wins_b, args.n_b, args.delta)
    lo, hi = r["ci"]
    print(f"新条件 {r['p_new']:.3f} / 基準 {r['p_base']:.3f} / "
          f"差 {r['diff']:+.3f} (95%CI {lo:+.3f}〜{hi:+.3f})")
    print(f"判定: {r['verdict']}  [最小効果量 {r['delta']}]")


if __name__ == "__main__":
    main()
