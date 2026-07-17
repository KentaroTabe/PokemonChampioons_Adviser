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

# --- 観測空間の次元(暫定値。encoders.py の実装に合わせて調整する) ---
# 1体分のポケモン特徴量: 種族値6 + タイプone-hot(自分18*2) + HP割合1 + 状態異常one-hot(6)
#                        + 技4つ分の特徴(威力/命中/優先度/タイプone-hot 簡略化) ...
# プロトタイプとして「ざっくり固定長」にし、後で精緻化する。
POKEMON_FEATURE_DIM = 64          # 1体あたりの特徴量次元(自分側・詳細情報あり)
OPPONENT_POKEMON_FEATURE_DIM = 48  # 相手側(情報が一部欠落するため次元を減らす、不明分はメタ事前分布で埋める)
FIELD_FEATURE_DIM = 16             # 天候/場の状態などの特徴量次元

BATTLE_OBS_DIM = (
    POKEMON_FEATURE_DIM * 3       # 自分の場に出せる3体(選出済み)
    + OPPONENT_POKEMON_FEATURE_DIM * 3
    + FIELD_FEATURE_DIM
)

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
