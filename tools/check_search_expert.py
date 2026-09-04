"""探索エキスパート (search_expert) の実戦診断。

vs ベンチマークで対戦させ、意思決定の内訳 (探索成功/フォールバック率、
技/交代比率、平均ターン数) と勝率を測る。RandomPlayer の基準線も併記して
「探索がどれだけ上積みしているか」を見る。

    python -m tools.check_search_expert --battles 20 --depth 1
"""
from __future__ import annotations

import argparse
import asyncio
import os
import time
from pathlib import Path

from champions_agent.config import TRAINING_BATTLE_FORMAT


async def run(n_battles: int, depth: int, by: str = "recommended",
              use_value: bool = True, belief_k: int = 0,
              opp_seed: int | None = None, json_out: str | None = None,
              skip_random: bool = False, opp_prior_mix: float = 0.0,
              sensor_noise: float = 0.0, sensor_q: float = 0.0,
              sensor_delta: float = 0.25) -> None:
    import json
    import random
    from poke_env import AccountConfiguration
    from poke_env.player import Player, RandomPlayer
    from champions_agent.env.ranked_teams import (
        RankedTeambuilder, pinned_meta_snapshot_id)
    from champions_agent.env.search_expert import decide, teampreview_order
    from champions_agent.env.showdown_env import (
        TrainingServerConfiguration, make_benchmark_player,
    )

    # 評価軸: META_PIN があれば固定スナップショットの型、相手列は opp_seed で再現
    meta_pin = pinned_meta_snapshot_id()
    stats = {"decide": 0, "fallback": 0, "error": 0,
             "move": 0, "switch": 0, "mega": 0}
    latencies: list = []

    class _DiagExpert(Player):
        def choose_move(self, battle):
            t0 = time.perf_counter()
            try:
                d = decide(battle, depth=depth, by=by, use_value=use_value,
                           belief_k=belief_k, opp_prior_mix=opp_prior_mix,
                           sensor_noise=sensor_noise, sensor_q=sensor_q,
                           sensor_delta=sensor_delta)
            except Exception as e:
                stats["error"] += 1
                stats.setdefault("last_error", repr(e))
                d = None
            latencies.append((time.perf_counter() - t0) * 1000.0)
            if d is None:
                stats["fallback"] += 1
                return self.choose_random_move(battle)
            stats["decide"] += 1
            stats[d["kind"]] += 1
            if d["mega"]:
                stats["mega"] += 1
            if d["kind"] == "move":
                return self.create_order(d["move"], mega=d["mega"])
            return self.create_order(d["pokemon"])

        def teampreview(self, battle):
            try:
                return teampreview_order(battle)
            except Exception:
                return self.random_teampreview(battle)

    uid = os.getpid() % 100000
    own_rng = random.Random(opp_seed + 1) if opp_seed is not None else None
    expert = _DiagExpert(
        account_configuration=AccountConfiguration(f"DXex{uid}", None),
        battle_format=TRAINING_BATTLE_FORMAT,
        server_configuration=TrainingServerConfiguration,
        team=RankedTeambuilder(rng=own_rng, meta_snapshot_id=meta_pin),
    )
    opp_team = None
    if opp_seed is not None:
        opp_team = RankedTeambuilder(top_n=60, include_external=False,
                                     rng=random.Random(opp_seed),
                                     meta_snapshot_id=meta_pin)
    bench1 = make_benchmark_player(
        battle_format=TRAINING_BATTLE_FORMAT, team=opp_team,
        account_configuration=AccountConfiguration(f"DXop{uid}", None))
    t0 = time.time()
    await expert.battle_against(bench1, n_battles=n_battles)
    dt = time.time() - t0
    turns = [b.turn for b in expert.battles.values()]
    outcomes = [1 if b.won else 0 for b in expert.battles.values()]
    n_dec = stats["decide"] + stats["fallback"]
    lat_sorted = sorted(latencies)
    p50 = lat_sorted[len(lat_sorted) // 2] if lat_sorted else 0.0
    p95 = lat_sorted[int(len(lat_sorted) * 0.95)] if lat_sorted else 0.0
    print(f"=== 探索エキスパート (depth={depth} by={by} belief_k={belief_k} "
          f"value={'on' if use_value else 'off'} prior_mix={opp_prior_mix} "
          f"meta={meta_pin or 'latest'}) "
          f"vs ベンチマーク {n_battles}戦 ({dt:.0f}s) ===")
    print(f"勝率: {expert.n_won_battles / n_battles:.2f}")
    print(f"探索レイテンシ: p50 {p50:.0f}ms / p95 {p95:.0f}ms ({len(latencies)}決定)")
    if json_out:
        Path(json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(json_out).write_text(json.dumps({
            "n_battles": n_battles, "wins": expert.n_won_battles,
            "win_rate": expert.n_won_battles / n_battles,
            "outcomes": outcomes, "depth": depth, "by": by,
            "use_value": use_value, "belief_k": belief_k,
            "opp_prior_mix": opp_prior_mix,
            "sensor_noise": sensor_noise, "sensor_q": sensor_q,
            "sensor_delta": sensor_delta,
            "opp_seed": opp_seed, "meta_snapshot": meta_pin,
            "latency_p50_ms": round(p50, 1), "latency_p95_ms": round(p95, 1),
            "stats": {k: v for k, v in stats.items() if k != "last_error"},
            "elapsed_s": round(dt, 1),
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"保存: {json_out}")
    if skip_random:
        return
    print(f"平均ターン数: {sum(turns) / max(1, len(turns)):.1f}")
    print(f"意思決定: 探索{stats['decide']} / フォールバック{stats['fallback']} "
          f"({stats['fallback'] / max(1, n_dec):.0%}) / 例外{stats['error']}")
    print(f"内訳: 技{stats['move']} (うちメガ{stats['mega']}) "
          f"交代{stats['switch']}")
    if stats.get("last_error"):
        print(f"直近の例外: {stats['last_error']}")

    rand = RandomPlayer(
        account_configuration=AccountConfiguration(f"DXrd{uid}", None),
        battle_format=TRAINING_BATTLE_FORMAT,
        server_configuration=TrainingServerConfiguration,
        team=RankedTeambuilder(),
    )
    bench2 = make_benchmark_player(
        battle_format=TRAINING_BATTLE_FORMAT,
        account_configuration=AccountConfiguration(f"DXo2{uid}", None))
    await rand.battle_against(bench2, n_battles=n_battles)
    rturns = [b.turn for b in rand.battles.values()]
    print(f"--- 基準線: RandomPlayer 勝率 "
          f"{rand.n_won_battles / n_battles:.2f} "
          f"(平均{sum(rturns) / max(1, len(rturns)):.1f}ターン)")


def main() -> None:
    ap = argparse.ArgumentParser(description="探索エキスパートの実戦診断")
    ap.add_argument("--battles", type=int, default=20)
    ap.add_argument("--depth", type=int, default=1)
    ap.add_argument("--by", default="recommended",
                    choices=["recommended", "expected"])
    ap.add_argument("--no-value", action="store_true",
                    help="RL価値関数の葉評価ブレンドを無効化 (素の探索を測る)")
    ap.add_argument("--belief", type=int, default=0,
                    help="相手型の仮説数 (P7): 0=単一仮定 / 1=最尤 / 8=多世界")
    ap.add_argument("--opp-seed", type=int, default=None,
                    help="相手チーム列の乱数種 (対応比較用)")
    ap.add_argument("--json", default=None, help="結果の保存先")
    ap.add_argument("--skip-random", action="store_true",
                    help="RandomPlayer の基準線を省略 (時間短縮)")
    ap.add_argument("--opp-prior-mix", type=float, default=0.0,
                    help="相手行動の事前分布の混合率 λ (P6-b): 0=使用率のみ")
    ap.add_argument("--sensor-noise", type=float, default=0.0,
                    help="自分表示HPの固着確率 (P8 雑音注入)")
    ap.add_argument("--sensor-q", type=float, default=0.0,
                    help="探索が持つ『表示より低いHP』世界の確率 (P8)")
    ap.add_argument("--sensor-delta", type=float, default=0.25,
                    help="その世界でのHP低下幅 (P8)")
    args = ap.parse_args()
    asyncio.run(run(args.battles, args.depth, args.by,
                    use_value=not args.no_value, belief_k=args.belief,
                    opp_seed=args.opp_seed, json_out=args.json,
                    skip_random=args.skip_random,
                    opp_prior_mix=args.opp_prior_mix,
                    sensor_noise=args.sensor_noise, sensor_q=args.sensor_q,
                    sensor_delta=args.sensor_delta))


if __name__ == "__main__":
    main()
