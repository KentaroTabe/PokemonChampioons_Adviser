"""行動クローン (BC) による方策の初期化・微調整。

教師をベンチマーク相手と対戦させて (観測, 合法手マスク, 選択行動) を集め、
学生ネットを教師の選択に近づける。チェックポイントが無い/ネット幅が違う
場合は TRAIN_NET_WIDTH の新規ネットを作って初期化するため、ネット拡幅時に
「ゼロからの自己対戦」をスキップできる (本ツールの主目的)。

教師の選択 (--teacher):
- policy (既定): 既存の学習済み方策 (_best) を蒸留する。拡幅時はこれが基本。
  性格ごとに教師が違うため、データも性格ごとに収集する
- search: 探索エキスパート (advisor/search)。方策より強いときのみ有効

教師の実測強度 (tools/check_search_expert / evaluate, 各100戦・50戦):
- 学習済み方策 (_best): 0.70-0.76 / 学習中 (current): 0.56-0.62
- 探索エキスパート: 0.44 (素の2手読み) / 0.34 (価値ブレンド、2026-07-27)
  → 現状 search は方策に劣るため、拡幅の初期化には policy を使うこと

前提: ローカルShowdown (ポート8100) が起動していること。

使い方:
    # ネット拡幅の初期化 (3性格を各自の_bestから蒸留)
    python -m champions_agent.train.bc_pretrain \\
        --styles balance,offense,cycle --teacher policy \\
        --battles 400 --epochs 20 --lr 3e-4
    python -m champions_agent.train.bc_pretrain --style balance --dry-run

収集データは champions_agent/train/logs/bc_dataset_<teacher>_<style>.npz に
保存され、--reuse-data で再利用できる (収集をスキップして学習のみ)。
"""
from __future__ import annotations

import argparse
import asyncio
import time
from pathlib import Path

import numpy as np

from champions_agent.config import MODELS_DIR, TRAINING_BATTLE_FORMAT

LOG_DIR = Path(__file__).resolve().parent / "logs"
_collect_seq = 0    # 収集ごとのアカウント名連番 (nametaken防止)


def _order_to_action_index(battle, order):
    """poke_envのBattleOrder -> 学習環境のアクション番号 (逆引き)。

    0-5=交代 (battle.team の並び) / 6-9=技 / 10-13=技+メガ。
    対応が取れない場合は None (記録しない)。
    """
    mon = getattr(order, "order", None)
    if mon is None:
        return None
    # 技オーダー: .id を持つ (Move)
    move_id = getattr(mon, "id", None)
    active = battle.active_pokemon
    if move_id is not None and active is not None and \
            not hasattr(mon, "species"):
        moves = [m.id for m in list((active.moves or {}).values())[:4]]
        if move_id in moves:
            base = 10 if getattr(order, "mega", False) else 6
            return base + moves.index(move_id)
        return None
    # 交代オーダー: Pokemon (speciesを持つ)
    species = getattr(mon, "species", None)
    if species is not None:
        for i, p in enumerate(list(battle.team.values())[:6]):
            if p.species == species:
                return i
    return None


