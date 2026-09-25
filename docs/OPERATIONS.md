# 常駐プロセスの運用手順 (セッション非依存の起動)

Claude Codeセッションのバックグラウンドタスクとして起動したプロセスは、
**セッション終了時に一括killされる** (2026-07-20に学習ループとサーバーが
これで停止した実績)。常駐させるものは必ず `nohup ... & disown` で起動する。

## 一括起動 (推奨)

```bash
cd ~/GitHub/PokemonChampioons_Adviser
bash scripts/start_all_nohup.sh
```

ポートは `config/ports.env` の既定 (アドバイザー 8000 / フロントエンド 3000)。**既定ポートを別のプロセス
(他プロジェクトの開発サーバー等) が使っていれば、次の空きポート (+1 ずつ、最大 +20) へ自動でずらす。**
「稼働中」と判定するのは、そのポートで待ち受けているのがこのリポジトリから起動した uvicorn / http.server の
ときだけ (`scripts/lib/ports.sh`)。決めたポートは `logs/ports.env` と `config/ports.local.js` (フロントが
アドバイザーのポートを読む) に書かれるので、**起動スクリプトが表示した URL を開く**。手動でアドバイザーの
ポートを指定するときは URL に `?api=<port>` を付ける。

## 接続テストの操作パネル (ターミナル不要・チャット不要)

接続テストの開始 / 終了、experiment ラベルの ON / OFF、更新の反映を**ブラウザのボタン**で実行する常駐ページ
(`tools/control_panel.py`、launchd `com.championsadviser.control-panel`、KeepAlive)。アドバイザーは接続テスト中
しか動かないので、起動役としてアドバイザーとは別の常駐を置いている。実行できるのは固定のスクリプトだけ
(`start_connection_test.sh` / `end_connection_test.sh [--no-audit]` / `status.sh` / `deploy.sh` /
`experiment_label.sh` / `canary_summary.sh`)、同時に走るジョブは 1 つ、出力は同じページで読める。

```bash
bash scripts/control_panel_install.sh install     # 初回のみ (ログイン時に自動起動、落ちても復帰)。plist を変えたときも install
bash scripts/control_panel_install.sh status      # 実測 (URL を表示)
bash scripts/control_panel_install.sh uninstall   # 解除
```

- URL: http://localhost:8010/ (`config/ports.env` の `CONTROL_PORT_DEFAULT`。ずらさない)。LAN の別端末からは
  install / status が表示する `http://<MacのIP>:8010/`。待ち受けは `config/control_panel.env` の `CONTROL_BIND`
  (既定 0.0.0.0。認証は無いので、このMacだけにするなら 127.0.0.1)
- 表示: 常駐の実測 (アドバイザー / フロント / Showdown / 学習 / 構築の測定 run)、接続テストの進行 (開始マーカー以降の
  対戦数と勝敗、目標 20 戦 = `BUILD_SMOKE_CANARY_BATTLES`)、フレーム統計、experiment ラベルと registry の状態、警告
  (測定 run との同時実行、マーカーがあるのにアドバイザー停止 など)
- 手順: 「接続テスト開始」→ 表示されたフロントエンド URL を開いて対戦 → 「終了」(一括監査あり = sonnet 課金 / 監査なし)。
  終了処理の出力 (サマリー・決定監査・試用中 Package の実戦サマリー) はページの出力欄に残る
  (`logs/control_panel/<時刻>__<操作>.log`)。ラベルは終了処理で自動では外れない (OFF ボタン)
- 前景で試す: `bash scripts/control_panel.sh --port 8011 --bind 127.0.0.1`

## 構築提案で使わないポケモン (config/banned_species.txt)

- 1 行 1 体 (日本語名か Showdown の id、`#` 以降は注記)。行を足す / 消すだけで登録・解除になる
  (2026-09-25 ユーザー決定: 所持リストは持たず、使わないリストだけで管理する)。
