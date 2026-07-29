"""学習の変更前後を統計で比較する (平均・ばらつき・有意性の目安)。

training_changes.json の変更点を境に、ベンチ勝率の平均と標準偏差を出す。
「変更で改善したか」「ばらつきが減ったか」を目視でなく数値で判断するため。

    python -m tools.compare_periods                    # 直近2区間を比較
    python -m tools.compare_periods --at "2026-07-28 10:00"
    python -m tools.compare_periods --list             # 変更点の一覧
"""
from __future__ import annotations

import argparse
import statistics

from tools.watch_training import load_changes, parse_logs


def _bench_series(cycles: list) -> dict:
    """性格 -> [(時刻, ベンチ勝率)]"""
    out: dict = {}
    for c in cycles:
        for style, st in c["styles"].items():
            if st.get("benchmark") is not None:
                out.setdefault(style, []).append((c["ts"], st["benchmark"]))
    return out


def _stats(vals: list) -> dict:
    if not vals:
        return {}
    return {"n": len(vals), "mean": statistics.mean(vals),
            "sd": statistics.pstdev(vals) if len(vals) > 1 else 0.0}


def compare(at: str, cycles: list, since: str | None = None) -> None:
    series = _bench_series(cycles)
    if since:
        series = {k: [(ts, v) for ts, v in pts if ts >= since]
                  for k, pts in series.items()}
        print(f"=== 境界: {at} (前区間は {since} 以降に限定) ===")
    else:
        print(f"=== 境界: {at} ===")
    print(f"{'性格':<9}{'前 n':>5}{'平均':>8}{'SD':>7}   "
          f"{'後 n':>5}{'平均':>8}{'SD':>7}   {'差':>7}")
    for style in sorted(series):
        before = [v for ts, v in series[style] if ts < at]
        after = [v for ts, v in series[style] if ts >= at]
        b, a = _stats(before), _stats(after)
        if not b or not a:
            continue
        diff = a["mean"] - b["mean"]
        print(f"{style:<9}{b['n']:>5}{b['mean']:>8.3f}{b['sd']:>7.3f}   "
              f"{a['n']:>5}{a['mean']:>8.3f}{a['sd']:>7.3f}   {diff:>+7.3f}")
    # 3性格をまとめた全体像 (サイクル単位の平均で見る)
    all_before, all_after = [], []
    for style, pts in series.items():
        if style == "stall":
            continue
        for ts, v in pts:
            (all_before if ts < at else all_after).append(v)
    b, a = _stats(all_before), _stats(all_after)
    if b and a:
        print(f"\n全体 (stall除く): 前 {b['mean']:.3f}±{b['sd']:.3f} (n={b['n']})"
              f" → 後 {a['mean']:.3f}±{a['sd']:.3f} (n={a['n']})")
        # 平均の差の目安: 標準誤差2つぶん離れていれば「差あり」と読む
        se = ((b["sd"] ** 2 / b["n"]) + (a["sd"] ** 2 / a["n"])) ** 0.5
        d = a["mean"] - b["mean"]
        verdict = "有意な差あり" if abs(d) > 2 * se else "ノイズ範囲 (差なし)"
        print(f"  平均差 {d:+.3f} / 標準誤差 {se:.3f} → {verdict}")
        sd_change = (a["sd"] - b["sd"]) / b["sd"] * 100 if b["sd"] else 0.0
        print(f"  ばらつき (SD) の変化: {sd_change:+.0f}%")


def main() -> None:
    ap = argparse.ArgumentParser(description="学習の変更前後の統計比較")
    ap.add_argument("--at", default=None, help="境界時刻 (既定: 最新の変更点)")
    ap.add_argument("--since", default=None,
                    help="前区間の開始時刻 (直前の別変更の影響を除くため)")
    ap.add_argument("--list", action="store_true", help="変更点の一覧")
    args = ap.parse_args()

    changes = load_changes()
    if args.list:
        for at, kind, label, detail in changes:
            print(f"{at} [{kind}] {label}\n    {detail}")
        return
    cycles = parse_logs()
    if not cycles:
        print("学習ログがありません")
        return
    at = args.at or (changes[-1][0] if changes else None)
    if at is None:
        print("比較する変更点がありません (--at で指定)")
        return
    compare(at, cycles, args.since)


if __name__ == "__main__":
    main()