async def collect(style: str, n_battles: int, depth: int,
                  teacher: str = "policy") -> dict:
    """教師 vs ベンチマークで (観測, 合法手マスク, 選択行動) を収集する。

    teacher="policy": 既存の学習済み方策 (_best) を模倣する = 方策蒸留。
      ネット幅を広げる際、現行の強さを引き継いだ初期値を作るのに使う。
    teacher="search": 探索エキスパート (advisor/search)。方策より強い場合のみ有効。
    """
    from poke_env.player import Player
    from champions_agent.agent import encoders
    from champions_agent.env.ranked_teams import RankedTeambuilder
    from champions_agent.env.search_expert import decide, teampreview_order
    from champions_agent.env.showdown_env import (
        TrainingServerConfiguration, compute_action_mask,
        make_benchmark_player,
    )

    obs_list, mask_list, act_list = [], [], []

    def _record(battle, action_index):
        """合法手なら (観測, マスク, 行動) を記録する"""
        try:
            mask = compute_action_mask(battle)
            if action_index is not None and mask[action_index]:
                obs_list.append(encoders.encode_battle(battle))
                mask_list.append(mask)
                act_list.append(int(action_index))
        except Exception:
            pass

    class _RecordingExpert(Player):
        def choose_move(self, battle):
            try:
                d = decide(battle, depth=depth)
            except Exception:
                d = None
            if d is None:
                return self.choose_random_move(battle)
            _record(battle, d["action_index"])
            if d["kind"] == "move":
                return self.create_order(d["move"], mega=d["mega"])
            return self.create_order(d["pokemon"])

        def teampreview(self, battle):
            try:
                return teampreview_order(battle)
            except Exception:
                return self.random_teampreview(battle)

    class _RecordingPolicy(Player):
        """既存方策 (_best) の選択を記録しながらプレイする (方策蒸留の教師)"""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            from champions_agent.agent.policy_battle import BattlePolicy
            self.policy = BattlePolicy(play_style=style)

        def choose_move(self, battle):
            order = self.policy.choose_order(battle)
            _record(battle, _order_to_action_index(battle, order))
            return order

        def teampreview(self, battle):
            try:
                return teampreview_order(battle)
            except Exception:
                return self.random_teampreview(battle)

    # 学習ループや前回の収集と衝突しない一意なアカウント名。
    # 同一プロセスで複数性格を順に収集するため、PIDだけでは足りず
    # 呼び出しごとの連番を混ぜる (実測: 2性格目でnametakenになった)
    import os
    from poke_env import AccountConfiguration
    global _collect_seq
    _collect_seq += 1
    uid = f"{os.getpid() % 10000}x{_collect_seq}"
    cls = _RecordingPolicy if teacher == "policy" else _RecordingExpert
    expert = cls(
        account_configuration=AccountConfiguration(f"BC{teacher[:2]}{uid}",
                                                   None),
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
    # verbose=1: 保存値が学習再開時に復元されるため、ここで0にすると
    # 以後の学習でSB3の進捗テーブルが出なくなる (ステップ数が追えない)
    return MaskablePPO("MlpPolicy", _DummyBattleEnv(), device="cpu",
                       verbose=1,
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
    ap.add_argument("--teacher", default="policy",
                    choices=["policy", "search"],
                    help="policy=既存方策(_best)の蒸留 / search=探索エキスパート")
    ap.add_argument("--reuse-data", action="store_true",
                    help="保存済み教師データで微調整のみ行う")
    ap.add_argument("--dry-run", action="store_true",
                    help="チェックポイントを保存しない (疎通確認用)")
    args = ap.parse_args()

    styles = [s.strip() for s in (args.styles or args.style).split(",")
              if s.strip()]
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    def _load_data(style: str) -> dict:
        """style用の教師データを取得する (policyは性格ごとに別データ)"""
        path = LOG_DIR / f"bc_dataset_{args.teacher}_{style}.npz"
        if args.reuse_data and path.exists():
            z = np.load(path)
            d = {k: z[k] for k in ("obs", "mask", "act")}
            print(f"[bc_pretrain] 保存済みデータを使用: {path.name} "
                  f"({len(d['act'])}サンプル)", flush=True)
            return d
        t0 = time.time()
        d = asyncio.run(collect(style, args.battles, args.depth,
                                teacher=args.teacher))
        if not args.dry_run:   # 疎通確認の小データで保存済みを潰さない
            np.savez_compressed(path, obs=d["obs"], mask=d["mask"],
                                act=d["act"])
        print(f"[bc_pretrain] 収集完了 [{style}/{args.teacher}]: "
              f"{len(d['act'])}サンプル / {args.battles}戦 "
              f"(教師の勝率{d['expert_win_rate']:.2f}) "
              f"{time.time() - t0:.0f}s", flush=True)
        return d

    if args.teacher == "search":
        # 探索エキスパートは性格に依存しないため1回の収集を共有する
        data = _load_data(styles[0])
        if len(data["act"]) < 100 and not args.dry_run:
            raise SystemExit("教師データが少なすぎます (収集失敗の可能性)")
        for style in styles:
            finetune(style, data, args.epochs, args.lr, dry_run=args.dry_run)
        return
    for style in styles:
        data = _load_data(style)
        if len(data["act"]) < 100 and not args.dry_run:
            print(f"[bc_pretrain] [{style}] 教師データ不足のためスキップ",
                  flush=True)
            continue
        finetune(style, data, args.epochs, args.lr, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