- 提案される構築 (S4 のコンセプト、S5 の並び、S6 の型、`--extra-lineups` の持ち込み、`promote --install`) には
  ここに書いた種が入らない。使える候補は「参戦種 (メガ後・戦闘中だけのフォルムを除く) − このリスト」。
- 相手のパーティには一切適用しない (相手は最新環境の全種から合成する)。
- 解決できない名前があると run は S0 で止まり、その行を表示する。run の request.json に解決結果 (`banned`) と
  ファイルのハッシュ (`banned_source`) が残る。古い request.json を `--spec` で使い回してもファイルは常に効く。
- チャット / フォームの「除外」と `--banned` は、その run だけの追加 (ファイルは変えない)。
- 使わないポケモンを含む Package を承知の上で登録するときだけ `python -m tools.team_build.promote --install <id> --allow-banned`。

## 個別起動

すべてリポジトリルートで実行する。

### 1. アドバイザーサーバー (ポート8000)

```bash
nohup bash -c 'source .venv/bin/activate && \
  PYTHONUNBUFFERED=1 DEBUG_DUMP_FRAMES=1 \
  uvicorn server:app_asgi --host 0.0.0.0 --port 8000' \
  > logs/server_nohup.log 2>&1 & disown
```

### 2. フロントエンド配信 (ポート3000)

```bash
nohup python3 -m http.server 3000 > logs/frontend_nohup.log 2>&1 & disown
```

ブラウザで http://localhost:3000 を開く。
**接続拒否になったらまずこのプロセスの生存を確認する** (下記)。

### 3. Showdownサーバー (ポート8100、学習用)

```bash
nohup node pokemon-showdown/pokemon-showdown start 8100 --no-security \
  > logs/showdown_nohup.log 2>&1 & disown
```

### 4. 学習ループ (2026-09-17 から必要時のみ)

**常時学習は 2026-09-17 20:37 に停止した (ユーザー決定)。** 6 日間・約 330 サイクルでベンチが横ばい
(最良記録 9/11 から更新なし) で収束と判断し、CPU を構築の測定に回す。`config/training.env` の
`TRAINING_MODE=on_demand` により、`start_all_nohup.sh` と `end_connection_test.sh` は学習を自動起動・自動再開しない。
回すのは (1) レギュレーション/使用率の大きな変化、(2) 接続テスト後の実戦バンク更新、(3) 使用パーティへの特化微調整、
(4) 構築提案と連動した学習 (複数方向の構築それぞれへの適応) のときで、`start_training.sh` で登録し、終わったら
`stop_training.sh` で外す。再開時はベンチ軸を最新スナップショットへ張り替え、前後の値を記録する。

launchd (`com.championsadviser.train`, KeepAlive) で常駐させる。起動・停止は
スクリプト経由で行う (launchctl 直叩きは 2026-09-02 に Showdown を巻き添えにした):

```bash
bash scripts/start_training.sh   # 登録 + 実測表示
bash scripts/stop_training.sh    # 学習だけ止める (Showdown は残す)
bash scripts/stop_training.sh showdown   # Showdown も止める (メモリ解放時)
```

40分未満の一時停止なら `touch logs/PAUSE_TRAINING` でもよい
(train_forever.sh が鮮度40分で自動解除する)。Showdown は
`scripts/ensure_showdown.sh` で切り離し起動され、学習の停止に巻き込まれない。

## 更新の反映 (コード修正・最新学習チェックポイント)

```bash
bash scripts/deploy.sh          # 手動反映 (対戦中なら自動で中止する)
bash scripts/deploy.sh --force  # 強制反映
```

- 再起動で反映されるもの: コード修正、最新の学習チェックポイント
  (起動時読み込み)。`config/my_team.json` はホットリロードなので不要
- **毎朝5:00に自動反映** (launchd `com.championsadviser.daily-deploy`、
  ログ: logs/daily_deploy.log)。解除:
  `launchctl unload ~/Library/LaunchAgents/com.championsadviser.daily-deploy.plist`
