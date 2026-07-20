"""
champions_agent 全体で共有する設定値。

- パス設定
- レギュレーション(使用可能ポケモン範囲・テラスタルの有無・対戦後の編集可能範囲)は
  現時点では仮値。実際のポケモンチャンピオンズのルールに合わせて随時更新すること。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# --- パス設定 ---
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_DIR = DATA_DIR / "db"
DB_PATH = DB_DIR / "champions.sqlite3"
SCHEMA_PATH = DB_DIR / "schema.sql"

MODELS_DIR = BASE_DIR / "train" / "checkpoints"

# --- ローカルShowdownサーバー (学習用) ---
# アドバイザーのバックエンド (ポート8000) と常時併用できるよう別ポートで運用する。
SHOWDOWN_PORT = int(os.environ.get("SHOWDOWN_PORT", "8100"))
# チャンピオンズのシミュレーション形式:
# アップストリームShowdownの champions mod ([Gen 9 Champions] BSS Reg M-B) を使用。
# メガシンカ (交代後も継続する仕様含む)・まひ1/8・ねむり2-3T等のリバランス・
# チャンピオンズの技プール/新メガストーンが忠実に再現されている。
# Flat Rules = 6体構築から3体選出・Lv50・種族/アイテムクロース。
TRAINING_BATTLE_FORMAT = "gen9championsbssregmb"
TRAINING_TEAM_SIZE = 6

# --- 外部API設定 ---
POKEAPI_BASE_URL = "https://pokeapi.co/api/v2"
# 使用率統計の取得元 (優先順)。詳細は data/sources/ の各モジュール参照。
USAGE_STATS_SOURCES = {
    # 主軸: ゲーム内「バトルデータ」の日次収集API (規約でボット利用許可・要クレジット)
    "championsbattledata": {
        "enabled": True,
        "base_url": "https://championsbattledata.com",
        "credit": "Battle data provided by Pokémon Champions Battle Data",
    },
    # 補完: 上位ランカー構築の公式オープンデータ (使用率%/共起率の算出元)
    "pokedb_opendata": {
        "enabled": True,
        "base_url": "https://champs.pokedb.tokyo",
    },
    # フォールバック: champions実データが取得不能な場合のみ (gen9ou)
    "smogon": {
        "enabled": True,
        "base_url": "https://www.smogon.com/stats",
    },
}

# 収集対象フォーマット (チャンピオンズ ランクバトル シングル)
USAGE_TARGET_FORMAT = "champions-singles"
# Smogonフォールバック時のレーティング下限
USAGE_MIN_RATING = 1500


@dataclass
class Regulation:
    """ポケモンチャンピオンズのレギュレーション定義(暫定値)。

    実際のルール確定後、下記フィールドを更新すること。
    """
    name: str = "provisional"
    # 使用可能なポケモン図鑑番号や種族名のフィルタ(空 = 制限なし)
    allowed_species: list[str] = field(default_factory=list)
    banned_species: list[str] = field(default_factory=list)
    # テラスタルの使用可否
    tera_allowed: bool = True
    # パーティ編成ルール
    party_size: int = 6
    selection_size: int = 3
    # 対戦後にパーティを編集できる範囲
    # "free": 6体全て自由に入れ替え可能
    # "bench_only": ベンチ(選出しなかった3体)のみ入れ替え可能
    # "none": 編集不可
    post_battle_edit_scope: str = "free"


DEFAULT_REGULATION = Regulation()

# --- 学習設定(暫定デフォルト) ---
RANDOM_SEED = 42
SELFPLAY_OPPONENT_POOL_SIZE = 50  # team_builder が保持する対戦相手チーム候補数


@dataclass
class PlayStyle:
    """エージェントの「性格(プレイスタイル)」定義。

    特定のパーティ/戦術に偏らないよう、複数の性格を持つエージェント群を
    並行して育てる想定。team_builder(チーム生成バイアス)と
    reward(報酬シェイピング)の両方に反映される。
    """
    name: str
    # 役割タグ(data/role_tagger.py の ROLES)ごとの重み倍率。
    # 1.0が基準。値を大きくするほど、その役割のポケモン/型が選ばれやすくなる。
    role_weight_multipliers: dict[str, float] = field(default_factory=dict)
    description: str = ""


PLAY_STYLES: dict[str, PlayStyle] = {
    "offense": PlayStyle(
        name="offense",
        role_weight_multipliers={
            "sweeper": 2.0, "wallbreaker": 1.8, "hazard_setter": 1.2,
            "pivot": 0.8, "wall": 0.3, "hazard_removal": 0.6, "status_support": 0.7,
        },
        description="対面構築・高火力アタッカーを好む攻撃的な性格。速攻決着を狙う。",
    ),
    "cycle": PlayStyle(
        name="cycle",
        role_weight_multipliers={
            "pivot": 2.0, "hazard_setter": 1.5, "hazard_removal": 1.3,
            "sweeper": 1.0, "wallbreaker": 0.9, "wall": 0.9, "status_support": 1.1,
        },
        description="交代読み合い・とんぼ返り等での有利対面構築を好むサイクル戦術の性格。",
    ),
    "stall": PlayStyle(
        name="stall",
        role_weight_multipliers={
            "wall": 2.2, "status_support": 1.6, "hazard_removal": 1.2,
            "hazard_setter": 1.0, "pivot": 1.0, "sweeper": 0.3, "wallbreaker": 0.4,
        },
        description="耐久・受けループ・定数ダメージによる長期戦を好む受け性格。",
    ),
    "balance": PlayStyle(
        name="balance",
        role_weight_multipliers={r: 1.0 for r in
                                  ["sweeper", "wallbreaker", "wall", "pivot",
                                   "hazard_setter", "hazard_removal", "status_support"]},
        description="特定の戦術に偏らないバランス型の性格。",
    ),
}

DEFAULT_PLAY_STYLE = "balance"

