# ポケモンチャンピオンズ AIアドバイザー

スマホ版ポケモンチャンピオンズの画面をMacへミラーリングし、対戦状況を画像から抽出して、
リアルタイムに行動アドバイス(技/交代の推奨と理由)を返すシステム。

- 全体構成・モジュール解説: [ARCHITECTURE.md](ARCHITECTURE.md)
- 残タスク・レギュレーション変更時の対応・拡張方針: [ROADMAP.md](ROADMAP.md)
- 状態抽出: `vision/` (シーン分類 / OCR / イベント辞書 / BattleStateV2)
- アドバイス: `advisor/` (SV準拠ダメージ計算 + 使用率DBによる型予測 + 期待値評価)
- 強化学習基盤 (実験用): `champions_agent/` (poke-env + Stable-Baselines3)

---

## 0. セットアップ

```bash
cd PokemonChampioons_Adviser
python3 -m venv .venv                     # 既にある場合は不要
source .venv/bin/activate
pip install -r champions_agent/requirements.txt
pip install easyocr opencv-python fastapi "python-socketio" uvicorn
pip install pyobjc-framework-Vision   # macOS: Apple Vision OCR (推奨・EasyOCRより高精度高速)

# 静的データの生成 (生成済みならスキップ可。ネットワーク必要)
python -m vision.data.fetch_jp_names      # 日本語名→英語ID辞書
python -m advisor.data.fetch_dex          # 種族値/技/タイプ相性

# 使用率DBの構築/更新 (championsbattledata + pokedb 実環境データ)
bash champions_agent/scripts/update_usage_db.sh
```

学習テストには追加で node (>=18) が必要です: `brew install node`

> **ポート構成**: アドバイザーサーバー=8000 / フロントエンド=3000 /
> 学習用Showdown=**8100** (環境変数 `SHOWDOWN_PORT` で変更可)。
> ポートを分けているため**実運用 (ライブアドバイス) と学習は同時に実行できます**。

---

## 1. 実運用テスト (ライブアドバイス)

### 1-1. 事前チェック (実機なしでの動作確認)

ミラーリングを繋ぐ前に、スクリーンショットでパイプラインを検証できます:

```bash
# 単発テスト (どのシーンとして認識され、何が抽出されたかを表示)
python -m tools.run_images images/mini_test/battle_1.PNG

# 対戦の流れを連番で流し込む (状態が累積され、イベントが発火する)
python -m tools.run_images images/battle_screenshot

# 抽出ゾーンのズレ確認 (ゾーン枠を書き込んだ画像を出力)
python -m tools.debug_zones images/mini_test/battle_1.PNG /tmp/zones.png battle
```

ユニットテスト:

```bash
python -m tests.test_events    # メッセージ→イベント解析 (OCR誤読ケース含む)
python -m tests.test_advisor   # ダメージ計算/型予測/アドバイス生成
```

### 1-2. ミラーリング準備 (OBS)

1. iPhoneとMacを**有線**接続 (AirPlayは遅延とノイズでOCR精度が落ちるため非推奨)
2. OBS Studioで「映像キャプチャデバイス」→ iPhoneを選択し、映像がキャンバス全体に
   ぴったり収まるよう調整 (黒帯が入らないように)
3. 設定→映像: 基本/出力解像度とも **1920x1080**、FPSは10〜30
   (**720pでは名前などの小さい文字が潰れてOCRできません**。1080p必須)
4. 「仮想カメラ開始」をクリック (解像度変更後は仮想カメラを停止→再開)
   (初回はブラウザの再起動が必要な場合あり。詳細なトラブルシューティングは後述)

### 1-3. 起動

```bash
# ターミナル1: バックエンド (初回はEasyOCRモデルのロードに数十秒かかる)
source .venv/bin/activate
uvicorn server:app_asgi --host 0.0.0.0 --port 8000

# ターミナル2: フロントエンド
python3 -m http.server 3000
```

ブラウザで `http://localhost:3000` を開き:

1. 「カメラ映像取得開始」→ カメラ許可 → **OBS Virtual Camera** を選択
2. ゲームでランクバトルを開始

### 1-4. 実運用テストのチェックリスト

