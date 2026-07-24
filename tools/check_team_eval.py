"""チーム固定評価の一貫性ゲート検証 (進化探索フェーズ2の着手条件)。

複数チームをそれぞれ2回評価し、
  1. 同一チームの2回の勝率差が二項ノイズの想定範囲内か
  2. チーム間の順位が2回の測定で入れ替わらないか
を確認する。通れば評価器は「AとBどちらが強いか」を安定して言える。

    python -m tools.check_team_eval [--battles 60] [--teams 3]
"""
from __future__ import annotations

import argparse
import asyncio
import math
import time

from champions_agent.env.ranked_teams import build_ranked_teams
from champions_agent.env.team_builder import build_random_team_text
from tools.evaluate_team import evaluate_team_text


async def run(n_battles: int, n_teams: int) -> None:
    ranked = build_ranked_teams()
    teams = [("上位構築#1", ranked[0]), ("上位構築#10", ranked[9])]
    while len(teams) < n_teams:
        teams.append((f"生成チーム#{len(teams) - 1}",
                      build_random_team_text(size=6)))
    teams = teams[:n_teams]

    results = {}
    for trial in (1, 2):
        t0 = time.time()
        evals = await asyncio.gather(*[
            evaluate_team_text(text, n_battles=n_battles)
            for _, text in teams])
        dt = time.time() - t0
        for (name, _), r in zip(teams, evals):
            results.setdefault(name, []).append(r["win_rate"])
        print(f"--- 試行{trial}: {len(teams)}チーム x {n_battles}戦 "
              f"({dt:.0f}s, 評価者={evals[0]['evaluator']})", flush=True)

    # 1) 再現性: 二項ノイズ想定 (2回測定の差のSD = sqrt(2*p*(1-p)/n))
    sigma = math.sqrt(2 * 0.25 / n_battles)
    ok = True
    for name, (a, b) in results.items():
        z = abs(a - b) / sigma
        flag = "OK" if z < 2.5 else "⚠再現性低い"
        if z >= 2.5:
            ok = False
        print(f"{name}: {a:.2f} / {b:.2f} (差{abs(a - b):.2f}, "
              f"{z:.1f}σ) {flag}")

    # 2) 順位安定性
    order1 = sorted(results, key=lambda k: -results[k][0])
    order2 = sorted(results, key=lambda k: -results[k][1])
    stable = order1 == order2
    print(f"順位: 試行1 {order1} / 試行2 {order2} "
          f"{'一致' if stable else '⚠入れ替わり'}")
    print("ゲート判定: " + ("合格 (進化探索に進める)" if ok and stable else
                       "不合格 (対戦数を増やすか方策改善が必要)"))


def main() -> None:
    ap = argparse.ArgumentParser(description="チーム評価の一貫性検証")
    ap.add_argument("--battles", type=int, default=60)
    ap.add_argument("--teams", type=int, default=3)
    args = ap.parse_args()
    asyncio.run(run(args.battles, args.teams))


if __name__ == "__main__":
    main()
