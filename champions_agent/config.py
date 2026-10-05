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
# 日次定点の評価 1 回 (champions_agent.train.evaluate) の打ち切り。戦数に比例した秒数を
# evaluate の --timeout と subprocess の timeout の両方に渡す (後者は前者 + 猶予)。
# 2026-09-18: agents 軸の 3,000 戦が Showdown との通信待ちで 5 日間止まり、launchd の
# 日次ジョブが再発火せず 9/18〜9/23 の定点が欠測した
# (docs/incidents/reports/2026-09-18-track-progress-hang-no-timeout.md)。
# 通常は 1,000 戦あたり約 130 秒 (9/17 実測: 11,000 戦 24 分)。構築 run と重なると約 1.6 倍
TRACK_PROGRESS_EVAL_TIMEOUT_PER_1K = 900   # 1,000 戦あたりの秒数 (通常の約 7 倍)
TRACK_PROGRESS_TIMEOUT_GRACE_S = 60        # evaluate 側の自己終了を待つ猶予


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



# --- 画面認識・対戦状態 (2026-09-16 第15回接続テスト後) ---
# ゲームの枠数: パーティ 6 体から 3 体選出 (シングル)。3 体目のひんし = 陣営の全滅
PARTY_SIZE = 6
BSS_PICK_COUNT = 3
# 3 体目のひんしを見てから終了を確定するまでの猶予 (秒)。この間にその陣営の交代を観測したら取り消す (ひんしの帰属誤り対策)。
# 勝負文言の取り逃し (フレーム破棄率 42%) だとリザルト画面まで終了が分からず、リザルト画面の誤分類で助言が出続けた
BATTLE_END_FAINT_CONFIRM_SEC = 6.0
# ランク画面のレートの 1 戦あたりの変動の上限 (実測: 勝っても負けても 20 弱)。連続する対戦のレート差がこれを超えたら
# 読み違い (OCR の数字誤読) の疑いとして分析に出す (自動では直さない)
RATE_MAX_DELTA_PER_BATTLE = 30.0
# レートの増減から勝敗を推定するときの差の範囲 (battle_logger)。上限を超える差は数字の誤読として使わない (2026-10-06 まで
# battle_logger に直書きされていた 60)。下限未満の差は小数の読み違いとして増減に数えない (実測の 1 戦の変動は 12〜19)
RATE_INFER_MAX_DELTA = 60.0
RATE_INFER_MIN_DELTA = 1.0
# 勝敗の推定「最後に HP 0% を観測した側の負け」に使う観測の有効時間 (秒。これより前の観測は使わない。2026-10-06 まで
# battle_logger に直書き)
OUTCOME_LAST_ZERO_MAX_SEC = 180.0
OUTCOME_ZERO_HP_PCT = 3.0      # HP がこの % 以下に落ちた観測を「HP 0%」に数える (HP バーの読みの誤差。同じく直書きだった値)
# 選出画面の相手枠の推定 (タイプアイコン + スプライト照合) で、同じ種が別枠に既にあるとき: 既存が推定で、新しい推定の
# 視覚照合スコアがこの余裕以上高ければ既存を取り消して入れ替える (同種 2 体はルール上あり得ない)。小さいとフレーム間の
# スコア揺れで入れ替わり続ける
SELECTION_GUESS_REPLACE_MARGIN = 0.05
# 選出画面の相手枠: タイプからの候補の事前確率がこれ以上なら「候補が実質 1 体」として視覚照合なしで採る
# (vision/spriteid.identify_species。2026-10-06 まで spriteid に直書きされていた値)
SELECTION_PRIOR_AUTO_ACCEPT = 0.85
# 選出画面の推定の枠を、画面で「ほぼ確定」として候補のプルダウンなしで出す事前確率の下限 (第18回接続テスト: ほぼ確定の枠にも
# 選択肢が出て、選ぶことを求められているように見えた)。実測 (9/29 以降の実戦で場に出た相手 延べ 97 体、修正後の重み):
# 第一候補の確率が 0.95 以上なら的中 55/55、0.85〜0.95 は 6/9 (オニシズクモ・エアームド・ポットデス が外れ)。
# 0.85〜0.95 の帯は視覚照合なしで採られる (上の閾値) が、外れることがあるので候補は出す
SELECTION_GUESS_SURE_PROB = 0.95
# 手入力の種族名 (相手の枠の ✏️ → 種族) を解決するときの類似度の下限 (my_team の保存時の種族名の解決と同じ値)
MANUAL_SPECIES_RESOLVE_CUTOFF = 0.85
# タイプからの種の推測 (advisor/infer.prior_weights) の重み: 使用率% の下限と、最新スナップショットに無い種に掛ける減衰
# (どちらも 2026-10-06 まで advisor/infer に直書きされていた値)
INFER_USAGE_FLOOR = 0.05
INFER_PAST_USAGE_DECAY = 0.25
# 自分側の HUD 名の解決 (vision/extractors.resolve_my_species): 表記どおりの種族 (汎用解決の高閾値) → 今の対戦のロスター
# への一致 (OCR 揺れの救済) → 登録名への吸着 → 汎用。登録名を先に見ると未登録の種が登録済みの似た名前に化ける
# (第15回: ミミロップ → ミミッキュ (類似度 0.6) に解決され、場のメタグロス枠を上書きして 7 体目が生えた)
MY_ROSTER_MATCH_RATIO = 0.6
MY_EXACT_RESOLVE_CUTOFF = 0.92
MY_REGISTERED_MATCH_RATIO = 0.55