画面右のパネルとイベントログで、以下が起きれば正常です:

| タイミング | 期待される動作 |
|---|---|
| 選出画面 | 自分6匹の名前+持ち物、相手6匹のタイプアイコンが「パーティ」欄に入る |
| 相手がポケモンを繰り出す | イベントログに `switch_opponent`、種族がタイプ枠に紐付く |
| コマンド/技選択画面 | HP・技PP・COMMAND残り秒数が更新され、**アドバイス欄に推奨行動**が出る |
| 様子を見る画面 | 自分のタイプ/特性/持ち物、味方のHP実数値、相手のHP%が入る |
| 天候・設置技・ランク変化のメッセージ | ログにイベントIDが付き、場の状態タグ (画面上部) が更新される |
| メガシンカ | 該当ポケモンに「メガ」タグ、天候特性はフィールド表示に反映 |

- 状態がおかしくなったら「状態リセット」ボタン (新しい対戦を始める時は自動リセット)
- アドバイスはコマンド選択/技選択/様子を見る画面のときだけ再計算されます
- サーバー側のターミナルにもアドバイスがテキストで流れます

### 1-5. うまく動かないとき

- **まずサーバーのターミナルを見る**: 5秒ごとに
  `[server] scene=... 受信=N 処理=N 破棄=N` が出ます。
  - この行が出ない → フレームが届いていない (接続/ポートの問題)
  - `scene=field` のまま張り付く → シーン分類の問題 (下記)
  - イベント検知時は `[server] イベント検知: ...` が出ます
- **実映像でのゾーン確認**: `DEBUG_DUMP_FRAMES=1 uvicorn server:app_asgi --port 8000`
  で起動すると受信フレームが約10秒ごとに `debug_frames/` に保存されます。
  そのフレームを `python -m tools.run_images debug_frames/` に通せば、
  実映像に対する認識結果をオフラインで確認できます
- **枠の位置がズレている** (機種の解像度差など): `tools.debug_zones` でスクショに枠を
  描いて確認し、`vision/zones.py` の相対座標を調整
- **シーンが誤分類される**: `vision/scenes.py` の `classify()` が返す `scores` を
  確認 (`python -c "import cv2; from vision.scenes import classify; print(classify(cv2.imread('スクショ.png')))"`)
- **名前・技の誤読**: `vision/normalize.py` の照合が吸収しきれない場合は
  イベントログの生テキストを確認して `vision/events.py` のキーワードを追加
- OBS仮想カメラがブラウザに出ない: Chromeを完全終了 (Cmd+Q) して再起動

---

## 2. 学習テスト (強化学習・実験用)

アドバイスの主軸は探索ベース (`advisor/`) ですが、セルフプレイ評価・将来の
ハイブリッド化のためRL基盤 (`champions_agent/`) を維持しています。
シミュレーションはShowdownの**チャンピオンズ公式mod**
(`gen9championsbssregmb` = BSS Reg M-B: メガシンカ継続仕様・まひ1/8等の
リバランス・6体→3体選出・Lv50) で行い、チームは実環境の使用率DB
(championsbattledata由来、能力ポイント0-32スケールのまま) からバトルごとに生成されます。
対戦相手は最初はRandom、**vs Random勝率75%を超えると自動で過去チェックポイントとの
selfplay (population-based) に移行**します。

### 2-1. ローカルShowdownサーバー (ポート8100)

```bash
bash champions_agent/scripts/setup_showdown.sh          # 初回のみ (clone + npm install)
bash champions_agent/scripts/setup_showdown.sh --start  # 起動 (localhost:8100)
```

アドバイザーサーバー (8000) とポートが分かれているため、**同時起動できます**。

### 2-2. 使用率DBとチーム生成の準備

学習用の対戦チームは使用率DB (`meta_sets` / 役割タグ) から生成されます。
`update_usage_db.sh` を実行済みなら準備完了です。個別に実行する場合:

```bash
python -m champions_agent.data.ingest --skip-static --source auto  # champions実データ
python -m champions_agent.data.build_meta                          # 代表的な型を構築
python -m champions_agent.data.role_tagger                         # 役割タグ付与
```

### 2-3. 学習と評価

