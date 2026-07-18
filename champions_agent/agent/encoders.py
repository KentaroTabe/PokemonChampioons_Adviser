"""ポケモン/技/盤面を固定長特徴ベクトルへ変換するエンコーダ群。

主役は encode_battle(battle): poke-env の AbstractBattle から直接、
技・タイプ相性・ランク・場の状態まで含む観測ベクトルを構築する
(DBアクセスなし。学習ループのホットパスで呼ばれるため)。

旧来の encode_own_pokemon / encode_opponent_pokemon / encode_field は
選出方策 (policy_selection) 用に残置している (DB参照ベース)。
"""
from __future__ import annotations

import numpy as np

from champions_agent.agent.spaces import (
    POKEMON_FEATURE_DIM, OPPONENT_POKEMON_FEATURE_DIM, FIELD_FEATURE_DIM,
    BATTLE_OBS_DIM, N_MOVE_SLOTS, MOVE_FEAT_DIM,
)

ALL_TYPES = [
    "normal", "fire", "water", "electric", "grass", "ice", "fighting", "poison",
    "ground", "flying", "psychic", "bug", "rock", "ghost", "dragon", "dark",
    "steel", "fairy",
]
TYPE_TO_IDX = {t: i for i, t in enumerate(ALL_TYPES)}

STATUS_LIST = ["none", "brn", "par", "slp", "frz", "psn", "tox"]
STATUS_TO_IDX = {s: i for i, s in enumerate(STATUS_LIST)}

BOOST_KEYS = ["atk", "def", "spa", "spd", "spe", "accuracy", "evasion"]
BASE_STAT_KEYS = ["hp", "atk", "def", "spa", "spd", "spe"]


# ==============================================================================
# battleオブジェクトベースの新エンコーダ (学習・推論の本線)
# ==============================================================================
def _types_multihot(pokemon) -> np.ndarray:
    vec = np.zeros(len(ALL_TYPES), dtype=np.float32)
    try:
        for t in (pokemon.types or []):
            if t is None:
                continue
            name = t.name.lower()
            if name in TYPE_TO_IDX:
                vec[TYPE_TO_IDX[name]] = 1.0
    except Exception:
        pass
    return vec


def _base_stats_vec(pokemon) -> np.ndarray:
    vec = np.zeros(len(BASE_STAT_KEYS), dtype=np.float32)
    try:
        bs = pokemon.base_stats or {}
        for i, k in enumerate(BASE_STAT_KEYS):
            vec[i] = (bs.get(k) or 0) / 200.0
    except Exception:
        pass
    return vec


def _boosts_vec(pokemon) -> np.ndarray:
    vec = np.zeros(len(BOOST_KEYS), dtype=np.float32)
    try:
        boosts = pokemon.boosts or {}
        for i, k in enumerate(BOOST_KEYS):
            vec[i] = (boosts.get(k) or 0) / 6.0
    except Exception:
        pass
    return vec


def _status_vec(pokemon) -> np.ndarray:
    vec = np.zeros(len(STATUS_LIST), dtype=np.float32)
    idx = 0
    try:
        if pokemon.status is not None:
            idx = STATUS_TO_IDX.get(pokemon.status.name.lower(), 0)
    except Exception:
        pass
    vec[idx] = 1.0
    return vec


def _hp_frac(pokemon) -> float:
    try:
        return float(pokemon.current_hp_fraction or 0.0)
    except Exception:
        return 0.0


def _eff_mult(defender, move_or_type) -> float:
    """タイプ相性倍率 (0/0.25/0.5/1/2/4)。取得失敗時は1.0"""
    try:
        return float(defender.damage_multiplier(move_or_type))
    except Exception:
        return 1.0