- 反映後はブラウザ (http://localhost:3000) を再接続する

## Python 環境 (venv) の作り直しと版の移行

`.venv` は 2026-09 まで Xcode 付属の Python 3.9.6 (EOL) だった。新しい SDK (Anthropic Python SDK 1.0 など) は 3.10 以上を
要求するため、Homebrew の `python@3.12` で作り直す (2026-09-24、docs/TECH_WATCH_2026-09.md §A-6)。依存は
`requirements-full.txt` (移行前の venv と同じ主版に固定。poke-env は 0.10 のまま)。

```bash
brew install python@3.12                                        # 初回のみ
bash scripts/venv_rebuild.sh /opt/homebrew/bin/python3.12 .venv312   # 使用中の .venv は触らない
bash scripts/ci_tests.sh .venv312/bin/python                    # CI サブセット + 画面認識の実データテストで確認
```

切替は **シンボリックリンク** で行う (venv は絶対パスを中に持つので、ディレクトリを rename すると activate と
console script が壊れる。リンクなら `.venv312` の中のパスはそのまま有効):

```bash
bash scripts/venv_switch.sh .venv312       # .venv → .venv39 に退避し、.venv を .venv312 へのリンクにする
bash scripts/run_test.sh test_advisor test_ocr_parse   # 切替後の確認
bash scripts/venv_switch.sh --rollback     # 戻す
```

- 切替は常駐 (アドバイザー・学習・構築の測定) が動いていないときに行う (スクリプトは `.venv/bin/python` が動いていれば止まる)。
  動いている Python は古い venv のファイルを掴んでいる
- 2026-09-24 の確認: 3.12 の venv で CI サブセットと画面認識の実データテストを回し、3.9 と同じ結果 (test_team_proposal の
  `test_myteam_text_completes_missing_evs` と test_look_more の持ち物・特性・技は 3.9 でも失敗していた既存の問題で、移行とは無関係。
  同日に修正: 実ログの選出ロスターが優先される仕様と、能力タブ登録の 2 フレーム確認にテストを合わせた)
- **2026-09-24 に切替済み**: `.venv` → `.venv312` (Python 3.12.14) のリンク、旧環境は `.venv39`。切替後に test_advisor
  (RL 方策の読込を含む) と test_ocr_parse を確認。問題があれば `bash scripts/venv_switch.sh --rollback`。
  `.gitignore` は `.venv` だけなので `.venv312` / `.venv39` は未追跡として見える (`.venv*` を足すとよい)
- launchd の各ジョブは `source .venv/bin/activate` 経由なので、リンクを差し替えれば次回起動から新環境になる
- `champions_agent/.venv` (3.9) はどのスクリプトからも参照されていない (2026-09-24 確認)。消してよい

## 生存確認

```bash
lsof -nP -iTCP:8000 -sTCP:LISTEN   # アドバイザー
lsof -nP -iTCP:3000 -sTCP:LISTEN   # フロントエンド
lsof -nP -iTCP:8100 -sTCP:LISTEN   # Showdown
lsof -nP -iTCP:8010 -sTCP:LISTEN   # 操作パネル (bash scripts/control_panel_install.sh status でも可)
pgrep -fl train_forever            # 学習ループ
```

## 停止

```bash
pkill -f "uvicorn server:app_asgi"
pkill -f "http.server 3000"
bash scripts/stop_training.sh showdown   # 学習 + Showdown
```

## 注意

- アドバイザーサーバーの再起動は**ユーザーの試合中を避ける** (接続断で
  試合データが失われる)。試合の合間に行う
- 起動時に不要ログの自動掃除 (tools/cleanup_logs) が走る (server.py)
- `scripts/start_servers.sh` はフォアグラウンド起動 (Ctrl+C で両方停止)
  の開発用。常駐には本書のnohup方式を使う
