"""選出適応の learning curve: 適応戦数 N ごとのチェックポイントを同一相手列で測り、
screening 用の cheap adaptation の規模を決める (pick_ablation の結果が Case C = 適応の上げ幅が
候補で大きく違い順位が反転する、のときに回す)。

  各候補: adapt_selection(min_battles=points[0], chunk, patience=無限, max_battles=points[-1], keep_checkpoints)
        → selection_model_n{N}.pt (N ∈ points)
  各 N  : SEARCH fold 1 (S8a / ablation と同じ相手列・seed) で n 戦、--selection-model でチェックポイントを使う
  出力  : WR(N) と CI、対応差 Δ(N) = WR(N) − WR(teampreview) / − WR(収束 fresh)、
          N ごとの候補順位と、収束 fresh の順位に対する反転 (CI 超えか)

teampreview / generic / 収束 fresh の勝敗列は pick_ablation の測定結果 (同一相手列) を再利用する。

    python -m tools.team_build.adapt_curve --run-id chat_0906 --candidates L00_C001,L02_C023,L03_C027
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

from champions_agent.config import BUILD_CI_Z, BUILD_EQUIV_EPS, BUILD_PICK_ABLATION_N
from tools.team_build import adapt as AD
from tools.team_build import racing as R
from tools.team_build.pick_ablation import (STAGES, _cell, _order, _paired, _reversals, _run_jobs, log_line,
                                            reuse_outcomes_multi)

REPO = Path(__file__).resolve().parent.parent.parent
DEFAULT_POINTS = (1000, 2000, 3000, 5000)
NO_PATIENCE = 10 ** 9


def analyze_curve(curve: dict, baseline: dict, candidates: list, points: list,
                  eps: float = BUILD_EQUIV_EPS, z: float = BUILD_CI_Z) -> dict:
    """curve[(cid, N)] / baseline[(cid, cond)] = 勝敗列 (同一相手列)。純粋関数"""
    cells = {f"{c}/n{N}": _cell(list(curve[(c, N)]), z) for (c, N) in curve if curve[(c, N)]}
    for (c, cond), outs in baseline.items():
        if outs:
            cells[f"{c}/{cond}"] = _cell(list(outs), z)
    deltas = {}
    for c in candidates:
        for N in points:
            outs = curve.get((c, N))
            if not outs:
                continue
            deltas[f"{c}/n{N}"] = {"vs_teampreview": _paired(outs, baseline.get((c, "teampreview")), eps, z),
                                   "vs_fresh": _paired(outs, baseline.get((c, "fresh")), eps, z),
                                   "vs_generic": _paired(outs, baseline.get((c, "generic")), eps, z)}
    # N ごとの順位と、収束 fresh の順位に対する反転
    merged = {}
    for (c, cond), outs in baseline.items():
        merged[(c, cond)] = outs
    for (c, N), outs in curve.items():
        merged[(c, f"n{N}")] = outs
    order_final = _order(cells, candidates, "fresh")
    orders, reversals = {}, {}
    for N in points:
        cond = f"n{N}"
        orders[cond] = _order(cells, candidates, cond)
        reversals[cond] = _reversals(merged, orders[cond], order_final, cond, "fresh", eps, z)
    for cond in ("teampreview", "generic"):
        orders[cond] = _order(cells, candidates, cond)
        reversals[cond] = _reversals(merged, orders[cond], order_final, cond, "fresh", eps, z)
    orders["fresh"] = order_final
    return {"cells": cells, "deltas": deltas, "orders": orders, "reversals": reversals, "points": list(points),
            "eps": eps, "z": z}


def _fmt(p: Optional[dict]) -> str:
    if not p or p.get("mean") is None:
        return "-"
    return f"{p['mean']:+.3f} [{p['ci_low']:+.3f}, {p['ci_high']:+.3f}] {p['state']}"


def to_markdown(a: dict, candidates: list) -> str:
    conds = ["teampreview", "generic"] + [f"n{N}" for N in a["points"]] + ["fresh"]
    lines = ["| Team | " + " | ".join(conds) + " |", "|---|" + "---:|" * len(conds)]
    for c in candidates:
        row = [c]
        for cond in conds:
            cell = a["cells"].get(f"{c}/{cond}")
            row.append(f"{cell['win_rate']:.3f}" if cell else "-")
        lines.append("| " + " | ".join(row) + " |")
    lines += ["", "| Team | N | Δ vs teampreview | Δ vs 収束 fresh | Δ vs generic |", "|---|---:|---|---|---|"]
    for c in candidates:
        for N in a["points"]:
            d = a["deltas"].get(f"{c}/n{N}")
            if d:
                lines.append(f"| {c} | {N} | {_fmt(d['vs_teampreview'])} | {_fmt(d['vs_fresh'])} | {_fmt(d['vs_generic'])} |")
    lines.append("")
    for cond, order in a["orders"].items():
        revs = a["reversals"].get(cond, [])
        n_res = sum(1 for r in revs if r["resolved"])
        tag = "" if cond == "fresh" else f" … 収束 fresh に対する反転 {len(revs)} 対 (CI 超え {n_res})"
        lines.append(f"順位 ({cond}): {' > '.join(order) if order else '-'}{tag}")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--points", default=",".join(str(p) for p in DEFAULT_POINTS),
                    help="測るチェックポイントの適応戦数 (chunk の倍数、昇順)")
    ap.add_argument("--n", type=int, default=BUILD_PICK_ABLATION_N)
    ap.add_argument("--parallel", type=int, default=R.PARALLEL)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--ablation-dir", default=None, help="pick_ablation の出力 (既定 run/evaluation/pick_ablation)")
    ap.add_argument("--adapt-dir", default=None, help="既定 run/advisors_curve")
    ap.add_argument("--out", default=None, help="既定 run/evaluation/adapt_curve")
    ap.add_argument("--skip-adapt", action="store_true")
    args = ap.parse_args(argv)

    run_dir = REPO / "logs" / "build_search" / "runs" / args.run_id
    eval_dir = run_dir / "evaluation"
    abl_dir = Path(args.ablation_dir) if args.ablation_dir else eval_dir / "pick_ablation"
    out_dir = Path(args.out) if args.out else eval_dir / "adapt_curve"
    out_dir.mkdir(parents=True, exist_ok=True)
    adapt_dir = Path(args.adapt_dir) if args.adapt_dir else run_dir / "advisors_curve"
    log_path = out_dir / "curve.log"
    log = lambda m: log_line(log_path, m)   # noqa: E731
    points = sorted(int(x) for x in args.points.split(",") if x.strip())
    candidates = [c.strip() for c in args.candidates.split(",") if c.strip()]
    s8a_path = eval_dir / "s08a_screen.json"
    s8a = json.loads(s8a_path.read_text(encoding="utf-8")) if s8a_path.exists() else {}
    seed = args.seed if args.seed is not None else int(s8a.get("seed", 0))
    fold, tier = s8a.get("fold", 1), s8a.get("tier", "search")
    models_dir = (s8a.get("reference") or {}).get("models_dir")
    split_file = run_dir / "opponent_families.json"
    teams = {cid: run_dir / "s06_sets" / f"{cid}.txt" for cid in candidates}
    for cid, tf in teams.items():
        if not tf.exists():
            raise SystemExit(f"チーム本文が無い: {tf}")
    if any(p % AD.CHUNK for p in points):
        raise SystemExit(f"points は chunk ({AD.CHUNK}) の倍数にする: {points}")
    log(f"adapt curve run={args.run_id} candidates={candidates} points={points} n={args.n} seed={seed} fold={fold}")

    # 1) 適応 (チェックポイント保存、収束停止なし、points[-1] で打ち切り)
    ckpts = {}
    for cid, tf in teams.items():
        have = {N: adapt_dir / cid / f"selection_model_n{N}.pt" for N in points}
        if args.skip_adapt and all(p.exists() for p in have.values()):
            ckpts[cid] = {N: str(p) for N, p in have.items()}
            log(f"[adapt] {cid}: 既存チェックポイントを再利用")
            continue
        t0 = time.time()
        r = AD.adapt_selection(cid, tf, split_file, adapt_dir, seed, min_battles=points[0], chunk=AD.CHUNK,
                               patience=NO_PATIENCE, max_battles=points[-1], log=log, keep_checkpoints=True)
        ckpts[cid] = {N: str(p) for N, p in have.items() if p.exists()}
        log(f"[adapt] {cid}: checkpoints={sorted(ckpts[cid])} stop={r.get('stop_reason')} {time.time() - t0:.0f}s")
    (out_dir / "checkpoints.json").write_text(json.dumps(ckpts, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    # 2) 測定 (同一相手列)
    jobs = []
    for cid in candidates:
        for N, path in sorted(ckpts.get(cid, {}).items()):
            arm = R.Arm(f"{cid}_n{N}", teams[cid], path, models_dir, pick_policy="advisor")
            out_json = out_dir / f"curve_{cid}_n{N}_0_{args.n}.json"
            cmd = R.measure_cmd(arm, args.n, 0, seed, split_file, tier, fold, out_json,
                                out_dir / "battles" / f"curve_{cid}_n{N}.jsonl")
            jobs.append(((cid, N), arm, 0, args.n, out_json, cmd, out_dir / f"curve_{cid}_n{N}.log"))
    log(f"measure jobs={len(jobs)} parallel={args.parallel} battles={sum(j[3] for j in jobs)}")
    measured = _run_jobs(jobs, args.parallel, log)
    curve = {k: v["outcomes"] for k, v in measured.items() if v}

    # 3) 基準 (pick_ablation の同一相手列の結果) を再利用
    baseline = {}
    for cid in candidates:
        baseline[(cid, "teampreview")] = reuse_outcomes_multi([(eval_dir, "s08a_screen"), (abl_dir, STAGES["teampreview"])],
                                                              cid, args.n)
        baseline[(cid, "generic")] = reuse_outcomes_multi([(abl_dir, STAGES["generic"])], cid, args.n)
        baseline[(cid, "fresh")] = reuse_outcomes_multi([(abl_dir, STAGES["fresh"])], cid, args.n)
    a = analyze_curve(curve, baseline, candidates, points)
    a["config"] = {"run_id": args.run_id, "n": args.n, "seed": seed, "fold": fold, "tier": tier,
                   "models_dir": models_dir, "checkpoints": ckpts,
                   "baseline_n": {f"{k[0]}/{k[1]}": len(v) for k, v in baseline.items()}}
    (out_dir / "adapt_curve.json").write_text(json.dumps(a, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    md = to_markdown(a, candidates)
    (out_dir / "adapt_curve.md").write_text(md, encoding="utf-8")
    log("done")
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
