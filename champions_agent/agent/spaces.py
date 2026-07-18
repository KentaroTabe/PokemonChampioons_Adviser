"""
戦闘フェーズにおける観測空間・行動空間の定義。

行動空間: 技4つ + 交代候補2体 = 最大6行動(合法手のみ有効。違法手はマスクする)。
観測空間: encoders.py で構築する固定長特徴ベクトルの次元をここで定義する。
"""
from __future__ import annotations

# --- 行動空間 ---
# 0-3: 自分の場のポケモンが持つ技1-4
# 4-5: ベンチのポケモン(選出済み3体のうち場に出ていない2体)への交代
N_BATTLE_ACTIONS = 6
MOVE_ACTION_OFFSET = 0
SWITCH_ACTION_OFFSET = 4

# --- 戦闘観測空間 (encoders.encode_battle が生成。内訳は下記コメント) ---
N_MOVE_SLOTS = 4
MOVE_FEAT_DIM = 9   # 威力/命中/優先度/物理/特殊/変化/STAB/相性倍率/PP残

# 自分の場のポケモン: タイプ18 + 種族値6 + ランク7 + HP1 + 状態異常7 + 技4x9 = 75
_OWN_ACTIVE_DIM = 18 + 6 + 7 + 1 + 7 + N_MOVE_SLOTS * MOVE_FEAT_DIM
# 相手の場のポケモン: タイプ18 + 種族値6 + ランク7 + HP1 + 状態異常7 + 判明技情報2 = 41
_OPP_ACTIVE_DIM = 18 + 6 + 7 + 1 + 7 + 2
# 控え: 自分2体x(タイプ18+HP+ひんし)=40 / 相手2体x(+視認フラグ)=42 + 残数1 = 43
_BENCH_DIM = 2 * 20 + 2 * 21 + 1
# 陣営の場 (設置技/壁/おいかぜ) 8x2 + 天候/フィールド/TR/ターン10 + 素早さ比較2 = 28
_FIELD_SIDE_DIM = 8 * 2 + 10 + 2

BATTLE_OBS_DIM = _OWN_ACTIVE_DIM + _OPP_ACTIVE_DIM + _BENCH_DIM + _FIELD_SIDE_DIM  # = 227

# --- 選出方策用の旧エンコーダ次元 (encoders.encode_own_pokemon 等) ---
POKEMON_FEATURE_DIM = 64
OPPONENT_POKEMON_FEATURE_DIM = 48
FIELD_FEATURE_DIM = 16

# --- 選出フェーズの行動空間 ---
# 6体から3体を選び順序を決める = 6P3 = 120通りの組み合わせを列挙し、インデックスで表現する
import itertools

SELECTION_SIZE = 3
PARTY_SIZE = 6
SELECTION_PERMUTATIONS = list(itertools.permutations(range(PARTY_SIZE), SELECTION_SIZE))
N_SELECTION_ACTIONS = len(SELECTION_PERMUTATIONS)  # 120

# 選出フェーズの観測次元: 自分6体(詳細) + 相手6体(種族のみ+メタ事前分布)
SELECTION_OBS_DIM = (
    POKEMON_FEATURE_DIM * PARTY_SIZE
    + OPPONENT_POKEMON_FEATURE_DIM * PARTY_SIZE
)
