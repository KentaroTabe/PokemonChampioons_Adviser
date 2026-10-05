"""実験 13: 環境チーム (相手) の操縦の妥当性 (対戦あり、約 1 時間)。

参照 (登録チーム) を、相手の操縦 (heuristic / rl) × 選出 (matchup / rule / model / prior) の組ごとに同一相手列で N 戦ずつ測り、
勝率を実戦の勝率 (対戦記録、同じ 6 体) と並べる。実戦に最も近い組を config BUILD_OPP_PILOT / BUILD_OPP_PICK_POLICY の既定にする。
系統ごとの比較はしない (実戦の試合数が足りない)。

  python -m tools.team_build.experiments.opponent_pilot_validity --run-id ace_lopunny_1003 [--n 300] [--combos heuristic:heuristic,rl:rule,...]
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

from tools.team_build import racing as R
from tools.team_build.experiments import RUNS, load_json, write_result

DEFAULT_COMBOS = "heuristic:heuristic,heuristic:rule,rl:matchup,rl:rule,rl:model,rl:prior"


def gap_rows(outcomes_by_arm: dict, real: Optional[float]) -> list:
    """{arm_id: [勝敗 (1 / 0)]} と実戦の勝率 → 腕ごとの行 [{"arm", "n", "win_rate", "gap_to_real"}] (純粋)。
    実戦との差の絶対値が小さい順 (差が出せない行は末尾、同じなら入力の順)"""
    rows = []
    for arm_id, outcomes in outcomes_by_arm.items():
        wr = (sum(outcomes) / len(outcomes)) if outcomes else None
        rows.append({"arm": arm_id, "n": len(outcomes), "win_rate": round(wr, 4) if wr is not None else None,
                     "gap_to_real": (round(wr - real, 4) if (wr is not None and real is not None) else None)})
    rows.sort(key=lambda r: (r["gap_to_real"] is None, abs(r["gap_to_real"] or 0.0)))
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="実験 13: 相手の操縦 × 選出ごとの参照の勝率と実戦の勝率")
    ap.add_argument("--run-id", required=True, help="相手プール (opponent_families.json) と参照 (reference_team.txt) を取る run")
    ap.add_argument("--n", type=int, default=300)
    ap.add_argument("--combos", default=DEFAULT_COMBOS, help="pilot:pick のカンマ区切り")
    ap.add_argument("--tier", default="selection")
    ap.add_argument("--seed", type=int, default=20261005)
    ap.add_argument("--parallel", type=int, default=3)
    ap.add_argument("--real-win-rate", type=float, default=None,
                    help="実戦の勝率 (省略時は登録チームと同じ 6 体の対戦記録 (直近 --real-days 日) を直接数える。無ければ env_validity.json)")
    ap.add_argument("--real-days", type=float, default=90.0)
    args = ap.parse_args()
    run_dir = RUNS / args.run_id
    split = run_dir / "opponent_families.json"
    ref = run_dir / "reference_team.txt"
    summary = load_json(run_dir / "evaluation" / "summary.json") or {}
    models_dir = summary.get("models_dir")
    rv = summary.get("reference_variant") or {}
    arms = []
    for combo in [c.strip() for c in args.combos.split(",") if c.strip()]:
        pilot, pick = combo.split(":")
        arms.append(R.Arm(f"ref_{pilot}_{pick}", ref, rv.get("selection_model"), models_dir,
                          extra_args=["--opp-pilot", pilot, "--opp-pick-policy", pick], pick_policy=rv.get("pick_policy") or "advisor"))
    out_dir = run_dir / "evaluation" / "opponent_pilot"
    R.measure_round(arms, args.n, 0, args.seed, split, args.tier, None, out_dir, "opp_pilot", parallel=args.parallel)
    real = args.real_win_rate
    real_n = None
    if real is None:
        from tools.team_build.env_match import registered_real_record
        rec = registered_real_record(args.real_days)
        if rec.get("n"):
            real, real_n = rec["win_rate"], rec["n"]
    if real is None:
        ev = load_json(Path(__file__).resolve().parent.parent.parent.parent / "logs" / "build_search" / "experiments" / "env_validity.json") or {}
        real = ev.get("real_win_rate")
    rows = gap_rows({a.arm_id: a.outcomes for a in arms}, real)
    result = {"run_id": args.run_id, "n": args.n, "tier": args.tier, "real_win_rate": real, "real_n": real_n, "rows": rows,
              "closest": rows[0]["arm"] if rows and rows[0]["gap_to_real"] is not None else None}
    p = write_result("opponent_pilot_validity", result)
    for r in rows:
        print(f"{r['arm']:24s} n={r['n']} 勝率={r['win_rate']} 実戦との差={r['gap_to_real']}")
    print(f"実戦 {real} に最も近い: {result['closest']}。保存: {p}")


if __name__ == "__main__":
    main()