def _encode_move(move, opponent) -> np.ndarray:
    """1技分の特徴量 (MOVE_FEAT_DIM=9)"""
    vec = np.zeros(MOVE_FEAT_DIM, dtype=np.float32)
    if move is None:
        return vec
    try:
        power = float(move.base_power or 0)
        acc = float(move.accuracy if move.accuracy is not None else 1.0)
        if acc > 1.0:
            acc /= 100.0
        cat = move.category.name.lower() if move.category else "status"
        pp_frac = 0.0
        if move.max_pp:
            pp_frac = max(0.0, float(move.current_pp or 0) / move.max_pp)
        eff = _eff_mult(opponent, move) if (opponent is not None and power > 0) else 1.0

        vec[0] = min(power, 150.0) / 150.0
        vec[1] = acc
        vec[2] = (float(move.priority or 0) + 5.0) / 10.0
        vec[3] = 1.0 if cat == "physical" else 0.0
        vec[4] = 1.0 if cat == "special" else 0.0
        vec[5] = 1.0 if cat == "status" else 0.0
        vec[7] = eff / 4.0
        vec[8] = pp_frac
    except Exception:
        return vec
    return vec


def _encode_active(pokemon, opponent, with_moves: bool) -> np.ndarray:
    """場に出ているポケモンの特徴量。

    with_moves=True: 技4スロット分の特徴 (自分側。poke-envの行動indexと同じ
    `list(pokemon.moves.values())` 順で並べる)
    """
    parts = [
        _types_multihot(pokemon),
        _base_stats_vec(pokemon),
        _boosts_vec(pokemon),
        np.array([_hp_frac(pokemon)], dtype=np.float32),
        _status_vec(pokemon),
    ]
    if with_moves:
        moves = []
        try:
            moves = list(pokemon.moves.values())
        except Exception:
            pass
        for i in range(N_MOVE_SLOTS):
            mv = moves[i] if i < len(moves) else None
            stab_vec = _encode_move(mv, opponent)
            # STABフラグ (index 6) はここで付与
            try:
                if mv is not None and mv.type is not None and pokemon.types:
                    if any(t is not None and t.name == mv.type.name for t in pokemon.types):
                        stab_vec[6] = 1.0
            except Exception:
                pass
            parts.append(stab_vec)
    return np.concatenate(parts)


def _encode_bench_slot(pokemon, with_seen_flag: bool) -> np.ndarray:
    """控え1体分: タイプ18 + HP1 + ひんし1 (+視認済み1)"""
    dim = 20 + (1 if with_seen_flag else 0)
    vec = np.zeros(dim, dtype=np.float32)
    if pokemon is None:
        return vec
    vec[:18] = _types_multihot(pokemon)
    vec[18] = _hp_frac(pokemon)
    try:
        vec[19] = 1.0 if pokemon.fainted else 0.0
    except Exception:
        pass
    if with_seen_flag:
        vec[20] = 1.0
    return vec


def _side_conditions_vec(conditions) -> np.ndarray:
    """陣営の場: [SR, まきびし層/3, どくびし層/2, ネット, リフレクター, 光の壁, ベール, おいかぜ]"""
    vec = np.zeros(8, dtype=np.float32)
    try:
        for cond, value in (conditions or {}).items():
            name = cond.name.lower()
            if name == "stealth_rock":
                vec[0] = 1.0
            elif name == "spikes":
                vec[1] = min(int(value or 1), 3) / 3.0
            elif name == "toxic_spikes":
                vec[2] = min(int(value or 1), 2) / 2.0
            elif name == "sticky_web":
                vec[3] = 1.0
            elif name == "reflect":
                vec[4] = 1.0
            elif name == "light_screen":
                vec[5] = 1.0
            elif name == "aurora_veil":
                vec[6] = 1.0
            elif name == "tailwind":
                vec[7] = 1.0
    except Exception:
        pass
    return vec


def _field_vec(battle) -> np.ndarray:
    """天候4 + フィールド4 + トリックルーム1 + ターン1"""
    vec = np.zeros(10, dtype=np.float32)
    try:
        for w in (battle.weather or {}):
            name = w.name.lower()
            if "sand" in name:
                vec[0] = 1.0
            elif "rain" in name:
                vec[1] = 1.0
            elif "sun" in name:
                vec[2] = 1.0
            elif "hail" in name or "snow" in name:
                vec[3] = 1.0
    except Exception:
        pass
    try:
        for f in (battle.fields or {}):
            name = f.name.lower()
            if "electric" in name:
                vec[4] = 1.0
            elif "grassy" in name:
                vec[5] = 1.0
            elif "psychic" in name:
                vec[6] = 1.0
            elif "misty" in name:
                vec[7] = 1.0
            elif "trick_room" in name:
                vec[8] = 1.0
    except Exception:
        pass
    try:
        vec[9] = min(int(battle.turn or 0), 50) / 50.0
    except Exception:
        pass
    return vec


