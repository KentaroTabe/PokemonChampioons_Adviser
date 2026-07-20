# 開発者向けコマンドリファレンス

すべてリポジトリルートで `source .venv/bin/activate` 後に実行する。
常駐プロセスの起動/停止は docs/OPERATIONS.md を参照。

## サーバー運用

| コマンド | 用途 |
|---|---|
| `bash scripts/start_all_nohup.sh` | 全常駐プロセスの一括起動 (稼働中はスキップ) |
| `bash scripts/deploy.sh` | 更新の反映 (対戦中は自動中止、`--force`で強制) |
| `bash scripts/start_servers.sh` | 開発用フォアグラウンド起動 (Ctrl+Cで停止) |
| `python -m tools.cleanup_logs --dry-run` | 不要ログ掃除の対象確認 (実行は引数なし) |

## launchd 常駐 (再起動後も自動復帰)

学習ループは launchd で常駐管理している。`RunAtLoad` でログイン時に自動起動し
(OSアップデートの再起動後も復帰)、`KeepAlive` でクラッシュ時に自動再起動する。
`train_nightly.sh` の `--resume` により、落ちても直前チェックポイントから継続する。
※ LaunchAgent はユーザーがログインして初めて起動する (完全無人化には自動ログイン設定が必要)。

| コマンド | 用途 |
|---|---|
| `launchctl list \| grep champion` | 常駐状態の確認 (PID / 最終終了コード / ラベル) |
| `tail -f logs/train_forever.log` | 学習の進捗ログを追う |
| `launchctl unload ~/Library/LaunchAgents/com.championsadviser.train.plist` | 学習の一時停止 |
| `launchctl load -w ~/Library/LaunchAgents/com.championsadviser.train.plist` | 学習の再開 |
| `cp scripts/com.championsadviser.train.plist ~/Library/LaunchAgents/` | plist更新の反映 (unload→cp→load の順) |

plist本体は `scripts/com.championsadviser.train.plist` (repo管理)。編集後は
`~/Library/LaunchAgents/` へコピーし、`unload` → `load -w` で再読込する。
既存の `com.championsadviser.daily-deploy` (毎朝5時のdeploy.sh) も同じ仕組み。

### アドバイザー/フロントエンドの常駐化 (未適用・参考)

現状これらは手動運用 (対戦中の再起動を避けるため)。再起動後も自動復帰させたい場合は
学習と同様に LaunchAgent 化できる。`scripts/com.championsadviser.train.plist` を雛形に、
`ProgramArguments` を各起動コマンドへ差し替えて `~/Library/LaunchAgents/` へ置き `load -w` する。

```bash
# 例: アドバイザー(8000) を常駐化する場合の ProgramArguments 差し替え先
#   /bin/bash -lc 'cd <repo> && source .venv/bin/activate && \
#     PYTHONUNBUFFERED=1 uvicorn server:app_asgi --host 0.0.0.0 --port 8000'
# 例: フロントエンド(3000)
#   /bin/bash -lc 'cd <repo> && python3 -m http.server 3000'
# 例: Showdown(8100)
#   /bin/bash -lc 'cd <repo> && node pokemon-showdown/pokemon-showdown start 8100 --no-security'
# ラベル(Label)とログ出力先(Standard*Path)は plist ごとに一意にすること。
# WorkingDirectory と PATH(/opt/homebrew/bin を含める) を明示すると確実。
```

## テスト (すべて `python -m tests.<名前>`)

| モジュール | 対象 |
|---|---|
| test_events | メッセージ→イベント解析 (急所/連結/デデュープ/特性帰属/固定特性) |
| test_ocr_parse | HP分数/百分率のOCRテキスト解析 |
| test_pick_detection | 自選出の白リボン検出+選出順 (実フレーム) |
| test_battle_logger | 対戦ログの回転デバウンス |
| test_abilities_calc | 特性考慮 (天候素早さ/火力/耐久) |
| test_advisor | 行動評価の統合 (状態辞書→アドバイス) |
| test_search | 同時手番探索 (択/2手読み/性能) |
| test_endgame | 詰み筋/勝ち筋の1v1行列 |
| test_ev_infer | 相手の型推定 (仮説/先後/ダメージ/こだわりロック) |
| test_my_team | 自分の型登録 (HABCDS/性格/エンジン反映) |
| test_team_advice | パーティ診断 |
| test_ja_names | フォルムIDの日本語表示 |
| test_selection_advice | 選出提案 |
| test_advice_serializable | アドバイスのJSON直列化 (配信互換) |

