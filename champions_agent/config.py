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

# 型合成の整合規則 (2026-09-02: 性格・配分・持ち物を属性ごとの最多で独立に
# 選ぶと「ずぶとい+CS極振り+スカーフ」のような実在しない型が合成される。
# 受け型は配分の票が細かく割れて性格だけ最多に残るのが典型的な発生機序)。
# 攻撃的持ち物: この持ち物のときは攻撃的な配分 (atk/spa が
# SPREAD_OFFENSE_MIN_POINTS 以上) を優先して選ぶ
OFFENSIVE_ITEM_IDS = ("choicescarf", "choiceband", "choicespecs", "lifeorb")
SPREAD_OFFENSE_MIN_POINTS = 24
# 性格の補正先ステータスに最低限求める振り (能力ポイント 1点=8EV)。
# 実測ではダンプ振りは1-2点、意図した部分投資は14点以上なので8で分離できる。
# 例外: 攻撃/特攻補正は、該当分類の技を持つなら無振りでも許す
# (種族値受けのいじっぱりハッサム H32/B32/A2 のような実在型のため)
NATURE_ALIGN_MIN_POINTS = 8
# 技データの薄さの判定 (2026-09-05): championsbattledata のページが日によって
# 一部の種で主要技を落とす (カバルドン: じしん98% → 翌日は まもる12% が最多)。
# 最多技の採用率がこの値未満なら『薄い』とみなし、直近の健全なスナップショットの
# 代表型を引き継ぐ (学習の相手プールと型予測が寄せ集め型を読まないように)
META_THIN_MOVE_PCT = 40.0
# 引き継ぎが起きた種のうち、使用率がこの値以上のものは日次ログに名前を出す
# (上位種の欠落は相手プールへの影響が大きい。8/19〜9/4 は日に2〜5種)
META_THIN_LOG_MIN_USAGE = 5.0
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



# --- パーティ構築システム (docs/TEAM_BUILDING_IMPLEMENTATION.md §9 の決定値、2026-09-06) ---
# 対応差 (候補 − 参照) の判定: 実用差 ε の帯に CI が収まれば「実用上同等」
BUILD_EQUIV_EPS = 0.02
BUILD_CI_Z = 1.96                      # 95% 信頼区間 (1 回だけ判定するとき: holdout、ablation)
# 追加測定の戦数の目安 (絶対上限ではない。必要な精度に達したら終了、達しなければ Uncertain 終了)
BUILD_RACE_STEPS = (100, 300, 600, 1200, 2400, 4800, 9600)
# racing は同じ候補を段階ごとに繰り返し判定する (optional stopping)。段数 K に応じて判定の z を
# 広げ、途中打ち切り込みで全体の α を保つ。pocock: Pocock 境界 (両側 α=0.05、Jennison & Turnbull
# Table 2.1)、表に無い段数は bonferroni (α/K) で代用。none: 各段で BUILD_CI_Z (2026-09-06 以前の挙動)
BUILD_RACE_LOOK_CORRECTION = "pocock"  # none / pocock / bonferroni
BUILD_RACE_ALPHA = 0.05
BUILD_RACE_POCOCK_Z = {1: 1.960, 2: 2.178, 3: 2.289, 4: 2.361, 5: 2.413,
                       6: 2.453, 7: 2.485, 8: 2.512, 9: 2.535, 10: 2.555}
