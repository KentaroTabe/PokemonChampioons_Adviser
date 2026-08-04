"""実対戦ログから読み負荷 (重い択) を事後計測する。

    python -m tools.read_burden_report [--days 30]

対戦ログの advice レコードには択評価 (gtheory: 行動ごとの期待値/保証値) が
残っているため、追加の記録なしで過去の対戦へ遡って測れる。
docs/READ_BURDEN_DESIGN.md Phase A/B: 勝ち戦と負け戦で読み負荷が違うかを見る。
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import time
from pathlib import Path

from champions_agent.env.read_burden import HEAVY_GAP, top_gap

BATTLE_DIR = Path(__file__).resolve().parent.parent / "logs" / "battles"


def battle_burden(path: str) -> dict | None:
    """1対戦ぶんのログから (重い択の数, 択評価のあったアドバイス数, 勝敗)"""
    heavy, n_advice, gaps = 0, 0, []
    outcome = None
    n_battle_scenes = 0
    for line in open(path, encoding="utf-8"):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("type") == "scene" and d.get("scene") in (
                "command", "move_select", "field"):
            n_battle_scenes += 1
        if d.get("type") == "outcome" or d.get("outcome"):
            outcome = d.get("outcome") or outcome
        st = (d.get("state") or {})
        if st.get("outcome"):
            outcome = st["outcome"]
        if d.get("type") != "advice" or d.get("kind") != "battle":
            continue
        actions = ((d.get("advice") or {}).get("gtheory") or {}).get("actions")
        gap = top_gap(actions or [])
        if gap is None:
            continue
        n_advice += 1
        gaps.append(gap)
        if gap > HEAVY_GAP:
            heavy += 1
    if n_battle_scenes < 3 or n_advice == 0:
        return None
    return {"file": Path(path).name, "outcome": outcome,
            "heavy": heavy, "n_advice": n_advice,
            "gap_mean": sum(gaps) / len(gaps)}


def main() -> None:
    ap = argparse.ArgumentParser(description="実対戦の読み負荷の事後計測")
    ap.add_argument("--days", type=float, default=30)
    args = ap.parse_args()

    cutoff = time.time() - args.days * 86400
    rows = []
    for f in sorted(glob.glob(str(BATTLE_DIR / "*.jsonl"))):
        if Path(f).stat().st_mtime < cutoff:
            continue
        r = battle_burden(f)
        if r:
            rows.append(r)

    if not rows:
        print("択評価つきの対戦ログがありません")
        return

    print(f"■ 読み負荷レポート (過去{args.days:.0f}日 / {len(rows)}戦 / "
          f"重い択のしきい値 gap>{HEAVY_GAP})")
    print(f"{'対戦':34s} {'勝敗':4s} {'重い択':>4s} {'択評価数':>5s} "
          f"{'平均gap':>7s}")
    for r in rows:
        print(f"  {r['file']:32s} {str(r['outcome']):4s} "
              f"{r['heavy']:4d} {r['n_advice']:5d} {r['gap_mean']:7.3f}")

    wins = [r for r in rows if r["outcome"] == "win"]
    losses = [r for r in rows if r["outcome"] == "loss"]

    def mean(v):
        return sum(v) / len(v) if v else float("nan")

    print(f"\n全体: 重い択 平均{mean([r['heavy'] for r in rows]):.1f}回/戦 "
          f"/ 平均gap {mean([r['gap_mean'] for r in rows]):.3f}")
    if wins and losses:
        hw = mean([r["heavy"] for r in wins])
        hl = mean([r["heavy"] for r in losses])
        print(f"勝ち{len(wins)}戦: 重い択{hw:.1f}回/戦 / "
              f"負け{len(losses)}戦: {hl:.1f}回/戦 / 差 {hl - hw:+.1f}")
        # 点双列相関 (勝敗 vs 重い択回数)
        xs = [1.0 if r["outcome"] == "win" else 0.0
              for r in rows if r["outcome"] in ("win", "loss")]
        ys = [float(r["heavy"])
              for r in rows if r["outcome"] in ("win", "loss")]
        n = len(xs)
        mx, my_ = mean(xs), mean(ys)
        sxy = sum((x - mx) * (y - my_) for x, y in zip(xs, ys))
        sxx = sum((x - mx) ** 2 for x in xs)
        syy = sum((y - my_) ** 2 for y in ys)
        if sxx > 0 and syy > 0:
            r_ = sxy / math.sqrt(sxx * syy)
            print(f"勝敗と重い択の相関 r={r_:+.2f} (n={n}, "
                  "負なら「択が多い試合ほど負けている」)")
    else:
        print("勝敗確定の内訳が片側のみのため勝敗比較はスキップ")


if __name__ == "__main__":
    main()
