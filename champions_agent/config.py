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
TRAINING_BATTLE_FORMAT = "gen9championsbssregmc"   # 2026-09-11 M-C へ切替 (M-B は gen9championsbssregmb、mod championsregmb)
# poke-env が知らないチャンピオンズ固有の効果名 (Effect.UNKNOWN に落ちる)。既知のものは警告ログを抑止する
# (2026-09-11: 上流更新でメガメガニウムの特性 Mega Sol が "[from] ability" として流れ、1 戦ごとに数行の警告が出た)
TRAIN_IGNORED_UNKNOWN_EFFECTS = ("MEGA_SOL",)
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
# improved / equivalent は MIN_TERMINAL_N 戦未満では確定させない (degraded の早期脱落は残す: 誤脱落は候補 1 つの損、
# 誤昇格は推薦の誤り)。封印 holdout は上限まで回す。勝者は S8b と S10 の両分割で Δ ≥ 0 でなければ holdout に進めない
# (再現性の門)。2026-09-10: rule_0909 の L21 が S8b 100 戦で improved (+0.150) → 別分割の S10 で degraded (−0.043) に反転
BUILD_RACE_MIN_TERMINAL_N = 300
BUILD_HOLDOUT_RUN_TO_MAX = True
BUILD_REPRO_GATE = True
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

