"""run の測定統計を要約し、ε・分割比・戦数段階の見直し材料を出す (判定はしない)。

    python -m tools.team_build.review_run --run-id m3_llm

出力: 各 racing 段階の終了戦数の分布と最終状態、対応差 CI 半幅の推移、適応の収束履歴、holdout、所要時間、
      推奨 (uncertain で上限到達が多い → 上限/ε の見直し、CI 半幅が ε を大きく超える → 戦数段階の見直し、
      相手系統の分割の偏り → 分割比/系統閾値の見直し)。
"""
from __future__ import annotations

import argparse
import json
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


def recommendations(review: dict) -> list:
    rec = []
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
