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


def _constant_teambuilder(text: str):
    """固定チームテキストを毎戦出す Teambuilder (last_text で型登録に使う)"""
    from poke_env.teambuilder import Teambuilder

    class _Const(Teambuilder):
        def __init__(self):
            self.last_text = text
            self._packed = self.join_team(self.parse_showdown_team(text))

        def yield_team(self):
            return self._packed

    return _Const()


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
              search_blend: float | None = None,
              team_file: str | None = None,
              pick_policy: str = "advisor", selection_model: str | None = None,
              opp_split: str | None = None, battle_log: str | None = None,
              pick_noise: float = 0.0, action_noise: float = 0.0,
              user_policy: str = "full", candidate_id: str | None = None,
              opp_offset: int = 0) -> None:
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
    if team_file:
        team_text = Path(team_file).read_text(encoding="utf-8")
        # 不正なチームだと Showdown が拒否し poke-env が対戦を待ち続けるので、先に合法性を検査して即終了する
        try:
            from tools.team_build.sets import validate_team_text
            ok, errs = validate_team_text(team_text, TRAINING_BATTLE_FORMAT)
        except Exception:
            ok, errs = True, []
        if not ok:
            raise SystemExit(f"チーム本文が不正 (validate-team): {errs[:3]}")
        own_tb = _constant_teambuilder(team_text)
    else:
        own_tb = _remembering_teambuilder(RankedTeambuilder(
            rng=random.Random(opp_seed + 1) if opp_seed is not None else None,
            meta_snapshot_id=meta_pin))
    # 相手列: --opp-split FILE:TIER[:FOLD] なら系統分割の階層から決定的な相手列 (対応比較用)、
    # 無ければ従来どおり上位 60 構築からシードで抽選
    opp_team, split_info, family_of = None, None, {}
    if opp_split:
        from tools.team_build.opponents import (
            SequenceTeambuilder, load_split, opponent_sequence, tier_ids)
        parts = opp_split.split(":")
        doc = load_split(Path(parts[0]))
        tier = parts[1] if len(parts) > 1 else "selection"
        fold = int(parts[2]) if len(parts) > 2 else None
        # 相手列は「階層の全構築を seed で並べた列」を offset から n 戦ぶん使う (追加測定は続きから)
        seq = opponent_sequence(tier_ids(doc, tier, fold), opp_offset + n_battles, opp_seed or 0)
        opp_team = SequenceTeambuilder(seq, doc["texts"], offset=opp_offset)
        family_of = {tid: f["family_id"] for f in doc["families"] for tid in f["teams"]}
        split_info = {"file": parts[0], "tier": tier, "fold": fold, "offset": opp_offset,
                      "sealed_id": doc.get("sealed_id"), "run_id": doc.get("run_id")}
    elif opp_seed is not None:
        opp_team = RankedTeambuilder(top_n=60, include_external=False,
                                     rng=random.Random(opp_seed),
                                     meta_snapshot_id=meta_pin)
    recorder = None
    if battle_log:
        from tools.team_build.battle_log import BattleRecorder
        recorder = BattleRecorder(
            Path(battle_log), candidate_team_id=candidate_id or (team_file or "ranked"),
            advisor_policy_id=(f"rl:{os.environ.get('CHAMPIONS_MODELS_DIR', 'default')}"
                               f"|sel:{selection_model or 'default'}"),
            user_policy=user_policy, battle_seed=opp_seed, family_of=family_of)
    player = make_advisor_player(
        team_source=own_tb, stats=stats, latencies=latencies,
        pick_policy=pick_policy, selection_model_path=selection_model,
        pick_noise=pick_noise, action_noise=action_noise, user_policy=user_policy,
        rng=random.Random((opp_seed or 0) + 7), recorder=recorder, opp_source=opp_team,
        account_configuration=AccountConfiguration(f"ADv{uid}", None),
        battle_format=TRAINING_BATTLE_FORMAT,
        server_configuration=TrainingServerConfiguration,
        team=own_tb)
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
            "team_file": team_file, "candidate_id": candidate_id,
            "pick_policy": pick_policy, "selection_model": selection_model,
            "opp_split": split_info, "battle_log": battle_log,
            "pick_noise": pick_noise, "action_noise": action_noise, "user_policy": user_policy,
            "models_dir": os.environ.get("CHAMPIONS_MODELS_DIR"),
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
    ap.add_argument("--pick-policy", choices=["advisor", "teampreview"], default="advisor",
                    help="選出方策: advisor = 実助言と同じ選出モデル (既定) / teampreview = 相性順")
    ap.add_argument("--selection-model", default=None, help="候補専用の選出モデル (.pt)")
    ap.add_argument("--opp-split", default=None,
                    help="相手列: opponent_families.json のパス:階層[:fold] (例 runs/x/opponent_families.json:selection)")
    ap.add_argument("--battle-log", default=None, help="対戦記録 (JSONL) の出力先")
    ap.add_argument("--pick-noise", type=float, default=0.0, help="STRESS: 選出を乱択する確率")
    ap.add_argument("--action-noise", type=float, default=0.0, help="STRESS: 2位の手を選ぶ確率")
    ap.add_argument("--user-policy", choices=["full", "high", "mixed", "expert"], default="full",
                    help="遵守モデル (tools.team_build.user_model)")
    ap.add_argument("--models-dir", default=None,
                    help="行動方策 (RL) のピン dir。CHAMPIONS_MODELS_DIR に設定してから読み込む")
    ap.add_argument("--candidate-id", default=None, help="対戦記録に付ける候補 id")
    ap.add_argument("--opp-offset", type=int, default=0,
                    help="相手列の開始位置 (racing の追加測定で同じ相手列の続きを使う)")
    ap.add_argument("--team-file", default=None,
                    help="自分側を固定チーム (Showdownテキスト) にする (構築の操縦しやすさ測定)")
    args = ap.parse_args()
    if args.models_dir:
        # rl_bridge は import 時に CHAMPIONS_MODELS_DIR を読むので、run (内部で import) の前に設定する
        os.environ["CHAMPIONS_MODELS_DIR"] = str(Path(args.models_dir).resolve())
    asyncio.run(run(args.battles, args.opp_seed, args.json, args.skip_random,
                    args.belief_k, args.sensor_q, args.workers, args.no_rl_blend,
                    search_blend=args.search_blend, team_file=args.team_file,
                    pick_policy=args.pick_policy, selection_model=args.selection_model,
                    opp_split=args.opp_split, battle_log=args.battle_log,
                    pick_noise=args.pick_noise, action_noise=args.action_noise,
                    user_policy=args.user_policy, candidate_id=args.candidate_id,
                    opp_offset=args.opp_offset))


if __name__ == "__main__":
    main()
