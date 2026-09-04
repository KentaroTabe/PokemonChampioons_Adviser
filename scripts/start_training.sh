#!/usr/bin/env bash
# 常時学習 (launchd: com.championsadviser.train) を再開する。
#   bash scripts/start_training.sh
# Showdown は学習サイクルが scripts/ensure_showdown.sh で切り離し起動する。
set -uo pipefail
cd "$(dirname "$0")/.."

PLIST="$HOME/Library/LaunchAgents/com.championsadviser.train.plist"
if launchctl print "gui/$(id -u)/com.championsadviser.train" >/dev/null 2>&1; then
  echo "[start_training] 既に登録済みです"
else
  launchctl bootstrap "gui/$(id -u)" "$PLIST" \
    && echo "[start_training] launchd ジョブを登録しました" \
    || { echo "[start_training] 登録に失敗しました"; exit 1; }
fi

sleep 5
echo "=== 実測 ==="
pgrep -fl "train_forever" || echo "train_forever: 未起動 (数秒後に再確認してください)"
lsof -nP -iTCP:8100 -sTCP:LISTEN >/dev/null 2>&1 \
  && echo "Showdown 8100: 稼働中" || echo "Showdown 8100: 起動待ち (サイクル開始時に起動)"
