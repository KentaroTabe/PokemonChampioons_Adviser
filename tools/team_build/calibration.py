"""較正の測定 (安全装置 6): 選出モデルの予測勝率と実際の勝敗の Brier score / 分位ごとの信頼性表。
型推定 (SpreadEstimator) の較正は、実戦ログに確率が記録されるようになってから同じ関数で測る。判定には使わない。"""
from __future__ import annotations

from typing import Sequence


def brier(probs: Sequence[float], outcomes: Sequence[int]) -> float:
    n = min(len(probs), len(outcomes))
    if n == 0:
        return float("nan")
    return round(sum((float(probs[i]) - float(outcomes[i])) ** 2 for i in range(n)) / n, 5)


def reliability_table(probs: Sequence[float], outcomes: Sequence[int], bins: int = 5) -> list:
    """予測確率の分位ごとに (平均予測, 実際の勝率, n)。80% と言った群が本当に 80% 勝つか"""
    n = min(len(probs), len(outcomes))
    rows = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i in range(n) if lo <= float(probs[i]) < hi or (b == bins - 1 and float(probs[i]) == 1.0)]
        if not idx:
            continue
        rows.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": len(idx),
                     "mean_pred": round(sum(float(probs[i]) for i in idx) / len(idx), 3),
                     "actual": round(sum(float(outcomes[i]) for i in idx) / len(idx), 3)})
    return rows


def expected_calibration_error(table: list) -> float:
    n = sum(r["n"] for r in table)
    if not n:
        return float("nan")
    return round(sum(r["n"] * abs(r["mean_pred"] - r["actual"]) for r in table) / n, 4)


def selection_model_calibration(data_npz, model_path) -> dict:
    """候補データ (npz) と選出モデルで Brier と信頼性表を出す (学習に使った分も含む参考値)"""
    import numpy as np
    import torch
    from champions_agent.agent.selection_model import load_model
    from champions_agent.train.train_selection import load_dataset
    X, y, _meta = load_dataset(data_npz)
    net = load_model(model_path)
    if net is None:
        return {"error": "model not found"}
    with torch.no_grad():
        p = torch.sigmoid(net(torch.from_numpy(X))).squeeze(-1).numpy().tolist()
    table = reliability_table(p, y.tolist())
    return {"n": int(len(y)), "brier": brier(p, y.tolist()), "reliability": table, "ece": expected_calibration_error(table)}
