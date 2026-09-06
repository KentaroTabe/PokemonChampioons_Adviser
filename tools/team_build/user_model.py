"""ユーザー遵守モデル (Adherence × Deviation quality)。

docs/TEAM_BUILDING_IMPLEMENTATION.md §9-2:
- 基準遵守率は BUILD_USER_MODELS (full 1.0 / high 0.9 / mixed 0.7 / expert 0.5)
- P(follow) は定数ではなく助言の 1 位と 2 位の差 (confidence gap) で変調する:
  差が小さいほど離反しやすく、差が大きいほど従う
- 非遵守時の行動は user type で質が違う: high / mixed は human-like (2 位の手)、
  expert は strong (RL 方策の選択があればそれ、無ければ 2 位)
初期はこのパラメトリック擬似ユーザー。実ログが溜まったら離反行動から学習する。
"""
from __future__ import annotations

import random
from typing import Optional

from champions_agent.config import BUILD_USER_MODELS

# gap (相対スコア差) がこの値で P(follow) が基準と 1.0 の中間になる (config 化候補、まず定数)
GAP_HALF = 0.10
DEVIATION_QUALITY = {"full": None, "high": "human", "mixed": "human", "expert": "strong"}


def confidence_gap(advice: Optional[dict]) -> float:
    """1 位と 2 位の相対スコア差 ∈ [0, 1]。候補が 1 つなら 1.0 (迷いなし)"""
    acts = list((advice or {}).get("actions") or [])
    if len(acts) < 2:
        return 1.0
    s1 = float(acts[0].get("score") or 0.0)
    s2 = float(acts[1].get("score") or 0.0)
    denom = max(abs(s1), abs(s2), 1e-6)
    return max(0.0, min(1.0, (s1 - s2) / denom))


def follow_probability(user_type: str, gap: float) -> float:
    """P(follow) = base + (1 − base) · gap / (gap + GAP_HALF)"""
    base = BUILD_USER_MODELS.get(user_type, 1.0)
    if base >= 1.0:
        return 1.0
    g = max(0.0, gap)
    return base + (1.0 - base) * (g / (g + GAP_HALF))


def deviation_choice(user_type: str, advice: dict, rl_choice: Optional[dict] = None) -> Optional[dict]:
    """非遵守時に選ぶ行動 (advice の actions 形式)。human = 2 位、strong = RL の選択 (無ければ 2 位)"""
    acts = list((advice or {}).get("actions") or [])
    quality = DEVIATION_QUALITY.get(user_type)
    if quality is None or len(acts) < 2:
        return None
    if quality == "strong" and rl_choice:
        return rl_choice
    return acts[1]


def decide(user_type: str, advice: dict, rng: random.Random,
           rl_choice: Optional[dict] = None) -> tuple:
    """(選んだ行動, followed: bool)。full なら常に 1 位"""
    if not advice or not advice.get("actions"):
        return None, True
    p = follow_probability(user_type, confidence_gap(advice))
    if rng.random() < p:
        return advice["actions"][0], True
    alt = deviation_choice(user_type, advice, rl_choice)
    if alt is None:
        return advice["actions"][0], True
    return alt, False
