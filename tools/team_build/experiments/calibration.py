"""実験 1: 代理評価の較正の予備試験 (純粋関数 + 配線)。

run ごとに S5 の点の項 (parts) と測定 Δ (S10 > S8b > S8a の順で深いもの) を集め、run 内の順位だけを信号にして
項の重みを当てはめ、leave-one-run-out で「当てはめた重み」と「現在の重み」の順位相関 (Spearman) を比べる。
採否の規則 (事前登録): 中央値の改善 ≥ min_gain、改善する run の割合 ≥ share、使える run ≥ min_runs (各 run の測定チーム ≥ min_teams)。
あわせて、同じ 6 体が複数の run で測られたときの Δ の散らばり (測定のゆらぎの床) を出す。

  python -m tools.team_build.experiments.calibration [--runs a,b,c] [--min-teams 6] [--grid 0,0.5,1,2,4]
"""
from __future__ import annotations

import argparse
import itertools
import statistics
from typing import Optional

from tools.team_build.experiments import RUNS, best_delta_by_team, load_json, write_result
from tools.team_build.review_run import spearman

DEFAULT_WEIGHTS = {
    "legacy": {"coverage": 1.0, "roles": 0.5, "synergy": 0.3, "redundancy": -0.4, "hole": -0.5},
    "joint": {"coverage": 1.0, "hole": -1.0, "roles": 0.05, "field_conflicts": -0.05, "utility": 1.0, "attacker_excess": -1.0, "dup": -1.0},
}
GRID = (0.0, 0.5, 1.0, 2.0, 4.0)


# ------------------------------------------------------------------ 純粋関数
def surrogate_score(parts: dict, weights: dict) -> float:
    return sum(float(w) * float(parts.get(k, 0.0) or 0.0) for k, w in weights.items())


def within_run_rho(rows: list, weights: dict) -> Optional[float]:
    """rows = [{"parts": {...}, "delta": 測定 Δ}] (1 run)。代理の点と Δ の Spearman"""
    if len(rows) < 3:
        return None
    return spearman([surrogate_score(r["parts"], weights) for r in rows], [r["delta"] for r in rows])


def mean_rho(runs: list, weights: dict) -> Optional[float]:
    """複数 run の run 内 ρ の、チーム数で重みづけした平均"""
    tot = wsum = 0.0
    for rows in runs:
        rho = within_run_rho(rows, weights)
        if rho is None:
            continue
        tot += rho * len(rows)
        wsum += len(rows)
    return round(tot / wsum, 4) if wsum else None


def fit_weights(runs: list, defaults: dict, grid=GRID, shrink: float = 0.02) -> dict:
    """既定の重みに格子の倍率を掛けた候補から、run 内 ρ の平均が最大のものを選ぶ (同点は既定に近いもの: 倍率 1 からの距離で縮小)。
    符号は既定のまま (被覆は正、穴は負)。候補数 = len(grid) ** 項数"""
    keys = list(defaults)
    best, best_key = dict(defaults), None
    for mults in itertools.product(grid, repeat=len(keys)):
        w = {k: defaults[k] * m for k, m in zip(keys, mults)}
        rho = mean_rho(runs, w)
        if rho is None:
            continue
        dist = sum(abs(m - 1.0) for m in mults)
        key = (-(rho - shrink * dist), dist)
        if best_key is None or key < best_key:
            best, best_key = w, key
    return best


def leave_one_run_out(runs: list, defaults: dict, grid=GRID) -> list:
    """各 run を 1 つ外して残りで当てはめ、外した run で 既定 / 当てはめ の ρ を出す。戻り値 [{"held_out", "rho_default", "rho_fitted", "weights"}]"""
    out = []
    for i, held in enumerate(runs):
        train = [r for j, r in enumerate(runs) if j != i]
        if not train:
            continue
        w = fit_weights(train, defaults, grid)
        out.append({"held_out": i, "n_teams": len(held), "rho_default": within_run_rho(held, defaults),
                    "rho_fitted": within_run_rho(held, w), "weights": {k: round(v, 4) for k, v in w.items()}})
    return out


def decide(loo: list, min_gain: float = 0.1, share: float = 2 / 3, min_runs: int = 5) -> dict:
    """採否の規則 (事前登録): 使える run ≥ min_runs、ρ の改善の中央値 ≥ min_gain、改善する run の割合 ≥ share"""
    rows = [r for r in loo if r.get("rho_default") is not None and r.get("rho_fitted") is not None]
    if len(rows) < min_runs:
        return {"adopt": False, "reason": f"使える run が {len(rows)} (必要 {min_runs})", "n_runs": len(rows)}
    gains = [r["rho_fitted"] - r["rho_default"] for r in rows]
    med = statistics.median(gains)
    improved = sum(1 for g in gains if g > 0) / len(gains)
    adopt = med >= min_gain and improved >= share
    return {"adopt": adopt, "n_runs": len(rows), "median_gain": round(med, 4), "improved_share": round(improved, 3),
            "median_rho_default": round(statistics.median(r["rho_default"] for r in rows), 4),
            "median_rho_fitted": round(statistics.median(r["rho_fitted"] for r in rows), 4),
            "reason": ("採用条件を満たす" if adopt else f"中央値の改善 {med:+.3f} (必要 ≥ {min_gain}) / 改善する run {improved:.0%} (必要 ≥ {share:.0%})")}