```bash
# 性格(プレイスタイル)別にPPO学習。まずは小さいステップ数で通しの動作確認を推奨
python -m champions_agent.train.train_battle --play-style balance --timesteps 5000

# 継続学習 (既存チェックポイントから再開。観測空間が変わった場合は自動で退避→新規)
python -m champions_agent.train.train_battle --play-style offense --timesteps 100000 --resume

# 評価 (vs ランダム / vs 他性格)
python -m champions_agent.train.evaluate --play-style balance --battles 20
python -m champions_agent.train.evaluate --play-style offense --opponent-play-style stall --battles 50
```

### 2-4. 夜間バッチ (推奨の運用形態)

```bash
# 全性格を各50kステップ継続学習 + vs Random評価。Showdownの起動/停止・
# スリープ抑止(caffeinate)・チェックポイント世代管理・ログまで自動
bash champions_agent/scripts/train_nightly.sh

# パラメータ調整例
TIMESTEPS=200000 STYLES="offense stall" bash champions_agent/scripts/train_nightly.sh
```

- チェックポイント: `champions_agent/train/checkpoints/battle_policy_{style}.zip`
- ログ: `champions_agent/train/logs/nightly_*.log`
- 性格の定義 (チーム生成バイアス+報酬シェイピング): `champions_agent/config.py` の `PLAY_STYLES`
- 観測ベクトル (227次元: 技/相性/ランク/控え/設置技/天候/素早さ比較):
  `champions_agent/agent/encoders.py` の `encode_battle`
- 注意: チャンピオンズ固有のリバランス (まひ12.5%等) とメガシンカは未反映。
  学習結果は近似としての扱い (ROADMAP.md の残障壁参照)

---

## 3. データの定期更新

使用率データ (championsbattledata.com + champs.pokedb.tokyo) は日次更新されています。
ローカルDBを毎日自動更新するには:

```bash
cp champions_agent/scripts/com.championsadviser.usage-update.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.championsadviser.usage-update.plist
# ログ/生データ: champions_agent/data/archive/
```

---

## 4. OBSミラーリング詳細 (トラブルシューティング込み)

<details>
<summary>クリックで展開</summary>

### アーキテクチャ

1. **iPhone → Mac**: OBS Studioの「映像キャプチャデバイス」で直接取り込み
2. **OBS → OS**: 仮想カメラ機能でゲーム画面をWebカメラ化
3. **Mac → ブラウザ**: `getUserMedia` APIで仮想カメラから映像取得
4. **ブラウザ → バックエンド**: Canvasで静止画を切り出しWebSocketで送信 (5fps)

### 必要なもの

- Mac本体 / iPhone (ゲーム稼働用) / USB-CまたはLightningケーブル
- [OBS Studio](https://obsproject.com/ja) (Mac版 Ver 26.1以降)

### 手順

1. iPhone接続時に「このコンピュータを信頼しますか?」→「信頼」
2. OBSのソース「+」→「映像キャプチャデバイス」→ iPhoneを選択
3. 映像がキャンバス全体に収まるよう赤枠をドラッグ (黒帯を入れない)
4. 設定→映像: 基本/出力解像度 1920x1080、FPS 10〜30 (720pだと文字OCR不可)
5. 「仮想カメラ開始」(初回は管理者パスワードを求められる場合あり)
6. 解像度変更後は仮想カメラを一度停止→再開すると確実に反映される

### トラブルシューティング

- **ブラウザのカメラ一覧に「OBS Virtual Camera」が出ない**
  → OBS側で仮想カメラを開始したまま、ブラウザを完全終了 (Cmd+Q) して再起動
- **「Tainted Canvas」「Unsafe attempt to load URL」エラー**
  → `file:///` で直接開いている。必ずローカルサーバー (`http://localhost:3000`) 経由で開く

</details>

---

## クレジット

- Battle data provided by [Pokémon Champions Battle Data](https://championsbattledata.com)
- 上位構築データ: [バトルデータベース チャンピオンズ](https://champs.pokedb.tokyo) (公式オープンデータ)
- 静的データ: [PokeAPI](https://pokeapi.co/) / [Pokémon Showdown](https://github.com/smogon/pokemon-showdown)
