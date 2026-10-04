"""実験 2: 構想の出所 (LLM / 規則 / 軸 / 記事 / 現行枝 / 修理) ごとに、並びがどの段まで到達したかを run を跨いで集計する。

出所は s04_concepts.json の families[].source (llm:<framing> / rule:<framing> / archetype:<axis> / article) を先頭の語で束ね、
現行枝 (tag incumbent / incumbent_mut)、修理の変種 (tag repair)、持ち込み (imported) は別に数える。
到達の段: generated (S5) → measured (S8a の腕がある) → s8a_survived (脱落していない) → s8b_contender → s10_contender → winner → holdout PASS。

  python -m tools.team_build.experiments.concept_origin [--runs a,b,c]
"""
from __future__ import annotations

import argparse

from tools.team_build.experiments import RUNS, best_delta_by_team, load_json, write_result

STAGES = ("generated", "measured", "s8a_survived", "s8b_contender", "s10_contender", "winner", "holdout_pass")


# ------------------------------------------------------------------ 純粋関数
def source_class(row: dict, fam_source: dict) -> str:
    """s06 の行 → 出所の種類。現行枝 / 修理 / 持ち込み は tag と origin で、それ以外は構想の source の先頭の語"""
    tag = row.get("tag") or ""
    if tag in ("incumbent", "incumbent_mut"):
        return "incumbent"
    if tag == "repair" or (row.get("origin") or {}).get("kind") == "repair":
        return "repair"
    if tag == "imported" or row.get("imported_from"):
        return "imported"
    cid = str(row.get("candidate_id") or "")
    concept = cid.split("_", 1)[1] if "_" in cid else ""
    src = fam_source.get(concept) or fam_source.get(row.get("concept") or "") or "unknown"
    return str(src).split(":", 1)[0]


def reach_table(rows: list, fam_source: dict, deltas8a: dict, s8b_contenders: list, s10: dict, winner, holdout_verdict) -> dict:
    """出所ごとの到達数 {source: {stage: n}} と、測定された並びの一覧"""
    s10_alive = {aid for aid, a in ((x.get("arm_id"), x) for x in (s10 or {}).get("arms", []))
                 if a.get("eliminated_at") is None and a.get("state") != "degraded"}
    table: dict = {}
    teams: list = []
    for row in rows:
        if not row.get("ok"):
            continue
        cid = row.get("candidate_id")
        src = source_class(row, fam_source)
        t = table.setdefault(src, {s: 0 for s in STAGES})
        t["generated"] += 1
        m = deltas8a.get(cid)
        if m is None:
            continue
        t["measured"] += 1
        reach = "measured"
        if not m["eliminated"]:
            t["s8a_survived"] += 1
            reach = "s8a_survived"
        if cid in (s8b_contenders or []):
            t["s8b_contender"] += 1
            reach = "s8b_contender"
        if cid in s10_alive:
            t["s10_contender"] += 1
            reach = "s10_contender"
        if cid == winner:
            t["winner"] += 1
            reach = "winner"
            if str(holdout_verdict or "").startswith("PASS"):
                t["holdout_pass"] += 1
                reach = "holdout_pass"
        teams.append({"candidate_id": cid, "source": src, "score": row.get("score"), "delta_s8a": round(m["delta"], 4), "reach": reach})
    return {"table": table, "teams": teams}


def aggregate(per_run: list) -> dict:
    agg: dict = {}
    for r in per_run:
        for src, t in r["table"].items():
            a = agg.setdefault(src, {s: 0 for s in STAGES})
            for s in STAGES:
                a[s] += t.get(s, 0)
    for src, a in agg.items():
        a["survive_rate_s8a"] = round(a["s8a_survived"] / a["measured"], 3) if a["measured"] else None
        a["s10_rate"] = round(a["s10_contender"] / a["measured"], 3) if a["measured"] else None
    return agg


# ------------------------------------------------------------------ 配線
def collect_run(run_dir):
    s04 = load_json(run_dir / "s04_concepts.json") or {}
    s06 = load_json(run_dir / "s06_sets.json")
    res8a = load_json(run_dir / "evaluation" / "s08a_screen.json")
    if not s06 or not res8a:
        return None
    fam_source = {f.get("family_id"): f.get("source") or "unknown" for f in (s04.get("families") or [])}
    summary = load_json(run_dir / "evaluation" / "summary.json") or {}
    s10 = load_json(run_dir / "evaluation" / "s10.json") or {}
    out = reach_table(s06, fam_source, best_delta_by_team(res8a), summary.get("s08b_contenders") or [], s10,
                      summary.get("winner"), (summary.get("holdout") or {}).get("verdict"))
    out["run_id"] = run_dir.name
    out["n_families_by_source"] = {}
    for src in fam_source.values():
        k = str(src).split(":", 1)[0]
        out["n_families_by_source"][k] = out["n_families_by_source"].get(k, 0) + 1
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="実験 2: 構想の出所ごとの到達率")
    ap.add_argument("--runs", default=None)
    args = ap.parse_args()
    run_ids = [r.strip() for r in args.runs.split(",")] if args.runs else sorted(p.name for p in RUNS.iterdir() if p.is_dir())
    per_run = [c for c in (collect_run(RUNS / r) for r in run_ids) if c]
    result = {"runs": per_run, "aggregate": aggregate(per_run)}
    p = write_result("concept_origin", result)
    print(f"{'出所':10s} " + " ".join(f"{s:>13s}" for s in STAGES) + "   S8a 生存率  S10 到達率")
    for src, a in sorted(result["aggregate"].items(), key=lambda kv: -kv[1]["measured"]):
        print(f"{src:10s} " + " ".join(f"{a[s]:>13d}" for s in STAGES) + f"   {a['survive_rate_s8a']}  {a['s10_rate']}")
    print(f"run: {[r['run_id'] for r in per_run]}")
    print(f"保存: {p}")


if __name__ == "__main__":
    main()
