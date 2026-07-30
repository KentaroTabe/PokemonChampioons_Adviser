"""報酬設計の並列スイープ。

    python -m tools.reward_sweep --steps 400000 --battles 400
    python -m tools.reward_sweep --list

同じ種チェックポイントから複数の報酬設定を並列に学習させ、同一条件で
評価して比較する。本番のチェックポイントは CHAMPIONS_MODELS_DIR で
分離するので汚さない。

■ なぜやり直すのか
champions_agent/train/auto_tune.py が2026-07-24に同種のスイープを回して
0.15/0.03/3e-4 に「定着」しているが、その全試行 (7設定x7サイクル) は
評価凍結バグ (2026-07-26修正) の期間中で、どの試行も同じ凍結モデルを
測っていた。試行間の差 0.432-0.482 はノイズであり、現行設定はノイズで
選ばれている。さらに判定閾値 IMPROVE_EPS=0.02 は 350戦の標準誤差0.027を
下回っており、仮に凍結バグが無くても解像できていなかった。

■ 測り方
学習後に certify と同じ条件 (ベンチマーク相手・上位60構築・相性選出) で
--battles 戦する。既定400戦で標準誤差0.024。設定間の差が0.05程度と
見込まれるため、これ未満だと判定できない。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC_MODELS = REPO / "champions_agent" / "train" / "checkpoints"
SWEEP_ROOT = REPO / "logs" / "reward_sweep"

# 比較する報酬設計。scale はシェイピング全体の強さ、override は報酬の「形」
VARIANTS = [
    {"name": "control", "scale": "0.15", "override": "",
     "desc": "現行設定 (auto_tuneが定着させた値)"},
    {"name": "outcome", "scale": "0.0", "override": "",
     "desc": "勝敗のみ。盤面シェイピングを完全に切る"},
    {"name": "ko", "scale": "0.15", "override":
     "hp_diff_weight=0.3,faint_bonus=4.0,fainted_penalty=4.0",
     "desc": "KO重視。HP削りの評価を下げ、落とす/落とされるを重く"},
    {"name": "shaped", "scale": "0.45", "override": "",
     "desc": "シェイピング強め (現行の3倍)"},
]


def _seed_dir(name: str) -> Path:
    return SWEEP_ROOT / name / "checkpoints"


def prepare(styles: list) -> None:
    """各条件の作業ディレクトリを作り、同じ種チェックポイントを配る"""
    for v in VARIANTS:
        d = _seed_dir(v["name"])
        d.mkdir(parents=True, exist_ok=True)
        for style in styles:
            src = SRC_MODELS / f"battle_policy_{style}.zip"
            if src.exists():
                shutil.copy(src, d / src.name)
    print(f"[sweep] 種チェックポイントを配布: {SWEEP_ROOT}")


def train_cmd(v: dict, style: str, steps: int, n_envs: int) -> subprocess.Popen:
    env = dict(os.environ)
    env["CHAMPIONS_MODELS_DIR"] = str(_seed_dir(v["name"]))
    env["REWARD_SHAPE_SCALE"] = v["scale"]
    env["REWARD_OVERRIDE"] = v["override"]
    env["N_ENVS"] = str(n_envs)
    # 学習ループ側の設定は引き継がない (auto_env.sh は source しない)
    env["TRAIN_ENT_COEF"] = env.get("TRAIN_ENT_COEF", "0.03")
    env["TRAIN_LR"] = env.get("TRAIN_LR", "3e-4")
    log = SWEEP_ROOT / v["name"] / f"train_{style}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(
        [sys.executable, "-m", "champions_agent.train.train_battle",
         "--timesteps", str(steps), "--play-style", style, "--resume",
         "--n-envs", str(n_envs)],
        cwd=REPO, env=env,
        stdout=log.open("w"), stderr=subprocess.STDOUT)


def evaluate(v: dict, style: str, battles: int) -> float:
    """学習後の条件を、本番と同じベンチマーク条件で測る。

    別プロセスで動かす。同一プロセスで CHAMPIONS_MODELS_DIR を差し替えて
    モジュールを再読込する方式は、乱数シードごと再初期化されて
    アカウント名が重複し (nametaken)、対戦が成立せずハングした。
    --no-save で本番の last_eval_*.json を汚さない。
    """
    env = dict(os.environ)
    env["CHAMPIONS_MODELS_DIR"] = str(_seed_dir(v["name"]))
    env.pop("REWARD_OVERRIDE", None)      # 評価条件は全条件で共通にする
    env.pop("REWARD_SHAPE_SCALE", None)
    r = subprocess.run(
        [sys.executable, "-m", "champions_agent.train.evaluate",
         "--play-style", style, "--battles", str(battles),
         "--opponent", "benchmark", "--checkpoint", "current", "--no-save"],
        cwd=REPO, env=env, capture_output=True, text=True)
    for line in r.stdout.splitlines():
        if line.startswith("[evaluate] "):
            import ast
            try:
                return float(ast.literal_eval(line[len("[evaluate] "):])
                             ["win_rate"])
            except (ValueError, SyntaxError, KeyError):
                pass
    print(f"  ⚠ {v['name']}/{style} の評価に失敗\n{r.stdout[-500:]}")
    return float("nan")


def main() -> None:
    ap = argparse.ArgumentParser(description="報酬設計の並列スイープ")
    ap.add_argument("--steps", type=int, default=400_000)
    ap.add_argument("--battles", type=int, default=400)
    ap.add_argument("--styles", default="balance")
    ap.add_argument("--n-envs", type=int, default=1)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--eval-only", action="store_true",
                    help="学習をとばして評価だけ行う (中断からの再開用)")
    ap.add_argument("--rounds", type=int, default=1,
                    help="--steps を何回積み増すか (打ち切られにくくするため)")
    ap.add_argument("--resume-sweep", action="store_true",
                    help="種チェックポイントを配り直さず、前回の続きから積む")
    args = ap.parse_args()

    if args.list:
        for v in VARIANTS:
            print(f"  {v['name']:8s} scale={v['scale']:5s} "
                  f"{v['override'] or '(既定の形)':50s} {v['desc']}")
        return

    styles = [s for s in args.styles.split(",") if s]
    SWEEP_ROOT.mkdir(parents=True, exist_ok=True)

    if not args.eval_only:
        if not args.resume_sweep:
            prepare(styles)
        # 1回の実行が長いと打ち切られて条件間の学習量がずれるため、
        # 短い区間を繰り返して積み増す (--resume で継続学習になる)
        for rnd in range(1, args.rounds + 1):
            for style in styles:
                print(f"\n[sweep] {style}: {len(VARIANTS)}条件を並列学習 "
                      f"(第{rnd}/{args.rounds}回 x {args.steps}ステップ)",
                      flush=True)
                t0 = time.time()
                procs = [(v, train_cmd(v, style, args.steps, args.n_envs))
                         for v in VARIANTS]
                for v, p in procs:
                    code = p.wait()
                    print(f"  {v['name']:8s} 終了 (exit={code}, "
                          f"{time.time() - t0:.0f}s)", flush=True)

    print(f"\n[sweep] 評価 (各{args.battles}戦)", flush=True)
    se = (0.25 / args.battles) ** 0.5
    results = {}
    for style in styles:
        results[style] = {}
        for v in VARIANTS:
            wr = evaluate(v, style, args.battles)
            results[style][v["name"]] = wr
            print(f"  {style:8s} {v['name']:8s} {wr:.3f} ± {se:.3f}",
                  flush=True)

    out = SWEEP_ROOT / f"result_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps(
        {"steps": args.steps, "battles": args.battles, "results": results,
         "variants": VARIANTS}, ensure_ascii=False, indent=1), encoding="utf-8")

    print("\n=== 比較 (controlとの差) ===")
    for style in styles:
        base = results[style].get("control")
        for name, wr in sorted(results[style].items(), key=lambda x: -x[1]):
            d = wr - base if base is not None else 0.0
            verdict = "" if name == "control" else (
                "有意" if abs(d) > 2 * se * (2 ** 0.5) else "ノイズ範囲")
            print(f"  {style:8s} {name:8s} {wr:.3f}  {d:+.3f} {verdict}")
    print(f"\n記録: {out}")


if __name__ == "__main__":
    main()