# --- 実戦の相手バンク (tools/real_opponents、2026-09-09): 対戦ログの相手の「実際の選出・先発・判明した型」 ---
# 自己対戦の相手は使用率メタの代表型 + 相性ヒューリスティクスの選出で机上の分布になる。実戦で当たった相手の
# 構成と選出傾向を混ぜて補正する (ユーザー決定 9/9)。学習に影響する変更は training_changes.json に記録
REAL_BANK_PATH = "logs/real_opponents/bank.json"
REAL_BANK_MIN_ROSTER = 4               # バンクに載せる相手ロースターの最小既知数 (6 体そろわない対戦もある)
REAL_BANK_MIN_TEAMS = 5                # 自己対戦に混ぜるのに必要な (本文が合法な) チーム数
REAL_BANK_MIN_PICK_OBS = 2             # 観測した選出をそのまま相手の選出分布に使う最小観測数 (未満はヒューリスティクス)
REAL_BANK_PICK_FLOOR = 0.05            # 観測で一度も選ばれなかった個体にも残す選出確率
TRAIN_REAL_OPP_MIX = 0.30              # 学習環境で相手チームを実戦バンクから出す確率 (残りは従来の混合)
# 相手の選出: 同じ構成の観測が REAL_BANK_MIN_PICK_OBS 以上ならその構成の観測分布、無ければ種ごとの実戦選出率
# (出現 REAL_BANK_SPECIES_MIN_APPEAR 以上の種が 3 体以上いるとき) で 3 体をサンプルする。
# 確率 TRAIN_REAL_PICK_PROB でこの実戦傾向の選出、残りは従来の相性ヒューリスティクス (多様性を残す)
REAL_BANK_SPECIES_MIN_APPEAR = 3
TRAIN_REAL_PICK_PROB = 0.7
# 選出助言の条件づけ: 相手スロットの重み = 1 + MIX × (その種の実戦選出率 − 平均) / 平均 (平均は 1 のまま)
SELECTION_REAL_PRIOR_MIX = 0.5
SELECTION_REAL_PRIOR_MIN_APPEAR = 3    # 実戦での出現数がこれ未満の種は事前分布を使わない
SELECTION_REAL_PRIOR_CLIP = (0.5, 1.5)

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
# 構築のコンセプト規則 (tools/team_build/rules.py、hard constraint)。psychic_terrain_priority_ace のエース = 接地していて
# 次のいずれかの型 (2026-09-10 改訂。閾値はより良い値が見つかれば変更してよい: ユーザー合意)。
#   速攻型: 上位脅威への先手率 (S3 の役割 speed、加速前) ≥ FAST_SPEED_SHARE かつ 防御種族値 (メガ後) ≤ FAST_MAX_DEF
#   自己加速型: かそく/かるわざ/加速技を持ち、加速後の先手率 (speed_boost) ≥ BOOST_SPEED_SHARE かつ 1 発耐える耐久 (bulk) ≥ BOOST_MIN_BULK
#   トリックルーム型: 先手率 ≤ TR_SPEED_SHARE で、同じ 6 体に TR 使い (代表型に TRICK_ROOM_MOVES) がいる
# 規則から作る軸 (設置役 × エース) は使用率の和の上位 MAX_CORES 対まで
BUILD_RULE_ACE_FAST_SPEED_SHARE = 0.75
BUILD_RULE_ACE_FAST_MAX_DEF = 75
BUILD_RULE_ACE_BOOST_SPEED_SHARE = 0.8
BUILD_RULE_ACE_BOOST_MIN_BULK = 0.25
BUILD_RULE_ACE_TR_SPEED_SHARE = 0.3
BUILD_TRICK_ROOM_MOVES = ("trickroom",)
# エース共通の火力・技範囲の条件 (2026-09-10 ユーザー指摘: 加速するだけの補助型 (ペロリーム) がエースに入っていた)。
# 代表型の攻撃技 (威力 > 0) から: 使う側の攻撃種族値 (物理なら攻撃、特殊なら特攻、メガ後) ≥ MIN_OFFENSE、
# 攻撃技の本数 ≥ MIN_ATTACK_MOVES、攻撃技のタイプ数 ≥ MIN_ATTACK_TYPES、脅威への平均被覆 ≥ MIN_COVERAGE
BUILD_RULE_ACE_MIN_OFFENSE = 100
BUILD_RULE_ACE_MIN_ATTACK_MOVES = 2
BUILD_RULE_ACE_MIN_ATTACK_TYPES = 2
BUILD_RULE_ACE_MIN_COVERAGE = 0.3
BUILD_RULE_MAX_CORES = 24
# 並びの採点 (S5): 穴の罰則 = 最も薄い脅威の不足分 (HOLE_THRESHOLD − 最良被覆)+ × 脅威の重み (最大を 1 に正規化) × HOLE_WEIGHT。
# 30 脅威の平均に薄まる 1 体の穴 (rule_0909 のカイリュー 0.18) を候補間の差と同じ桁で罰する
BUILD_LINEUP_HOLE_THRESHOLD = 0.4
BUILD_LINEUP_HOLE_WEIGHT = 0.5
# 規則の対 (設置役, エース) の相補性: 共通の苦手 (両方 < HOLE_THRESHOLD) を他の 4 体が PAIR_COVER 以上で見ている割合。
# 見ていない割合 × PAIR_WEIGHT を並びの点から引く (記事にも 共通の苦手 / 見ている個体 / 未対策 を書く)
BUILD_RULE_PAIR_COVER = 0.6
BUILD_RULE_PAIR_WEIGHT = 0.5
# 型ライブラリ (S6): 代替 (持ち物/技/配分の単独入替) は被覆スコアから「代表型との使用率差 (0..1) × USAGE_WEIGHT」を
# 引いた値で代表型と比べる (使用率の事前分布。珍しい持ち物が被覆の差だけで採られるのを防ぐ。2026-09-10 レビュー対応)
BUILD_SET_USAGE_WEIGHT = 0.3
# learnset からの型生成 (tools/team_build/gen_sets.py、2026-09-11 ユーザー決定): 使用率が無い種 (シーズン序盤・新種) は
# 覚える技から型を作って代表型の代わりにし、使用率がある種でも候補に加える (使用率差の罰則は代替と同じ重み)。
# 全探索はせず、技プールの刈り込み → テンプレート → 貪欲な被覆選択 (想定する相手の重みつき) で範囲を絞る
BUILD_GEN_SETS = "auto"                      # auto: 全ての種で生成して候補に加える / missing: 代表型が無い種だけ / off
BUILD_GEN_ATTACKS_PER_TYPE = 1               # 攻撃技はタイプ × 分類ごとに上位 N 本
BUILD_GEN_CATEGORY_TOLERANCE = 0.85          # 攻撃/特攻の低い方がこの比率以上なら両分類の攻撃技を残す
BUILD_GEN_MAX_ATTACKS = 8                    # 刈り込み後の攻撃技の上限
BUILD_GEN_MAX_SETS = 6                       # 種ごとの生成型の上限
BUILD_GEN_AVOID_MOVES = ("hyperbeam", "gigaimpact", "explosion", "selfdestruct", "skyattack", "solarbeam", "solarblade",
                         "futuresight", "doomdesire", "lastresort", "dreameater", "synchronoise", "beatup", "fling",
                         "naturalgift", "endeavor", "counter", "mirrorcoat", "metalburst", "bide", "focuspunch",
                         "skullbash", "razorwind", "freezeshock", "iceburn", "geomancy", "meteorbeam", "electroshot",
                         "mistyexplosion", "steelbeam", "mindblown", "chloroblast", "finalgambit", "memento",
                         # 反動 (次のターン動けない) と 2 ターン技 (生成型では扱わない)
                         "blastburn", "frenzyplant", "hydrocannon", "rockwrecker", "roaroftime", "eternabeam",
                         "fly", "dig", "dive", "bounce", "phantomforce", "shadowforce", "skydrop")
