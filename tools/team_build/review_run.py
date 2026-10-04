"""run の測定統計を要約し、ε・分割比・戦数段階の見直し材料を出す (判定はしない)。

    python -m tools.team_build.review_run --run-id m3_llm

出力: 各 racing 段階の終了戦数の分布と最終状態、対応差 CI 半幅の推移、適応の収束履歴、holdout、所要時間、
      推奨 (uncertain で上限到達が多い → 上限/ε の見直し、CI 半幅が ε を大きく超える → 戦数段階の見直し、
      相手系統の分割の偏り → 分割比/系統閾値の見直し)。
"""
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent.parent
RUNS = REPO / "logs" / "build_search" / "runs"


def _load(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def racing_summary(doc: dict) -> dict:
    arms = doc.get("arms") or []
    states = {}
    for a in arms:
        states[a["state"]] = states.get(a["state"], 0) + 1
    n_done = [a.get("n_done") or 0 for a in arms]
    at_cap = sum(1 for a in arms if a["state"] == "uncertain" and (a.get("n_done") or 0) >= doc.get("max_battles", 0))
    halfwidths = []
    for a in arms:
        r = a.get("result") or {}
        if r.get("ci_low") is not None:
            halfwidths.append((r["ci_high"] - r["ci_low"]) / 2)
    return {"stage": doc.get("stage"), "n_arms": len(arms), "states": states,
            "n_done_min_max": [min(n_done, default=0), max(n_done, default=0)],
            "uncertain_at_cap": at_cap, "max_battles": doc.get("max_battles"), "eps": doc.get("eps"),
            "ci_halfwidth_mean": round(sum(halfwidths) / len(halfwidths), 4) if halfwidths else None,
            "elapsed_s": doc.get("elapsed_s")}


def stage_durations(run_log: Path) -> dict:
    """run.log のタイムスタンプから段ごとの所要 (秒)"""
    if not run_log.exists():
        return {}
    marks = []
    for line in run_log.read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"\[(\d\d):(\d\d):(\d\d)\] (.*)", line)
        if not m:
            continue
        t = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
        marks.append((t, m.group(4)))
    out = {}
    for i in range(1, len(marks)):
        dt = marks[i][0] - marks[i - 1][0]
        if dt < 0:
            dt += 86400
        key = marks[i][1][:28]
        out[key] = out.get(key, 0) + dt
    return dict(sorted(out.items(), key=lambda kv: -kv[1])[:12])


def _ranks(values: list) -> list:
    """降順の平均順位 (同値は平均。最大値が 1)"""
    order = sorted(range(len(values)), key=lambda i: -values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for t in range(i, j + 1):
            ranks[order[t]] = avg
        i = j + 1
    return ranks


def spearman(x: list, y: list):
    n = len(x)
    if n < 3:
        return None
    rx, ry = _ranks(list(x)), _ranks(list(y))
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx)
    vy = sum((b - my) ** 2 for b in ry)
    if vx == 0 or vy == 0:
        return None
    return round(cov / math.sqrt(vx * vy), 4)


def surrogate_quality(sets_rows: list, res8a: dict, k: int = 4) -> dict:
    """S5 の代理スコアの順位が、S8a の実測 Δ (対 参照) の順位をどれだけ再現するか (純粋関数)。

    Spearman ρ / Precision@k (代理上位 k のうち実測上位 k に入る数) / 実測最良の代理順位 /
    Regret@k (実測最良 Δ − 代理上位 k の中の実測最良 Δ)。実測のある候補だけで計算する。
    代理スコアで候補を絞る権限を与えるかは、この数値が run を跨いで安定してから判断する
    """
    score = {r["candidate_id"]: r.get("score") for r in (sets_rows or []) if r.get("ok")}
    measured = {}
    for a in (res8a or {}).get("arms", []):
        d = (a.get("result") or {}).get("mean")
        cid = a.get("arm_id")
        if d is not None and cid in score and score[cid] is not None:
            measured[cid] = d
    ids = list(measured)
    n = len(ids)
    if n == 0:
        return {"n": 0}
    by_score = sorted(ids, key=lambda c: -score[c])
    by_delta = sorted(ids, key=lambda c: -measured[c])
    kk = min(k, n)
    top_s, top_d = set(by_score[:kk]), set(by_delta[:kk])
    best = by_delta[0]
    regret = measured[best] - max(measured[c] for c in by_score[:kk])
    return {"n": n, "k": kk, "spearman": spearman([score[c] for c in ids], [measured[c] for c in ids]),
            "precision_at_k": round(len(top_s & top_d) / kk, 3), "best_surrogate_rank": by_score.index(best) + 1,
            "regret_at_k": round(regret, 4), "order_surrogate": by_score, "order_measured": by_delta}


