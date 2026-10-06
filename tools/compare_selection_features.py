"""選出モデルの特徴量 (v1 / v3) を同じ分割・同じ手順で学習して比べる (配布モデル・ピンには触れない)。

    python -m tools.compare_selection_features --epochs 200 --seeds 3

門 (v2 の比較で事前登録したもの): (1) 未知チーム検証の MSE の「平均予測からの改善率」、
(2) 対応のある比較 (同じ相手・同じチームでの勝ち/負けの選出の組) の順位付け精度。
v3 = メガシンカ (自分は石の有無、相手は石の事前分布) 込み。ダイマックス等が増えても同じ枠で足す。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from champions_agent.config import RANDOM_SEED

OUT = Path(__file__).resolve().parent.parent / "champions_agent" / "train" / "logs" / "compare_selection_features.json"


def _train_eval(X: np.ndarray, y: np.ndarray, meta: dict, make_net, epochs: int, holdout: float, seed: int,
                pair_weight: float = 0.5) -> dict:
    import torch
    from torch.optim import Adam
    from champions_agent.train.train_selection import build_pairs
    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    keys = meta["team_key"]
    uniq = sorted(set(keys))
    rng.shuffle(uniq)
    held = set(uniq[:max(1, int(len(uniq) * holdout))])
    val_idx = np.array([i for i, k in enumerate(keys) if k in held])
    tr_idx = np.array([i for i, k in enumerate(keys) if k not in held])
    Xtr, ytr = torch.from_numpy(X[tr_idx]), torch.from_numpy(y[tr_idx]).unsqueeze(1)
    Xva, yva = torch.from_numpy(X[val_idx]), torch.from_numpy(y[val_idx]).unsqueeze(1)
    net = make_net()
    opt = Adam(net.parameters(), lr=1e-3, weight_decay=1e-3)
    mse = torch.nn.MSELoss()

    def loss_fn(pred, target):
        return mse(torch.sigmoid(pred), target)

    win_i, lose_i = build_pairs(meta["group"], y, tr_idx)
    Wtr = torch.from_numpy(X[tr_idx][win_i]) if len(win_i) else None
    Ltr = torch.from_numpy(X[tr_idx][lose_i]) if len(win_i) else None
    vwin, vlose = build_pairs(meta["group"], y, val_idx)
    best_val, best_state = float("inf"), None
    for _ep in range(epochs):
        net.train()
        perm = torch.randperm(len(Xtr))
        for s in range(0, len(Xtr), 512):
            sel = perm[s:s + 512]
            opt.zero_grad()
            loss = loss_fn(net(Xtr[sel]), ytr[sel])
            if Wtr is not None:
                p = torch.randint(0, len(Wtr), (min(512, len(Wtr)),))
                diff = (net(Wtr[p]) - net(Ltr[p])).squeeze(-1)
                loss = loss + pair_weight * torch.nn.functional.softplus(-diff).mean()
            loss.backward()
            opt.step()
        net.eval()
        with torch.no_grad():
            v = float(loss_fn(net(Xva), yva))
        if v < best_val:
            best_val, best_state = v, {k: t.clone() for k, t in net.state_dict().items()}
    net.load_state_dict(best_state)
    base = float(((yva - ytr.mean()) ** 2).mean())
    gain = (base - best_val) / base * 100 if base else 0.0
    pair_acc = None
    if len(vwin):
        with torch.no_grad():
            sw = net(torch.from_numpy(X[val_idx][vwin])).squeeze(-1)
            sl = net(torch.from_numpy(X[val_idx][vlose])).squeeze(-1)
        pair_acc = float((sw > sl).float().mean())
    return {"val_mse": best_val, "baseline_mse": base, "gain_pct": gain, "pair_acc": pair_acc,
            "n_val": int(len(val_idx)), "n_val_pairs": int(len(vwin)), "held_teams": len(held)}


def main() -> None:
    ap = argparse.ArgumentParser(description="選出モデルの特徴量 v1 / v3 の比較 (同じ分割・同じ手順)")
    ap.add_argument("--epochs", type=int, default=200)
    ap.add_argument("--holdout", type=float, default=0.2)
    ap.add_argument("--seeds", type=int, default=3)
    args = ap.parse_args()
    from champions_agent.agent.selection_features_v3 import build_features_v3, make_net_v3
    from champions_agent.agent.selection_model import build_features, make_net
    from champions_agent.train.train_selection import load_dataset
    print("[compare] データ読込 (v1 特徴量)")
    X1, y, meta = load_dataset(builder=build_features)
    print("[compare] データ読込 (v3 特徴量)")
    X3, y3, meta3 = load_dataset(builder=build_features_v3)
    assert len(y) == len(y3) and meta["n"] == meta3["n"]
    print(f"[compare] {meta['n']} 件 / チーム {meta['teams']} 種 / v1 {X1.shape[1]} 次元 / v3 {X3.shape[1]} 次元")
    results = {"v1": [], "v3": []}
    for s in range(args.seeds):
        seed = RANDOM_SEED + s
        r1 = _train_eval(X1, y, meta, make_net, args.epochs, args.holdout, seed)
        r3 = _train_eval(X3, y, meta, make_net_v3, args.epochs, args.holdout, seed)
        results["v1"].append(r1)
        results["v3"].append(r3)
        print(f"  seed {seed}: v1 改善 {r1['gain_pct']:+.1f}% 対応精度 {r1['pair_acc']} | "
              f"v3 改善 {r3['gain_pct']:+.1f}% 対応精度 {r3['pair_acc']} (検証 {r1['n_val']} 件 / 対 {r1['n_val_pairs']})")
    summary = {k: {"gain_pct_mean": float(np.mean([r["gain_pct"] for r in v])),
                   "pair_acc_mean": float(np.mean([r["pair_acc"] for r in v if r["pair_acc"] is not None])) if any(r["pair_acc"] is not None for r in v) else None}
               for k, v in results.items()}
    print("[compare] 平均:", json.dumps(summary, ensure_ascii=False))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"n": meta["n"], "teams": meta["teams"], "epochs": args.epochs, "holdout": args.holdout,
                               "results": results, "summary": summary}, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    print(f"[compare] 保存: {OUT}")


if __name__ == "__main__":
    main()
