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

# --- v4拡張: 環境使用率上位の特殊要素 (フラグ6) ---
# 確定耐え (きあいのタスキ3285/がんじょう630: 満タン+タスキ/がんじょう) 自分+相手 = 2
# いかく (特性使用率1位1323: 陣営内に持ちがいるか) 自分+相手 = 2
# いたずらごころ (4位584: アクティブが持ちか) 自分+相手 = 2
# ※あわせて次元不変の正確化: タイプ相性にふゆう/もらいび等の無効特性を反映、
#   素早さ比較にすいすい等の天候補正+こだわりスカーフを反映
_EXTRA_DIM_V4 = 6

# --- v5拡張: 連続まもるカウンタ (自分/相手, /3)。連続使用で成功率が
#     減衰するため「まもるで1ターン稼ぐ」戦術の学習に必須 ---
_EXTRA_DIM_V5 = 2

# --- v6拡張: マルチスケイル満タンフラグ (自分/相手: 被ダメ半減状態) +
#     リジェネレーター (特性5位: アクティブが持ちか、交代サイクルの誘因) ---
_EXTRA_DIM_V6 = 4

# --- v7拡張: ダメージレース + 控えの種族値 (docs/RL_V7_SET_ENCODER_DESIGN.md)
# 弱点分析 (2026-08-07: 負けの49%が中盤の交換効率由来) への対応:
# レース6 = [自HP計/3, 相手HP計/3(未視認=満タン扱い), HP差, 自残/3, 相残/3, 残数差]
# 自控え2 x (種族値6/255 + 対面素早さ比較1) = 14
# 相手控え2 x 種族値6/255 (視認済みのみ) = 12
_EXTRA_DIM_V7 = 6 + 2 * 7 + 2 * 6  # = 32

BATTLE_OBS_DIM = (_OBS_DIM_V1 + _EXTRA_DIM_V2 + _EXTRA_DIM_V3
                  + _EXTRA_DIM_V4 + _EXTRA_DIM_V5 + _EXTRA_DIM_V6
                  + _EXTRA_DIM_V7)  # = 420

# ==============================================================================
# 観測ブロックのオフセット表 (set encoder用)
# ==============================================================================
# encode_battle の連結順と完全に一致させる。手書きのスライスを散らかすと
# v8で必ず壊れるため、エンティティ分解はこの表からのみ導出する。
# 合計が BATTLE_OBS_DIM と一致することを test_rl_bridge が検証する。
OBS_PARTS = [
    # --- v1 ---
    ("own_active", _OWN_ACTIVE_DIM),
    ("opp_active", 18 + 6 + 7 + 1 + 7),
    ("opp_extra", 2),
    ("own_bench0", 20), ("own_bench1", 20),
    ("opp_bench0", 21), ("opp_bench1", 21),
    ("opp_count", 1),
    ("my_side", 8), ("opp_side", 8), ("field", 10),
    ("speed", 2),
    # --- v2 ---
    ("opp_moves", N_MOVE_SLOTS * MOVE_FEAT_DIM),
    ("own_vol", 6), ("opp_vol", 6),
    ("mega", 3),
    ("own_item", N_ITEM_CATS), ("opp_item", N_ITEM_CATS),
    ("misc", 2),
    ("bench_tactics", 6),
    # --- v3 ---
    ("own_move_effects", 4 * 8), ("opp_move_effects", 4 * 8),
    ("profile", 4), ("utility", 4),
    ("field_remaining", 2), ("bench_matchup", 4),
    # --- v4/v5/v6 ---
    ("special", 6), ("protect", 2), ("ability", 4),
    # --- v7 ---
    ("race", 6),
    ("own_bench0_v7", 7), ("own_bench1_v7", 7),
    ("opp_bench0_v7", 6), ("opp_bench1_v7", 6),
]

# エンティティ (ポケモン) ごとのブロック割当。混在ブロック (bench_tactics等の
# 複数体をまたぐもの) はグローバル扱い
ENTITY_PARTS = {
    "own_active": ["own_active", "own_vol", "own_item",
                   "own_move_effects", "utility"],
    "opp_active": ["opp_active", "opp_extra", "opp_moves", "opp_vol",
                   "opp_item", "opp_move_effects"],
    "own_bench0": ["own_bench0", "own_bench0_v7"],
    "own_bench1": ["own_bench1", "own_bench1_v7"],
    "opp_bench0": ["opp_bench0", "opp_bench0_v7"],
    "opp_bench1": ["opp_bench1", "opp_bench1_v7"],
}


def obs_part_slices() -> dict:
    """ブロック名 -> (開始, 終了) のオフセット表"""
    out, off = {}, 0
    for name, dim in OBS_PARTS:
        out[name] = (off, off + dim)
        off += dim
    return out


def entity_index_groups() -> tuple:
    """set encoder用: (エンティティ名 -> インデックス列, グローバルのインデックス列)"""
    slices = obs_part_slices()
    assigned = set()
    groups = {}
    for ent, names in ENTITY_PARTS.items():
        idx = []
        for n in names:
            s, e = slices[n]
            idx.extend(range(s, e))
            assigned.add(n)
        groups[ent] = idx
    global_idx = []
    for name, _dim in OBS_PARTS:
        if name not in assigned:
            s, e = slices[name]
            global_idx.extend(range(s, e))
    return groups, global_idx

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