def family_calibration(family_values: dict, records: list, min_n: int = 5, plan_file=None) -> dict:
    """系統ごとの予測 (S5 の family_values: 系統 → 最良 3 体の値) と実測 (対戦記録の系統ごとの勝率) の較正 (純粋)。
    戻り値 {"n_families", "spearman", "rows": [{family, predicted, win_rate, n}], "plan": 計画と選出の一致 (plan_file があれば)}。
    2026-10-04: 系統ごとの予測の順位相関は 0.02〜0.45 だった。run ごとに記録して閾値・項の見直しの材料にする"""
    from collections import defaultdict
    wl: dict = defaultdict(lambda: [0, 0])
    for r in records or []:
        fam = r.get("opponent_family_id")
        if not fam:
            continue
        wl[fam][0 if r.get("won") else 1] += 1
    rows = []
    for fam, (w, l) in wl.items():
        n = w + l
        if n >= min_n and fam in (family_values or {}):
            rows.append({"family": fam, "predicted": float(family_values[fam]), "win_rate": round(w / n, 3), "n": n})
    rows.sort(key=lambda r: -r["n"])
    out = {"n_families": len(rows), "n_records": len(records or []),
           "spearman": spearman([r["predicted"] for r in rows], [r["win_rate"] for r in rows]) if len(rows) >= 3 else None,
           "rows": rows}
    if plan_file:
        try:
            from tools.team_build.plan_prior import load_plan, plan_agreement
            plan = load_plan(plan_file)
            out["plan"] = plan_agreement(records or [], plan) if plan else None
        except Exception:
            out["plan"] = None
    return out


def recommendations(review: dict) -> list:
    rec = []
    sq = review.get("surrogate") or {}
    if sq.get("n", 0) >= 3:
        rho = sq.get("spearman")
        weak = (rho is not None and rho < 0.3) or (sq.get("precision_at_k") is not None and sq["precision_at_k"] < 0.5)
        rec.append(f"代理スコア (S5) の順位予測力: n={sq['n']} Spearman={rho} Precision@{sq['k']}={sq['precision_at_k']} "
                   f"実測最良の代理順位={sq['best_surrogate_rank']} Regret@{sq['k']}={sq['regret_at_k']}"
                   + (" → 弱い。代理スコアで候補を絞らない運用を維持し、S5 特徴の較正材料にする" if weak else ""))
    for st in review.get("racing", []):
        if st["n_arms"] and st["uncertain_at_cap"] / st["n_arms"] >= 0.5:
            rec.append(f"{st['stage']}: 半数以上が上限 {st['max_battles']} 戦で uncertain。上限の延長か ε の見直し (現在 {st['eps']}) を検討")
        if st.get("ci_halfwidth_mean") and st.get("eps") and st["ci_halfwidth_mean"] > 2 * st["eps"]:
            rec.append(f"{st['stage']}: 終了時の CI 半幅 {st['ci_halfwidth_mean']} が ε の 2 倍超。戦数段階を粗くしすぎていないか確認")
    split = review.get("split") or {}
    tiers = (split.get("summary") or {})
    if tiers:
        fam = {k: v.get("families") for k, v in tiers.items()}
        teams = {k: v.get("teams") for k, v in tiers.items()}
        if teams.get("holdout") and teams["holdout"] < 30:
            rec.append(f"HOLDOUT の構築数 {teams['holdout']} が少ない。top_n の拡大か分割比の見直し")
        rec.append(f"系統数: {fam} / 構築数: {teams} (系統の Jaccard 閾値 {split.get('min_jaccard')})")
    ad = review.get("adapt") or {}
    stops = [v.get("stop_reason") for v in ad.values() if isinstance(v, dict)]
    if stops and stops.count("converged") < len(stops) / 2:
        rec.append(f"適応の停止理由 {stops}: 収束前に上限到達が多いなら BUILD_ADAPT_PATIENCE / chunk を見直す")
    hold = review.get("holdout") or {}
    if hold.get("verdict") == "INCONCLUSIVE":
        rec.append("holdout が INCONCLUSIVE: 次 run では holdout の上限戦数を延長する (封印は新しいものを使う)")
    return rec


def review(run_id: str) -> dict:
    run = RUNS / run_id
    out = {"run_id": run_id, "racing": [], "durations": stage_durations(run / "run.log")}
    for name in ("s08a_screen", "s08b_adapted", "s10"):
        doc = _load(run / "evaluation" / f"{name}.json")
        if doc:
            out["racing"].append(racing_summary(doc))
    out["holdout"] = _load(run / "evaluation" / "s12_holdout.json")
    out["adapt"] = _load(run / "evaluation" / "s07_adapt.json") or {}
    out["surrogate"] = surrogate_quality(_load(run / "s06_sets.json") or [],
                                         _load(run / "evaluation" / "s08a_screen.json") or {})
    out["split"] = {k: v for k, v in (_load(run / "opponent_families.json") or {}).items() if k in ("summary", "min_jaccard", "ratios", "n_teams", "n_families")}
    rob = _load(run / "evaluation" / "robustness.json")
    if rob:
        out["robustness_worst"] = rob.get("worst_sensitivity_candidate")
    llm = list((run / "llm").glob("*.json")) if (run / "llm").exists() else []
    tok = {"calls": len(llm), "output_tokens": 0, "cache_creation": 0, "cache_read": 0}
    for p in llm:
        u = (_load(p) or {}).get("usage") or {}
        tok["output_tokens"] += u.get("output_tokens", 0) or 0
        tok["cache_creation"] += u.get("cache_creation_input_tokens", 0) or 0
        tok["cache_read"] += u.get("cache_read_input_tokens", 0) or 0
    out["llm_tokens"] = tok
    out["recommendations"] = recommendations(out)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="run の測定統計レビュー")
    ap.add_argument("--run-id", required=True)
    args = ap.parse_args()
    r = review(args.run_id)
    print(json.dumps(r, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
