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

_OBS_DIM_V1 = _OWN_ACTIVE_DIM + _OPP_ACTIVE_DIM + _BENCH_DIM + _FIELD_SIDE_DIM  # = 227

# --- v2拡張観測 (頭打ち対策の観測拡充。v1の227次元プレフィックスは不変で
#     末尾に追記する: 旧チェックポイントは観測スライスで引き続き動く) ---
# 相手の判明技4スロット (自分を防御側とした技特徴) 4x9 = 36
# 揮発状態 (混乱/やどりぎ/身代わり/ちょうはつ/アンコール/ねむけ) 自分6+相手6 = 12
# メガ進化 (自分可能/自分側使用済み/相手側使用済み) = 3
# 持ち物カテゴリ (スカーフ/ハチマキ/メガネ/珠/残飯/その他判明) 自分6+相手6 = 12
# 自分の残数1 + 相手判明技の最大優先度1 = 2
# 控えの戦術情報: 自分控え2x(打点相性+被STAB相性) + 相手控え2x(打点相性) = 6
N_VOLATILE_SLOTS = 6
N_ITEM_CATS = 6
_EXTRA_DIM_V2 = N_MOVE_SLOTS * MOVE_FEAT_DIM + 12 + 3 + 12 + 2 + 6  # = 71

# --- v3拡張: 技の付随効果 + 脅威プロファイル + 天候残り + 控え同士 ---
# 技効果8次元 = ステータス別の符号付き自己ブースト (A/B/C/D/S) 5
#             + 相手ランク低下 + 状態異常付与率 + 回復率
#   自分の技4 + 相手の判明技4 に付与 = 64
#   (りゅうのまい[A+S]とてっぺき[B]を別物として観測する。合計スカラーでは
#    「B上げは相手が特殊型だと活きない」という文脈依存が学習できない)
# 攻撃プロファイル4 = 相手の物理/特殊脅威シェア + 自分の物理/特殊シェア
# ブースト効用4 = 自分の各技のランク技効用 (ステータス別ブースト x 文脈重み。
#   例: B上げの効用は相手の物理脅威シェアで重み付け -> 相手が特殊型なら0)
# 天候/フィールドの残りターン (概算/8) = 2
# 自分控え2 x 相手控え2 の打点相性 (突破後の詰め筋評価) = 4
MOVE_EFFECT_DIM = 8
BOOST_STAT_KEYS = ("atk", "def", "spa", "spd", "spe")
_EXTRA_DIM_V3 = 2 * N_MOVE_SLOTS * MOVE_EFFECT_DIM + 4 + N_MOVE_SLOTS + 2 + 4  # = 78

BATTLE_OBS_DIM = _OBS_DIM_V1 + _EXTRA_DIM_V2 + _EXTRA_DIM_V3  # = 376

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
