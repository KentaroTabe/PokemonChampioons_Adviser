"""助言エンジン (advisor-as-player) の実戦診断: vs ベンチマークの勝率とレイテンシ。

    python -m tools.check_advisor_player --battles 100 --opp-seed 20260904 --json out.json
    オプション: --belief-k K (engine.BELIEF_K) / --sensor-q q / --workers N /
                --no-rl-blend (RL_BLEND_WEIGHT=0) / --skip-random
探索プレイヤー (check_search_expert) と同じ固定軸 (META_PIN) と相手列で測る。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import time
from pathlib import Path

from champions_agent.config import TRAINING_BATTLE_FORMAT


def _remembering_teambuilder(inner):
    """RankedTeambuilder をラップし、直近に出したチームテキストを覚える
    (poke-env の Player は Teambuilder 派生でないと受理しないため、
    import を遅延させてクラスを作る)"""
    from poke_env.teambuilder import Teambuilder

    class _Remembering(Teambuilder):
        def __init__(self):
            self.inner = inner
            self.last_text = None

        def yield_team(self):
            text = self.inner.rng.choice(self.inner.teams)
            self.last_text = text
            return self.join_team(self.parse_showdown_team(text))

    return _Remembering()


async def run(n_battles: int, opp_seed: int | None, json_out: str | None,
              skip_random: bool, belief_k: int | None, sensor_q: float | None,
              workers: int | None, no_rl_blend: bool,
              search_blend: float | None = None) -> None:
    from poke_env import AccountConfiguration
    from poke_env.player import RandomPlayer
    import advisor.engine as eng
    from champions_agent.env.advisor_player import make_advisor_player
    from champions_agent.env.ranked_teams import (
        RankedTeambuilder, pinned_meta_snapshot_id)
    from champions_agent.env.showdown_env import (
        TrainingServerConfiguration, make_benchmark_player)

    if belief_k is not None:
        eng.BELIEF_K = belief_k
    if sensor_q is not None:
        eng.SENSOR_Q_DEFAULT = sensor_q
    if workers is not None:
        eng.SEARCH_WORKERS = workers
    if no_rl_blend:
        os.environ["RL_BLEND_WEIGHT"] = "0"
    if search_blend is not None:
        eng.SEARCH_BLEND = search_blend
    meta_pin = pinned_meta_snapshot_id()
    stats: dict = {}
    latencies: list = []
    uid = os.getpid() % 100000
    own_tb = _remembering_teambuilder(RankedTeambuilder(
        rng=random.Random(opp_seed + 1) if opp_seed is not None else None,
        meta_snapshot_id=meta_pin))
    player = make_advisor_player(
        team_source=own_tb, stats=stats, latencies=latencies,
        account_configuration=AccountConfiguration(f"ADv{uid}", None),
        battle_format=TRAINING_BATTLE_FORMAT,
        server_configuration=TrainingServerConfiguration,
        team=own_tb)
    opp_team = None
    if opp_seed is not None:
        opp_team = RankedTeambuilder(top_n=60, include_external=False,
                                     rng=random.Random(opp_seed),
                                     meta_snapshot_id=meta_pin)
    bench = make_benchmark_player(
        battle_format=TRAINING_BATTLE_FORMAT, team=opp_team,
        account_configuration=AccountConfiguration(f"ADo{uid}", None))
    t0 = time.time()
    await player.battle_against(bench, n_battles=n_battles)
    dt = time.time() - t0
    outcomes = [1 if b.won else 0 for b in player.battles.values()]
    lat = sorted(latencies)
    p50 = lat[len(lat) // 2] if lat else 0.0
    p95 = lat[int(len(lat) * 0.95)] if lat else 0.0
    n_dec = stats.get("decide", 0) + stats.get("fallback", 0)
    print(f"=== 助言エンジン (belief_k={eng.BELIEF_K} sensor_q={eng.SENSOR_Q_DEFAULT} "
          f"search_blend={eng.SEARCH_BLEND} "
          f"workers={eng.SEARCH_WORKERS} rl_blend={os.environ.get('RL_BLEND_WEIGHT', '25')} "
          f"meta={meta_pin or 'latest'}) vs ベンチマーク {n_battles}戦 ({dt:.0f}s) ===")
    print(f"勝率: {player.n_won_battles / n_battles:.2f}")
    print(f"助言レイテンシ: p50 {p50:.0f}ms / p95 {p95:.0f}ms ({len(lat)}決定)")
    print(f"意思決定: 助言{stats.get('decide', 0)} / フォールバック{stats.get('fallback', 0)} "
          f"({stats.get('fallback', 0) / max(1, n_dec):.0%}) / 例外{stats.get('error', 0)}")
    if stats.get("last_error"):
        print(f"直近の例外: {stats['last_error']}")
    if json_out:
        Path(json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(json_out).write_text(json.dumps({
            "n_battles": n_battles, "wins": player.n_won_battles,
            "win_rate": player.n_won_battles / n_battles, "outcomes": outcomes,
            "belief_k": eng.BELIEF_K, "sensor_q": eng.SENSOR_Q_DEFAULT,
            "workers": eng.SEARCH_WORKERS, "search_blend": eng.SEARCH_BLEND,
            "rl_blend": os.environ.get("RL_BLEND_WEIGHT", "25"),
            "opp_seed": opp_seed, "meta_snapshot": meta_pin,
            "latency_p50_ms": round(p50, 1), "latency_p95_ms": round(p95, 1),
            "stats": {k: v for k, v in stats.items()
                      if k not in ("last_error", "_registered")},
            "elapsed_s": round(dt, 1),
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"保存: {json_out}")
    if skip_random:
        return
    rand = RandomPlayer(
        account_configuration=AccountConfiguration(f"ADr{uid}", None),
        battle_format=TRAINING_BATTLE_FORMAT,
        server_configuration=TrainingServerConfiguration,
        team=RankedTeambuilder(meta_snapshot_id=meta_pin))
    bench2 = make_benchmark_player(
        battle_format=TRAINING_BATTLE_FORMAT,
        account_configuration=AccountConfiguration(f"ADr2{uid}", None))
    await rand.battle_against(bench2, n_battles=n_battles)
    print(f"--- 基準線: RandomPlayer 勝率 {rand.n_won_battles / n_battles:.2f}")


def main() -> None:
    ap = argparse.ArgumentParser(description="助言エンジンの実戦診断")
    ap.add_argument("--battles", type=int, default=20)
    ap.add_argument("--opp-seed", type=int, default=None)
    ap.add_argument("--json", default=None)
    ap.add_argument("--skip-random", action="store_true")
    ap.add_argument("--belief-k", type=int, default=None)
    ap.add_argument("--sensor-q", type=float, default=None)
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--no-rl-blend", action="store_true")
    ap.add_argument("--search-blend", type=float, default=None,
                    help="探索の推奨値をスコアへ統合する重み (P9)。0=無効")
    args = ap.parse_args()
    asyncio.run(run(args.battles, args.opp_seed, args.json, args.skip_random,
                    args.belief_k, args.sensor_q, args.workers, args.no_rl_blend,
                    search_blend=args.search_blend))


if __name__ == "__main__":
    main()