# --- パーティ構築システム (docs/TEAM_BUILDING_IMPLEMENTATION.md §9 の決定値、2026-09-06) ---
# 対応差 (候補 − 参照) の判定: 実用差 ε の帯に CI が収まれば「実用上同等」
BUILD_EQUIV_EPS = 0.02
BUILD_CI_Z = 1.96                      # 95% 信頼区間 (1 回だけ判定するとき: holdout、ablation)
# 追加測定の戦数の目安 (絶対上限ではない。必要な精度に達したら終了、達しなければ Uncertain 終了)
BUILD_RACE_STEPS = (100, 300, 600, 1200, 2400, 4800, 9600)
# 測定の子プロセス (tools.check_advisor_player、racing が腕ごとに 1 プロセスを同時に回す) の数値計算のスレッド数。
# 既定のまま (コア数ぶん) だと 5〜6 プロセスでスレッドを取り合う。2026-10-05 の実測 (8 コア、参照チーム、相手 heuristic、30 戦ずつ):
# 6 腕同時 既定 25.4 戦/分 → 1 スレッド 36.5 戦/分 (+44%)、8 腕同時 1 スレッド 35.7 戦/分。勝率は同じ (0.667 / 0.672)。
# 呼び出し側の環境変数 (OMP_NUM_THREADS など) が指定されていればそちらを優先する。None で従来どおり
BUILD_MEASURE_THREADS = 1
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
# 2026-10-05 (操縦はアドバイザーが行う): モデル無しの基準の選出は、実戦の助言と同じ相性の規則 "rule" (advisor.selection、ダメージ計算の
# 対面行列)。従来の "teampreview" (タイプ相性の簡易規則) は実戦の経路に無いので variant から外す (指定すれば使える)
BUILD_SCREEN_VARIANTS = ("rule", "generic", "cheap")
BUILD_PICK_VARIANTS = ("rule", "generic", "fresh")
# 環境チーム (相手) の操縦: heuristic = poke-env SimpleHeuristicsPlayer (従来) / rl = 学習済み行動方策 (ピンの ema)。選出は
# heuristic (Player 自身) / matchup / rule (実戦の助言と同じ規則) / model (汎用の選出モデル) / prior (実戦の選出率に比例)。
# 既定は実戦に近い方へ寄せる仮置き (rl + rule)。実験 12 (選出の一致率) と 13 (勝率の実戦との差) で決め直す
BUILD_OPP_PILOT = "rl"                     # 確定 (2026-10-05 実験 13: 参照の勝率は heuristic 0.78 / 0.73、rl 0.54〜0.61。実戦 0.44 (16 戦 7 勝) と矛盾しないのは rl)
BUILD_OPP_PICK_POLICY = "rule"            # 確定 (実験 12: どの方策も実戦の選出と区別できず、実験 13 でも差が出ない → 既に入っている rule)
# 現行チーム (config/my_team.json の登録 6 体) を exploitation pool として候補に必ず入れる: 代理スコアの較正点 +
# 近傍 (1 枠入替、入替枠を散らして上位) を BUILD_INCUMBENT_NEIGHBORS 並び。探索 (exploration) の quota とは別枠。
# 現行と近傍の登録済み個体は登録の型 (持ち物・配分・技) をそのまま使う
BUILD_INCUMBENT_NEIGHBORS = 4
# 候補ごとの適応 (選出モデル / 行動 adapter): 最低戦数と収束停止
BUILD_ADAPT_MIN_BATTLES = 5000
BUILD_ADAPT_PATIENCE = 3               # 連続でこの回数、改善 < BUILD_ADAPT_EPS_TRAIN なら停止
BUILD_ADAPT_EPS_TRAIN = 0.01
# 参照チームの対照実験 (tools/team_build/reference_adapt): 参照にも S7 と同じ深さの適応を与えて勝者と同一相手列で比べる戦数
# (medium プロファイルの race_max と同じ)
BUILD_REFERENCE_CONTROL_BATTLES = 600
# 参照 (登録チーム) にも S7 と同じ深さの選出モデル適応を与え、fresh を参照の variant に加える (S8b 以降の参照は variant の最善)。
# rule_0913 の対照実験 (2026-09-15): 参照 teampreview 0.757 / 参照+適応 0.830 / 勝者 (適応済み) 0.788 →
# 候補だけ収束まで適応し参照は cheap 1000 戦だけ、という手順は候補に有利だった (docs/incidents/reports/2026-09-15-*.md)
BUILD_REFERENCE_FULL_ADAPT = True
# 参照の variant に配布版 (本番) の選出モデルも加える (登録チームで微調整済み。候補には無い)。rule_0913 の対照実験 (9/16):
# 本番 0.848 > 参照+適応 0.830 > teampreview 0.757 (本番 − teampreview +0.092 improved、適応 − 本番 −0.018 uncertain)
BUILD_REFERENCE_PRODUCTION_VARIANT = True
# 複数の方向性の構築を最終候補に残す (2026-09-17 ユーザー決定: 1 つの勝者ではなく方向性の違う K 並びを提案し、それぞれに
# 学習 (選出モデルの適応・行動 adapter) と封印 holdout を与える。汎用性とユーザーへの適合の両方を上げる)
BUILD_FINALISTS = 3                 # 最終候補の数 (方向性の違うものが足りなければ少なくなる)
BUILD_FINALIST_MAX_SHARED = 3       # 「方向性が違う」= 既に選んだ候補と共通するメンバーがこの数以下 (6 体中)
BUILD_FINALIST_HOLDOUT_ALL = True   # 全最終候補に封印 holdout を行う (False なら 1 位だけ。STRESS と ablation は常に 1 位だけ)
BUILD_MAX_REPAIRS = 2                  # 同じ系統の改修反復。3 回目以降は新しい concept branch
BUILD_MAX_CHANGES = 2                  # 1 反復あたりの入替枠数。3 枠以上は新系統
# 測定からの戻り (S8a / S8b → S5 修理モード。docs/TEAM_BUILD_REDESIGN_1002.md §14 / §16.2。LLM の仮説は使わない: D-28)
BUILD_REPAIR_ROUNDS = 1                # 周回数 (2026-10-05 判断 #6: 1 周 (S8a 後) だけ。1003 の変種 6 本は最終比較で全部 −0.04〜−0.01)。run.py --repairs の既定
BUILD_REPAIR_ARMS = 3                  # 1 周あたりに racing へ加える変種の上限 (親が複数なら分け合う。判断 #6: 3 本)
BUILD_REPAIR_MIN_CHANGES = 2           # 入替 (A) の変種は親との違いがこの枠数以上 (±0.04 の 1 枠の変種は測っても分からない。判断 #6)。型だけの変種 (B) は可
BUILD_REPAIR_PARENTS = 2               # 1 周あたりに診断して修理する親の並びの数 (Δ の上位から)
BUILD_REPAIR_MIN_N = 20                # 診断に使う対戦数の下限 (系統は負けの多い順に束ねてこの数に達するまで)
BUILD_REPAIR_LOSS_RATE_MIN = 0.5       # 「負けに効いた」系統 / 相手種の敗率の下限
BUILD_REPAIR_UNUSED_RATE = 0.05        # 選出率がこれ以下の個体は差し替え対象 (一度も選出されなかった個体を含む)
BUILD_REPAIR_KO_MIN_N = 3              # 「誰に何で倒されたか」の集中とみなす回数の下限
BUILD_REPAIR_ITEM_UNUSED_RATE = 0.1    # 消費アイテムが発動した割合 (選出あたり) がこれ未満なら型の変更対象
BUILD_REPAIR_FAMILY_BOOST = 2.0        # 修理モードで「負けに効いた」系統の重みに掛ける倍率 (1 + この値)
BUILD_REPAIR_MIN_GAIN = 0.005          # 変種として採る点の増分の下限 (親より上がらない変種は作らない)
# 2026-10-04 run 1003 の点検 (測定と戻りの手続き) への対処
BUILD_IDENTICAL_REFERENCE_SKIP = True  # 参照 (登録チーム) と同じ 6 体・同じ型の候補は腕にしない (同じチームどうしの差は選出モデルの学習の
                                       # ばらつきだけ。1002d の PASS +0.107 は構築の差ではなかった)。修理の親としては参照の記録で診断する
BUILD_REPAIR_EXPLORE_PARENTS = 1       # 1 周あたり、探索の並び (現行枝でないもの) から Δ 上位を親に加える数 (S8a で脱落していても診断する。
                                       # 1003 では探索の 7 並びが全部脱落し、修理の対象が現行チームと近傍だけになった)
BUILD_REPAIR_POOL_VARIANTS = True      # 診断の対戦記録は同じ並びの全 variant (teampreview / generic / cheap …) の腕を束ねる
                                       # (1003: 選んだ腕だけの 300 戦では負けに効いた系統が 18 戦しか束ねられず下限 20 を割った)