## 画面認識のデバッグ

| コマンド | 用途 |
|---|---|
| `python -m tools.debug_zones <img> <out> <scene>` | ゾーンの可視化 (座標調整) |
| `python -m tools.run_images <dir>` | 静止画ディレクトリの一括解析 |
| `python -m tools.check_scene_sweep [N]` | 直近Nフレームのシーン分類分布 |
| `python -m tools.check_type_scores <frame...>` | タイプアイコンのスコア診断 (混同調査) |
| `python -m tools.make_type_templates <frame> "<ラベル>"` | タイプテンプレートの採取 |
| `python -m tools.check_flow_tracking [dir]` | ターン/HP/スムージングの検証 |
| `python -m tools.check_field_my_hp <frame...>` | 自分HPゾーンのOCR診断 |
| `python -m tools.check_selection_frame <frame>` | 選出画面の抽出診断 |
| `python -m tools.check_battle_log [file]` | 対戦ログの内容確認 |
| `python -m tools.analyze_corrections` | 手動修正ログの集計 (誤認識ランキング) |

デバッグフレーム: サーバーを `DEBUG_DUMP_FRAMES=1` で起動すると
`debug_frames/` に保存される (通常10秒毎、場の状況=fc_/選出=sel_は2秒毎)。

## パーティ構築

| コマンド | 用途 |
|---|---|
| `python -m tools.team_report [--suggest] [--top N]` | 構築診断 (マッチアップ/穴/S関係/補完) |
| `python -m tools.generate_teams <コア名> [--beam N] [--n N]` | 共起ビーム探索で構築生成 |

## 強化学習 (champions_agent)

| コマンド | 用途 |
|---|---|
| `bash champions_agent/scripts/setup_showdown.sh` | ローカルShowdown (8100) の準備 |
| `bash champions_agent/scripts/train_forever.sh` | 連続学習ループ (nohup推奨) |
| `bash champions_agent/scripts/train_nightly.sh` | 夜間バッチ1サイクル |
| `python -m champions_agent.train.evaluate --opponent benchmark` | ベンチマーク評価 |
| `python -m tools.probe_policy` | 方策の健全性プローブ (攻撃率/抜群率) |
| `python -m tools.smoke_train` / `smoke_selfplay` | 短時間の学習/セルフプレイ疎通 |
| `python -m tools.validate_teams` | 生成チームのShowdownバリデーション |
| `python -m tools.check_action_mask` | 行動マスクの検証 |

## データ更新

| コマンド | 用途 |
|---|---|
| `bash champions_agent/scripts/update_usage_db.sh` | 使用率DBの更新 (championsbattledata+pokedb) |
| `python -m vision.data.fetch_jp_names` | 日本語名テーブルの再生成 |
| `python -m advisor.data.fetch_dex` | 種族値/技データの再生成 |
| `node tools/export_champions_dex.js` | Showdownからchampions dexを再エクスポート |
| `python -m tools.fetch_sprites` | 種族特定用スプライトの取得 |

## 主要ドキュメント

- `README.md` — セットアップと実運用/学習テストの手順
- `ARCHITECTURE.md` — 全体設計
- `docs/OPERATIONS.md` — 常駐プロセス運用 (nohup起動/反映/停止)
- `docs/TOP_PLAYER_PLAN.md` — 機能ロードマップと実施状況
- `docs/WINDOWS_IPHONE_TEST.md` — Windows+iPhoneでのテスト手順
- `docs/REPORT_EXTRACTION_PLAN.md` — 課題提出用の部分公開方針
- `ROADMAP.md` — レギュレーション変更時の対応手順
