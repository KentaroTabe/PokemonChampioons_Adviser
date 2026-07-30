"""A/B比較の採否をあらかじめ決めた基準で機械的に判定する。

    python -m tools.ab_decision --plan            # 必要な対戦数を出す
    python -m tools.ab_decision --a 0.425 --b 0.467 --n 600

■ なぜ事前に決めるのか
このセッションでは「測定が壊れているのに、もっともらしい結論が出る」失敗を
繰り返した (評価凍結バグ / _bestの上振れ / auto_tuneのノイズ選択 /
同一チェックポイントの4回測定)。結果を見てから基準を決めると、
差が出た側に都合よく解釈できてしまう。判定式を先に固定する。

■ 事前登録する基準
  最小有効差 MIN_EFFECT = 0.03
    これ未満の改善のために報酬設計を変える価値はない (目標までの差0.069に
    対して小さすぎ、複雑さと再現性のリスクに見合わない)
  有意水準 ALPHA = 0.05 (両側) / 検出力 POWER = 0.80

■ 判定
  採用   : 差 >= MIN_EFFECT かつ 95%信頼区間の下限 > 0 かつ 全シードで符号一致
  棄却   : 95%信頼区間の上限 < MIN_EFFECT (意味のある改善を否定できる)
  判定不能: 上記以外。**1回だけ**対戦数を倍にして再測定し、それでも
            判定不能なら棄却として扱う (何度も測り直すと偶然の有意が出る)
"""
from __future__ import annotations

import argparse
import math

MIN_EFFECT = 0.03
ALPHA = 0.05
POWER = 0.80
Z_ALPHA = 1.96      # 両側5%
Z_BETA = 0.84       # 検出力80%
SEEDS = 3           # 1条件あたりの独立した学習回数


def required_n(delta: float = MIN_EFFECT, p: float = 0.5) -> int:
    """条件あたりに必要な対戦数 (2群比較)"""
    return math.ceil(2 * (Z_ALPHA + Z_BETA) ** 2 * p * (1 - p) / delta ** 2)


def decide(a: float, b: float, n: int, seed_signs: list = None) -> dict:
    """a=基準条件の勝率 / b=候補の勝率 / n=条件あたりの対戦数"""
    diff = b - a
    se = math.sqrt(a * (1 - a) / n + b * (1 - b) / n)
    lo, hi = diff - Z_ALPHA * se, diff + Z_ALPHA * se
    agree = (seed_signs is None
             or all(s > 0 for s in seed_signs)
             or all(s < 0 for s in seed_signs))
    if diff >= MIN_EFFECT and lo > 0 and agree:
        verdict = "採用"
    elif hi < MIN_EFFECT:
        verdict = "棄却"
    else:
        verdict = "判定不能"
    return {"diff": diff, "se": se, "ci": (lo, hi), "verdict": verdict,
            "seeds_agree": agree}


def main() -> None:
    ap = argparse.ArgumentParser(description="A/B比較の事前登録された判定")
    ap.add_argument("--plan", action="store_true", help="必要な対戦数を表示")
    ap.add_argument("--a", type=float, help="基準条件の勝率")
    ap.add_argument("--b", type=float, help="候補条件の勝率")
    ap.add_argument("--n", type=int, help="条件あたりの対戦数")
    ap.add_argument("--seed-signs", default="",
                    help="シードごとの差の符号 (例: 1,1,-1)")
    args = ap.parse_args()

    if args.plan or args.a is None:
        print(f"■ 事前登録した基準")
        print(f"  最小有効差 {MIN_EFFECT} / 有意水準 {ALPHA} / 検出力 {POWER}")
        print(f"  1条件あたりの学習回数 (シード) {SEEDS}")
        print(f"\n■ 必要な対戦数 (1条件あたり)")
        for d in (0.05, 0.04, 0.03, 0.02):
            n = required_n(d)
            mark = " ← 採用基準" if abs(d - MIN_EFFECT) < 1e-9 else ""
            print(f"  差{d:.2f}を検出: {n:,}戦 (2条件で{n * 2:,}戦){mark}")
        print(f"\n  ※ 相手チームの並びを両条件で揃えると分散が下がるため、"
              f"実効的にはこれより少なくて済む")
        return

    signs = [float(s) for s in args.seed_signs.split(",") if s]
    r = decide(args.a, args.b, args.n, signs or None)
    print(f"  基準 {args.a:.3f} / 候補 {args.b:.3f} / 各{args.n:,}戦")
    print(f"  差 {r['diff']:+.3f} ± {r['se']:.3f} "
          f"(95%CI {r['ci'][0]:+.3f}〜{r['ci'][1]:+.3f})")
    if signs:
        print(f"  シード間の符号一致: {'はい' if r['seeds_agree'] else 'いいえ'}")
    print(f"\n  判定: {r['verdict']}")
    if r["verdict"] == "判定不能":
        print(f"  → 対戦数を倍 ({args.n * 2:,}戦) にして1回だけ再測定する。"
              "それでも判定不能なら棄却とする")


if __name__ == "__main__":
    main()