BUILD_REPAIR_MIN_N_SHARE = 0.06        # 診断の下限の相対値: min(BUILD_REPAIR_MIN_N, 対戦数 × この値) を下限にする。届かなくても部分の証拠として使う
BUILD_REPAIR_ANSWER_MIN = 0.5          # 「誰に何で倒されたか」の集中への受け: 入替先はその相手の型への被覆 (行の値) がこれ以上の種に限る
BUILD_REPAIR_DISTINCT_IN = True        # 入替 (A) の変種は入れる種を散らす (同じ入替先ばかりにしない。1003: ヒスイヌメルゴン / バクフーン / カイリューだけ)
BUILD_REPAIR_FULL_ADAPT_ROUND2 = True  # 2 周目の変種にも S7 と同じ適応 (収束まで + 検証 fold の checkpoint 選択) を与えてから S10 に出す
                                       # (1003: 他の並びは 8000 戦以上の適応モデル、2 周目の変種は 1000 戦の簡易モデルかモデルなしだった)
BUILD_REFERENCE_PRODUCTION_GAP = 0.10  # 参照の fresh (run 内で適応) − 本番 (配布版) の勝率差がこれ以上なら「本番の選出モデルが現行チームで弱い」と
                                       # 記録し、fresh のモデルを registry に候補として登録する (1003: 本番 0.417 / 適応 0.73)
BUILD_STRESS_ACTION_NOISE = (0.05, 0.10)
BUILD_SMOKE_CANARY_BATTLES = 20        # 性能判定には使わない (crash / illegal action / 読込 / ログ / latency)
BUILD_STRESS_ONLY_ON_PASS = True       # STRESS と ablation は封印 holdout が PASS のときだけ (INCONCLUSIVE / FAIL なら約 2.5 時間を省く。判断 #1)
BUILD_PROMOTE_MIN_FULL_RUNS = 3        # 昇格条件: 同じ 6 体で holdout PASS の full run (下) 3 回 (= full run 2 本 + 確認 1 回。2026-10-05 Phase 5)
                                       # + 全 gate PASS + 重大 regression 0 + 人手 approve
BUILD_PROMOTE_MIN_HOLDOUT_N = 600      # "full run" の定義 1: 封印 holdout の対戦数がこれ以上 (fast の上限 300 の暫定の PASS は数えない。判断 #7)
BUILD_PROMOTE_DISTINCT_SPLITS = True   # "full run" の定義 2: 別の封印の分割 (sealed_id) で数える (同じ分割の PASS は 1 回)
# 実戦評価の重み w_N: 有効標本と CI 半幅で決める (200 戦は early signal に留める)
BUILD_REAL_MIN_EFFECTIVE_N = 1000
BUILD_REAL_MAX_CI_HALFWIDTH = 0.03
# 再構築の引き金 (tools/team_build/triggers): 閾値は基準線 (前回 run で記録した値) との差で測る (2026-10-05: 絶対値では季節で意味が変わる)
BUILD_TRIGGER_REAL_GAP = 0.2           # 登録チームの実戦勝率 (同じ 6 体、直近 BUILD_TRIGGER_REAL_DAYS 日) − シムの参照の勝率 が、基準線の差より これ以上 悪化したら
                                       # (判断 #8: 30 戦どうしの差の標準誤差は約 0.13。0.15 だと変化が無くても約 12% で発火、0.2 なら約 6%)
BUILD_TRIGGER_REAL_DAYS = 30           # 実戦の勝率の移動平均の窓 (日)
BUILD_TRIGGER_POOL_MATCH_DROP = 0.5    # 相手プールの構築単位の一致率が基準線の この倍率 を割ったら (記録に残す値)
BUILD_TRIGGER_POOL_MATCH_FIRE = False  # 一致率で引き金を引くか。判断 #8: プールの seed だけで 0.08〜0.18 に動くので、後から来た対戦だけで測れて
                                       # 整合した実戦が 100 戦を超えるまでは記録だけ
BUILD_TRIGGER_MIN_REAL_BATTLES = 30    # 実戦の勝率を引き金に使う最小の試合数 (基準線・現在の両方に要る)
# 遵守モデルの基準遵守率 (P(follow) は助言の 1 位と 2 位の差で変調する)
# BUILD_USER_MODELS (遵守モデル) は 2026-10-05 に廃止: 操縦はアドバイザーが行う。実戦の遵守率・時間内率は real_eval が記録だけ残す
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
# 環境スナップショット (S1、tools/team_build/meta_snapshot.py): 上位種と脅威リスト。
# 脅威は「pokedb 上位ランカー構築の使用率% 上位 BUILD_META_TOP_N」と「ゲーム内バトルデータの使用率順位
# (championsbattledata の列位置 = DB の pokemon_usage.rank) 上位 BUILD_META_INGAME_N」の和集合から、重み
# (使用率% と、順位 r を使用率曲線の r 番目に読み替えた値の大きい方) の順に BUILD_META_THREATS_N 種。
# 2026-09-18: M-C 序盤、上位ランカー構築に載らないボーマンダ (ゲーム内 1 位) / グソクムシャ (5 位) が脅威に入らなかった
BUILD_META_TOP_N = 60
BUILD_META_THREATS_N = 30
BUILD_META_INGAME_N = 30
# 構築の軸 (tools/team_build/archetypes.py、docs/TEAM_BUILD_ARCHETYPES.md。2026-09-18 ユーザー決定: 積み構築は交代しない・
# 壁で耐久を補う → 軸ごとに役割の構造を持ち、S4 で軸 × 分岐の core を出し、S5 で役割の最小数を制約、S6 で技・持ち物を保証)
BUILD_ARCHETYPES = True                    # run.py --archetypes on|off の既定
BUILD_ARCHETYPE_CORES_PER_BRANCH = 2       # 分岐ごとに S4 へ出す core の数
BUILD_ARCHETYPE_MIN_FIT = 0.35             # 環境適合 (0..1) がこれ未満の分岐は core を出さない (記録だけ残す)
BUILD_ARCHETYPE_ROLE_TOP = 4               # 役割ごとに core の組み合わせに使う候補数
BUILD_ARCHETYPE_LLM_ROUNDS = 11            # LLM を軸ごとに回す上限 (coverage 停止あり)
# 軸ごとの framing では、全部の軸を 1 回ずつ回し終えるまで coverage 停止 (新系統の割合 / 重複率) を効かせない。
# 2026-09-24 の回帰測定で Opus 5.5 が stall の軸で重複 75% を出し、残り 4 軸 (hyper_offense / priority_bulky / anti_meta / special)
# を回さずに止まった。軸は互いに独立なので、1 軸の重複で他の軸を打ち切る理由が無い
BUILD_ARCHETYPE_FULL_PASS = True
BUILD_ARCHETYPE_SPECIAL_MAX_CORES = 3      # 特殊な勝ち筋 (ほろびのうた等) は全分岐で合計この数まで (一覧・候補を膨らませない)
BUILD_ARCHETYPE_SPECIAL_HYBRIDS = 3        # 特殊な勝ち筋を他の軸の core に 1 役足した併用案 (special_branch) の数 (ユーザー決定 9/18)
# 構築の LLM 呼び出し (tools/team_build/llm/provider.py、claude CLI ヘッドレス)。2026-09-24: docs/TECH_WATCH_2026-09.md §A
# - tier → モデル id。切替は固定入力の regression (python -m tools.team_build.llm.regression) で測ってから
BUILD_LLM_MODELS = {"opus": "claude-opus-5", "sonnet": "claude-sonnet-5", "haiku": "claude-haiku-4-5-20251001"}
# - 段ごとのモデル (tier の対応を上書き。空 = tier のまま)。2026-09-24 の回帰測定 (arch_0918 の固定入力、11 軸):
#   S4 は Opus 5 既定で LLM 系統 71 / 74 ($8.5、28 分)、Opus 5.5 既定で 59 ($5.9、10 分、重複が増える)、
#   Opus 5.5 xhigh で 82 ($10.6、44 分、重複ほぼ 0) → S4 だけ 5.5 + xhigh にして run arch_0924 で確認。
#   2026-09-25 の結果: 系統 73 (Opus 5 の 71 と同水準)、下流 (holdout) でも同じ環境の前回 1 位と判別できる差なし、費用 +$2.2 /
#   時間 +14 分 → Opus 5 既定に戻す (docs/TECH_WATCH_2026-09.md §A-2)。5.5 xhigh に戻すなら {"s04_concepts": "claude-opus-5-5"} と
#   BUILD_LLM_EFFORT["s04_concepts"] = "xhigh"。介入仮説 (opus) は未測定なので据え置き
BUILD_LLM_STAGE_MODELS = {}
# - 段ごとの effort (claude CLI --effort: low / medium / high / xhigh / max。None = CLI の既定)。
#   S13 記事は Sonnet 5 の既定 119 秒 / $0.23 と low 38 秒 / $0.15 で本文の長さは同じだが low は個体ごとの小見出しが落ちる → 既定のまま
BUILD_LLM_EFFORT = {"s04_concepts": None, "s13_report": None, "interventions": None, "articles": None}
# - 1 呼び出しの費用上限 (USD、CLI --max-budget-usd)。再試行の暴走に対する保険。通常の S4 呼び出しは $1 前後 (Opus 5 実測)
BUILD_LLM_MAX_BUDGET_USD = 5.0
# 記事バンクの前段 (tools/team_build/articles_ingest): シーズン → その季節の規制 (合法性の検証に使う)。確かな対応だけ載せる
BUILD_ARTICLE_SEASON_REGULATION = {"M-1": "gen9championsbssregma", "M-2": "gen9championsbssregmb", "M-3": "gen9championsbssregmb",
                                   "M-4": "gen9championsbssregmb", "M-5": "gen9championsbssregmb", "M-6": "gen9championsbssregmc"}
                                   # 2026-10-05 判断 #9: M-1 = M-A (題名の表記 4 件)、M-2〜M-5 = M-B (M-5 は手順書)、M-6 = M-C (9/9 から)。MCS だけの行は unknown
