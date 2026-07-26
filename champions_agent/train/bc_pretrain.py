"""探索エンジンを教師とする行動クローン (BC) 微調整。

探索エンジン (advisor/search) にベンチマーク級の相手と対戦させて
(観測, 合法手マスク, 選択行動) を収集し、既存チェックポイントの方策を
教師の選択に近づける教師あり微調整を行う。新規学習ではなく既存の
チェックポイントへの上書き微調整のため、RLの継続学習 (--resume) と両立する。

教師の強度 (tools/check_search_expert で確認):
- 2026-07-24: 素の2手読み = ベンチ0.41 (方策未満、BC見送り)
- 2026-07-26: RL価値関数の葉評価ブレンド = ベンチ0.64 (方策超え、BC解禁)
チェックポイントが無い/ネット幅が違う場合は TRAIN_NET_WIDTH の新規ネットを
作ってBC初期化する (容量拡大時のゼロからの自己対戦をスキップする用途)。

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


class _DummyBattleEnv:
    """BC専用のスペース定義env (Showdown接続なしで新規モデルを作るため)"""

    def __new__(cls):
        import gymnasium as gym
        import numpy as np
        from champions_agent.agent.spaces import BATTLE_OBS_DIM

        class _E(gym.Env):
            observation_space = gym.spaces.Box(
                low=-np.inf, high=np.inf, shape=(BATTLE_OBS_DIM,),
                dtype=np.float32)
            action_space = gym.spaces.Discrete(26)

            def reset(self, *a, **k):
                import numpy as _np
                return _np.zeros(BATTLE_OBS_DIM, dtype=_np.float32), {}

            def step(self, action):
                import numpy as _np
                return (_np.zeros(BATTLE_OBS_DIM, dtype=_np.float32),
                        0.0, True, False, {})

        return _E()


def _load_or_create(style: str):
    """チェックポイントをロードする。無い/ネット幅が違う場合は
    TRAIN_NET_WIDTH (既定512) の新規モデルを作る (容量拡大の初期化に使う)"""
    import os
    from sb3_contrib import MaskablePPO

    width = int(os.environ.get("TRAIN_NET_WIDTH", "512"))
    ckpt = MODELS_DIR / f"battle_policy_{style}.zip"
    if ckpt.exists():
        model = MaskablePPO.load(str(ckpt), device="cpu")
        try:
            actual = model.policy.mlp_extractor.policy_net[0].out_features
        except Exception:
            actual = None
        if actual == width:
            print(f"[bc_pretrain] 既存チェックポイントへ微調整: {ckpt.name} "
                  f"(net={actual})", flush=True)
            return model
        print(f"[bc_pretrain] ネット幅不一致 (既存{actual}≠希望{width}) → "
              f"新規{width}x{width}モデルをBC初期化します", flush=True)
    else:
        print(f"[bc_pretrain] チェックポイントなし → 新規{width}x{width}"
              "モデルをBC初期化します", flush=True)
    return MaskablePPO("MlpPolicy", _DummyBattleEnv(), device="cpu",
                       policy_kwargs={"net_arch": [width, width]})


def finetune(style: str, data: dict, epochs: int, lr: float,
             batch_size: int = 256, dry_run: bool = False) -> None:
    """チェックポイント (無ければ新規ネット) を教師データでBC学習して保存する"""
    import torch

    ckpt = MODELS_DIR / f"battle_policy_{style}.zip"
    model = _load_or_create(style)
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
    if ckpt.exists():
        ckpt_prev = ckpt.with_suffix(".zip.prev_bc")
        ckpt_prev.write_bytes(ckpt.read_bytes())
    model.save(str(ckpt))
    print(f"[bc_pretrain] 保存: {ckpt.name}", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="探索エンジンの行動クローン微調整")
    ap.add_argument("--style", default="balance")
    ap.add_argument("--styles", default=None,
                    help="複数性格を同じ教師データでBCする (カンマ区切り)")
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

    styles = [s.strip() for s in (args.styles or args.style).split(",")
              if s.strip()]
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    data_path = LOG_DIR / f"bc_dataset_{styles[0]}.npz"
    if args.reuse_data and data_path.exists():
        z = np.load(data_path)
        data = {k: z[k] for k in ("obs", "mask", "act")}
        print(f"[bc_pretrain] 保存済みデータを使用: {data_path.name} "
              f"({len(data['act'])}サンプル)", flush=True)
    else:
        t0 = time.time()
        data = asyncio.run(collect(styles[0], args.battles, args.depth))
        if not args.dry_run:   # 疎通確認の小データで保存済みを潰さない
            np.savez_compressed(data_path, obs=data["obs"], mask=data["mask"],
                                act=data["act"])
        print(f"[bc_pretrain] 収集完了: {len(data['act'])}サンプル / "
              f"{args.battles}戦 (エキスパート勝率"
              f"{data['expert_win_rate']:.2f}) {time.time() - t0:.0f}s",
              flush=True)
    if len(data["act"]) < 100 and not args.dry_run:
        raise SystemExit("教師データが少なすぎます (収集失敗の可能性)")
    for style in styles:
        finetune(style, data, args.epochs, args.lr, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
