"""目標勝率の認定測定 (Phase 0)。

    python -m tools.certify --battles 400
    python -m tools.certify --battles 400 --styles balance
    python -m tools.certify --battles 200 --skip holdout

14分ごとの通常ベンチ (50戦) は標準誤差0.071あり、0.58と0.65すら区別できない。
目標 (0.70) に届いたかを判定するには対戦数を増やす必要がある。
本ツールは**既存ベンチには一切触れず**、別枠で以下を測る:

  1. matchup / current … 既存ベンチと同条件 (連続性の確認用)
  2. model   / current … 自分側だけ選出モデル (配布アドバイザーと同じ条件)
  3. model   / best    … 配布版の実力。best_checkpoint の記録値は
                         「改善時のみ更新」= ノイズの最大値なので上振れしている
  4. model   / holdout … 学習に使っていない構築だけで測る。1-3の上昇が
                         相手ヒューリスティクスへの過学習でないかの検証

結果は logs/certify_<時刻>.json に残す。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from champions_agent.config import PLAY_STYLES
from champions_agent.train.evaluate import run_evaluation

OUT_DIR = Path(__file__).resolve().parent.parent / "logs"

# (名前, checkpoint, selection, own_teams, 説明)
CASES = [
    ("matchup", "current", "matchup", "train", "既存ベンチと同条件"),
    ("model", "current", "model", "train", "選出モデル (配布条件)"),
    ("best", "best", "model", "train", "配布版 + 選出モデル"),
    ("holdout", "current", "model", "holdout", "未学習の構築のみ"),
]


def _se(p: float, n: int) -> float:
    return (p * (1 - p) / n) ** 0.5 if n else 0.0


async def _run(styles, battles, skip) -> dict:
    out = {"battles": battles, "at": time.strftime("%Y-%m-%d %H:%M:%S"),
           "results": {}}
    for name, ckpt, sel, teams, desc in CASES:
        if name in skip:
            continue
        out["results"][name] = {}
        for style in styles:
            r = await run_evaluation(
                play_style=style, n_battles=battles,
                opponent_kind="benchmark", checkpoint=ckpt,
                selection=sel, own_teams=teams)
            wr = r["win_rate"]
            out["results"][name][style] = wr
            print(f"  {name:8s} {style:8s} {wr:.3f} ± {_se(wr, battles):.3f}",
                  flush=True)
        vals = list(out["results"][name].values())
        mean = sum(vals) / len(vals) if vals else 0.0
        out["results"][name]["_mean"] = mean
        print(f"  {name:8s} {'平均':8s} {mean:.3f}  ({desc})\n", flush=True)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="目標勝率の認定測定")
    ap.add_argument("--battles", type=int, default=400)
    ap.add_argument("--styles", default=",".join(PLAY_STYLES))
    ap.add_argument("--skip", default="", help="実行しないケース (カンマ区切り)")
    ap.add_argument("--target", type=float, default=0.70)
    args = ap.parse_args()

    styles = [s for s in args.styles.split(",") if s]
    skip = {s for s in args.skip.split(",") if s}
    print(f"[certify] 各ケース {args.battles}戦 / 性格 {styles} "
          f"(標準誤差 約{_se(0.6, args.battles):.3f})\n")

    out = asyncio.run(_run(styles, args.battles, skip))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"certify_{time.strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1),
                    encoding="utf-8")

    print("=== 判定 ===")
    res = out["results"]
    for name, _, _, _, desc in CASES:
        if name not in res:
            continue
        mean = res[name]["_mean"]
        se = _se(mean, args.battles * len(styles))
        ok = "達成" if mean - 2 * se >= args.target else \
             ("到達可能性あり" if mean + 2 * se >= args.target else "未達")
        print(f"  {name:8s} {mean:.3f} ± {se:.3f} → 目標{args.target} {ok}"
              f"  ({desc})")
    if "model" in res and "holdout" in res:
        gap = res["model"]["_mean"] - res["holdout"]["_mean"]
        note = "⚠ 未学習構築で大きく落ちる (過学習の疑い)" if gap > 0.08 \
            else "未学習構築でも同水準 (過学習の兆候なし)"
        print(f"\n  学習済み構築 - 未学習構築 = {gap:+.3f} → {note}")
    print(f"\n記録: {path}")


if __name__ == "__main__":
    main()