BUILD_ARTICLE_DOUBLE_WORDS = ("ダブル", "double", "vgc")   # 記事の題名にあればダブルの記事 (記事バンクから除く)
# - CLI に載せるツール。空 = ツール定義を system prompt に載せない (9/24 実測: 12.8k トークン。不許可リスト方式は 24.7k)
BUILD_LLM_CLI_TOOLS = ""
# 視覚監査 (tools/audit_subtask, audit_session) のモデル。8/18 に haiku / sonnet / opus を同一フレーム 30 枚で比較して opus に固定、
# 9/24 に同一の 5 対戦 20 枚で claude-opus-5 (331 秒) と claude-opus-5-5 (112 秒) を比較: 主要な乖離 (7 匹化、ひんし後の HP
# 再表示、HP の誤読、シーン誤判定) は両方が検出し、5.5 は技欄・メガ表示の記述がより具体的で幻覚なし → 安い 5.5 に切替
AUDIT_MODEL = "claude-opus-5-5"
# 測定の相手プール (S2、tools/team_build/opponents.py)。ranked = POOL_PIN の上位ランカー構築 (従来)、
# latest = 最新の使用率スナップショットの全種から「使用率% ∪ ゲーム内順位」の重みと共起 (teammate_usage) で合成
# (2026-09-18 ユーザー決定: ブラックリストなしの最新ポケモン全体。上位構築に載らない今期の主役も相手に出る)
BUILD_POOL_SOURCE = "latest"               # latest = 使用率からの合成 / ranked = POOL_PIN の上位構築 / mixed = 実戦で当たった構築 (選出画面で 6 体
                                           # 読めて整合した対戦) を先に入れ、足りない分だけ合成 (2026-10-05 判断 #1。型は合成のまま)
BUILD_POOL_REAL_DAYS = 90                  # mixed: 実戦ログをこの日数以内に限る
BUILD_POOL_REAL_MIN_N = 1                  # mixed: 構築 (6 体の組) をプールに入れる遭遇回数の下限
BUILD_POOL_MATCH_THRESHOLD = 0.5           # 構築単位の一致率 (env_match): 実戦の相手がプールの構築と「同じ」とみなす重なり (Jaccard) の下限
BUILD_POOL_MATCH_MIN_N = 20                # 一致率を目安として読む整合した実戦の最小数 (mixed で入れた対戦を除くと 4 戦しか残らない等。judgement は sufficient を見る)
BUILD_POOL_TEAMMATE_MIX = 0.5              # 合成の 2 体目以降: (1 − mix) × 種の重み + mix × 選んだ種との共起
BUILD_ARCHETYPE_TR_SPEED_SHARE = 0.3       # トリックルームのエース: 上位脅威への先手率がこれ以下
BUILD_ARCHETYPE_FAST_SPEED_SHARE = 0.6     # 速攻役: 先手率がこれ以上 (タスキ / スカーフでも可)
BUILD_ARCHETYPE_WALL_BULK = 0.45           # 受け役の耐久 (S3 roles.bulk)
BUILD_ARCHETYPE_PRIORITY_BULK = 0.35       # 先制技持ちの高火力のうち耐久を求める側
BUILD_ARCHETYPE_FAST_THREAT_SPE = 100      # トリックルーム / 先制技の適合: 素早さ種族値 (メガ後) がこれ以上の脅威
BUILD_ARCHETYPE_THREAT_MOVE_PCT = 20.0     # 脅威が技 / 特性を「持つ」と数える使用率 (%)
BUILD_ARCHETYPE_ANSWER_COVERAGE = 0.6      # 環境上位への回答: 上位 5 脅威への被覆
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
                         "fly", "dig", "dive", "bounce", "phantomforce", "shadowforce", "skydrop",
                         # きのみを食べた後しか出せず 1 試合に実質 1 回 (2026-10-02 ユーザー指摘: ガラルヤドキングの生成型に入った)
                         "belch")
