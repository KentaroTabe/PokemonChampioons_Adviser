"""選出モデルの学習 (実対戦データからの勝率回帰)。

tools/collect_selection_data.py が集めた実対戦データ
(選出 → 戦闘 → 勝敗) から、「この3体をこの順で出したときの勝率」を
予測するモデルを学習する。推論時は120通りを総当たりして最良を選ぶ。

    python -m champions_agent.train.train_selection
    python -m champions_agent.train.train_selection --epochs 300 --holdout 0.2

前提: python -m tools.collect_selection_data --battles 2000 でデータを収集済み。
学習後は tools/check_selection --strategies matchup,model で実戦比較する。

⚠ 現在のデータは単一チームで集めたもの。モデルは機能埋め込みを入力に
  取るため構造上は他チームへ汎化しうるが、実際に汎化させるには
  複数チームでの収集が要る (単一チームだけだと「このチーム専用」になる)。
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from champions_agent.agent.selection_model import (
    META_PATH, MODEL_PATH, build_features, make_net,
)
from champions_agent.agent.spaces import SELECTION_PERMUTATIONS
from champions_agent.config import MODELS_DIR, RANDOM_SEED

DATA_PATH = Path(__file__).resolve().parent / "logs" / "selection_data.npz"


def load_dataset(path: Path = DATA_PATH):
    """収集データ -> (特徴量, 勝敗, メタ情報)"""
    if not path.exists():
        raise SystemExit(
            f"収集データがありません: {path}\n"
            "先に python -m tools.collect_selection_data --battles 2000 を実行")
    d = np.load(path)
    if "team" not in d.files:
        raise SystemExit("古い形式のデータです。収集し直してください")
    feats, rewards = [], []
    for i in range(len(d["action"])):
        team = [str(s) for s in d["team"][i]]
        perm = SELECTION_PERMUTATIONS[int(d["action"][i])]
        # 相手の6体は観測に含まれるが、収集時点では種族が未判明のことが多い。
        # 埋め込み側は空を許容する (相手情報なしでも自分の選出は学べる)
        feats.append(build_features(team, [], perm))
        rewards.append(float(d["reward"][i]))
    return (np.stack(feats), np.array(rewards, dtype=np.float32),
            {"n": len(rewards), "teams": len({tuple(t) for t in d["team"]})})


def train(epochs: int = 300, lr: float = 1e-3, holdout: float = 0.2) -> None:
    import torch
    from torch.optim import Adam

    torch.manual_seed(RANDOM_SEED)
    rng = np.random.default_rng(RANDOM_SEED)

    X, y, meta = load_dataset()
    print(f"[train_selection] データ {meta['n']}件 / チーム{meta['teams']}種 / "
          f"特徴{X.shape[1]}次元 / 全体勝率{y.mean():.3f}")
    if meta["teams"] == 1:
        print("  ※ 単一チームのデータのため、学習結果はそのチーム専用")

    idx = rng.permutation(len(y))
    n_val = max(1, int(len(y) * holdout))
    val_idx, tr_idx = idx[:n_val], idx[n_val:]
    Xtr = torch.from_numpy(X[tr_idx])
    ytr = torch.from_numpy(y[tr_idx]).unsqueeze(1)
    Xva = torch.from_numpy(X[val_idx])
    yva = torch.from_numpy(y[val_idx]).unsqueeze(1)

    net = make_net()
    opt = Adam(net.parameters(), lr=lr)
    loss_fn = torch.nn.MSELoss()
    best_val, best_state = float("inf"), None
    for ep in range(1, epochs + 1):
        net.train()
        opt.zero_grad()
        loss = loss_fn(net(Xtr), ytr)
        loss.backward()
        opt.step()
        net.eval()
        with torch.no_grad():
            vloss = float(loss_fn(net(Xva), yva))
        if vloss < best_val:
            best_val, best_state = vloss, {k: v.clone()
                                           for k, v in net.state_dict().items()}
        if ep % max(1, epochs // 6) == 0:
            print(f"  epoch {ep}/{epochs} train={float(loss.detach()):.4f} "
                  f"val={vloss:.4f}", flush=True)

    if best_state is not None:
        net.load_state_dict(best_state)
    # ベースライン: 常に全体平均を予測した場合のMSE (これを下回れば学習できている)
    base = float(((yva - ytr.mean()) ** 2).mean())
    print(f"[train_selection] 最良val MSE={best_val:.4f} "
          f"(平均予測のみ={base:.4f})")
    if best_val >= base:
        print("  ⚠ 平均予測を超えられていません。データ量か特徴量を見直すこと")

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    torch.save(net.state_dict(), MODEL_PATH)
    # 学習に使ったチームを記録する。未学習のチームでは予測が外挿になるため、
    # アドバイザー側で「参考値」と断って表示するのに使う
    import json
    d = np.load(DATA_PATH)
    teams = sorted({tuple(sorted(str(s).lower() for s in t))
                    for t in d["team"]})
    META_PATH.write_text(json.dumps(
        {"teams": [list(t) for t in teams], "n_samples": int(meta["n"])},
        ensure_ascii=False), encoding="utf-8")
    print(f"[train_selection] 保存: {MODEL_PATH}")

    _report_top(net, X, y)


def _report_top(net, X, y) -> None:
    """学習済みモデルが推す選出を、実データの勝率と並べて表示する"""
    import torch
    from advisor.infer import species_ja_name
    d = np.load(DATA_PATH)
    team = [str(s) for s in d["team"][0]]
    feats = np.stack([build_features(team, [], p)
                      for p in SELECTION_PERMUTATIONS])
    with torch.no_grad():
        pred = net(torch.from_numpy(feats)).squeeze(-1).numpy()
    order = np.argsort(-pred)
    print("\n■ モデルが推す選出 (上位5, ★=先発)")
    for rank in order[:5]:
        perm = SELECTION_PERMUTATIONS[rank]
        mask = d["action"] == rank
        actual = f"実測{d['reward'][mask].mean():.2f}({mask.sum()}回)" \
            if mask.sum() >= 3 else "実測データ僅少"
        names = " → ".join(species_ja_name(team[i]) or team[i] for i in perm)
        print(f"  予測{pred[rank]:.2f} / {actual}  ★{names}")


def main() -> None:
    ap = argparse.ArgumentParser(description="選出モデルの学習")
    ap.add_argument("--epochs", type=int, default=300)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--holdout", type=float, default=0.2)
    args = ap.parse_args()
    train(args.epochs, args.lr, args.holdout)


if __name__ == "__main__":
    main()