# テンプレート: attacks = 攻撃技の本数、utility = 補助技の役割 (順に埋める。埋まらなければそのテンプレートは捨てる)
BUILD_GEN_TEMPLATES = (
    {"name": "attack3_setup", "attacks": 3, "utility": ("setup",)},
    {"name": "attack3_protect", "attacks": 3, "utility": ("protect",)},
    {"name": "attack3_priority", "attacks": 3, "utility": ("priority",)},
    {"name": "attack3_pivot", "attacks": 3, "utility": ("pivot",)},
    {"name": "attack2_setup_protect", "attacks": 2, "utility": ("setup", "protect")},
    {"name": "attack4", "attacks": 4, "utility": ()},
    {"name": "attack2_hazard_status", "attacks": 2, "utility": ("hazard", "status")},
    {"name": "attack2_heal_status", "attacks": 2, "utility": ("heal", "status")},
    {"name": "attack3_field", "attacks": 3, "utility": ("field",)},
    {"name": "attack2_screens", "attacks": 2, "utility": ("screens", "screens")},
)
# 役割 → 技 (積み/先制/交代/設置/除去/状態異常/回復/まもる は既存の辞書 (advisor.search、features、interaction) から取る)
BUILD_GEN_UTILITY_MOVES = {
    "field": ("psychicterrain", "electricterrain", "grassyterrain", "mistyterrain", "sunnyday", "raindance",
              "sandstorm", "snowscape", "tailwind", "trickroom"),
    "screens": ("reflect", "lightscreen", "auroraveil"),
}
# 型の定型。能力ポイントは合計 BUILD_GEN_POINT_BUDGET (ゲーム内の上限 66)。性格は「+Spe で上を取れる脅威が増えるか」で選ぶ
BUILD_GEN_POINT_BUDGET = 66
BUILD_GEN_ARCHETYPES = {
    # 持ち物はチャンピオンズに存在するものだけ (こだわりハチマキ/メガネ・とつげきチョッキは無い)。生成時にシムの一覧でも検査する
    "fast_physical":  {"evs": "2/32/0/0/0/32", "natures": ("jolly", "adamant"), "items": ("focussash", "lifeorb", "choicescarf")},
    "fast_special":   {"evs": "2/0/0/32/0/32", "natures": ("timid", "modest"), "items": ("focussash", "lifeorb", "choicescarf")},
    "bulky_physical": {"evs": "32/32/0/0/0/2", "natures": ("adamant",), "items": ("leftovers", "sitrusberry", "lumberry")},
    "bulky_special":  {"evs": "32/0/0/32/0/2", "natures": ("modest",), "items": ("leftovers", "sitrusberry", "lumberry")},
    # 壁型の性格は (物理攻撃向け −SpA, 特殊攻撃向け −Atk) の順 (攻撃に使わない側を下げる。速攻型の (+Spe, +攻撃) とは意味が違う)
    "wall_physical":  {"evs": "32/0/32/0/2/0", "natures": ("impish", "bold"), "items": ("leftovers", "rockyhelmet", "sitrusberry")},
    "wall_special":   {"evs": "32/0/0/0/32/2", "natures": ("careful", "calm"), "items": ("leftovers", "sitrusberry")},
}
BUILD_GEN_SETUP_ITEMS = ("sitrusberry", "lumberry", "focussash")   # 積みテンプレートの持ち物 (じゃくてんほけんは M-C に無い)
# メガ石を持つ種は、メガ後の種族値・タイプ・特性 (メガリザードン Y のひでり等) でメガ型を別に生成する (フォルムごとに上限 N 型)
BUILD_GEN_MEGA_SETS = 2
# 想定する相手ごとの能力ポイントの微調整 (2026-09-11 ユーザー指示)。配分の候補を全ポイント使い切りで列挙し、
# 脅威の重みつきで「上を取れる」「最大打点を 1 発 / 2 発耐える (+余裕)」「最大打点で 1 発 / 2 発で倒す (+割合)」の和が
# 最大の配分を採る (基準線を越える配分を優先し、連続項は同点の中での選好)
BUILD_GEN_EV_TUNE = True
BUILD_GEN_EV_POINT_CAP = 32          # 1 能力あたりの上限ポイント (ゲーム内)
BUILD_GEN_EV_STEP = 8                # 攻撃/耐久の刻み (素早さは「脅威の上を取る最小ポイント」を候補にする)
BUILD_GEN_EV_WEIGHTS = {"outspeed": 1.0, "survive": 1.0, "survive_2hit": 0.5, "survive_margin": 0.25,
                        "ko": 1.0, "ko_2hko": 0.5, "ko_margin": 0.25}
