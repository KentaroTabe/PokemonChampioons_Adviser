"""日次の進捗トラッキング: チェックポイントと配布条件を定点測定する。

    python -m tools.track_progress [--battles 3000]

- current + 相性選出 (学習の生の進捗)
- _best + モデル選出 (配布条件 = 目標0.70を測る土俵)
を同一シードで測り、logs/progress_tracking.jsonl へ追記する。
直近の履歴から「停滞 (2日連続で上昇なし)」の簡易判定も表示する。
どちらも --no-save (昇格判定・プール抽選を汚さない)。
"""
from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "logs" / "progress_tracking.jsonl"
OPP_SEED = "20260730"


def _run_eval(battles: int, checkpoint: str, selection: str,
              models_dir: str | None = None) -> dict | None:
    import os
    env = dict(os.environ)
    if models_dir:
        env["CHAMPIONS_MODELS_DIR"] = models_dir
    r = subprocess.run(
        [sys.executable, "-m", "champions_agent.train.evaluate",
         "--play-style", "balance", "--battles", str(battles),
         "--opponent", "benchmark", "--checkpoint", checkpoint,
         "--selection", selection, "--opp-seed", OPP_SEED, "--no-save"],
        cwd=REPO, env=env, capture_output=True, text=True)
    for line in r.stdout.splitlines():
        if line.startswith("[evaluate] "):
            try:
                return ast.literal_eval(line[len("[evaluate] "):])
            except (ValueError, SyntaxError):
                pass
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description="日次の進捗トラッキング")
    ap.add_argument("--battles", type=int, default=3000)
    args = ap.parse_args()

    row = {"date": time.strftime("%Y-%m-%d %H:%M"),
           "battles": args.battles, "opp_seed": int(OPP_SEED)}
    for key, ckpt, sel in [("current_matchup", "current", "matchup"),
                           ("best_model", "best", "model")]:
        r = _run_eval(args.battles, ckpt, sel)
        row[key] = round(r["win_rate"], 4) if r else None
        print(f"{key}: {row[key]}")

    # 新アーキ (v7+set encoder) の隔離学習が走っていれば同条件で定点測定
    # (docs/RL_V7_SET_ENCODER_DESIGN.md。判定は開始7日後の事前登録に従い、
    #  途中の値で結論は出さない)
    arch_dir = REPO / "logs" / "arch_v7" / "checkpoints"
    if (arch_dir / "battle_policy_balance.zip").exists():
        r = _run_eval(args.battles, "current", "matchup",
                      models_dir=str(arch_dir))
        row["arch_v7"] = round(r["win_rate"], 4) if r else None
        print(f"arch_v7: {row['arch_v7']}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # 停滞の簡易判定: 直近3点の current_matchup を見る
    rows = [json.loads(l) for l in OUT.read_text(encoding="utf-8").splitlines()
            if l.strip()]
    vals = [r["current_matchup"] for r in rows[-3:]
            if r.get("current_matchup") is not None]
    se = (0.25 / args.battles) ** 0.5
    print(f"\n履歴 (直近{len(rows[-7:])}件):")
    for r in rows[-7:]:
        arch = (f" arch_v7={r['arch_v7']}"
                if r.get("arch_v7") is not None else "")
        print(f"  {r['date']}  current={r.get('current_matchup')} "
              f"best_model={r.get('best_model')}{arch}")
    if len(vals) >= 3 and max(vals[1:]) <= vals[0] + se:
        print(f"⚠ 停滞の兆候: 直近2回が {vals[0]:.3f}+SE({se:.3f}) を超えていない。"
              "2日連続なら次の設計へ移る取り決め")
    else:
        print("上昇継続または判定にはデータ不足")


if __name__ == "__main__":
    main()
