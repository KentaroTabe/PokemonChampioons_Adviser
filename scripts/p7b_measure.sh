#!/usr/bin/env bash
# P7'判定 (観測更新つき多世界探索) の測定: 現行 (K=0) と 観測更新つき K=8 を同一相手列で対に測る。
# 事前登録: champions_agent/train/training_changes.json 2026-09-05 10:00 (P7')
#   bash scripts/p7b_measure.sh [battles] [arm: current|updated|both] [workers]
set -euo pipefail
cd "$(dirname "$0")/.."
# RLモデルを測定開始時点にピン止め (腕の起動時刻差で後の腕が有利になるのを防ぐ、9/5)
if [ -z "${CHAMPIONS_MODELS_DIR:-}" ]; then
  export CHAMPIONS_MODELS_DIR="$(bash scripts/pin_models.sh)"
  echo "[pin] CHAMPIONS_MODELS_DIR=$CHAMPIONS_MODELS_DIR"
fi
N="${1:-600}"; ARM="${2:-both}"; W="${3:-4}"
SEED=20260904
OUT=logs/p7b_verdict
LOG="$OUT/p7b_$(date +%Y%m%d_%H%M)_${ARM}.log"
mkdir -p "$OUT"
run_arm() {  # name extra
  echo "=== [$1] value=on $(date '+%F %T') ===" >> "$LOG"
  .venv/bin/python -m tools.check_search_expert --battles "$N" --depth 2 \
    --opp-seed "$SEED" --skip-random $2 --json "$OUT/p7b_$1.json" >> "$LOG" 2>&1
}
case "$ARM" in
  current) run_arm current "--belief 0" ;;
  updated) run_arm updated "--belief 8 --belief-updates --workers $W" ;;
  both)    run_arm current "--belief 0"; run_arm updated "--belief 8 --belief-updates --workers $W" ;;
esac
echo "=== done $(date '+%F %T') ===" >> "$LOG"
if [ -f "$OUT/p7b_current.json" ] && [ -f "$OUT/p7b_updated.json" ]; then
  .venv/bin/python -m tools.p7b_verdict "$OUT" | tee -a "$LOG"
fi
