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
OPP_SEED = 20260730   # 全条件で同じ相手列を使うための固定シード

# 比較する報酬設計。scale はシェイピング全体の強さ、override は報酬の「形」
ALL_VARIANTS = [
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
VARIANTS = list(ALL_VARIANTS)


def select_arms(names: str, seeds: int) -> None:
    """比較する条件を絞り、シードごとに複製する。

    1条件1回の学習では「その回の運」と報酬設計の効果が区別できないため、
    採否を決める比較では複数シードを回して符号の一致を見る。
    """
    global VARIANTS
    wanted = [n for n in names.split(",") if n] if names else None
    base = ([v for v in ALL_VARIANTS if v["name"] in wanted] if wanted
            else list(ALL_VARIANTS))
    if seeds <= 1:
        VARIANTS = base
        return
    out = []
    for s in range(seeds):
        for v in base:
            w = dict(v)
            w["name"] = f"{v['name']}_s{s}"
            w["arm"] = v["name"]
            w["seed"] = s
            out.append(w)
    VARIANTS = out


def _seed_dir(name: str) -> Path:
    return SWEEP_ROOT / name / "checkpoints"


def _digest(path: Path) -> str:
    import hashlib
    return hashlib.md5(path.read_bytes()).hexdigest() if path.exists() else ""


def check_distinct(styles: list) -> bool:
    """条件間でチェックポイントが実際に違うことを確認する。

    train_battle は学習の最後にしか保存しないため、実行が途中で打ち切られると
    どの条件も種チェックポイントのままになる。それに気づかず評価すると
    「同一モデルを4回測って差がない」という無意味な結論が出る
    (2026-07-30に実際にこれをやった)。
    """
    ok = True
    for style in styles:
        digests = {v["name"]: _digest(_seed_dir(v["name"]) /
                                     f"battle_policy_{style}.zip")
                   for v in VARIANTS}
        uniq = set(d for d in digests.values() if d)
        if len(uniq) <= 1:
            print(f"  ⚠ {style}: 全条件のチェックポイントが同一です。"
                  "学習が保存前に打ち切られています。評価しても意味がありません")
            ok = False
    return ok


def prepare(styles: list) -> None:
    """各条件の作業ディレクトリを作り、同じ種チェックポイントと相手プールを配る。

    ⚠ selfplay相手プール (checkpoints/pool) も配ること。これを忘れると
    「selfplayプール0件」となり、ヒューリスティクス/探索/ランダムだけを
    相手に学習する状態になる。本番と学習分布が変わり、絶対値が本番と
    比較できなくなる (2026-07-30に発生し、全条件が0.54→0.43まで落ちた)。
    """
    for v in VARIANTS:
        d = _seed_dir(v["name"])
        d.mkdir(parents=True, exist_ok=True)
        for style in styles:
            for name in (f"battle_policy_{style}.zip",
                         f"battle_policy_{style}_best.zip"):
                src = SRC_MODELS / name
                if src.exists():
                    shutil.copy(src, d / name)
        pool_src = SRC_MODELS / "pool"
        if pool_src.is_dir():
            shutil.copytree(pool_src, d / "pool", dirs_exist_ok=True)
    n_pool = len(list((SRC_MODELS / "pool").glob("*.zip"))) \
        if (SRC_MODELS / "pool").is_dir() else 0
    print(f"[sweep] 種チェックポイントと相手プール({n_pool}件)を配布: {SWEEP_ROOT}")


def train_cmd(v: dict, style: str, steps: int, n_envs: int) -> subprocess.Popen:
    env = dict(os.environ)
    env["CHAMPIONS_MODELS_DIR"] = str(_seed_dir(v["name"]))
    env["REWARD_SHAPE_SCALE"] = v["scale"]
    env["REWARD_OVERRIDE"] = v["override"]
    env["N_ENVS"] = str(n_envs)
    # 学習ループ側の設定は引き継がない (auto_env.sh は source しない)
    env["TRAIN_ENT_COEF"] = env.get("TRAIN_ENT_COEF", "0.03")
    env["TRAIN_LR"] = env.get("TRAIN_LR", "3e-4")
    # シードごとに学習の乱数を変える (同じ設定でも別の軌跡になるようにする)
    if "seed" in v:
        env["TRAIN_SEED"] = str(1000 + v["seed"])
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
         "--opponent", "benchmark", "--checkpoint", "current", "--no-save",
         # 全条件に同じ相手列を当てる (差が相手の引き運に埋もれないように)
         "--opp-seed", str(OPP_SEED)],
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
    ap.add_argument("--force", action="store_true",
                    help="チェックポイントが同一でも評価する")
    ap.add_argument("--arms", default="",
                    help="比較する条件を絞る (例: control,ko)")
    ap.add_argument("--seeds", type=int, default=1,
                    help="1条件あたりの独立した学習回数")
    args = ap.parse_args()
    select_arms(args.arms, args.seeds)

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

    print(f"\n[sweep] 評価前の確認", flush=True)
    if not check_distinct(styles) and not args.force:
        raise SystemExit(
            "評価を中止します。--steps を小さくして (1回が数分で終わる量に) "
            "確実に保存されるようにするか、scripts/reward_sweep_bg.sh で\n"
            "打ち切られない形で回してください (--force で強行可)")

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
        by_arm = {}
        for v in VARIANTS:
            by_arm.setdefault(v.get("arm", v["name"]), []).append(
                results[style][v["name"]])
        base_runs = by_arm.get("control", [])
        base = sum(base_runs) / len(base_runs) if base_runs else None
        for arm, runs in sorted(by_arm.items(),
                                key=lambda x: -sum(x[1]) / len(x[1])):
            m = sum(runs) / len(runs)
            detail = ("  [" + " ".join(f"{r:.3f}" for r in runs) + "]"
                      if len(runs) > 1 else "")
            d = m - base if base is not None else 0.0
            print(f"  {style:8s} {arm:10s} {m:.3f}  {d:+.3f}{detail}")

        # 事前に決めた基準で機械的に判定する (結果を見てから基準を決めない)
        if base is not None and len(by_arm) == 2:
            from tools.ab_decision import decide
            arm = [a for a in by_arm if a != "control"][0]
            runs = by_arm[arm]
            signs = ([r - b for r, b in zip(runs, base_runs)]
                     if len(runs) == len(base_runs) else None)
            r = decide(base, sum(runs) / len(runs),
                       args.battles * len(base_runs), signs)
            print(f"\n  差 {r['diff']:+.3f} ± {r['se']:.3f} "
                  f"(95%CI {r['ci'][0]:+.3f}〜{r['ci'][1]:+.3f})")
            print(f"  判定: {r['verdict']}  [{arm} vs control]")
    print(f"\n記録: {out}")


if __name__ == "__main__":
    main()