# 生成型の補助技の優先 (2026-10-02 ユーザー指摘: ウルガモスの生成型にちょうのまいでなくめいそうが入った。役割辞書
# (advisor.search.SETUP_MOVES) の並び順をそのまま先頭から採っていた)。役割ごとに、使用率がある種は使用率が
# UTILITY_USAGE_MIN % 以上の技を使用率順で先に、残り (使用率が無い種は全部) の積み技は能力変化の段数の重みつき和で並べる:
#   主能力の上昇 × attack (攻撃型は型の分類の攻撃、壁型は守る側: wall_physical = 防御 / wall_special = 特防)、
#   素早さの上昇 × speed_fast (速攻型) / speed_bulky (耐久型)、他の上昇 × other、下降 × down (引く)。
#   攻撃型で主攻撃が上がらない積み技 (こうそくいどう等) は最後。他の役割は使用率順の後は辞書の順のまま
BUILD_GEN_UTILITY_USAGE_MIN = 10.0
BUILD_GEN_SETUP_RANK = {"attack": 1.0, "speed_fast": 1.0, "speed_bulky": 0.5, "other": 0.25, "down": 0.25}
# advisor.search.SETUP_MOVES に無い積み技の能力変化 (生成型の並べ替えだけに使う。助言の探索の辞書は変えない)
BUILD_GEN_SETUP_BOOSTS = {"rockpolish": {"spe": 2}, "autotomize": {"spe": 2}, "shiftgear": {"atk": 1, "spe": 2},
                          "tidyup": {"atk": 1, "spe": 1}, "geomancy": {"spa": 2, "spd": 2, "spe": 2},
                          "flamecharge": {"spe": 1}, "trailblaze": {"spe": 1}, "rapidspin": {"spe": 1},
                          "aquastep": {"spe": 1}}
# 型の常識フィルタ (sets.set_sanity): きのみを食べた後しか出せない技は、きのみ以外の持ち物と組ませない
BUILD_SET_BERRY_MOVES = ("belch",)
BUILD_SET_LINT_GATE = True             # 型の常識規則 (tools/team_build/set_lint、2026-10-05 判断 #14): 誤り (性格と技 / 持ち物 / 場の重複 /
                                       # 技 4 つ未満) の型は生成の最終検査で落とす。警告 (タイプ一致技の欠落) は記録だけ。False なら数えるだけ
# こだわり系の持ち物と組ませない変化技のうち積み技以外 (設置 / 回復 / まもる / みがわり)。積み技は sets.setup_move_ids
# (advisor/data/boost_moves.json + advisor.search.SETUP_MOVES) から作る (2026-10-02: 手書きの一覧の漏れをなくした)
BUILD_SET_CHOICE_LOCK_MOVES = ("stealthrock", "spikes", "toxicspikes", "stickyweb",
                               "roost", "recover", "slackoff", "softboiled", "milkdrink", "shoreup", "moonlight",
                               "morningsun", "synthesis", "strengthsap", "rest", "wish",
                               "protect", "detect", "banefulbunker", "spikyshield", "burningbulwark", "silktrap",
                               "substitute")
