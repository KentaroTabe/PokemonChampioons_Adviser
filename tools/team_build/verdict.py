"""対応差の統計判定 (純粋関数)。

候補 A と参照 B を同一相手 × 同一シードで戦わせ、対応差 Δ = W_A − W_B の信頼区間を
実用差 ε の帯と比べて 4 状態に分類する (docs/TEAM_BUILDING_IMPLEMENTATION.md §9-3)。

  improved   : CI 全体 > +ε
  degraded   : CI 全体 < −ε
  equivalent : CI ⊂ [−ε, +ε] (統計的に有意でも実用上同等。これ以上戦数を増やさない)
  uncertain  : それ以外 → 追加測定 (必要な精度に達しなければ uncertain のまま終了できる)
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Optional, Sequence

from champions_agent.config import (
    BUILD_CI_Z, BUILD_EQUIV_EPS, BUILD_RACE_STEPS, BUILD_REAL_MAX_CI_HALFWIDTH,
    BUILD_REAL_MIN_EFFECTIVE_N)

IMPROVED, EQUIVALENT, DEGRADED, UNCERTAIN = "improved", "equivalent", "degraded", "uncertain"


@dataclass(frozen=True)
class PairedResult:
    n: int
    mean: Optional[float]
    se: Optional[float]
    ci_low: Optional[float]
    ci_high: Optional[float]
    state: str
    eps: float

    def to_dict(self) -> dict:
        return asdict(self)


def paired_diff(a_outcomes: Sequence[int], b_outcomes: Sequence[int]) -> tuple:
    """対応のある勝敗列 (1/0) から (n, 平均差, 標準誤差)。n < 2 なら (n, None, None)"""
    n = min(len(a_outcomes or []), len(b_outcomes or []))
    if n < 2:
        return n, None, None
    d = [float(a_outcomes[i]) - float(b_outcomes[i]) for i in range(n)]
    mean = sum(d) / n
    var = sum((x - mean) ** 2 for x in d) / (n - 1)
    return n, mean, math.sqrt(var / n)


def classify(ci_low: float, ci_high: float, eps: float = BUILD_EQUIV_EPS) -> str:
    if ci_low > eps:
        return IMPROVED
    if ci_high < -eps:
        return DEGRADED
    if -eps <= ci_low and ci_high <= eps:
        return EQUIVALENT
    return UNCERTAIN


def verdict4(a_outcomes: Sequence[int], b_outcomes: Sequence[int],
             eps: float = BUILD_EQUIV_EPS, z: float = BUILD_CI_Z) -> PairedResult:
    n, mean, se = paired_diff(a_outcomes, b_outcomes)
    if mean is None:
        return PairedResult(n, None, None, None, None, UNCERTAIN, eps)
    lo, hi = mean - z * se, mean + z * se
    return PairedResult(n, mean, se, lo, hi, classify(lo, hi, eps), eps)


def next_step(n_done: int, steps: Sequence[int] = BUILD_RACE_STEPS,
              cap: Optional[int] = None) -> Optional[int]:
    """次の目標戦数 (n_done より大きい最初の段階)。cap を超える段階は返さない"""
    for s in steps:
        if s > n_done and (cap is None or s <= cap):
            return s
    return None


def still_contender(vs_best: PairedResult) -> bool:
    """best に対して degraded でなければ「best である可能性がまだ残る」候補として残す"""
    return vs_best.state != DEGRADED


def binomial_halfwidth(p: float, n: int, z: float = BUILD_CI_Z) -> float:
    """勝率 p の二項近似 95% CI 半幅 (報告用)"""
    if n <= 0:
        return float("inf")
    p = min(max(p, 0.0), 1.0)
    return z * math.sqrt(p * (1 - p) / n)


def real_weight(n_effective: float, ci_halfwidth: float,
                min_n: float = BUILD_REAL_MIN_EFFECTIVE_N,
                max_halfwidth: float = BUILD_REAL_MAX_CI_HALFWIDTH) -> float:
    """実戦評価の重み w_N ∈ [0, 1]。有効標本が min_n 未満、または CI 半幅が閾値より広いほど小さい"""
    if n_effective <= 0 or min_n <= 0:
        return 0.0
    w_n = min(1.0, n_effective / min_n)
    if ci_halfwidth <= max_halfwidth or ci_halfwidth == 0:
        w_ci = 1.0
    else:
        w_ci = max_halfwidth / ci_halfwidth
    return max(0.0, min(1.0, w_n * w_ci))


def blended_score(wr_real: Optional[float], wr_synthetic: float, w: float) -> float:
    if wr_real is None:
        return wr_synthetic
    return w * wr_real + (1.0 - w) * wr_synthetic
