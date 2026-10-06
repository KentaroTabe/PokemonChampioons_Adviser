# ポケモンチャンピオンズ AIアドバイザー — アーキテクチャ

スマホ版ポケモンチャンピオンズの画面をミラーリングし、対戦状況を画像から抽出して、
リアルタイムに行動アドバイスを返すシステム。

```
iPhone ─(USB)─ OBS 仮想カメラ ─ ブラウザ(index.html) ─ WebSocket ─ server.py
                                                                    │
                                              ┌─────────────────────┤
                                              ▼                     ▼
                                       vision/ (状態抽出)      advisor/ (行動評価)
```

## 1. vision/ — 画面からの状態抽出

| モジュール | 役割 |
|---|---|
| `pipeline.py` | フレーム処理の起点 `VisionPipeline.process(img)`。シーン分類→抽出器→メッセージ安定化検知→イベント解析 |
| `scenes.py` | 色ヒューリスティクスによるシーン分類 (selection / standby / command / move_select / watch / battle_hud / field) |
| `zones.py` | UI領域の定義。**すべて相対座標 (0..1)** なので解像度に依存しない (1334x750 スクショと 1280x720 OBS の両対応) |
| `extractors.py` | 画面種別ごとの抽出。選出画面 (自パーティ名+持ち物 / 相手タイプアイコン)、バトルHUD (名前/HP/COMMAND秒数)、技選択 (技名/PP/相性ヒント)、様子を見る画面 (タイプ/技/特性/持ち物/パーティHP/相手HP%) |
| `typeicons.py` | タイプアイコン分類。パネル色 (Lab距離) + テンプレ (dHash) のハイブリッド。18タイプ対応 |
| `events.py` | メッセージ/ポップアップのイベント辞書。天候・フィールド・設置技・壁・状態異常・ランク変化・交代・メガシンカ・技/特性/持ち物の判明を検出 |
| `normalize.py` | OCR誤読に強い正規化 (清音化・小書き通常化・漢字/カナ混同吸収) と日本語名→英語IDのファジー解決 (`NameResolver`) |
| `ocr.py` | EasyOCRラッパ。縁取り文字の検知マスク + 生画像直接OCR (直接OCRの方が高精度) |
| `state.py` | `BattleStateV2`: 場 (天候/フィールド/TR) + 陣営 (設置技/壁/おいかぜ) + ポケモン (HP/状態異常/ランク/技PP/特性/持ち物/メガ) |
| `data/jp_names.json` | 日本語名→英語ID辞書 (種族1115/技901/特性310/持ち物2090/タイプ)。`python -m vision.data.fetch_jp_names` で再生成 |

### 抽出の設計ポイント
- **メッセージは「白文字+黒縁取り」で背景ウィンドウが無い** → 縁取り検証マスクで文字の存在と描画完了 (数フレーム安定) を検知し、OCR自体は生クロップに対して行う
- **相手のポケモンは選出画面ではタイプアイコンしか分からない** → 交代メッセージ「〜を繰り出した!」で種族が判明した時点で、種族のタイプと一致する選出枠に自動で紐付ける (`link_active_to_party`)
- **HP%はOCRとバー色解析をクロスバリデーション** (「1%」→「19」のような誤読対策)
- 相手のトレーナー名/ニックネームは韓国語・中国語もあり得るため、種族特定はメッセージとタイプ照合に依存する

## 2. advisor/ — 行動アドバイス

2025年のPokéAgent Challenge (NeurIPS) では**探索ベース (ダメージ計算+MCTS) の foul-play が
強化学習 (Metamon) やLLMを上回った**ため、本システムも探索/期待値ベースを核に採用。
その後、champions_agent/ の強化学習は「残置」から**融合**へ移行した:
自己対戦で鍛えた方策の行動確率を助言スコアへブレンドし (`RL_BLEND_WEIGHT`)、
配布に使う方策は事前登録実験 (P5, 2026-08-26: current/EMAの対測定 計18,000戦、
差+0.027 z=5.3) で **EMA平均方策** に切り替えた。詳細な判断履歴は
`champions_agent/train/training_changes.json` と README「設計判断」を参照。

| モジュール | 役割 |
|---|---|
| `engine.py` | 行動評価。自分の各技の期待ダメージ/KO確率/行動順と、相手の技候補 (判明技+使用率予測) からの被ダメージを計算しスコアリング。交代先も設置技ダメージ込みで評価 |
| `damage.py` | SV準拠ダメージ計算 (Lv50/個体値31固定)。STAB/相性/天候/フィールド/壁/やけど/ランク/主要特性・持ち物対応 |
| `dex.py` | 種族値/タイプ/技データ (`data/dex.json`、`python -m advisor.data.fetch_dex` で再生成) |
| `sets.py` | 相手の型予測。使用率DB (champions_agent/data/db/champions.sqlite3) から技/持ち物/特性の採用率を取得 |
| `service.py` | `Advisor.advise(state_dict)` → 行動ランキング + 日本語の理由 |

### 使用率DB (チャンピオンズ実環境データ)

2ソースを統合して日次更新する (`champions_agent/data/ingest.py --source auto`):

1. **championsbattledata.com API** (主軸): ゲーム内「バトルデータ」の日次収集。
   ポケモンごとの技/持ち物/特性/性格/能力ポイント配分の採用率%。
   規約でプログラム利用が許可されており、クレジット表記が必要:
   *Battle data provided by Pokémon Champions Battle Data (https://championsbattledata.com)*
2. **champs.pokedb.tokyo 公式オープンデータ** (補完): 上位ランカー構築 (チーム+持ち物)。
   ここからポケモン使用率% (チーム採用頻度) とチームメイト共起率を集計する。
   ※詳細ページのスクレイピングは同サイトの規約で禁止されているため行わない
3. **Smogon gen9ou** (フォールバック): 上記が取得不能な場合のみ

取得した生JSONは `champions_agent/data/archive/` に gzip で保全される
(配信元停止時も最後のスナップショットで動作継続できる)。
能力ポイントは **0〜32スケール** (32 ≒ 従来の努力値252相当) で格納される点に注意。

```bash
# 手動更新 (ingest -> meta_sets -> role_tags)
bash champions_agent/scripts/update_usage_db.sh

# 毎日06:30の自動更新 (launchd)
cp champions_agent/scripts/com.championsadviser.usage-update.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.championsadviser.usage-update.plist
```

## 3. ゲーム仕様の前提 (2026-07 調査)

- ランクバトル シングル: 6匹→3匹選出、Lv50固定、個体値31固定、技PP固定
- ギミックは**メガシンカのみ** (1試合1回、持ち物枠のメガストーン、交代しても解除されない)
- 計算式は第9世代 (SV) 準拠 + リバランス (まひ12.5%、こおり自然解除強化、新状態「ねむけ」等)
- 技選択画面に相性ヒントが出る: ◎ばつぐん / ○こうかあり(等倍) / △いまひとつ / ✕こうかなし

## 4. 実行方法

```bash
source .venv/bin/activate

# サーバー起動 (フロントエンドは index.html を http.server 等で開く)
uvicorn server:app_asgi --host 0.0.0.0 --port 8000

# 静止画/ディレクトリでの検証
python -m tools.run_images images/battle_screenshot
python -m tools.debug_zones images/mini_test/battle_1.PNG /tmp/zones.png battle

# テスト
python -m tests.test_events
python -m tests.test_advisor
```