# ---- 役割の雛形 (docs/TEAM_BUILD_REDESIGN_1002.md §6、2026-10-02): 役割 → 特性・持ち物・技・性格・配分を一体で作る
# (tools/team_build/role_sets.py)。attacks = 攻撃技の本数、utility = 補助枠の種類 (順に埋める。"a|b" は先に見つかった方)、
# items = 持ち物のクラスの順 (BUILD_ITEM_CLASSES、§10)、spread = 配分の方針 (fast / bulky / auto = 担当への先手率で決める /
# wall_physical / wall_special / wall_auto = 被ダメの偏りで決める / tr = 素早さ 0 で下を取る / tr_bulky)、
# setup_kind = 積み技で上げる側 (offense / defense / none)、ability_tags = 特性の適合で優先するタグ (§9.2、ability_effects.json の
# tags)、offensive = 攻撃技の被覆 (担当) で評価する役割か、field_moves = 天候・フィールド依存技を並びの始動源があれば許すか
BUILD_ROLE_TEMPLATES = {
    "sweeper_setup":   {"attacks": 3, "utility": ("setup",), "items": ("setup_berry", "sash", "orb", "stone"), "spread": "auto",
                        "setup_kind": "offense", "ability_tags": ("offense", "speed", "setup", "immunity", "defense", "contact_punish"),
                        "offensive": True},
    "breaker":         {"attacks": 4, "utility": (), "items": ("choice", "orb", "stone", "type_item"), "spread": "auto",
                        "setup_kind": "none", "ability_tags": ("offense", "type_change", "immunity", "defense", "contact_punish"),
                        "offensive": True},
    "cleaner":         {"attacks": 3, "utility": ("priority",), "items": ("sash", "orb", "scarf", "stone"), "spread": "fast",
                        "setup_kind": "none", "ability_tags": ("speed", "offense", "priority", "type_change", "immunity", "contact_punish"),
                        "offensive": True},
    "tr_ace":          {"attacks": 4, "utility": (), "items": ("orb", "choice_power", "stone", "type_item"), "spread": "tr",
                        "setup_kind": "none", "ability_tags": ("offense", "slow_offense", "immunity", "defense"),
                        "offensive": True},
    "weather_ace":     {"attacks": 3, "utility": ("setup|protect|pivot",), "items": ("orb", "sash", "stone", "choice"), "spread": "auto",
                        "setup_kind": "offense", "ability_tags": ("weather_user", "offense", "speed", "defense"), "offensive": True,
                        "field_moves": True},
    "terrain_ace":     {"attacks": 3, "utility": ("setup|protect|pivot",), "items": ("orb", "sash", "stone", "choice"), "spread": "auto",
                        "setup_kind": "offense", "ability_tags": ("terrain_user", "offense", "speed", "defense"), "offensive": True,
                        "field_moves": True},
    "hazard_lead":     {"attacks": 2, "utility": ("hazard", "status|protect|phaze"), "items": ("sash", "helmet", "leftovers"),
                        "spread": "wall_auto", "setup_kind": "none", "ability_tags": ("immunity", "defense", "hazard"), "offensive": False},
    "hazard_removal":  {"attacks": 2, "utility": ("removal", "heal|pivot"), "items": ("leftovers", "helmet", "berry_heal"),
                        "spread": "wall_auto", "setup_kind": "none", "ability_tags": ("regenerator", "defense", "immunity"), "offensive": False},
    "speed_control":   {"attacks": 2, "utility": ("speed_control", "status|protect|hazard"), "items": ("mental_herb", "sash", "leftovers"),
                        "spread": "tr_bulky", "setup_kind": "none", "ability_tags": ("priority", "defense"), "offensive": False},
    "weather_setter":  {"attacks": 2, "utility": ("field", "pivot|status|heal"), "items": ("weather_rock", "leftovers", "sash"),
                        "spread": "wall_auto", "setup_kind": "none", "ability_tags": ("weather_setter", "defense"), "offensive": False},
    "terrain_setter":  {"attacks": 2, "utility": ("field", "pivot|status|heal"), "items": ("terrain_extender", "leftovers", "sash"),
                        "spread": "wall_auto", "setup_kind": "none", "ability_tags": ("terrain_setter", "defense"), "offensive": False},
    "pivot":           {"attacks": 2, "utility": ("pivot", "heal|status|hazard"), "items": ("leftovers", "helmet", "scarf"),
                        "spread": "wall_auto", "setup_kind": "none", "ability_tags": ("regenerator", "intimidate", "immunity"), "offensive": False},
    "wall":            {"attacks": 2, "utility": ("heal", "status|protect|phaze"), "items": ("leftovers", "helmet", "berry_heal"),
                        "spread": "wall_auto", "setup_kind": "defense", "ability_tags": ("regenerator", "wall", "defense", "immunity"), "offensive": False},
    "status_spreader": {"attacks": 2, "utility": ("status", "heal|protect"), "items": ("leftovers", "berry_heal", "sash"),
                        "spread": "wall_auto", "setup_kind": "none", "ability_tags": ("defense", "immunity", "priority"), "offensive": False},
    "support_screens": {"attacks": 1, "utility": ("screens", "screens", "selfko|pivot|status"), "items": ("lightclay",),
                        "spread": "wall_auto", "setup_kind": "none", "ability_tags": ("priority", "speed", "defense"), "offensive": False},
    "support_veil":    {"attacks": 2, "utility": ("auroraveil", "field|status|pivot"), "items": ("lightclay",),
                        "spread": "wall_auto", "setup_kind": "none", "ability_tags": ("weather_setter", "defense"), "offensive": False},
    "phazer":          {"attacks": 2, "utility": ("phaze", "heal|hazard"), "items": ("leftovers", "helmet"),
                        "spread": "wall_auto", "setup_kind": "none", "ability_tags": ("defense", "immunity"), "offensive": False},
    "trapper":         {"attacks": 2, "utility": ("trap", "status|setup|protect"), "items": ("leftovers", "sash"),
                        "spread": "wall_auto", "setup_kind": "offense", "ability_tags": ("trapper", "defense"), "offensive": True},
    "suicide_lead":    {"attacks": 1, "utility": ("hazard|status", "status|protect", "selfko|destinybond|pivot"), "items": ("sash",),
                        "spread": "fast", "setup_kind": "none", "ability_tags": ("speed", "priority"), "offensive": False},
}
# 軸 (archetypes.ROLE_SPECS) と S4 の役割 id → 雛形。天候・フィールドの始動役 / エース (sun_setter / psychic_abuser 等) は接尾辞で解決する
BUILD_ROLE_ALIASES = {
    "setup_ace": "sweeper_setup", "self_booster": "sweeper_setup", "baton": "sweeper_setup",
    "partner": "breaker", "answer": "breaker", "receiver": "breaker", "ohko_user": "breaker",
    "fast_attacker": "cleaner", "priority_attacker": "cleaner", "priority_bulky": "cleaner", "sucker": "cleaner",
    "steel_priority": "cleaner",
    "tr_setter": "speed_control", "tailwind": "speed_control", "web_setter": "hazard_lead",
    "rocks_setter": "hazard_lead", "spikes_setter": "hazard_lead", "hazard_setter": "hazard_lead",
    "lead_support": "suicide_lead", "destiny_bond": "suicide_lead", "endeavor_sash": "suicide_lead",
    "regen_wall": "wall", "counter_user": "wall", "curse_ghost": "wall", "leech_seeder": "wall", "wall_physical": "wall",
    "wall_special": "wall",
    "status_user": "status_spreader", "screens_dual": "support_screens", "screens_veil": "support_veil",
    "snow_source": "weather_setter", "perish_singer": "trapper", "hazard_removal": "hazard_removal",
}
# 補助枠の種類 → 技の候補 (順 = 同評価のときの選好。状態異常は担当の物理/特殊の偏りで並べ替える)
BUILD_ROLE_UTILITY_MOVES = {
    "hazard": ("stealthrock", "spikes", "stickyweb", "toxicspikes"),
    "removal": ("rapidspin", "defog", "tidyup", "mortalspin"),
    "status": ("spore", "willowisp", "thunderwave", "yawn", "toxic", "glare", "sleeppowder", "stunspore", "nuzzle", "hypnosis"),
    "heal": ("recover", "roost", "slackoff", "softboiled", "milkdrink", "shoreup", "synthesis", "moonlight", "morningsun",
             "strengthsap", "wish", "rest"),
    "protect": ("protect", "detect", "spikyshield", "banefulbunker", "burningbulwark", "silktrap", "kingsshield"),
    "pivot": ("uturn", "voltswitch", "flipturn", "partingshot", "teleport", "chillyreception", "batonpass"),
    "priority": ("suckerpunch", "bulletpunch", "shadowsneak", "aquajet", "extremespeed", "machpunch", "iceshard", "quickattack",
                 "accelerock", "vacuumwave", "firstimpression", "jetpunch", "thunderclap", "grassyglide", "upperhand"),
    "screens": ("reflect", "lightscreen"),
    "auroraveil": ("auroraveil",),
    "speed_control": ("trickroom", "tailwind", "stickyweb", "thunderwave", "icywind", "electroweb"),
    "phaze": ("roar", "whirlwind", "dragontail", "circlethrow", "haze"),
    "trap": ("meanlook", "block", "jawlock", "spiritshackle", "anchorshot", "thousandwaves", "octolock", "spiderweb"),
    "selfko": ("explosion", "selfdestruct", "mistyexplosion", "memento", "finalgambit", "healingwish"),
    "destinybond": ("destinybond",),
    "trick": ("trick", "switcheroo"),
}
BUILD_ROLE_FIELD_MOVES = {"sun": ("sunnyday",), "rain": ("raindance",), "sand": ("sandstorm",), "snow": ("snowscape", "chillyreception"),
                          "psychic": ("psychicterrain",), "grassy": ("grassyterrain",), "electric": ("electricterrain",),
                          "misty": ("mistyterrain",)}
# 持ち物のクラス → 候補 (順 = 選好。使用率 (pokedb の種 × 持ち物) があればその順を先に)。choice は攻撃技の分類で決める
BUILD_ITEM_CLASSES = {
    "setup_berry": ("sitrusberry", "lumberry"), "sash": ("focussash",), "orb": ("lifeorb",),
    "choice": ("choiceband", "choicespecs", "choicescarf"), "choice_power": ("choiceband", "choicespecs"), "scarf": ("choicescarf",),
    "leftovers": ("leftovers",), "helmet": ("rockyhelmet",), "berry_heal": ("sitrusberry",), "lightclay": ("lightclay",),
    "mental_herb": ("mentalherb",), "terrain_extender": ("terrainextender",), "white_herb": ("whiteherb",), "chesto": ("chestoberry",),
    "weather_rock": ("heatrock", "damprock", "smoothrock", "icyrock"), "stone": (), "type_item": (),
}
BUILD_WEATHER_ROCKS = {"sun": "heatrock", "rain": "damprock", "sand": "smoothrock", "snow": "icyrock"}
BUILD_TYPE_ITEMS = {"Normal": "silkscarf", "Fire": "charcoal", "Water": "mysticwater", "Electric": "magnet", "Grass": "miracleseed",
                    "Ice": "nevermeltice", "Fighting": "blackbelt", "Poison": "poisonbarb", "Ground": "softsand", "Flying": "sharpbeak",
                    "Psychic": "twistedspoon", "Bug": "silverpowder", "Rock": "hardstone", "Ghost": "spelltag", "Dragon": "dragonfang",
                    "Dark": "blackglasses", "Steel": "metalcoat", "Fairy": "fairyfeather"}