BUILD_GEN_EV_THREATS = 12            # 微調整で見る相手の数 (重みの大きい順。候補配分 × 相手の計算量を抑える)
# フィールド/天候の補正 (場を張る特性/技、タイプ別倍率、技固有の効果、優先度) は advisor/data/field_effects.json が
# 唯一の源 (advisor.damage と gen_sets が共有。2026-09-11 に config から移した)
BUILD_GEN_FAST_SPEED_SHARE = 0.6    # 上位脅威への先手率 (+Spe 性格・振り切り) がこれ以上なら速攻型
BUILD_GEN_WALL_OFFENSE_MAX = 90     # 使う側の攻撃種族値がこれ以下なら壁型 (攻撃技 2 本のテンプレートを優先)
BUILD_GEN_SPEED_GAIN_MIN = 1.0      # +Spe 性格で上を取れる脅威 (重みの和) がこれ以上増えれば +Spe 性格
BUILD_GEN_ABILITY_PRIORITY = ("psychicsurge", "grassysurge", "electricsurge", "mistysurge", "drought", "drizzle",
                              "sandstream", "snowwarning", "intimidate", "regenerator", "protean", "libero",
                              "adaptability", "toughclaws", "sheerforce", "hugepower", "purepower", "speedboost",
                              "unburden", "magicguard", "prankster", "technician", "guts", "multiscale", "levitate",
                              "moldbreaker", "sharpness", "supremeoverlord", "roughskin", "ironbarbs", "stamina")
# 規則のエースの持ち物: その種での使用率が MIN_PCT 以上なら ACE_ITEMS の先頭から優先し、アイテムクローズでもエースが残す
BUILD_RULE_ACE_ITEMS = ("focussash",)
BUILD_RULE_ACE_ITEM_MIN_PCT = 10.0
# 自己加速 (S3 の役割 speed_boost = 加速後に上を取れる脅威の割合): 特性の倍率 (かるわざは消費アイテム持ちのときだけ) と
# 加速技の倍率 (1 回積んだ後)。効果は最大のもの 1 つを採る
BUILD_SPEED_BOOST_ABILITIES = {"speedboost": 1.5, "unburden": 2.0}
BUILD_SPEED_SETUP_MOVES = {"agility": 2.0, "rockpolish": 2.0, "autotomize": 2.0, "shellsmash": 2.0, "shiftgear": 2.0,
                           "geomancy": 2.0, "dragondance": 1.5, "quiverdance": 1.5, "flamecharge": 1.5,
                           "trailblaze": 1.5, "rapidspin": 1.5, "tidyup": 1.5, "victorydance": 1.5, "aquastep": 1.5}
BUILD_CONSUMABLE_ITEMS = ("whiteherb", "focussash", "sitrusberry", "lumberry", "chestoberry", "salacberry", "liechiberry",
                          "petayaberry", "apicotberry", "custapberry", "mentalherb", "powerherb", "throatspray",
                          "weaknesspolicy", "electricseed", "psychicseed", "grassyseed", "mistyseed", "boosterenergy",
                          "airballoon", "redcard", "ejectbutton", "ejectpack", "absorbbulb", "cellbattery",
                          "luminousmoss", "snowball", "roomservice", "adrenalineorb", "keeberry", "marangaberry",
                          "occaberry", "chopleberry", "yacheberry", "wacanberry", "rindoberry", "passhoberry",
                          "shucaberry", "cobaberry", "payapaberry", "tangaberry", "chartiberry", "kasibberry",
                          "habanberry", "colburberry", "babiriberry", "roseliberry", "chilanberry")
BUILD_PROTOCOL_VERSION = "1"
