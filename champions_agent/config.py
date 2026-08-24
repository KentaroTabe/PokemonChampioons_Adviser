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

# CHAMPIONS_MODELS_DIR で差し替えられる。報酬スイープなど、本番の
# チェックポイントを汚さずに複数条件を並列で学習するときに使う
MODELS_DIR = Path(os.environ.get("CHAMPIONS_MODELS_DIR")
                  or BASE_DIR / "train" / "checkpoints")

# TRAIN_SEED で学習の乱数 (RANDOM_SEED) を差し替える。A/B比較で
# 「同じ設定を複数回」回し、その回の運と設定の効果を切り分けるために使う
TRAIN_SEED_OVERRIDE = os.environ.get("TRAIN_SEED")


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
# pokedb オープンデータを「使える」と見なす最小構築数。
# シーズン切替直後のopendataはほぼ空で、最新シーズンを無条件に採用すると
# 使用率%・共起率が数構築から算出され壊れる (2026-08-05: 285構築のM-3から
# 3構築のM-4へ黙って切り替わり、自己対戦のチーム抽選が実質14種に偏った)。
# ingest側の足切りと、ベンチ用チームプール (env/ranked_teams.py) の
# 足切りで同じ値を使う。
USAGE_MIN_RANKED_TEAMS = 100
# meta_sets が前スナップショットから「実質変化」(技集合/持ち物/特性/性格/配分の
# いずれか) した種がこの数以上なら、日次更新ログに警告を出す。
# 評価軸 (ベンチ・h2h のチーム中身) は meta_sets 経由で動くため、大量の
# セット回転は絶対値ベンチの前後比較を壊す (2026-08-19: 127種回転で
# 凍結_bestのベンチが0.59→0.46に段差。5日間気付けなかった)。
META_SET_CHANGE_WARN = 10
# 日次定点 (tools/track_progress) で、凍結参照 (_best) の測定値が前回から
# この標準誤差倍数を超えて動いたら警告する。凍結重みの定点が動くのは
# 測定軸側の変化のサイン (同上インシデントで11SEの段差を見逃した)。
FROZEN_REF_WARN_SIGMA = 3.0


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
RANDOM_SEED = int(TRAIN_SEED_OVERRIDE) if TRAIN_SEED_OVERRIDE else 42
SELFPLAY_OPPONENT_POOL_SIZE = 50  # team_builder が保持する対戦相手チーム候補数

# --- 学習時のリソース管理 (2026-08-19 メモリ枯渇対策) ---
# poke-env の Player は対戦オブジェクト (ターンごとのイベント履歴を含む) を
# close まで解放しないため、長時間学習ではプロセスRSSが対戦数に比例して増える。
# エピソード完結型の学習は過去バトルを参照しないので、終了済みバトルを
# 定期的に破棄する。何エピソードごとに掃除するか / 直近何件残すか。
TRAIN_BATTLE_PRUNE_EVERY = int(os.environ.get("TRAIN_BATTLE_PRUNE_EVERY", "20"))
TRAIN_BATTLE_PRUNE_KEEP = int(os.environ.get("TRAIN_BATTLE_PRUNE_KEEP", "5"))
# torch のCPUスレッド数上限。8コア中2コアを他用途 (アドバイザー/他アプリ) に
# 残す。OMP_NUM_THREADS の既定にも同じ値を使う (tools/smoke_train.py)。
TRAIN_TORCH_THREADS = int(os.environ.get("TRAIN_TORCH_THREADS", "6"))
# 相手プール/アンカー方策のワーカー内キャッシュ上限 (LRU)。
# 無上限だと pool 20 + anchor 6 の全世代 (展開後 約30-40MB/個) が
# ワーカーごとに載り、最悪 ~0.9GB/ワーカーまで育つ (2026-08-20 実測)。
# 抽選分布は変えず、オブジェクトの保持数だけを絞る。超過分は再ロード (~1秒)。
TRAIN_OPP_POLICY_CACHE = int(os.environ.get("TRAIN_OPP_POLICY_CACHE", "3"))


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