# 配分の定型 (役割の雛形の spread)。能力ポイント (0-32) "hp/atk/def/spa/spd/spe"
BUILD_ROLE_SPREADS = {
    "fast_physical": ("2/32/0/0/0/32", ("jolly", "adamant")), "fast_special": ("2/0/0/32/0/32", ("timid", "modest")),
    "bulky_physical": ("32/32/0/0/0/2", ("adamant",)), "bulky_special": ("32/0/0/32/0/2", ("modest",)),
    "wall_physical": ("32/0/32/0/2/0", ("impish", "bold")), "wall_special": ("32/0/0/0/32/2", ("careful", "calm")),
    "tr_physical": ("32/32/2/0/0/0", ("brave",)), "tr_special": ("32/0/2/32/0/0", ("quiet",)),
    "tr_bulky_physical": ("32/0/32/0/2/0", ("relaxed",)), "tr_bulky_special": ("32/0/2/0/32/0", ("sassy",)),
}
BUILD_SET_CANDIDATES_PER_ROLE = 3    # 種 × 役割ごとの候補型の上限 (雛形の生成型 + 使用率の型)
BUILD_WALL_SPEED_MAX = 95            # 素早さ種族値がこれを超える種は壁型の候補にしない (D-17。95 のグライオンは入る。耐久が BUILD_WALL_FAST_BULK_MIN
                                     # 以上なら超えても入る。使用率の壁型は一体の候補として入る)
BUILD_BULK_POINTS_MIN = 24           # HP または防御側への投資がこれ以上なら「耐久」(安定技を優先。D-03)
BUILD_FAST_POINTS_MIN = 24           # 素早さへの投資がこれ以上で耐久投資が無ければ「速攻」(1 発化のデメリット技を許す)
BUILD_OHKO_BONUS = 0.5               # 担当する相手を 2 発 → 1 発にできる技への加点 (被覆 1 体分の半分。D-04)
BUILD_OHKO_THREAT_MIN_W = 0.3        # 1 発化の加点を数える相手の重みの下限 (最大を 1 に正規化)
BUILD_DEMERIT_NEED_MIN = 0.15        # 耐久型でデメリット技を許す「安定技だけでは担当に空く穴」の最小 (被覆の差)
BUILD_ROLE_FAST_SHARE = 0.5          # spread=auto: +Spe 振り切りで担当の半分以上に先手なら fast、でなければ bulky
# 並びと型の同時探索 (S5 統合段。docs/TEAM_BUILD_REDESIGN_1002.md §5 / §16.2。初期値は実測で見直す: D-27)
BUILD_SEARCH_MODE = "joint"          # joint = 核の型を同時に決め補完を順に足す (S5 統合段) / legacy = 従来の S5 (種の並び) → S6 (型)
BUILD_CORE_BEAM = 3                  # 核の型の組 (≤ 27) のうち残す数
BUILD_COMPLEMENT_BEAM = 3            # 補完 1 枠ごとに残す並びの数 (純粋な貪欲の詰まりを避ける最小幅)
BUILD_COMPLEMENT_SPECIES_K = 24      # 補完 1 枠で型まで作る種の数 (S3 の特徴で穴の相手への被覆が高い順 + 未充足の役割を満たせる種)
BUILD_COMPLEMENT_ROLE_SPECIES_K = 6  # そのうち、未充足の役割 1 つにつき役割を満たせる種を先頭に入れる数
BUILD_LINEUP_MAX_MEGA_STONES = 1     # 同時探索の並びのメガ石の上限 (1 試合に 1 体。構想の mega_id が持ち、他の個体の石持ち型は候補にしない)
BUILD_WEATHER_CONFLICT_PENALTY = 0.05  # 並びの始動源 (天候 / フィールド) が 2 系統衝突するときの減点 (被覆 0.05 相当。禁止ではない)
BUILD_ROLE_FULFIL_BONUS = 0.05       # 構想・規則が要求する役割を全部満たした並びの加点 (被覆 0.05 相当。充足率に比例)
BUILD_ROLE_OFFENSE_MIN = 90          # 役割の指定が無い種に攻撃役 (breaker / sweeper_setup) を試す攻撃種族値 (A か C) の下限
BUILD_ROLE_WALL_OFFENSE_MAX = 110    # 役割の指定が無い種に壁役を試す攻撃種族値の上限 (回復技を覚えるとき)
BUILD_ARCHETYPE_SPEED_PLAN = {"trick_room": "trick_room", "speed": "outspeed"}   # 軸 → 速度の計画 (他は neutral)
BUILD_JOINT_REFINE_TARGETS = True    # 仕上げ: 並びが決まった後、各個体の型を担当 (選出計画の相手) に合わせて作り直す (点が上がるときだけ。D-04)
BUILD_SELFKO_COST = 1.0              # 自爆・捨て技 (だいばくはつ / おきみやげ / みちづれ / いのちがけ) の費用 (§12): 被覆の行では 1 回だけ数え (最も効く相手 1 体にだけ技の利得を足す)、利得をその個体の他の相手への被覆の平均 × この値だけ割り引く
# 2026-10-04 run 1003 の点検 (並びの評価と選出 / 型と役割) への対処。設計文書 §5.3 の「役割の充足」「重複の減点」の実装
BUILD_UTILITY_BONUS = {"hazard": 0.03, "removal": 0.02, "priority": 0.02, "speed_control": 0.02}   # 並びに設置 / 除去 / 先制 / 速度操作が
                                       # あることの価値 (被覆相当。種類ごとに 1 回。1003 の 79 並びのうち 25 は 4 つとも無かった)
BUILD_MAX_ATTACKERS = 4                # 攻撃役 (breaker / sweeper_setup / cleaner / tr_ace / weather_ace / terrain_ace) の数がこれを超えると減点
BUILD_ATTACKER_EXCESS_PENALTY = 0.03   # 超過 1 体あたりの減点 (1003: 79 並びのうち 65 が攻撃役 4 体以上)
BUILD_DUP_ROLE_PENALTY = 0.02          # 同じ仕事の個体 (同じ雛形 × 同じ速度帯 (配分の fast / bulky / tr)) が 2 体以上あるとき 1 組あたりの減点
BUILD_TRIO_MIX_BONUS = 0.03            # 3 体選出に攻撃役と補助・受け役の両方が入る系統の値への加点 (役割の充足: 先発・勝ち筋・受けが揃うか)
BUILD_CALIBRATION_SLOTS = 4            # 較正の標本 (2026-10-05 判断 #10): S5 が生成して保持しなかった並びから層化抽出でこの数を S8a だけ測る
                                       # (昇格・修理には使わない。上位だけを測る選択バイアスを避ける)。0 で無効
BUILD_CALIBRATION_STRATA = 4           # 層化の層数 (代理の点の分位)
BUILD_CALIBRATION_VARIANTS = ("cheap",)  # 較正の標本は軽い適応の腕だけ測る (較正に使うのはその Δ だけ。3 変種なら +1.5 時間、1 変種なら +0.7 時間。判断 #4)
# 代理評価の採否の検定 (計画書 §3.1 / §3.2。experiments/learned_surrogate の層化と帰無分布、2026-10-05):
BUILD_SURROGATE_MIN_GAIN = 0.2         # 学習の代理を探索の主項にする条件: 探索の並びでの run 内順位相関の中央値が被覆より これ以上 高い
BUILD_NULL_ALPHA = 0.05                # 並べ替え検定の有意水準 (「帰無分布の 95 点を超える」= 片側 p < 0.05)
BUILD_NULL_PERMUTATIONS = 2000         # 帰無分布の並べ替えの回数 (run 内で Δ を入れ替える。p の刻みは 1 / 回数)
BUILD_SPECIES_SHARE_MAX = 0.5          # 保持する並びのうち同じ種が入る割合の上限 (固定枠・エースは除く。1003: カイリューが 79 並び全部に入った)
BUILD_LOCK_IMMUNE_DISCOUNT = 0.5       # こだわり系 + 数ターン固定の技 (げきりん等) の型: その技を無効にする種が居る系統の相手への被覆を
                                       # この割合だけ割り引く (スカーフげきりんの技固定とフェアリー無効を計算が見ていなかった)
