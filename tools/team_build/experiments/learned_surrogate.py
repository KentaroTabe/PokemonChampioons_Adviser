"""実験 8: 学習の代理評価 (選出モデルの予測勝率で並びを直接評価する) の予備試験。対戦は要らない。

判断 #11 (2026-10-05): 代理評価の主役を「計算の被覆」から「学習の代理」へ、データが多く対戦なしで検証できる方を先に試す。
run ごとに、測定された並び (S8a / S8b / S10 の Δ がある並び) について、汎用の選出モデルで
    V_learned(T) = 平均_{相手チーム o ∈ 探索 fold B} max_{3 体 S ⊂ T} P(勝ち | T, o, S)
を出し、測定 Δ との run 内 Spearman を、S5 の点 (被覆) の Spearman と並べる。モデルが未学習の並び (分布外) は印をつける。

層化と帰無分布 (2026-10-05): 全部の並びで比べると「現行チームの系統 (現行枝・近傍・修理の変種) が高く測定され、モデルもその系統を
高く見る」ことだけで相関が出る (10/5 の集計: 全部では学習 +0.23 / 被覆 −0.40、探索の並びだけでは学習 +0.10 / 被覆 +0.45)。
順位づけたいのは探索の並びなので、層 (all / exploration) ごとに run 内の順位相関を出し、run 内で Δ を並べ替えた帰無分布に対する
片側の p を添える。採否 (decide) は exploration の層で行う: 学習の代理の p < BUILD_NULL_ALPHA かつ 中央値が被覆より
BUILD_SURROGATE_MIN_GAIN 以上高い。

  python -m tools.team_build.experiments.learned_surrogate [--runs a,b] [--model general|deployed|PATH] [--max-opponents 100]
         [--n-perm N] [--seed S]
"""
from __future__ import annotations

import argparse
import random
import statistics
from pathlib import Path
from typing import Callable, Optional

from champions_agent.config import BUILD_NULL_ALPHA, BUILD_NULL_PERMUTATIONS, BUILD_SURROGATE_MIN_GAIN
from tools.team_build.experiments import RUNS, load_json, write_result
from tools.team_build.experiments.calibration import measured_deltas
from tools.team_build.review_run import spearman

INCUMBENT_TAGS = ("incumbent", "incumbent_mut", "repair")
STRATA = ("all", "exploration")
MIN_ROWS = 3                 # run 内の順位相関を出す最小の並びの数 (compare_surrogates と同じ)


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


def is_incumbent_family(row: dict) -> bool:
    """現行チームの系統か (現行枝・その近傍・修理の変種)。tag か、candidate_id の現行枝の印 (_INC) で判定する"""
    return str(row.get("tag") or "") in INCUMBENT_TAGS or "_INC" in str(row.get("candidate_id") or "")


def stratum_rows(runs: list, stratum: str) -> list:
    """runs = [[row, ...], ...] (run ごとの並び) → 層に入る並びだけ (run の区切りは保つ)。
    all = 全部 / exploration = 現行チームの系統を除く (順位づけたい対象)"""
    if stratum == "all":
        return [list(rows) for rows in runs]
    if stratum == "exploration":
        return [[r for r in rows if not is_incumbent_family(r)] for rows in runs]
    raise ValueError(f"未知の層: {stratum}")


def pooled_rho(runs: list, key: str, min_rows: int = MIN_ROWS) -> dict:
    """run 内の Spearman (key vs delta) を run ごとに出し、並びの数で重みづけた平均と中央値にまとめる (純粋)。
    並びが min_rows 未満の run は数えない。戻り値 {"per_run": [ρ or None], "n_runs", "n_rows", "mean", "median"}"""
    per, tot, w = [], 0.0, 0
    for rows in runs:
        ok = [r for r in rows if r.get(key) is not None and r.get("delta") is not None]
        if len(ok) < min_rows:
            per.append(None)
            continue
        rho = spearman([r[key] for r in ok], [r["delta"] for r in ok])
        per.append(rho)
        tot += rho * len(ok)
        w += len(ok)
    vals = [p for p in per if p is not None]
    return {"per_run": per, "n_runs": len(vals), "n_rows": w, "mean": (round(tot / w, 4) if w else None),
            "median": (round(statistics.median(vals), 4) if vals else None)}


def permutation_p(runs: list, key: str, n_perm: int = BUILD_NULL_PERMUTATIONS, seed: int = 0,
                  min_rows: int = MIN_ROWS) -> Optional[float]:
    """帰無分布 (key と Δ が無関係) に対する片側の p: run 内で Δ を並べ替えた pooled_rho の平均が、観測の平均以上になる割合 (純粋)。
    run をまたいでは入れ替えない (run 間の Δ の水準の違いを信号にしない)。使える run が無ければ None"""
    obs = pooled_rho(runs, key, min_rows)["mean"]
    if obs is None or n_perm <= 0:
        return None
    rng = random.Random(seed)
    base = [[r for r in rows if r.get(key) is not None and r.get("delta") is not None] for rows in runs]
    ge = 0
    for _ in range(n_perm):
        shuffled = []
        for rows in base:
            ds = [r["delta"] for r in rows]
            rng.shuffle(ds)
            shuffled.append([{key: r[key], "delta": d} for r, d in zip(rows, ds)])
        m = pooled_rho(shuffled, key, min_rows)["mean"]
        if m is not None and m >= obs - 1e-12:
            ge += 1
    return round(ge / n_perm, 4)


