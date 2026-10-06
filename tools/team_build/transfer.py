"""レギュレーション遷移の転移量制御 (λ_old): 新旧プールの種族重なりから距離を出し、
一般モデルの事前重み・環境混合の比率を弱める。勝率は CI つきで報告する (§9-11、レビュー3 §10)。純粋関数。"""
from __future__ import annotations

import math
from typing import Iterable

# 距離 d ∈ [0,1] (1 − 種族集合の Jaccard) に対する λ_old = exp(−d / D_SCALE)。D_SCALE は config 化候補
D_SCALE = 0.35


def regulation_distance(old_pool: Iterable[str], new_pool: Iterable[str]) -> float:
    a, b = set(old_pool), set(new_pool)
    if not a and not b:
        return 0.0
    return 1.0 - len(a & b) / len(a | b)


def lambda_old(distance: float, scale: float = D_SCALE) -> float:
    """旧環境の事前分布の重み ∈ (0, 1]。距離 0 で 1、距離が大きいほど指数的に小さく"""
    return round(math.exp(-max(0.0, distance) / scale), 4)


def mix_prior(old_value: float, new_value: float, n_new: int, distance: float, n_ref: int = 1000) -> float:
    """旧環境の推定 (old_value) と新環境の推定 (new_value、標本 n_new) を、λ_old と標本量で混ぜる"""
    lam = lambda_old(distance)
    w_new = min(1.0, n_new / max(1, n_ref))
    w_old = lam * (1.0 - w_new)
    tot = w_old + w_new
    return round((w_old * old_value + w_new * new_value) / tot, 4) if tot > 0 else new_value
