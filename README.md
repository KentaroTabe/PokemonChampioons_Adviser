# ポケモンチャンピオンズ AIアドバイザー

ポケモンチャンピオンズの **6体の構築提案・3体の選出支援・対戦中の行動助言**を行うシステム。
使用率データと対戦シミュレーションで構築候補を比較し、実戦では iPhone のミラーリング映像から
盤面を復元して、選出・技・交代の候補と理由をブラウザに表示します。ゲーム内の操作はプレイヤーが行います。

## 主な機能

| 場面 | 実装していること | 主な実装 |
|---|---|---|
| 構築を作る・改善する | 固定枠・エース・除外・技の指定から、6体と各個体の型を探索。構築ごとの選出モデルを適応させ、登録チームとシミュレーションで比較 | `tools/team_build/` |
| 3体を選ぶ | 自分と相手のパーティから3体と初手を提案。学習済み選出モデルと相性・ダメージ計算による規則を併用し、使ったモデルと推奨の出所を記録 | `advisor/selection.py`、`champions_agent/agent/selection_model.py` |
| 対戦中に判断する | OCR・画像認識・対戦メッセージから HP、技、交代、天候、能力変化、メガシンカなどを追跡。ダメージ計算、相手の型予測、行動評価から技・交代を提案 | `vision/`、`advisor/` |
| 実戦を振り返る | 勝敗、選出、助言と実行、モデルの版、助言の生成・表示時刻を記録。認識誤り、助言の遅れ、実行不能な推奨を調べる | `battle_logger.py`、`tools/advice_trace.py`、`tools/scene_eval.py` |
| 方策を学習・評価する | ローカルの Pokémon Showdown と poke-env / Stable-Baselines3 による自己対戦、選出モデルの学習、候補の対戦評価 | `champions_agent/` |

構築の成果は、6体の並びだけでなく **型・選出モデル・評価に使った行動方策・環境データの版**を含む
Package として管理します。候補の生成、対戦での測定、実戦での試用・採用は別の段階です。

## 現在の実装範囲 (2026-10-07 時点)

- **行動助言**はダメージ計算と規則による採点が基本です。利用可能な RL モデルがあれば行動確率を加味します。
  探索による読み合いの評価もありますが、最終順位への統合は既定で無効です
  (`advisor/engine.py` の `SEARCH_BLEND = 0.0`)。常に最善手を求められるという意味ではありません。
- **選出助言**はモデルの推奨を第一候補にし、モデルを利用できない場合は規則に戻ります。
  登録チームで未学習の配布モデルを使う場合も、その旨を区別して表示・記録します。
  Package の試用時には同梱の選出モデルを使う経路があります。
- **構築探索の LLM 利用は任意**です。既定は `--llm none`。
  `--llm headless` ではコンセプト生成や説明に利用しますが、合法性の検査・対戦評価・採否の判定はコード側で行います。
- **記事バンク**は、記事からの構造化、型・形態・実数値の検査、手入力、重複・更新の管理まで実装済みです。
  バンクを構築の弱点検査や相手の選出予測へ接続する作業は今後の段階です。対戦中の技・交代の判断には接続しません。
- **シミュレーション成績は実戦勝率とは別の指標**です。シミュレーションは OCR や表示遅延を通らないため、
  実戦ログと正解を付けた局面でも検証します。実戦での勝率改善を確立したシステムではありません。

## セットアップ

主な運用環境は **macOS・Python 3.12・iPhone・OBS Studio・ブラウザ**です。
構築の対戦測定と学習には Node.js / npm とローカルの Pokémon Showdown も使います。
macOS では Apple Vision OCR を使用します。その他の環境向けの OCR 経路もありますが、
以下の起動・運用手順は macOS を前提としています。

リポジトリのルートで実行します。既存の実行環境がある場合は作り直さず、
移行手順を [運用手順](docs/OPERATIONS.md) で確認してください。

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-full.txt

# 使用率 DB と代表的な型・役割データを準備・更新する (ネットワーク接続が必要)
bash champions_agent/scripts/update_usage_db.sh
```

図鑑・日本語名・効果表などの静的データはリポジトリに含まれています。
個人の登録チーム、使用率 DB、学習済みモデル、実戦ログ、記事バンクはローカルで管理するため、
clone だけでは運用中のデータやモデルは揃いません。

### 自分のチームを登録する

[config/my_team.example.json](config/my_team.example.json) を参考に `config/my_team.json` を作成し、
使う個体の種族・フォルム、持ち物、特性、性格、能力ポイント、技を登録します。
能力ポイントはゲーム内の 0〜32 表記です。既存の登録ファイルがある場合は上書きせず編集してください。

登録内容は選出評価・ダメージ計算・素早さ比較に使われます。ファイルの変更は再起動なしで読み直します。
Showdown 形式のチーム本文から取り込む場合は `tools.register_my_team` も使えます。
このツールの `EVs` 行は、本プロジェクトの能力ポイント表記を前提とします。

### シミュレーターを準備する

構築の測定・学習・接続テスト用です。Node.js / npm を用意してから実行します。

```bash
# 初回: Showdown の取得と依存のインストール
bash champions_agent/scripts/setup_showdown.sh