BUILD_PLAN_PRIOR = "off"               # 選出計画 (S5 の selection_plan) を選出モデルの初期値にするか: off (既定、2026-10-05 ユーザー判断 #25:
                                       # 未測定のまま既定 on にしない) / on (測定と適応の収集で使う) / ab (S8b に fresh_plan の腕を足し、
                                       # 同じ並び・同じモデルで 計画あり vs なし を同一相手列で対応比較する。勝ってから on)
BUILD_PLAN_PRIOR_MIX = 0.3             # 計画の 3 体と一致する選出の予測勝率に足す重み (モデルが無い / 分布外のときは計画そのものを使う)
BUILD_PLAN_EXPLORE_SHARE = 0.5         # (on のとき) cheap adaptation の収集で、探索枠 (--explore) のうち計画の選出を使う割合 (残りは乱択)
BUILD_ROLE_SUPPORT_BULK_MIN = 7000     # 受け・設置除去・吹き飛ばし・技だけの始動役の適性: 種族値の HP × 防御 か HP × 特防 がこれ以上
                                       # (リザードン 78×85=6630 は外れ、エンブオー 110×65=7150 は攻撃種族値の上限で外れる)
BUILD_WALL_FAST_BULK_MIN = 9000        # 素早さ種族値が BUILD_WALL_SPEED_MAX を超えても、耐久 (HP × 防御 か HP × 特防) がこれ以上なら壁の候補にする
BUILD_ITEM_FALLBACK = ("leftovers", "lifeorb", "sitrusberry", "lumberry", "focussash", "expertbelt", "rockyhelmet")   # カゴのみは ねむる の型だけ (常識規則)
                                       # 雛形のクラスの持ち物が並びで全部使用済みのときの予備 (持ち物なしの型を作らない。それも尽きれば候補なし)
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
# 特性ごとのエースの持ち物 (2026-09-11 ユーザー指摘: かるわざのエースにタスキは要らない。発動させる消耗品で良く、タスキは
# 他に回せる)。かるわざ = しろいハーブ (自分の能力を下げる技: インファイト等) / ノーマルジュエル (ノーマル攻撃技) / タスキ。
# 実データ (M-B 末、オオニューラ): しろいハーブ 37.9% > タスキ 26.7% > オボン 16.8%、ノーマルジュエルは上位 10 に無い
# M-C の実データ (9/12、オオニューラ): タスキ 25.6% > オボン 20.3% > サイコシード 18.4% (サイコフィールドで消費 → かるわざ発動。
# 規則のフィールドと噛み合う) > しろいハーブ 4.4%。シード類は並びの場 (規則の前提) が一致するときだけ使える
BUILD_RULE_ACE_ITEMS_BY_ABILITY = {"unburden": ("psychicseed", "electricseed", "grassyseed", "mistyseed",
                                                "whiteherb", "normalgem", "focussash")}
BUILD_UNBURDEN_TRIGGERS = {"whiteherb": "self_stat_drop", "normalgem": "normal_attack",
                           "psychicseed": "terrain:psychic", "electricseed": "terrain:electric",
                           "grassyseed": "terrain:grassy", "mistyseed": "terrain:misty"}   # 発動条件
# メガ石の所持数 (2026-09-11 ユーザー指摘で調査): ランク上位 200 構築の実測は 0 個 1% / 1 個 33% / 2 個 61% / 3 個 4% / 4 個 0.5%。
# 以前は「1 試合 1 回のメガシンカ」を「1 構築 1 個の石」と取り違えて 1 個に制限していた。石が 2 個までは冗長の罰則なし、上限 3
BUILD_MAX_MEGA_STONES = 3
BUILD_MEGA_FREE_STONES = 2
# 指定エース (BuildSpec.ace、run.py --ace): エースがメガ石を持てる種なら、探索の並びではエースだけが石を持つ (他のメンバーは
# メガ石以外の最良代替)。「エース = 固定枠 + その構築の唯一のメガ」という読み (2026-10-02 ユーザー依頼「メガミミロップをエースと
# する構築」)。2 個目の石を許すならここを増やす。現行チーム枝と参照 (登録の型) には適用しない
BUILD_ACE_MAX_MEGA_STONES = 1
BUILD_THEME_ACE_PICK_MIN = 0.5         # テーマの検査 (tools/team_build/theme_check): 指定エースの測定での選出率がこれ未満の並びは「テーマを満たさない」
BUILD_THEME_GATE = False               # True なら S10 で、テーマを満たす並びが最良の並びと同等 (Δ の差 ≤ BUILD_EQUIV_EPS) のときだけ勝者を入れ替える
                                       # (判断 #3: 単純に外すと 1002d・1003 とも現行より勝率の低い並びが勝者になる。閾値 0.5 は据え置き、門は off)。
                                       # 2026-10-05: まず 1 run 記録してから on にする (判断 #4)
# 1 試合 1 回の資源 (メガシンカ) の推定 (advisor/gimmick.py。2026-09-11 ユーザー指摘「相手がメガ先を読まないのは致命的」):
# 相手の種族ごとの「メガ石を持つ確率」は使用率 DB の石の使用率。石を持てるが使用率が無い種の既定値と、無視する下限
GIMMICK_DEFAULT_STONE_PRIOR = 0.5
GIMMICK_MIN_PRIOR = 0.02
# 助言の探索: 相手がこの確率以上でメガシンカし得るなら「技 + メガシンカ」の分岐を相手の行動候補に加える
SEARCH_OPP_MEGA_MIN_PROB = 0.2
# 選出モデルの特徴量の版 (agent/selection_dispatch): "v1" = 種族埋め込みのみ (配布中)、"v3" = メガシンカ込み。
# 構築の相手の選出 (apply_model_teampreview)、測定の助言の選出 (advisor_pick_order)、候補専用モデルの適応が従う。
# v3 への切替は tools/compare_selection_features の門 (未知チームの MSE 改善・対応比較の順位精度が v1 に劣らない) を通してから
SELECTION_FEATURES = "v1"
# 実戦の選出助言の第一候補 (◎): True なら、登録チーム用の検証済みモデル (試用 Package) か分布内の配布版の推しを第一候補にし、
# 相性の規則の推奨は参考に併記する (advisor.selection.choose_primary)。False なら従来 (規則が ◎、モデルは併記)。
# 2026-10-05: 実戦の 9 戦で推奨とモデルの推しが一致した対戦は 0、測定はモデルの選出で測っているのに実戦は規則で選んでいた
SELECTION_PRIMARY_MODEL = True
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
