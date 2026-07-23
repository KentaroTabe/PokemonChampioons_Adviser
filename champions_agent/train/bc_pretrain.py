"""探索エンジンを教師とする行動クローン (BC) 微調整。

探索エンジン (advisor/search) にベンチマーク級の相手と対戦させて
(観測, 合法手マスク, 選択行動) を収集し、既存チェックポイントの方策を
教師の選択に近づける教師あり微調整を行う。新規学習ではなく既存の
チェックポイントへの上書き微調整のため、RLの継続学習 (--resume) と両立する。

⚠ 実行前に教師の強度を確認すること (tools/check_search_expert)。
2026-07-24時点の実測では教師のベンチ勝率0.41で学習済み方策 (0.46-0.48) を
上回っておらず、BCは方策を弱める可能性が高い。エキスパートが方策を
明確に上回ってから実行する (それまでは --dry-run で疎通のみ)。

前提: ローカルShowdown (ポート8100) が起動していること。

使い方:
    python -m champions_agent.train.bc_pretrain --style balance --battles 200
    python -m champions_agent.train.bc_pretrain --style balance --epochs 3 --lr 1e-4

収集した教師データは champions_agent/train/logs/bc_dataset_<style>.npz に
保存され、--reuse-data で再利用できる (収集をスキップして微調整のみ)。
"""
from __future__ import annotations

import argparse
import asyncio
import time
from pathlib import Path

import numpy as np

from champions_agent.config import MODELS_DIR, TRAINING_BATTLE_FORMAT

LOG_DIR = Path(__file__).resolve().parent / "logs"


async def collect(style: str, n_battles: int, depth: int) -> dict:
    """探索エキスパート vs ベンチマークで教師データを収集する"""
    from poke_env.player import Player
    from champions_agent.agent import encoders
    from champions_agent.env.ranked_teams import RankedTeambuilder
    from champions_agent.env.search_expert import decide, teampreview_order
    from champions_agent.env.showdown_env import (
        TrainingServerConfiguration, compute_action_mask,
        make_benchmark_player,
    )

    obs_list, mask_list, act_list = [], [], []

    class _RecordingExpert(Player):
        def choose_move(self, battle):
            try:
                d = decide(battle, depth=depth)
            except Exception:
                d = None
            if d is None:
                return self.choose_random_move(battle)
            try:
                obs = encoders.encode_battle(battle)
                mask = compute_action_mask(battle)
                if mask[d["action_index"]]:
                    obs_list.append(obs)
                    mask_list.append(mask)
                    act_list.append(d["action_index"])
            except Exception:
                pass
            if d["kind"] == "move":
                return self.create_order(d["move"], mega=d["mega"])
            return self.create_order(d["pokemon"])

        def teampreview(self, battle):
            try:
                return teampreview_order(battle)
            except Exception:
                return self.random_teampreview(battle)

    # 学習ループが同じShowdownを使用中でも衝突しないよう一意なアカウント名
    import os
    from poke_env import AccountConfiguration
    uid = os.getpid() % 100000
    expert = _RecordingExpert(
        account_configuration=AccountConfiguration(f"BCex{uid}", None),
        battle_format=TRAINING_BATTLE_FORMAT,
        server_configuration=TrainingServerConfiguration,
        team=RankedTeambuilder(),
    )
    opponent = make_benchmark_player(
        battle_format=TRAINING_BATTLE_FORMAT,
        account_configuration=AccountConfiguration(f"BCop{uid}", None))
    await expert.battle_against(opponent, n_battles=n_battles)
    return {
        "obs": np.asarray(obs_list, dtype=np.float32),
        "mask": np.asarray(mask_list, dtype=bool),
        "act": np.asarray(act_list, dtype=np.int64),
        "expert_win_rate": expert.n_won_battles / max(1, n_battles),
    }


def finetune(style: str, data: dict, epochs: int, lr: float,
             batch_size: int = 256, dry_run: bool = False) -> None:
    """既存チェックポイントを教師データで微調整して上書き保存する"""
    import torch
    from sb3_contrib import MaskablePPO

    ckpt = MODELS_DIR / f"battle_policy_{style}.zip"
    if not ckpt.exists():
        raise SystemExit(f"チェックポイントがありません: {ckpt}")
    model = MaskablePPO.load(str(ckpt), device="cpu")
    policy = model.policy
    optim = torch.optim.Adam(policy.parameters(), lr=lr)

    obs = torch.as_tensor(data["obs"])
    mask = torch.as_tensor(data["mask"])
    act = torch.as_tensor(data["act"])
    n = len(act)
    for ep in range(epochs):
        perm = torch.randperm(n)
        losses = []
        for i in range(0, n, batch_size):
            idx = perm[i:i + batch_size]
            _, log_prob, _ = policy.evaluate_actions(
                obs[idx], act[idx], action_masks=mask[idx])
            loss = -log_prob.mean()
            optim.zero_grad()
            loss.backward()
            optim.step()
            losses.append(float(loss.detach()))
        print(f"[bc_pretrain] epoch {ep + 1}/{epochs} "
              f"loss={sum(losses) / len(losses):.3f}", flush=True)

    if dry_run:
        print("[bc_pretrain] dry-run のため保存しません", flush=True)
        return
    ckpt_prev = ckpt.with_suffix(".zip.prev_bc")
    ckpt_prev.write_bytes(ckpt.read_bytes())
    model.save(str(ckpt))
    print(f"[bc_pretrain] 保存: {ckpt.name} (BC前は {ckpt_prev.name})",
          flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="探索エンジンの行動クローン微調整")
    ap.add_argument("--style", default="balance")
    ap.add_argument("--battles", type=int, default=200,
                    help="教師データ収集の対戦数")
    ap.add_argument("--depth", type=int, default=2,
                    help="探索の読み深さ (2=1手56ms程度でオフライン収集なら十分速い)")
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--reuse-data", action="store_true",
                    help="保存済み教師データで微調整のみ行う")
    ap.add_argument("--dry-run", action="store_true",
                    help="チェックポイントを保存しない (疎通確認用)")
    args = ap.parse_args()

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    data_path = LOG_DIR / f"bc_dataset_{args.style}.npz"
    if args.reuse_data and data_path.exists():
        z = np.load(data_path)
        data = {k: z[k] for k in ("obs", "mask", "act")}
        print(f"[bc_pretrain] 保存済みデータを使用: {data_path.name} "
              f"({len(data['act'])}サンプル)", flush=True)
    else:
        t0 = time.time()
        data = asyncio.run(collect(args.style, args.battles, args.depth))
        if not args.dry_run:   # 疎通確認の小データで保存済みを潰さない
            np.savez_compressed(data_path, obs=data["obs"], mask=data["mask"],
                                act=data["act"])
        print(f"[bc_pretrain] 収集完了: {len(data['act'])}サンプル / "
              f"{args.battles}戦 (エキスパート勝率"
              f"{data['expert_win_rate']:.2f}) {time.time() - t0:.0f}s",
              flush=True)
    if len(data["act"]) < 100 and not args.dry_run:
        raise SystemExit("教師データが少なすぎます (収集失敗の可能性)")
    finetune(args.style, data, args.epochs, args.lr, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