def noise_floor(team_runs: dict) -> dict:
    """同じ 6 体が複数 run で測られたときの Δ の標準偏差 (run 間のゆらぎの床)。team_runs = {members_key: [Δ ...]}"""
    rows = []
    for key, ds in team_runs.items():
        if len(ds) >= 2:
            rows.append({"members": key, "n_runs": len(ds), "deltas": [round(d, 4) for d in ds],
                         "sd": round(statistics.pstdev(ds), 4), "range": round(max(ds) - min(ds), 4)})
    rows.sort(key=lambda r: -r["n_runs"])
    sds = [r["sd"] for r in rows]
    return {"teams": rows, "median_sd": round(statistics.median(sds), 4) if sds else None}


# ------------------------------------------------------------------ 配線
def measured_deltas(run_dir) -> tuple:
    """run の測定 Δ {team_id: {"delta", "n", "stage"}} (S10 > S8b > S8a の順に深い段を優先)"""
    out: dict = {}
    for stage in ("s08a_screen", "s08b_adapted", "s10"):
        res = load_json(run_dir / "evaluation" / f"{stage}.json")
        if not res:
            continue
        for cid, d in best_delta_by_team(res).items():
            out[cid] = {"delta": d["delta"], "n": d["n"], "stage": stage}
    return out, sorted({v["stage"] for v in out.values()})


def collect_run(run_dir) -> Optional[dict]:
    s05 = load_json(run_dir / "s05_candidates.json")
    s06 = load_json(run_dir / "s06_sets.json")
    if not s05 or not s06:
        return None
    mode = "joint" if (s05.get("mode") == "joint") else "legacy"
    parts_by_cid: dict = {}
    lineups = s05.get("lineups") or []
    for row in s06:
        cid = row.get("candidate_id")
        idx = row.get("index")
        parts = row.get("parts")
        if not parts and isinstance(idx, int) and idx < len(lineups):
            lu = lineups[idx]
            if f"L{idx:02d}_{lu.get('concept')}" == cid:
                parts = lu.get("parts")
        if parts:
            parts_by_cid[cid] = {"parts": parts, "members": tuple(sorted(row.get("members") or [])), "tag": row.get("tag")}
    deltas, stages = measured_deltas(run_dir)
    rows = []
    for cid, d in deltas.items():
        if cid in parts_by_cid:
            rows.append({"candidate_id": cid, "parts": parts_by_cid[cid]["parts"], "delta": d["delta"], "n": d["n"],
                         "members": parts_by_cid[cid]["members"], "tag": parts_by_cid[cid]["tag"]})
    return {"run_id": run_dir.name, "mode": mode, "rows": rows, "stages": stages}


def main() -> None:
    ap = argparse.ArgumentParser(description="実験 1: 代理評価の較正の予備試験 (leave-one-run-out)")
    ap.add_argument("--runs", default=None, help="run_id のカンマ区切り (省略時は logs/build_search/runs の全部)")
    ap.add_argument("--min-teams", type=int, default=6)
    ap.add_argument("--min-runs", type=int, default=5)
    ap.add_argument("--min-gain", type=float, default=0.1)
    ap.add_argument("--grid", default=",".join(str(g) for g in GRID))
    args = ap.parse_args()
    grid = tuple(float(x) for x in args.grid.split(","))
    run_ids = [r.strip() for r in args.runs.split(",")] if args.runs else sorted(p.name for p in RUNS.iterdir() if p.is_dir())
    collected = [c for c in (collect_run(RUNS / r) for r in run_ids) if c]
    team_runs: dict = {}
    for c in collected:
        for r in c["rows"]:
            team_runs.setdefault("+".join(r["members"]), []).append(r["delta"])
    result = {"runs": [{"run_id": c["run_id"], "mode": c["mode"], "n_measured": len(c["rows"]), "stages": c["stages"]} for c in collected],
              "noise_floor": noise_floor(team_runs), "groups": {}}
    for mode, defaults in DEFAULT_WEIGHTS.items():
        group = [c for c in collected if c["mode"] == mode and len(c["rows"]) >= args.min_teams]
        keys = [k for k in defaults if all(any(k in r["parts"] for r in c["rows"]) for c in group)] if group else []
        d = {k: defaults[k] for k in keys}
        runs = [c["rows"] for c in group]
        g = {"n_runs": len(group), "run_ids": [c["run_id"] for c in group], "terms": keys,
             "rho_default_per_run": [within_run_rho(rows, d) for rows in runs] if d else []}
        if len(group) >= 2 and d:
            loo = leave_one_run_out(runs, d, grid)
            g["loo"] = loo
            g["decision"] = decide(loo, args.min_gain, 2 / 3, args.min_runs)
            g["fitted_all"] = {k: round(v, 4) for k, v in fit_weights(runs, d, grid).items()}
        else:
            g["decision"] = {"adopt": False, "reason": f"run が {len(group)} 本 (2 本以上で当てはめ、{args.min_runs} 本以上で採否)"}
        result["groups"][mode] = g
    p = write_result("calibration", result)
    for mode, g in result["groups"].items():
        print(f"[{mode}] runs={g['n_runs']} terms={g['terms']} ρ(既定)={g['rho_default_per_run']}")
        for r in g.get("loo") or []:
            print(f"   held_out={g['run_ids'][r['held_out']]} ρ既定={r['rho_default']} ρ当てはめ={r['rho_fitted']} w={r['weights']}")
        print(f"   decision: {g['decision']}")
    nf = result["noise_floor"]
    print(f"測定のゆらぎの床 (同じ 6 体の run 間 Δ の SD の中央値): {nf['median_sd']} ({len(nf['teams'])} チーム)")
    print(f"保存: {p}")


if __name__ == "__main__":
    main()
