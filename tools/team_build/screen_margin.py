"""screening の脱落 margin の見積もり (ablation 拡張の解析)。

run の S8a (cheap adaptation 段: teampreview / generic / cheap の最善) と S8b (full 段: teampreview / generic /
fresh の最善) の結果から、チームごとの
  Δ_cheap = S8a の最善 variant の Δ (対 参照)、Δ_full = S8b の最善 variant の Δ
  uplift  = Δ_full − Δ_cheap  (cheap 段からの伸び代。両段が別 seed なら独立な推定の差)
を出し、margin の候補ごとに「cheap 段で落としたが full 段では劣っていない (誤脱落)」と「cheap 段で残したが full 段で
劣っていた (無駄な適応)」の数を数える。順位反転 (S8a 順 → S8b 順) も出す。判定はしない (見直しの材料)。

    python -m tools.team_build.screen_margin --run-id chat_0907
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Optional

from champions_agent.config import BUILD_CI_Z, BUILD_EQUIV_EPS
from tools.team_build.pipeline import split_variant
from tools.team_build.verdict import DEGRADED

REPO = Path(__file__).resolve().parent.parent.parent
RUNS = REPO / "logs" / "build_search" / "runs"
MARGIN_GRID = (0.0, 0.03, 0.05, 0.08, 0.10)


def team_best_any(res: dict) -> dict:
    """チームごとに、脱落の有無によらず Δ が最大の variant (margin の見積もりでは脱落した腕も見る)。
    戻り値 {team: {"variant", "delta", "se", "n", "state"}}"""
    out = {}
    for a in res.get("arms", []):
        r = a.get("result") or {}
        d = r.get("mean")
        if d is None:
            continue
        cid, v = split_variant(a["arm_id"])
        if cid not in out or d > out[cid]["delta"]:
            out[cid] = {"variant": v, "delta": d, "se": r.get("se"), "n": a.get("n_done"), "state": a.get("state"),
                        "ci_high": r.get("ci_high"), "ci_low": r.get("ci_low")}
    return out


def analyze_margin(res8a: dict, res8b: dict, eps: float = BUILD_EQUIV_EPS, z: float = BUILD_CI_Z,
                   grid: tuple = MARGIN_GRID) -> dict:
    a, b = team_best_any(res8a), team_best_any(res8b)
    teams = [t for t in a if t in b]
    rows = []
    for t in teams:
        se = math.sqrt((a[t]["se"] or 0.0) ** 2 + (b[t]["se"] or 0.0) ** 2)
        rows.append({"team": t, "variant_cheap": a[t]["variant"], "delta_cheap": a[t]["delta"],
                     "ci_high_cheap": a[t].get("ci_high"), "variant_full": b[t]["variant"], "delta_full": b[t]["delta"],
                     "ci_high_full": b[t].get("ci_high"), "full_degraded": b[t]["state"] == DEGRADED,
                     "uplift": b[t]["delta"] - a[t]["delta"], "uplift_se": se})
    uplifts = sorted(r["uplift"] for r in rows)
    def q(p):
        if not uplifts:
            return None
        i = min(len(uplifts) - 1, max(0, int(round(p * (len(uplifts) - 1)))))
        return uplifts[i]
    table = []
    for m in grid:
        false_drop = [r["team"] for r in rows
                      if r["ci_high_cheap"] is not None and r["ci_high_cheap"] < -(eps + m) and not r["full_degraded"]]
        kept_bad = [r["team"] for r in rows
                    if not (r["ci_high_cheap"] is not None and r["ci_high_cheap"] < -(eps + m)) and r["full_degraded"]]
        table.append({"margin": m, "false_drop": false_drop, "kept_but_degraded": kept_bad})
    order_a = [r["team"] for r in sorted(rows, key=lambda r: -r["delta_cheap"])]
    order_b = [r["team"] for r in sorted(rows, key=lambda r: -r["delta_full"])]
    by = {r["team"]: r for r in rows}
    reversals = []
    for i, x in enumerate(order_a):
        for y in order_a[i + 1:]:
            if order_b.index(x) > order_b.index(y):
                # x は cheap 段で y より上、full 段で下。差が両段で CI を超えていれば resolved
                d_a = by[x]["delta_cheap"] - by[y]["delta_cheap"]
                d_b = by[y]["delta_full"] - by[x]["delta_full"]
                se_a = math.sqrt((a[x]["se"] or 0) ** 2 + (a[y]["se"] or 0) ** 2)
                se_b = math.sqrt((b[x]["se"] or 0) ** 2 + (b[y]["se"] or 0) ** 2)
                resolved = d_a - z * se_a > 0 and d_b - z * se_b > 0
                reversals.append({"above_in_cheap": x, "above_in_full": y, "gap_cheap": round(d_a, 4),
                                  "gap_full": round(d_b, 4), "resolved": resolved})
    suggested = max(0.0, q(0.9) or 0.0) if uplifts else None
    return {"n_teams": len(rows), "teams": rows, "uplift_mean": (sum(uplifts) / len(uplifts)) if uplifts else None,
            "uplift_q50": q(0.5), "uplift_q90": q(0.9), "uplift_max": (uplifts[-1] if uplifts else None),
            "suggested_margin_q90": suggested, "margin_table": table,
            "order_cheap": order_a, "order_full": order_b, "reversals": reversals, "eps": eps, "z": z}


def to_markdown(m: dict) -> str:
    lines = ["| Team | cheap (variant) | Δ_cheap | full (variant) | Δ_full | uplift |", "|---|---|---:|---|---:|---:|"]
    for r in m["teams"]:
        lines.append(f"| {r['team']} | {r['variant_cheap']} | {r['delta_cheap']:+.3f} | {r['variant_full']} | "
                     f"{r['delta_full']:+.3f}{' (degraded)' if r['full_degraded'] else ''} | {r['uplift']:+.3f} ±{r['uplift_se']:.3f} |")
    lines += ["", f"uplift: mean {m['uplift_mean']} / q50 {m['uplift_q50']} / q90 {m['uplift_q90']} / max {m['uplift_max']} "
                  f"→ margin の目安 (q90) {m['suggested_margin_q90']}", "",
              "| margin | 誤脱落 (cheap で落とすが full では劣らない) | 残したが full で劣る |", "|---:|---|---|"]
    for row in m["margin_table"]:
        lines.append(f"| {row['margin']:.2f} | {', '.join(row['false_drop']) or '-'} | {', '.join(row['kept_but_degraded']) or '-'} |")
    lines += ["", f"順位 (cheap): {' > '.join(m['order_cheap'])}", f"順位 (full): {' > '.join(m['order_full'])}"]
    if not m["reversals"]:
        lines.append("- 順位反転なし")
    for r in m["reversals"]:
        lines.append(f"- 反転: {r['above_in_cheap']} > {r['above_in_full']} (cheap) → 逆 (full)。差 cheap {r['gap_cheap']:+.3f} / "
                     f"full {r['gap_full']:+.3f} / {'CI 超え (resolved)' if r['resolved'] else '誤差の範囲'}")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-id", required=True)
    args = ap.parse_args(argv)
    ev = RUNS / args.run_id / "evaluation"
    res8a = json.loads((ev / "s08a_screen.json").read_text(encoding="utf-8"))
    res8b = json.loads((ev / "s08b_adapted.json").read_text(encoding="utf-8"))
    m = analyze_margin(res8a, res8b)
    (ev / "screen_margin.json").write_text(json.dumps(m, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    md = to_markdown(m)
    (ev / "screen_margin.md").write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
