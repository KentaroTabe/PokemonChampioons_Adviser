# 常駐プロセスの運用手順 (セッション非依存の起動)

Claude Codeセッションのバックグラウンドタスクとして起動したプロセスは、
**セッション終了時に一括killされる** (2026-07-20に学習ループとサーバーが
これで停止した実績)。常駐させるものは必ず `nohup ... & disown` で起動する。

## 一括起動 (推奨)

```bash
cd ~/GitHub/PokemonChampioons_Adviser
bash scripts/start_all_nohup.sh
```

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

### 4. 連続学習ループ

```bash
nohup bash champions_agent/scripts/train_forever.sh \
  > champions_agent/train/logs/train_forever_nohup.log 2>&1 & disown
```

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

## 生存確認

```bash
lsof -nP -iTCP:8000 -sTCP:LISTEN   # アドバイザー
lsof -nP -iTCP:3000 -sTCP:LISTEN   # フロントエンド
lsof -nP -iTCP:8100 -sTCP:LISTEN   # Showdown
pgrep -fl train_forever            # 学習ループ
```

## 停止

```bash
pkill -f "uvicorn server:app_asgi"
pkill -f "http.server 3000"
pkill -f train_forever   # 学習中のtrain_battleは次のループ境界で終了
pkill -f "pokemon-showdown start"
```

## 注意

- アドバイザーサーバーの再起動は**ユーザーの試合中を避ける** (接続断で
  試合データが失われる)。試合の合間に行う
- 起動時に不要ログの自動掃除 (tools/cleanup_logs) が走る (server.py)
- `scripts/start_servers.sh` はフォアグラウンド起動 (Ctrl+C で両方停止)
  の開発用。常駐には本書のnohup方式を使う
