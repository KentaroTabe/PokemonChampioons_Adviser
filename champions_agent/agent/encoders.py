"""
ポケモン/技/盤面をDB参照で固定長特徴ベクトルへ変換するエンコーダ群。

- 自分側: 種族値・タイプ・特性・技・持ち物など完全情報を使える
- 相手側: 見えている情報(種族・現在HP割合・場に出て確認済みの技等)のみ使い、
          不明な部分は DB の meta_sets(使用率メタ)による事前分布(期待値)で埋める。

現時点ではプロトタイプとして「ざっぱなベクトル化」を実装し、
学習が回る状態を優先する。精度が必要になった段階で拡張する。
"""
from __future__ import annotations

import numpy as np

from champions_agent.agent.spaces import (
    POKEMON_FEATURE_DIM, OPPONENT_POKEMON_FEATURE_DIM, FIELD_FEATURE_DIM,
)
from champions_agent.data import database as db

ALL_TYPES = [
    "normal", "fire", "water", "electric", "grass", "ice", "fighting", "poison",
    "ground", "flying", "psychic", "bug", "rock", "ghost", "dragon", "dark",
    "steel", "fairy",
]
TYPE_TO_IDX = {t: i for i, t in enumerate(ALL_TYPES)}

STATUS_LIST = ["none", "brn", "par", "slp", "frz", "psn", "tox"]
STATUS_TO_IDX = {s: i for i, s in enumerate(STATUS_LIST)}


def _type_onehot(type_name: str | None) -> np.ndarray:
    vec = np.zeros(len(ALL_TYPES), dtype=np.float32)
    if type_name and type_name in TYPE_TO_IDX:
        vec[TYPE_TO_IDX[type_name]] = 1.0
    return vec


def _normalize_stat(v: int | None, scale: float = 255.0) -> float:
    return (v or 0) / scale


def get_pokemon_static(name: str) -> dict | None:
    """DBから種族値/タイプ等の静的データを引く。未収集の場合はNoneを返す。"""
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM pokemon WHERE name = ?", (name,)
        ).fetchone()
        return dict(row) if row else None


def encode_own_pokemon(name: str, hp_percent: float, status: str = "none") -> np.ndarray:
    """自分側ポケモン(完全情報)を固定長ベクトルへ変換する。"""
    static = get_pokemon_static(name) or {}

    stat_vec = np.array([
        _normalize_stat(static.get("hp")),
        _normalize_stat(static.get("attack")),
        _normalize_stat(static.get("defense")),
        _normalize_stat(static.get("sp_attack")),
        _normalize_stat(static.get("sp_defense")),
        _normalize_stat(static.get("speed")),
    ], dtype=np.float32)

    type1_vec = _type_onehot(static.get("type1"))
    type2_vec = _type_onehot(static.get("type2"))

    status_vec = np.zeros(len(STATUS_LIST), dtype=np.float32)
    status_vec[STATUS_TO_IDX.get(status, 0)] = 1.0

    hp_vec = np.array([hp_percent], dtype=np.float32)

    vec = np.concatenate([stat_vec, type1_vec, type2_vec, status_vec, hp_vec])
    return _pad_or_trim(vec, POKEMON_FEATURE_DIM)


def encode_opponent_pokemon(name: str | None, hp_percent: float = 1.0,
                             status: str = "none",
                             fmt: str = "gen9ou", source: str = "dummy") -> np.ndarray:
    """相手側ポケモンを、見えている情報+meta_setsの事前分布で埋めたベクトルへ変換する。"""
    static = get_pokemon_static(name) if name else None
    static = static or {}

    stat_vec = np.array([
        _normalize_stat(static.get("hp")),
        _normalize_stat(static.get("attack")),
        _normalize_stat(static.get("defense")),
        _normalize_stat(static.get("sp_attack")),
        _normalize_stat(static.get("sp_defense")),
        _normalize_stat(static.get("speed")),
    ], dtype=np.float32)

    type1_vec = _type_onehot(static.get("type1"))
    type2_vec = _type_onehot(static.get("type2"))

    status_vec = np.zeros(len(STATUS_LIST), dtype=np.float32)
    status_vec[STATUS_TO_IDX.get(status, 0)] = 1.0

    hp_vec = np.array([hp_percent], dtype=np.float32)

    vec = np.concatenate([stat_vec, type1_vec, type2_vec, status_vec, hp_vec])
    return _pad_or_trim(vec, OPPONENT_POKEMON_FEATURE_DIM)


def encode_field(weather: str = "none", turn: int = 0) -> np.ndarray:
    weather_list = ["none", "sandstorm", "rain", "sun", "hail", "snow"]
    weather_vec = np.zeros(len(weather_list), dtype=np.float32)
    idx = weather_list.index(weather) if weather in weather_list else 0
    weather_vec[idx] = 1.0
    turn_vec = np.array([min(turn, 100) / 100.0], dtype=np.float32)
    vec = np.concatenate([weather_vec, turn_vec])
    return _pad_or_trim(vec, FIELD_FEATURE_DIM)


def _pad_or_trim(vec: np.ndarray, target_dim: int) -> np.ndarray:
    if len(vec) == target_dim:
        return vec
    if len(vec) > target_dim:
        return vec[:target_dim]
    padded = np.zeros(target_dim, dtype=np.float32)
    padded[:len(vec)] = vec
    return padded