# 起動 (別ターミナル。既定ポート 8100)
bash champions_agent/scripts/setup_showdown.sh --start
```

対戦形式は `champions_agent/config.py` の `TRAINING_BATTLE_FORMAT` で指定します。
現在は `gen9championsbssregmc` (M-C) です。規制を変更するときは、図鑑・使用率・相手プール・モデルも含めて
[レギュレーション変更手順](docs/REGULATION_CHANGE_RUNBOOK.md) を確認します。

## リアルタイム助言を使う

### 映像を接続する

1. iPhone と Mac を有線接続し、OBS の「映像キャプチャデバイス」で iPhone を選びます。
2. ゲーム画面を黒帯なしでキャンバスに合わせます。基本・出力解像度は **1920×1080**、FPS は 10〜30 を目安にします。
   読取領域は相対座標ですが、小さい文字を読むには十分な映像解像度が必要です。
3. OBS の「仮想カメラ開始」を押します。解像度を変更した場合は仮想カメラを再起動します。

### サーバーを起動する

動作確認には、バックエンドとフロントエンドをまとめて起動するスクリプトを使えます。

```bash
bash scripts/start_servers.sh
```

表示されたフロントエンド URL を開き、「カメラ映像取得開始」から **OBS Virtual Camera** を選びます。
選出画面では3体と初手、コマンド・技選択などの画面では技・交代の候補を確認できます。
前景で動かしたサーバーは `Ctrl+C` で停止します。

| サービス | 既定ポート |
|---|---|
| 助言サーバー | 8000 |
| フロントエンド | 3000 |
| Showdown | 8100 |
| 接続テストの操作パネル | 8010 |

助言サーバーとフロントエンドは、既定ポートが他のプロセスに使われていると空きポートへ移動します。
起動時に表示された URL を使ってください。設定は `config/ports.env`、実際の割当は `logs/ports.env` に残ります。

### 実戦で記録・検証する

接続テストには、開始マーカーやフレーム保存を含む専用の起動手順があります。

```bash
bash scripts/start_connection_test.sh
```

繰り返し使う場合は [操作パネルの導入・終了処理](docs/OPERATIONS.md) と
[接続テストのチェックリスト](docs/CONNECTION_TEST_CHECKLIST.md) を参照してください。
**終了処理は実戦相手バンクの更新や構築改善の測定起動も行います。**
`end_connection_test.sh --no-audit` が省略するのは外部 LLM による一括監査で、測定起動は省略しません。

学習は `config/training.env` の `TRAINING_MODE=on_demand` により必要時だけ起動します。
ライブ助言と重い学習・構築測定の同時実行は、CPU 負荷や熱による処理落ちの原因になります。
接続テスト開始時は必要時学習モードの学習ループを止めますが、構築測定の稼働状況も別に確認してください。

## 構築を提案・測定する

ブラウザの **「構築システム (Team × 専用助言方策)」** パネル、または CLI から依頼できます。
固定枠、使わないポケモン、構築の方向性などを指定すると、構築の軸と6体・型の候補を作ります。
CLI ではエース、必要な技、固定する型なども指定できます。

```bash
# 候補の生成まで。LLM と対戦測定を使わない
bash scripts/team_build.sh example_search \
  --favorites ペリッパー --profile fast --stages search --llm none

# 対戦測定まで行う例。Showdown・使用率 DB・評価用モデルの準備後に実行
# 数時間〜十数時間以上かかる場合があるため、ライブ助言と時間を分ける
bash scripts/team_build_nohup.sh example_full \
  --favorites ペリッパー --profile full --stages all --llm none
