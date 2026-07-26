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
| `python -m tools.check_look_more <frame...>` | もっと見る画面の読取診断 (タブ/対象/実数値/性格) |
| `python -m tools.check_battle_log [file]` | 対戦ログの内容確認 |
| `python -m tools.analyze_corrections` | 手動修正ログの集計 (誤認識ランキング) |
| `python -m tools.audit_extraction [--battle <log>]` | 監査ペア一覧 (フレーム×抽出主張、対戦中フェーズ主体) |
| `python -m tools.audit_subtask [--battle <log>] [--max-frames N]` | 1対戦の抽出監査をsonnetサブタスクで実行 → `logs/audit_reports/` |
| `python -m tools.audit_session [--last N] [--budget 30]` | セッション一括監査: 全対戦横断で矛盾候補の機械検出+層化サンプリング→sonnet 1回 |

監査サブタスクのモデルはsonnet固定 (2026-07-23実測: haikuは技名の取り違え・
HP幻視があり監査に不適。根拠はtools/audit_subtask.pyのdocstring参照)。
通常運用は end_connection_test.sh が自動実行する一括監査 (audit_session)。
HPの急回復・ひんし後の再表示・7匹化などはPython側で先に検出し、
sonnetは疑い箇所の検証+少数サンプルの網羅に専念する (タスク数最小化)。

デバッグフレーム: サーバーを `DEBUG_DUMP_FRAMES=1` で起動すると
`debug_frames/` に保存される (通常10秒毎、場の状況=fc_/選出=sel_は2秒毎)。

自パーティの型登録 (config/my_team.json) は「もっと見る」画面
(選出画面/交代画面で各ポケモンにカーソル→もっと見る) の自動読み取りで行われる。
能力タブ=技/特性/持ち物、ステータスタブ=能力ポイント/性格 (実数値との
理論値照合を通った場合のみ保存)。フロントのパーティ編集フォームは手動修正用。

## パーティ構築

| コマンド | 用途 |
|---|---|
| `python -m tools.team_report [--suggest] [--top N]` | 構築診断 (マッチアップ/穴/S関係/補完) |
| `python -m tools.generate_teams <コア名> [--beam N] [--n N]` | 共起ビーム探索で構築生成 |
| `python -m tools.evaluate_team <6体\|--myteam> [--battles N] [--random-preview]` | チーム固定の実対戦評価 (両側RL操縦+相性選出、構築の強さを分離測定) |
| `python -m tools.check_team_eval [--battles 60]` | 評価の一貫性ゲート (再現性/順位安定性。進化探索の前提確認) |
| `python -m tools.evolve_teams [--population 12] [--generations 3] [--battles 60] [--update-archive]` | 構築の進化探索 (対戦AIが評価関数。結果は logs/team_evolution/) |

| `python -m tools.evolve_teams --seed-myteam [--locked <種族,..>] [--max-changes 2]` | 制約付き改善: 自分のパーティを種に「少しだけ変える」探索 |
| `python -m tools.playbook [--opponents 12] [--battles 30]` | プレイブック生成: 相手構築別の選出チャート+勝ち筋 → `logs/playbooks/` |

進化探索は相手分布に `--forecast-mix` (使用率トレンドの1期外挿。履歴が
2ヶ月分たまるまで自動無効) と `--archive-mix` (過去の優勝チーム=PSRO反復)
を混ぜられる。`--update-archive` で今回の最優秀をアーカイブへ追加し、
定期実行すると「対策の対策」まで見た頑健な構築へ収束していく。

## 振り返り・環境分析

| コマンド | 用途 |
|---|---|
| `python -m tools.analyze_battles [--last N] [--days N]` | 敗因分析 (勝敗/レート推移/負け寄与ランキング/選出別勝率/ローカルメタ) |
| `python -m tools.review_battle [--battle <log>] [--all]` | ポストゲームレビュー (アドバイスと実際の行動の分岐点) |
| `python -m tools.meta_digest [--top N] [--days N]` | 環境ダイジェスト (使用率上位/トレンド/並び/自分のレート帯との比較) |

上記の分析とプレイブック生成/パーティ改善は、フロントエンド (3000) の
「📈 分析・コーチング」パネルからも実行できる (バックエンド8000経由。
実対戦を伴うジョブは対戦中は実行を拒否し、進捗を逐次表示する)。

