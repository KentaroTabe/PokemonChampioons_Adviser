#!/bin/bash
# 常時学習ランナー: train_nightly.sh のサイクルを停止するまで繰り返す。
#
#   bash champions_agent/scripts/train_forever.sh
#   TIMESTEPS=100000 bash champions_agent/scripts/train_forever.sh
#
# - サイクル間に休止 (既定60秒) を挟む
# - Ctrl+C で安全に停止 (実行中サイクルは完了させず即時終了)
# - 各サイクルのログは train_nightly.sh が logs/nightly_*.log に残す
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

SLEEP_BETWEEN="${SLEEP_BETWEEN:-60}"
CYCLE=0

echo "[forever] 常時学習を開始します (Ctrl+Cで停止)"
trap 'echo "[forever] 停止します (サイクル$CYCLE完了)"; exit 0' INT TERM

while true; do
  CYCLE=$((CYCLE + 1))
  echo "[forever] ===== サイクル $CYCLE 開始: $(date) ====="
  bash champions_agent/scripts/train_nightly.sh || \
    echo "[forever] サイクル$CYCLE が異常終了しました (続行)"
  echo "[forever] サイクル $CYCLE 完了。${SLEEP_BETWEEN}秒休止"
  sleep "$SLEEP_BETWEEN"
done
