#!/usr/bin/env bash
# 選出データ更新パイプラインを切り離して回す (約1.5時間)。
#   bash scripts/selection_refresh_bg.sh [比較戦数/条件] [相手シード]
#   tail -f logs/selection_refresh/run.log   で進捗を見る
set -euo pipefail
cd "$(dirname "$0")/.."

LOG="logs/selection_refresh/run.log"
mkdir -p logs/selection_refresh

PID=$(.venv/bin/python -m tools.spawn_detached "$LOG" \
  bash scripts/selection_refresh_run.sh "${1:-10000}" "${2:-20260803}")

sleep 3
if ! kill -0 "$PID" 2>/dev/null; then
  echo "[refresh] NG: 起動に失敗しました。ログを確認してください: $LOG"
  exit 1
fi

echo "[refresh] 切り離して開始しました (PID $PID)"
echo "[refresh] 進捗: tail -f $LOG"