def encode_battle(battle) -> np.ndarray:
    """AbstractBattle -> 固定長観測ベクトル (BATTLE_OBS_DIM)"""
    own = battle.active_pokemon
    opp = battle.opponent_active_pokemon

    # --- 自分の場のポケモン (技情報つき) ---
    if own is not None:
        own_vec = _encode_active(own, opp, with_moves=True)
    else:
        own_vec = np.zeros(18 + 6 + 7 + 1 + 7 + N_MOVE_SLOTS * MOVE_FEAT_DIM,
                           dtype=np.float32)

    # --- 相手の場のポケモン + 判明技情報 ---
    if opp is not None:
        opp_vec = _encode_active(opp, None, with_moves=False)
        revealed = []
        try:
            revealed = list(opp.moves.values())
        except Exception:
            pass
        max_eff = 0.0
        if own is not None:
            for mv in revealed:
                if (mv.base_power or 0) > 0:
                    max_eff = max(max_eff, _eff_mult(own, mv))
        opp_extra = np.array([min(len(revealed), 4) / 4.0, max_eff / 4.0],
                             dtype=np.float32)
    else:
        opp_vec = np.zeros(18 + 6 + 7 + 1 + 7, dtype=np.float32)
        opp_extra = np.zeros(2, dtype=np.float32)

    # --- 控え (自分: 最大2体 / 相手: 最大2体 + 視認フラグ + 残数) ---
    own_bench = []
    try:
        own_bench = [p for p in battle.team.values() if own is None or p is not own]
    except Exception:
        pass
    own_bench_vecs = [_encode_bench_slot(own_bench[i] if i < len(own_bench) else None,
                                          with_seen_flag=False) for i in range(2)]

    opp_bench = []
    try:
        opp_bench = [p for p in battle.opponent_team.values()
                     if opp is None or p is not opp]
    except Exception:
        pass
    opp_bench_vecs = [_encode_bench_slot(opp_bench[i] if i < len(opp_bench) else None,
                                          with_seen_flag=True) for i in range(2)]
    try:
        opp_remaining = sum(1 for p in battle.opponent_team.values() if not p.fainted)
    except Exception:
        opp_remaining = 3
    opp_count_vec = np.array([opp_remaining / 3.0], dtype=np.float32)

    # --- 場 (自陣営/敵陣営/全体) ---
    my_side = _side_conditions_vec(getattr(battle, "side_conditions", None))
    opp_side = _side_conditions_vec(getattr(battle, "opponent_side_conditions", None))
    field = _field_vec(battle)

    # --- 素早さ比較 ---
    speed_vec = np.zeros(2, dtype=np.float32)
    try:
        my_spe = (own.stats or {}).get("spe") or (own.base_stats or {}).get("spe") or 0
        opp_spe = (opp.base_stats or {}).get("spe") or 0
        if opp_spe > 0:
            ratio = my_spe / opp_spe
            speed_vec[0] = min(ratio, 2.0) / 2.0
            speed_vec[1] = 1.0 if ratio >= 1.0 else 0.0
    except Exception:
        pass

    vec = np.concatenate([own_vec, opp_vec, opp_extra,
                          *own_bench_vecs, *opp_bench_vecs, opp_count_vec,
                          my_side, opp_side, field, speed_vec]).astype(np.float32)

    # 固定長を保証
    if len(vec) < BATTLE_OBS_DIM:
        padded = np.zeros(BATTLE_OBS_DIM, dtype=np.float32)
        padded[:len(vec)] = vec
        return padded
    return vec[:BATTLE_OBS_DIM]


# ==============================================================================
# 旧エンコーダ (選出方策 policy_selection 用・DB参照ベース)
# ==============================================================================
from champions_agent.data import database as db


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
    """相手側ポケモンを、見えている情報で埋めたベクトルへ変換する。"""
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
