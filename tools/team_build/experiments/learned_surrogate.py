"""実験 8: 学習の代理評価 (選出モデルの予測勝率で並びを直接評価する) の予備試験。対戦は要らない。

判断 #11 (2026-10-05): 代理評価の主役を「計算の被覆」から「学習の代理」へ、データが多く対戦なしで検証できる方を先に試す。
run ごとに、測定された並び (S8a / S8b / S10 の Δ がある並び) について、汎用の選出モデルで
    V_learned(T) = 平均_{相手チーム o ∈ 探索 fold B} max_{3 体 S ⊂ T} P(勝ち | T, o, S)
を出し、測定 Δ との run 内 Spearman を、S5 の点 (被覆) の Spearman と並べる。モデルが未学習の並び (分布外) は印をつける。

  python -m tools.team_build.experiments.learned_surrogate [--runs a,b] [--model general|deployed|PATH] [--max-opponents 100]
"""
from __future__ import annotations

import argparse
import statistics
from pathlib import Path
from typing import Callable, Optional

from tools.team_build.experiments import RUNS, load_json, write_result
from tools.team_build.experiments.calibration import measured_deltas
from tools.team_build.review_run import spearman


# ------------------------------------------------------------------ 純粋関数
def learned_value(team: list, opponents: list, score_fn: Callable) -> Optional[float]:
    """score_fn(team, opp) → [(perm, p)] 降順 (空なら None)。相手ごとの最良の予測勝率の平均"""
    vals = []
    for opp in opponents:
        scored = score_fn(team, opp)
        if scored:
            vals.append(float(scored[0][1]))
    return round(sum(vals) / len(vals), 4) if vals else None


def compare_surrogates(rows: list) -> dict:
    """rows = [{"delta", "learned", "surrogate"}] (1 run) → 両方の代理の Spearman"""
    ok = [r for r in rows if r.get("learned") is not None and r.get("delta") is not None]
    out = {"n": len(ok)}
    if len(ok) >= 3:
        out["spearman_learned"] = spearman([r["learned"] for r in ok], [r["delta"] for r in ok])
        sur = [r for r in ok if r.get("surrogate") is not None]
        out["spearman_surrogate"] = spearman([r["surrogate"] for r in sur], [r["delta"] for r in sur]) if len(sur) >= 3 else None
    return out


# ------------------------------------------------------------------ 配線
def make_score_fn(model: str):
    from champions_agent.agent import selection_dispatch as SD
    from champions_agent.agent import selection_model as sm
    if model == "general":
        path = SD.general_model_path()
    elif model == "deployed":
        path = SD.deployed_model_path()
    else:
        path = Path(model)
    cache: dict = {}

    def score(team: list, opp: list) -> list:
        key = (tuple(team), tuple(opp))
        if key not in cache:
            cache[key] = SD.score_all(list(team), list(opp), path)
        return cache[key]
    return score, path, sm.is_in_distribution


def collect_run(run_dir: Path, score_fn, in_dist, max_opponents: int) -> Optional[dict]:
    from champions_agent.config import BUILD_FOLD_EVAL
    s06 = load_json(run_dir / "s06_sets.json")
    split = load_json(run_dir / "opponent_families.json")
    if not s06 or not split:
        return None
    folds = split.get("search_folds") or []
    ids = list(folds[BUILD_FOLD_EVAL]) if len(folds) > BUILD_FOLD_EVAL else list((split.get("tiers") or {}).get("search") or [])
    opponents = [list((split.get("teams") or {}).get(t, {}).get("species") or []) for t in ids[:max_opponents]]
    opponents = [o for o in opponents if len(o) == 6]
    deltas, _stages = measured_deltas(run_dir)
    rows = []
    for row in s06:
        cid = row.get("candidate_id")
        if cid not in deltas or len(row.get("members") or []) != 6:
            continue
        team = list(row["members"])
        rows.append({"candidate_id": cid, "delta": deltas[cid]["delta"], "surrogate": row.get("score"),
                     "learned": learned_value(team, opponents, score_fn), "in_distribution": bool(in_dist(team)), "tag": row.get("tag")})
    out = compare_surrogates(rows)
    out.update({"run_id": run_dir.name, "n_opponents": len(opponents), "rows": rows,
                "in_distribution_share": round(sum(1 for r in rows if r["in_distribution"]) / len(rows), 3) if rows else None})
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="実験 8: 学習の代理評価 (選出モデルの予測勝率) と測定 Δ の順位相関")
    ap.add_argument("--runs", default=None)
    ap.add_argument("--model", default="general", help="general (汎用) / deployed (配布版) / .pt のパス")
    ap.add_argument("--max-opponents", type=int, default=100)
    args = ap.parse_args()
    score_fn, path, in_dist = make_score_fn(args.model)
    run_ids = [r.strip() for r in args.runs.split(",")] if args.runs else sorted(p.name for p in RUNS.iterdir() if p.is_dir())
    per_run = [c for c in (collect_run(RUNS / r, score_fn, in_dist, args.max_opponents) for r in run_ids) if c and c.get("n", 0) >= 3]
    rl = [r["spearman_learned"] for r in per_run if r.get("spearman_learned") is not None]
    rs = [r["spearman_surrogate"] for r in per_run if r.get("spearman_surrogate") is not None]
    result = {"model": str(path), "runs": per_run,
              "median_spearman_learned": round(statistics.median(rl), 4) if rl else None,
              "median_spearman_surrogate": round(statistics.median(rs), 4) if rs else None}
    p = write_result("learned_surrogate", result)
    for r in per_run:
        print(f"{r['run_id']}: n={r['n']} 相手 {r['n_opponents']} ρ(学習)={r.get('spearman_learned')} ρ(被覆)={r.get('spearman_surrogate')} "
              f"分布内 {r['in_distribution_share']}")
    print(f"中央値: 学習 {result['median_spearman_learned']} / 被覆 {result['median_spearman_surrogate']} (model {path})")
    print(f"保存: {p}")


if __name__ == "__main__":
    main()
