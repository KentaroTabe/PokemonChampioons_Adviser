#!/usr/bin/env bash
# 報酬スイープの再評価を切り離して回す (学習なし)。
#   bash scripts/reward_sweep_eval_bg.sh [評価戦数] [性格] [条件] [シード数]
#   tail -f logs/reward_sweep/eval.log   で進捗を見る
set -euo pipefail
cd "$(dirname "$0")/.."

BATTLES="${1:-3000}"
STYLES="${2:-balance}"
ARMS="${3:-control,ko}"
SEEDS="${4:-3}"
LOG="logs/reward_sweep/eval.log"

mkdir -p logs/reward_sweep
PID=$(.venv/bin/python -m tools.spawn_detached "$LOG" \
  bash scripts/reward_sweep_eval_run.sh "$BATTLES" "$STYLES" "$ARMS" "$SEEDS")

sleep 3
if ! kill -0 "$PID" 2>/dev/null; then
  echo "[sweep-eval] NG: 起動に失敗しました。ログを確認してください: $LOG"
  bash scripts/reward_sweep_stop.sh
  exit 1
fi

echo "[sweep-eval] 切り離して開始しました (PID $PID)"
echo "[sweep-eval] 進捗: tail -f $LOG"
echo "[sweep-eval] 停止: bash scripts/reward_sweep_stop.sh"
