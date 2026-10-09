"""助言エンジン (advisor-as-player) の実戦診断: vs ベンチマークの勝率とレイテンシ。

    python -m tools.check_advisor_player --battles 100 --opp-seed 20260904 --json out.json
    オプション: --belief-k K (engine.BELIEF_K) / --sensor-q q / --workers N /
                --rl-blend W (RL 加点の重み。既定 = engine.RL_BLEND_DEFAULT) / --no-rl-blend (= --rl-blend 0) / --skip-random /
                --battle-format ID (既定 = config.TRAINING_BATTLE_FORMAT)
重みは evaluate の引数で渡す (2026-10-07: 環境変数 RL_BLEND_WEIGHT は書かない。起動時の既定値の供給源としてだけ読まれる)。
探索プレイヤー (check_search_expert) と同じ固定軸 (META_PIN) と相手列で測る。
自チーム: --team-file (固定) / --own-pool FILE (実験専用の構築プール、tools.scoring_pool。Random(相手 seed + 1) で対戦ごとに引く。
2026-10-09 M2d) / どちらも無ければ RankedTeambuilder (META_PIN の構築プール、同じ seed の取り方)。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import time
from pathlib import Path

from champions_agent import config as _CFG
from champions_agent.config import TRAINING_BATTLE_FORMAT


def _constant_teambuilder(text: str):
    """固定チームテキストを毎戦出す Teambuilder (text_for_battle で型登録に使う。どの対戦も同じ本文)"""
    from poke_env.teambuilder import Teambuilder

    class _Const(Teambuilder):
        def __init__(self):
            self.last_text = text
            self.texts = [text]
            self._packed = self.join_team(self.parse_showdown_team(text))

        def text_for_battle(self, k: int):
            return text

        def yield_team(self):
            return self._packed

    return _Const()


def _remembering_teambuilder(inner):
    """RankedTeambuilder をラップし、出したチームテキストを出した順に覚える
    (poke-env の Player は Teambuilder 派生でないと受理しないため、
    import を遅延させてクラスを作る)。
    型登録は text_for_battle(k) = k 番目の対戦で出したチームで行う (2026-10-09)。last_text (直近に出した本文) は
    poke-env が対戦 k の開始直後に対戦 k+1 のチームを引くため、対戦 k の時点で既に次のチームになっている
    (advisor_player.team_text_for_battle の説明)"""
    from poke_env.teambuilder import Teambuilder

    class _Remembering(Teambuilder):
        def __init__(self):
            self.inner = inner
            self.last_text = None
            self.texts: list = []

        def text_for_battle(self, k: int):
            return self.texts[k] if 0 <= k < len(self.texts) else None

        def yield_team(self):
            text = self.inner.rng.choice(self.inner.teams)
            self.last_text = text
            self.texts.append(text)
            return self.join_team(self.parse_showdown_team(text))

    return _Remembering()


class _PoolSource:
    """実験専用の構築プール (tools/scoring_pool のファイル) から引く元 (_remembering_teambuilder の inner。RankedTeambuilder と同じ
    teams / rng の 2 つだけを持つ)"""

    def __init__(self, texts: list, rng: random.Random):
        self.teams = list(texts)
        self.rng = rng


def resolve_rl_blend(rl_blend: float | None, no_rl_blend: bool, default: float) -> float:
    """測定で使う RL 加点の重み (純粋)。--no-rl-blend は 0 の別名、どちらも無ければ既定値。
    両方を矛盾する値で指定したらエラー (どちらを測ったか manifest と食い違わないように)"""
    if no_rl_blend:
        if rl_blend is not None and float(rl_blend) != 0.0:
            raise SystemExit(f"--no-rl-blend と --rl-blend {rl_blend} は同時に指定できない")
        return 0.0
    return float(default) if rl_blend is None else float(rl_blend)


async def run(n_battles: int, opp_seed: int | None, json_out: str | None,
              skip_random: bool, belief_k: int | None, sensor_q: float | None,
              workers: int | None, no_rl_blend: bool,
              search_blend: float | None = None,
              team_file: str | None = None,
              pick_policy: str = "advisor", selection_model: str | None = None,
              opp_split: str | None = None, battle_log: str | None = None,
              pick_noise: float = 0.0, action_noise: float = 0.0,
              candidate_id: str | None = None,
              opp_offset: int = 0, selection_plan: str | None = None,
              opp_pilot: str | None = None, opp_pick_policy: str | None = None,
              rl_blend: float | None = None, battle_format: str | None = None,
              own_pool: str | None = None) -> None:
    from poke_env import AccountConfiguration
    from poke_env.player import RandomPlayer
    import advisor.engine as eng
    from champions_agent.env.advisor_player import make_advisor_player, transition_timing
    from champions_agent.env.ranked_teams import (
        RankedTeambuilder, pinned_meta_snapshot_id)
    from champions_agent.config import BUILD_OPP_PICK_POLICY, BUILD_OPP_PILOT
    from champions_agent.env.showdown_env import TrainingServerConfiguration, make_benchmark_player
    from tools.team_build.pilot import make_opponent_player
    opp_pilot = opp_pilot or BUILD_OPP_PILOT
    # 対戦のフォーマット (2026-10-09: 過去の判定の再測定で当時の M-B を指定するため。既定は学習と同じ TRAINING_BATTLE_FORMAT)
    battle_format = battle_format or TRAINING_BATTLE_FORMAT
    opp_pick_policy = opp_pick_policy or BUILD_OPP_PICK_POLICY

    if belief_k is not None:
        eng.BELIEF_K = belief_k
    if sensor_q is not None:
        eng.SENSOR_Q_DEFAULT = sensor_q
    if workers is not None:
        eng.SEARCH_WORKERS = workers
    rl_blend_weight = resolve_rl_blend(rl_blend, no_rl_blend, eng.RL_BLEND_DEFAULT)
    if search_blend is not None:
        eng.SEARCH_BLEND = search_blend
    meta_pin = pinned_meta_snapshot_id()
    own_pool_info = None
    stats: dict = {}
    latencies: list = []
    uid = os.getpid() % 100000
    if team_file and own_pool:
        raise SystemExit("--team-file と --own-pool は同時に指定できない")
    if team_file:
        team_text = Path(team_file).read_text(encoding="utf-8")
        # 不正なチームだと Showdown が拒否し poke-env が対戦を待ち続けるので、先に合法性を検査して即終了する
        try:
            from tools.team_build.sets import validate_team_text
            ok, errs = validate_team_text(team_text, battle_format)
        except Exception:
            ok, errs = True, []
        if not ok:
            raise SystemExit(f"チーム本文が不正 (validate-team): {errs[:3]}")
        own_tb = _constant_teambuilder(team_text)
    elif own_pool:
        # 実験専用の構築プール (2026-10-09 M2d、tools/scoring_pool): RankedTeambuilder と同じく Random(相手 seed + 1) で対戦ごとに引く
        if opp_seed is None:
            raise SystemExit("--own-pool には --opp-seed が要る (条件間で自チームの列を対応させるため)")
        from tools.scoring_pool import load_pool
        try:
            pool_texts, pool_doc = load_pool(own_pool)
        except ValueError as e:
            raise SystemExit(f"--own-pool が不正: {e}")
        own_pool_info = {"path": str(own_pool), "sha16": pool_doc.get("sha16"), "n": len(pool_texts),
                         "snapshot_id": (pool_doc.get("conditions") or {}).get("snapshot_id"),
                         "battle_format": (pool_doc.get("conditions") or {}).get("battle_format")}
        own_tb = _remembering_teambuilder(_PoolSource(pool_texts, random.Random(opp_seed + 1)))
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
                               f"|sel:{selection_model or 'default'}|pick:{pick_policy}|opp:{opp_pilot}/{opp_pick_policy}"),
            user_policy="advisor", battle_seed=opp_seed, family_of=family_of)
    plan = None
    if selection_plan:
        from tools.team_build.plan_prior import load_plan
        plan = load_plan(selection_plan) or None
    player = make_advisor_player(
        team_source=own_tb, stats=stats, latencies=latencies,
        pick_policy=pick_policy, selection_model_path=selection_model,
        pick_noise=pick_noise, action_noise=action_noise,
        rng=random.Random((opp_seed or 0) + 7), recorder=recorder, opp_source=opp_team,
        selection_plan=plan, family_of=family_of, rl_blend_weight=rl_blend_weight,
        account_configuration=AccountConfiguration(f"ADv{uid}", None),
        battle_format=battle_format,
        server_configuration=TrainingServerConfiguration,
        team=own_tb)
    # 環境チームの操縦 (2026-10-05): 行動は opp_pilot (heuristic / rl)、選出は opp_pick_policy。全腕で同じ設定 (racing が渡す)
    bench = make_opponent_player(opp_pilot, opp_team, battle_format, AccountConfiguration(f"ADo{uid}", None),
                                 pick_policy=opp_pick_policy, rng=random.Random((opp_seed or 0) + 11))
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
          f"workers={eng.SEARCH_WORKERS} rl_blend={rl_blend_weight:g} "
          f"meta={meta_pin or 'latest'} format={battle_format}) vs ベンチマーク {n_battles}戦 ({dt:.0f}s) ===")
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
            "pick_policy": pick_policy, "selection_model": selection_model, "selection_plan": selection_plan,
            "opp_split": split_info, "battle_log": battle_log,
            "pick_noise": pick_noise, "action_noise": action_noise,
            "opp_pilot": opp_pilot, "opp_pick_policy": opp_pick_policy,
            "models_dir": os.environ.get("CHAMPIONS_MODELS_DIR"),
            "rl_blend": rl_blend_weight,
            # 採点方式 (2026-10-09 M2b。環境変数 ADVICE_SCORING_MODE → config)。transition の退避の件数は stats の scoring_fallback*
            "advice_scoring_mode": _CFG.ADVICE_SCORING_MODE,
            # 2026-10-09 M2d: 感度の計算の有無 (環境変数 TRANSITION_SENSITIVITY → config)、自チームのプール (--own-pool)、
            # transition の本採点と全処理時間 (感度込み) の p50 / p95
            "transition_sensitivity": _CFG.TRANSITION_SENSITIVITY_ENABLED,
            "own_pool": own_pool_info,
            "transition_timing": transition_timing(stats),
            "opp_seed": opp_seed, "meta_snapshot": meta_pin, "battle_format": battle_format,
            "latency_p50_ms": round(p50, 1), "latency_p95_ms": round(p95, 1),
            "stats": {k: v for k, v in stats.items()
                      if k not in ("last_error",) and not str(k).startswith("_")},
            "elapsed_s": round(dt, 1),
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"保存: {json_out}")
    if skip_random:
        return
    rand = RandomPlayer(
        account_configuration=AccountConfiguration(f"ADr{uid}", None),
        battle_format=battle_format,
        server_configuration=TrainingServerConfiguration,
        team=RankedTeambuilder(meta_snapshot_id=meta_pin))
    bench2 = make_benchmark_player(
        battle_format=battle_format,
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
    ap.add_argument("--no-rl-blend", action="store_true", help="RL 加点を切る (--rl-blend 0 の別名)")
    ap.add_argument("--rl-blend", type=float, default=None,
                    help="RL の行動確率の加点の重み (既定 = config RL_BLEND_WEIGHT_DEFAULT、起動時の環境変数 RL_BLEND_WEIGHT があればその値)")
    ap.add_argument("--search-blend", type=float, default=None,
                    help="探索の推奨値をスコアへ統合する重み (P9)。0=無効")
    ap.add_argument("--pick-policy", choices=["advisor", "rule", "teampreview"], default="advisor",
                    help="選出方策: advisor = 実助言と同じ (選出モデル → 相性の規則) / rule = 相性の規則だけ / teampreview = 従来の簡易相性順")
    ap.add_argument("--opp-pilot", choices=["heuristic", "rl"], default=None,
                    help="環境チーム (相手) の操縦: heuristic = poke-env SimpleHeuristicsPlayer / rl = 学習済み行動方策 (既定 config BUILD_OPP_PILOT)")
    ap.add_argument("--opp-pick-policy", choices=["heuristic", "matchup", "rule", "model", "prior"], default=None,
                    help="環境チームの選出 (既定 config BUILD_OPP_PICK_POLICY)")
    ap.add_argument("--selection-model", default=None, help="候補専用の選出モデル (.pt)")
    ap.add_argument("--selection-plan", default=None,
                    help="構築の選出計画 (s06_sets/<cid>.plan.json)。advisor の選出でモデルの予測に計画の事前を足す (--opp-split が要る)")
    ap.add_argument("--opp-split", default=None,
                    help="相手列: opponent_families.json のパス:階層[:fold] (例 runs/x/opponent_families.json:selection)")
    ap.add_argument("--battle-log", default=None, help="対戦記録 (JSONL) の出力先")
    ap.add_argument("--pick-noise", type=float, default=0.0, help="STRESS: 選出を乱択する確率")
    ap.add_argument("--action-noise", type=float, default=0.0, help="STRESS: 2位の手を選ぶ確率")
    ap.add_argument("--models-dir", default=None,
                    help="行動方策 (RL) のピン dir。CHAMPIONS_MODELS_DIR に設定してから読み込む")
    ap.add_argument("--candidate-id", default=None, help="対戦記録に付ける候補 id")
    ap.add_argument("--opp-offset", type=int, default=0,
                    help="相手列の開始位置 (racing の追加測定で同じ相手列の続きを使う)")
    ap.add_argument("--battle-format", default=None,
                    help="対戦のフォーマット id (既定 config TRAINING_BATTLE_FORMAT)。過去の判定の再測定で当時の M-B (gen9championsbssregmb) を指定する")
    ap.add_argument("--team-file", default=None,
                    help="自分側を固定チーム (Showdownテキスト) にする (構築の操縦しやすさ測定)")
    ap.add_argument("--own-pool", default=None,
                    help="自分側を実験専用の構築プール (tools.scoring_pool のファイル) から Random(相手 seed + 1) で対戦ごとに引く")
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
                    candidate_id=args.candidate_id,
                    opp_offset=args.opp_offset, selection_plan=args.selection_plan,
                    opp_pilot=args.opp_pilot, opp_pick_policy=args.opp_pick_policy,
                    rl_blend=args.rl_blend, battle_format=args.battle_format, own_pool=args.own_pool))


if __name__ == "__main__":
    main()