```

- 毎回除外する種は [config/banned_species.txt](config/banned_species.txt) に1行1体で記載します。
  相手プールからは除外しません。
- `--stages search` は候補生成、`measure` は生成済み候補の測定、`all` は両方を実行します。
  `fast / medium / full` は探索・測定の規模を指定するもので、名前だけで検証済みになるわけではありません。
- 測定では選出モデルの適応、候補の段階的な比較、弱点に応じた修正を行い、条件を満たした候補を最終評価へ進めます。
  探索用・選定用・最終評価用の相手を分け、改善を確認できなければ採用候補なしで終わります。
- 使用率スナップショット・実戦相手バンクの対象範囲・分割の seed は規制ごとに固定します。
  `python -m tools.team_build.season_pin --show` で確認できます。データを更新しても、評価条件を自動で張り替えるわけではありません。
- 出力は `logs/build_search/runs/<run_id>/`。依頼条件、データとモデルの版、候補の型、測定結果、レポートを保存します。
  Package は `logs/registry/` で管理し、試用・採用は `tools.team_build.promote` による明示的な操作で行います。

詳細は [構築システムの実装・評価設計](docs/TEAM_BUILDING_IMPLEMENTATION.md)、
[構築探索の改善計画](docs/TEAM_BUILD_PLAN_1005.md)、[構築の軸](docs/TEAM_BUILD_ARCHETYPES.md) を参照してください。

## 記事データと実戦ログ

記事バンクは、構築・単体の型・採用目的・苦手な相手・条件つき選出を、出典とともに構造化して扱うための基盤です。
ホスト・URL・用途ごとの取得方針、規制、情報不足と矛盾、通常形態とメガ形態を区別します。
新しい取得経路では本文を保存せず、構造化した候補から再取得なしに検査・保存できます。
編集部の推奨と実際の使用例を分け、記事の本数を遭遇頻度として数えません。
取り込み条件と今後の接続方針は [記事バンクの設計](docs/ARTICLE_BANK_DESIGN_1006.md) を参照してください。

実戦の記録は、内部評価と実際の助言を突き合わせるために使います。

```bash
source .venv/bin/activate
python -m tools.analyze_battles --last 10       # 直近の対戦を集計
python -m tools.advice_trace --last 3           # 版 → 状態 → 推奨 → 表示 → 実行可能性
python -m tools.team_build.real_eval           # 試用ラベルを付けた Package の実戦評価
```

`real_eval` は試用中の Package を対象にします。試用ラベルがない場合は `--package <Package ID>` で指定します。
`tools.scene_eval` は正しい状態・選択可能な行動・表示期限を人がラベル付けした局面と比較します。
`tools.advice_replay` による摂動試験は、認識誤りで推奨が変わる感度を調べるもので、助言の正しさそのものの測定ではありません。
記録と指標の定義は [助言の追跡](docs/ADVICE_TRACE_1005.md) を参照してください。

## 学習・開発・検証

強化学習基盤は `champions_agent/` にあり、学習した行動方策を助言や構築の対戦評価で使います。
継続学習の起動・停止は `scripts/start_training.sh` / `scripts/stop_training.sh` を使います。
常駐設定、環境の移行、更新の反映は [運用手順](docs/OPERATIONS.md) にまとめています。

```bash
# CI と同じ、コミット済みデータとモックで実行できるテスト群
bash scripts/ci_tests.sh .venv/bin/python

# 手元の画像で認識を確認 (画像は自分で用意)
python -m tools.run_images /path/to/screenshot.png
python -m tools.debug_zones /path/to/screenshot.png /tmp/zones.png battle
```

CI の成功だけでは、実映像の認識、ローカルモデルの読み込み、実対戦や表示の遅延までは検証できません。
これらは接続テストと実戦ログで別に確認します。

認識が進まない場合は、まずサーバーの受信・処理・破棄フレーム数を確認します。
領域のずれは `tools.debug_zones`、OBS がカメラ一覧に出ない場合は仮想カメラ開始後のブラウザ再起動を試します。
画面は `file://` で直接開かず、起動スクリプトが表示する HTTP の URL から開いてください。

## ドキュメント

- [全体構成・モジュール解説](ARCHITECTURE.md)
- [起動・停止・操作パネル・モデルの運用](docs/OPERATIONS.md)
- [接続テストの手順と実測記録](docs/CONNECTION_TEST_CHECKLIST.md)
- [既知の問題](docs/KNOWN_ISSUES.md)
- [実装と実験の状況](docs/PROJECT_STATUS.md)
- [レギュレーション変更手順](docs/REGULATION_CHANGE_RUNBOOK.md)
- [インシデントと対応の記録](docs/incidents/INDEX.md)

## クレジット

- Battle data provided by [Pokémon Champions Battle Data](https://championsbattledata.com)
- 上位構築データ: [バトルデータベース チャンピオンズ](https://champs.pokedb.tokyo) のオープンデータ
- 静的データ: [PokeAPI](https://pokeapi.co/) / [Pokémon Showdown](https://github.com/smogon/pokemon-showdown)