def stratified_summary(runs: list, n_perm: int = BUILD_NULL_PERMUTATIONS, seed: int = 0) -> dict:
    """層 (all / exploration) × 代理 (learned / surrogate) の順位相関と帰無分布の p (純粋)"""
    out: dict = {}
    for st in STRATA:
        sub = stratum_rows(runs, st)
        out[st] = {"n_rows_per_run": [len(rows) for rows in sub]}
        for key in ("learned", "surrogate"):
            d = pooled_rho(sub, key)
            d["p"] = permutation_p(sub, key, n_perm, seed)
            out[st][key] = d
    return out


def decide(summary: dict, min_gain: float = BUILD_SURROGATE_MIN_GAIN, alpha: float = BUILD_NULL_ALPHA) -> dict:
    """学習の代理を探索の主項にするか (純粋)。exploration の層だけで判定する:
    学習の代理の p < alpha (無関係ではない) かつ run 内順位相関の中央値が被覆より min_gain 以上高い。
    all の層は参考 (現行チームの系統との対比を含むので、探索の順位づけの根拠にしない)"""
    ex = (summary or {}).get("exploration") or {}
    le, su = ex.get("learned") or {}, ex.get("surrogate") or {}
    if le.get("median") is None or le.get("p") is None:
        return {"adopt": False, "reason": "探索の並びで順位相関を出せる run が無い", "stratum": "exploration"}
    gain = le["median"] - (su.get("median") if su.get("median") is not None else 0.0)
    significant = le["p"] < alpha
    adopt = bool(significant and gain >= min_gain)
    why = []
    if not significant:
        why.append(f"学習の代理の p = {le['p']} (必要 < {alpha})")
    if gain < min_gain:
        why.append(f"中央値の差 {gain:+.3f} (必要 ≥ {min_gain})")
    return {"adopt": adopt, "stratum": "exploration", "median_learned": le["median"], "median_surrogate": su.get("median"),
            "gain": round(gain, 4), "p_learned": le["p"], "p_surrogate": su.get("p"),
            "reason": ("採用条件を満たす" if adopt else " / ".join(why))}


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
        # --search-mode both の run では、従来方式の行 (search_mode legacy) の点は尺度が違うので代理の点として使わない
        rows.append({"candidate_id": cid, "delta": deltas[cid]["delta"],
                     "surrogate": (row.get("score") if row.get("search_mode") != "legacy" else None),
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
    ap.add_argument("--n-perm", type=int, default=BUILD_NULL_PERMUTATIONS, help="帰無分布の並べ替えの回数 (既定 config)")
    ap.add_argument("--seed", type=int, default=20261005, help="並べ替えの乱数の種")
    args = ap.parse_args()
    score_fn, path, in_dist = make_score_fn(args.model)
    run_ids = [r.strip() for r in args.runs.split(",")] if args.runs else sorted(p.name for p in RUNS.iterdir() if p.is_dir())
    per_run = [c for c in (collect_run(RUNS / r, score_fn, in_dist, args.max_opponents) for r in run_ids) if c and c.get("n", 0) >= 3]
    rl = [r["spearman_learned"] for r in per_run if r.get("spearman_learned") is not None]
    rs = [r["spearman_surrogate"] for r in per_run if r.get("spearman_surrogate") is not None]
    strata = stratified_summary([r["rows"] for r in per_run], args.n_perm, args.seed)
    decision = decide(strata)
    result = {"model": str(path), "runs": per_run,
              "median_spearman_learned": round(statistics.median(rl), 4) if rl else None,
              "median_spearman_surrogate": round(statistics.median(rs), 4) if rs else None,
              "strata": strata, "decision": decision, "n_perm": args.n_perm, "seed": args.seed}
    p = write_result("learned_surrogate", result)
    for r in per_run:
        print(f"{r['run_id']}: n={r['n']} 相手 {r['n_opponents']} ρ(学習)={r.get('spearman_learned')} ρ(被覆)={r.get('spearman_surrogate')} "
              f"分布内 {r['in_distribution_share']}")
    print(f"中央値: 学習 {result['median_spearman_learned']} / 被覆 {result['median_spearman_surrogate']} (model {path})")
    for st in STRATA:
        s = strata[st]
        print(f"[{st}] 並び {s['n_rows_per_run']}")
        for key, name in (("learned", "学習"), ("surrogate", "被覆")):
            d = s[key]
            print(f"   {name}: run ごと {d['per_run']} 中央値 {d['median']} 重みつき平均 {d['mean']} p (並べ替え {args.n_perm} 回、片側) {d['p']}")
    print(f"採否 (探索の並びの層): {decision}")
    print(f"保存: {p}")


if __name__ == "__main__":
    main()