BUILD_RACE_DEFAULT_MAX = 2400          # 通常候補の打ち切り。重要候補は延長可
# 相手系統: 種族集合の Jaccard がこれ以上 (6体中4体共通 = 4/8) で同一系統
BUILD_FAMILY_JACCARD = 0.5
BUILD_SPLIT_RATIOS = {"search": 0.5, "selection": 0.3, "holdout": 0.2}
BUILD_SEARCH_FOLDS = 3                 # SEARCH 内の cross-fitting (A: 適応の収集 / B: 評価 / V: checkpoint 選択の検証)
BUILD_FOLD_ADAPT, BUILD_FOLD_EVAL, BUILD_FOLD_VALIDATE = 0, 1, 2
# S7 の checkpoint 選択 (2026-09-07 決定): val_mse ではなく独立 fold (V) の実測勝率で選ぶ。learning curve で
# checkpoint の質が N でぶれ (収束比 −0.17 の落ち込み)、val_mse の停止が勝率を保証しなかった。
# 200 戦の CI 半幅は ±0.07 で、大きな落ち込みを弾く目的 (小差の優劣は S8b の variant 比較に任せる)
BUILD_ADAPT_VALIDATE_N = 200
BUILD_ADAPT_VALIDATE_MAX_CKPTS = 4     # 検証する checkpoint 数 (最初と最後を含めて等間隔)
# 選出データ収集 (1 chunk = 1,000 戦 ≈ 2 分) の無応答対策: 戦数比例の timeout と、seed を変えた 1 回の再試行
# (2026-09-07 chat_0907: Showdown の空理由の team rejected で poke-env が待ち続け、4 時間後の例外で run が落ちた)
BUILD_COLLECT_TIMEOUT_PER_1K = 1800    # 1,000 戦あたりの秒数 (通常の約 15 倍)
BUILD_COLLECT_RETRY_SEED_OFFSET = 7919
BUILD_POOL_TOP_N = 200
# 選出方策 ablation (teampreview / fresh adapted / production) の 1 条件あたりの戦数。
# 対応差の CI 半幅は 100 戦 ±0.10、300 戦 ±0.06、1,000 戦 ±0.035 (実測の分散から)。
# 300 戦で判別できるのは 10 pt 級の上げ幅と、12 pt 以上離れた候補対の順位反転まで
BUILD_PICK_ABLATION_N = 300
# S8a screening (2026-09-07 決定、ablation + learning curve より): 代理スコアや未適応の測定では候補を落とさず、
# 全候補を cheap adaptation (BUILD_SCREEN_ADAPT_BATTLES 戦の収集で 1 回学習) してから同一相手列で測る。
# learning curve (3 候補): N=1000 の順位が収束後の順位と一致。ただし N=1000 は収束比 −0.04〜−0.09 なので、
# 脱落は伸び代 margin 込み (CI 上端 + margin < −ε のときだけ)。margin は 3 候補の実測 (+0.05〜+0.10) の下側
BUILD_SCREEN_ADAPT_BATTLES = 1000
BUILD_SCREEN_MARGIN = 0.05
BUILD_SCREEN_STEPS = (100, 300)
BUILD_SCREEN_MAX = 300
# S8a / S8b は Team × PickVariant 評価 (2026-09-07 決定): チームの実力 = 選出方策 variant の最善。
# S8a は teampreview / generic (汎用基底) / cheap (screening 用の短い適応)、S8b は teampreview / generic /
# fresh (収束まで適応し、独立 fold の実測で選んだ checkpoint)。参照 (現行チーム) も同じ variant の最善で測る。
# ablation: #0 は generic が fresh より +0.07、#3 は fresh が generic より +0.26 と候補で逆なので測定で選ぶ
BUILD_SCREEN_VARIANTS = ("teampreview", "generic", "cheap")
BUILD_PICK_VARIANTS = ("teampreview", "generic", "fresh")
# 現行チーム (config/my_team.json の登録 6 体) を exploitation pool として候補に必ず入れる: 代理スコアの較正点 +
# 近傍 (1 枠入替、入替枠を散らして上位) を BUILD_INCUMBENT_NEIGHBORS 並び。探索 (exploration) の quota とは別枠。
# 現行と近傍の登録済み個体は登録の型 (持ち物・配分・技) をそのまま使う
BUILD_INCUMBENT_NEIGHBORS = 4
# 候補ごとの適応 (選出モデル / 行動 adapter): 最低戦数と収束停止
BUILD_ADAPT_MIN_BATTLES = 5000
BUILD_ADAPT_PATIENCE = 3               # 連続でこの回数、改善 < BUILD_ADAPT_EPS_TRAIN なら停止
BUILD_ADAPT_EPS_TRAIN = 0.01
BUILD_MAX_REPAIRS = 2                  # 同じ系統の改修反復。3 回目以降は新しい concept branch
BUILD_MAX_CHANGES = 2                  # 1 反復あたりの入替枠数。3 枠以上は新系統
BUILD_STRESS_ACTION_NOISE = (0.05, 0.10)
BUILD_SMOKE_CANARY_BATTLES = 20        # 性能判定には使わない (crash / illegal action / 読込 / ログ / latency)
BUILD_PROMOTE_MIN_FULL_RUNS = 3        # 昇格条件: 独立 full run 3 回 + 全 gate PASS + 重大 regression 0 + 人手 approve
# 実戦評価の重み w_N: 有効標本と CI 半幅で決める (200 戦は early signal に留める)
BUILD_REAL_MIN_EFFECTIVE_N = 1000
BUILD_REAL_MAX_CI_HALFWIDTH = 0.03
# 遵守モデルの基準遵守率 (P(follow) は助言の 1 位と 2 位の差で変調する)
BUILD_USER_MODELS = {"full": 1.0, "high": 0.9, "mixed": 0.7, "expert": 0.5}
BUILD_SCHEMA_VERSION = "1"

# --- 接続テスト後の自パーティ改善案 (tools/party_improvements、2026-09-09) ---
# 「動きづらさ」: 助言の最善手のスコアがこの値未満、または最善が交代だった決定を「圧力を受けた決定」と数える
PARTY_IMPROVE_LOW_SCORE = 60.0
PARTY_IMPROVE_MIN_DECISIONS = 2        # 相手個体ごとの圧力を出すのに必要な決定数
PARTY_IMPROVE_TOP_PARTIES = 3          # 動きづらかった相手パーティの掲載数
PARTY_IMPROVE_TOP_PROPOSALS = 3        # 1 枠入替の案の掲載数
PARTY_IMPROVE_TOP_THREATS = 8          # 対策候補を出す相手個体の数 (難易度の重み × 圧力の順)
PARTY_IMPROVE_DEFAULT_LAST = 12        # --session でマーカーが無いとき (終了処理後) に見る直近の対戦数
# 概念タグ: 「先制技に弱い速い個体」= 素早さ種族値 ≥ FAST かつ 防御/特防の低い方 ≤ FRAIL (メガライチュウY: S130 / B55)
PARTY_IMPROVE_FRAIL_FAST_SPE = 100
PARTY_IMPROVE_FRAIL_FAST_DEF = 60
PARTY_IMPROVE_SLOW_SPE = 50            # トリックルーム側の「遅い」
# 相手の難易度の重み: 1 + 負け + 圧力を受けた決定の割合 (改善案の脅威重みに使う)
PARTY_IMPROVE_LOSS_WEIGHT = 1.0
# 改善案の測定 (--measure): 現行 + 近傍 N 並びを構築システムの測定段 (S8a〜S13) に掛ける。
# セッションの相手は脅威重みに 1 + BOOST × (正規化した難易度) を掛けて近傍の選び方に反映する
PARTY_IMPROVE_MEASURE_NEIGHBORS = 3
PARTY_IMPROVE_MEASURE_PROFILE = "medium"
BUILD_SESSION_THREAT_BOOST = 2.0
BUILD_PROTOCOL_VERSION = "1"
