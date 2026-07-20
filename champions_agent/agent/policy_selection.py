"""
選出フェーズ(自分側パーティ6体から3体を選び、その順序を決める)の方策。

- 観測: 自分6体(完全情報) + 相手6体(種族のみ既知、型はDBのmeta_setsによる事前分布)
- 行動: 6P3 = 120通りの「3体+順序」の組み合わせ(spaces.SELECTION_PERMUTATIONS)

現時点ではまだ自己対戦データが十分でないため、
1. ルールベース(スコアリング)によるベースライン実装
2. 学習済みモデルがあればそちらを優先して使う推論関数
の2段構えにしてある。学習(train/train_selection.py)が整うまではベースラインで動作する。
"""
from __future__ import annotations

import numpy as np
import torch

from champions_agent.agent.encoders import encode_own_pokemon, encode_opponent_pokemon
from champions_agent.agent.model import SelectionPolicyNet
from champions_agent.agent.spaces import (
    SELECTION_PERMUTATIONS, N_SELECTION_ACTIONS, SELECTION_OBS_DIM,
)
from champions_agent.config import MODELS_DIR


def build_selection_observation(own_party: list[dict], opponent_party: list[dict]) -> np.ndarray:
    """選出フェーズの観測ベクトルを構築する。

    own_party: [{"species": str, "hp_percent": float, "status": str}, ...] (6件)
    opponent_party: [{"species": str|None}, ...] (6件、型は不明で良い)
    """
    own_vecs = [
        encode_own_pokemon(p["species"], p.get("hp_percent", 1.0), p.get("status", "none"))
        for p in own_party
    ]
    opp_vecs = [
        encode_opponent_pokemon(p.get("species"))
        for p in opponent_party
    ]
    return np.concatenate(own_vecs + opp_vecs).astype(np.float32)


def _score_pokemon_heuristic(p: dict) -> float:
    """ルールベースのスコアリング: 種族値合計 + HP割合を単純加算(プロトタイプ)。"""
    from champions_agent.agent.encoders import get_pokemon_static
    static = get_pokemon_static(p["species"]) or {}
    base_stat_total = sum(
        static.get(k) or 0
        for k in ("hp", "attack", "defense", "sp_attack", "sp_defense", "speed")
    )
    hp_percent = p.get("hp_percent", 1.0)
    return base_stat_total * hp_percent


def select_heuristic(own_party: list[dict]) -> tuple[int, int, int]:
    """ルールベースで、種族値合計が高い順に3体を選び、その順序で出す(プロトタイプ)。"""
    indexed = list(enumerate(own_party))
    indexed.sort(key=lambda x: _score_pokemon_heuristic(x[1]), reverse=True)
    chosen = tuple(i for i, _ in indexed[:3])
    return chosen  # type: ignore[return-value]


def _load_model_if_available() -> SelectionPolicyNet | None:
    path = MODELS_DIR / "selection_policy.pt"
    if not path.exists():
        return None
    model = SelectionPolicyNet(SELECTION_OBS_DIM, N_SELECTION_ACTIONS)
    model.load_state_dict(torch.load(str(path), map_location="cpu"))
    model.eval()
    return model


def select_team(own_party: list[dict], opponent_party: list[dict]) -> tuple[int, int, int]:
    """3体+順序を決定するエントリポイント。学習済みモデルがあればそれを使い、
    無ければヒューリスティックにフォールバックする。

    戻り値: own_party内のインデックス3つ(出す順)
    """
    model = _load_model_if_available()
    if model is None:
        return select_heuristic(own_party)

    obs = build_selection_observation(own_party, opponent_party)
    with torch.no_grad():
        logits = model(torch.from_numpy(obs).unsqueeze(0))
        action_idx = int(torch.argmax(logits, dim=-1).item())
    return SELECTION_PERMUTATIONS[action_idx]
