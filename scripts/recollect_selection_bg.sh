#!/usr/bin/env bash
# 選出データ再構築パイプラインを切り離して回す (数時間かかる)。
#   bash scripts/recollect_selection_bg.sh
#   tail -f logs/recollect_selection.log   で進捗を見る
set -euo pipefail
cd "$(dirname "$0")/.."

LOG="logs/recollect_selection.log"
mkdir -p logs

PID=$(.venv/bin/python -m tools.spawn_detached "$LOG" \
  bash scripts/recollect_selection_run.sh)

sleep 3
if ! kill -0 "$PID" 2>/dev/null; then
  echo "[recollect] NG: 起動に失敗しました。ログを確認してください: $LOG"
  exit 1
fi

echo "[recollect] 切り離して開始しました (PID $PID)"
echo "[recollect] 進捗: tail -f $LOG"
