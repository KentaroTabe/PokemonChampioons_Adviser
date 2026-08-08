#!/usr/bin/env bash
# 新アーキテクチャの隔離学習を切り離して回す (数日規模)。
#   bash scripts/arch_v7_bg.sh [ラウンド数] [ステップ数] [BC対戦数]
#   tail -f logs/arch_v7/run.log   で進捗を見る
# 本番のPAUSEフラグは使わない (並走する。判定は開始7日後)
set -euo pipefail
cd "$(dirname "$0")/.."

LOG="logs/arch_v7/run.log"
mkdir -p logs/arch_v7

PID=$(.venv/bin/python -m tools.spawn_detached "$LOG" \
  bash scripts/arch_v7_run.sh "${1:-40}" "${2:-100000}" "${3:-400}")

sleep 3
if ! kill -0 "$PID" 2>/dev/null; then
  echo "[arch_v7] NG: 起動に失敗しました。ログを確認してください: $LOG"
  exit 1
fi
echo "[arch_v7] 切り離して開始しました (PID $PID)"
echo "[arch_v7] 進捗: tail -f $LOG"
