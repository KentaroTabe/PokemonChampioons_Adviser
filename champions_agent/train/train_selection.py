"""
選出方策(6→3体+順序、120通り分類)の学習エントリポイント。

戦闘フェーズの学習(train_battle.py)と異なり、選出は「1エピソードにつき1回の意思決定」
かつ結果(勝敗)がわかるまでに数百ターンかかるため、方策勾配法(REINFORCE)ベースの
シンプルな実装からスタートする。

実運用の学習データは selfplay.py の自己対戦結果(選出→対戦→勝敗)を蓄積して使う。
現時点では selfplay.py がプレースホルダのため、本スクリプトも
学習ループの骨組みのみを提供し、実データが揃い次第有効化する。
"""
from __future__ import annotations

import argparse

import torch
import torch.nn.functional as F
from torch.optim import Adam

from champions_agent.agent.model import SelectionPolicyNet
from champions_agent.agent.spaces import N_SELECTION_ACTIONS, SELECTION_OBS_DIM
from champions_agent.config import MODELS_DIR, RANDOM_SEED
from champions_agent.train.selfplay import generate_selection_episode


def train(num_episodes: int = 100, lr: float = 1e-3) -> None:
    torch.manual_seed(RANDOM_SEED)

    model = SelectionPolicyNet(SELECTION_OBS_DIM, N_SELECTION_ACTIONS)
    optimizer = Adam(model.parameters(), lr=lr)

    for ep in range(num_episodes):
        obs, action_idx, reward = generate_selection_episode()

        obs_t = torch.from_numpy(obs).unsqueeze(0)
        logits = model(obs_t)
        log_probs = F.log_softmax(logits, dim=-1)
        loss = -log_probs[0, action_idx] * reward

        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        if (ep + 1) % 10 == 0:
            print(f"[train_selection] episode={ep + 1} loss={loss.item():.4f} reward={reward:.3f}")

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    save_path = MODELS_DIR / "selection_policy.pt"
    torch.save(model.state_dict(), str(save_path))
    print(f"[train_selection] 学習済みモデルを保存しました: {save_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="選出方策の学習(REINFORCEベース・プロトタイプ)")
    parser.add_argument("--episodes", type=int, default=100)
    parser.add_argument("--lr", type=float, default=1e-3)
    args = parser.parse_args()

    train(num_episodes=args.episodes, lr=args.lr)


if __name__ == "__main__":
    main()