## 強化学習 (champions_agent)

| コマンド | 用途 |
|---|---|
| `python -m tools.watch_training` | 学習経過のサマリー (稼働状態/性格別勝率/推移スパークライン) |
| `python -m tools.watch_training --history 20` | 直近20サイクルの評価履歴一覧 |
| `python -m tools.watch_training --history 100 --plot` | 全性格の勝率推移を1枚のグラフにPNG出力 (logs/training_history.png) |
| `python -m tools.watch_training --follow` | 学習ログのライブ追尾 (Ctrl+Cで終了) |
| `bash champions_agent/scripts/setup_showdown.sh` | ローカルShowdown (8100) の準備 |
| `bash champions_agent/scripts/train_forever.sh` | 連続学習ループ (nohup推奨) |
| `bash champions_agent/scripts/train_nightly.sh` | 夜間バッチ1サイクル |
| `python -m champions_agent.train.evaluate --opponent benchmark [--checkpoint current\|best]` | ベンチマーク評価 (current=学習進捗 / best=配布版) |
| `python -m champions_agent.train.best_checkpoint --list` | 最良チェックポイント (_best) の記録確認 |
| `python -m champions_agent.train.auto_tune --status` | 自律チューニングの状態 (試行履歴/現設定) |
| `tail -f logs/auto_tune.log` | チューナーの判定ログ |
| `python -m champions_agent.train.opponent_pool --list` | selfplay相手プールの一覧 |
| `python -m tools.probe_policy` | 方策の健全性プローブ (攻撃率/抜群率) |
| `python -m tools.check_search_expert [--battles N] [--depth 1\|2]` | 探索エキスパート (学習相手/BC教師) の実戦強度診断 |
| `python -m champions_agent.train.bc_pretrain --dry-run` | 探索エンジンの行動クローン微調整 (⚠実行条件はdocstring参照) |
| `python -m tools.smoke_train` / `smoke_selfplay` | 短時間の学習/セルフプレイ疎通 |
| `python -m tools.validate_teams` | 生成チームのShowdownバリデーション |
| `python -m tools.check_action_mask` | 行動マスクの検証 |

⚠ 2026-07-26以前の夜間ベンチ履歴は「凍結された_best」を測っていた
(評価が_best優先ロードだったバグ。watch_trainingの過去推移は学習進捗を
反映していない)。同日修正済みで、以後の履歴は current の真値。

## 人間 vs AI 対戦 (学習進捗の体感チェック)

| コマンド | 用途 |
|---|---|
| `python -m tools.human_battle --name <名前> [--opponent model\|benchmark\|search] [--style balance] [--battles N] [--timer]` | AIから対戦チャレンジを送る (`--mode accept` で人間からの申請を待つ) |
| `python -m tools.export_my_team_showdown [--out <ファイル>]` | my_team.json をShowdownチームテキストへ書き出し (貼り付け用) |
| `python -m tools.check_human_battle [--opponent <種別>]` | 疎通確認 (RandomPlayerが人間の代役で1戦、記録なし) |

手順:
1. ローカルShowdown (8100) 稼働中に `human_battle` を起動する
2. ブラウザで `https://play.pokemonshowdown.com/~~localhost:8100/` を開き、
   `--name` と同じ名前でログインする (パスワード不要。同一LANのスマホも可)
3. チームビルダーで `[Gen 9] Champions BSS Reg MB` を選び、
   `export_my_team_showdown` の出力を Import に貼り付ける
4. 届いたチャレンジを Accept する (表示は英語、挙動はchampions仕様)

結果は `logs/human_battles.jsonl` に記録される (人間相手の勝率=進捗の物差し)。
相手: model=学習済み方策 (性格別/_best優先) / benchmark=上位構築ヒューリスティクス /
search=探索エキスパート (アドバイザーの読み筋)。

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
- `docs/CONNECTION_TEST_CHECKLIST.md` — 接続テストで確認すべき項目 (2026-07-24更新分)
- `docs/TOP_PLAYER_PLAN.md` — 機能ロードマップと実施状況
- `docs/WINDOWS_IPHONE_TEST.md` — Windows+iPhoneでのテスト手順
- `docs/REPORT_EXTRACTION_PLAN.md` — 課題提出用の部分公開方針
- `ROADMAP.md` — レギュレーション変更時の対応手順
