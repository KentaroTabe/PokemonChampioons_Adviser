#!/usr/bin/env bash
# P10判定 (技イベントからの期待ダメージ推定でHP固着の害を取り戻せるか) の測定。
# 表示HP固着ノイズ (0.3) 下で、推定なし (unaware) と推定あり (estimate) を
# 同一相手列で対に測る。事前登録: training_changes.json 2026-09-05 (P10)
#   bash scripts/p10_measure.sh [battles] [arm: unaware|estimate|both]
set -euo pipefail
cd "$(dirname "$0")/.."
# RLモデルを測定開始時点にピン止め (腕の起動時刻差で後の腕が有利になるのを防ぐ、9/5)
if [ -z "${CHAMPIONS_MODELS_DIR:-}" ]; then
  export CHAMPIONS_MODELS_DIR="$(bash scripts/pin_models.sh)"
  echo "[pin] CHAMPIONS_MODELS_DIR=$CHAMPIONS_MODELS_DIR"
fi
N="${1:-600}"; ARM="${2:-both}"
SEED=20260904
OUT=logs/p10_verdict
LOG="$OUT/p10_$(date +%Y%m%d_%H%M)_${ARM}.log"
mkdir -p "$OUT"
run_arm() {  # name extra
  echo "=== [$1] noise=0.3 K=0 value=on $(date '+%F %T') ===" >> "$LOG"
  .venv/bin/python -m tools.check_search_expert --battles "$N" --depth 2 \
    --belief 0 --opp-seed "$SEED" --skip-random --sensor-noise 0.3 $2 \
    --json "$OUT/p10_$1.json" >> "$LOG" 2>&1
}
case "$ARM" in
  unaware)  run_arm unaware "" ;;
  estimate) run_arm estimate "--sensor-estimate" ;;
  both)     run_arm unaware ""; run_arm estimate "--sensor-estimate" ;;
esac
echo "=== done $(date '+%F %T') ===" >> "$LOG"
if [ -f "$OUT/p10_unaware.json" ] && [ -f "$OUT/p10_estimate.json" ]; then
  .venv/bin/python -m tools.p10_verdict "$OUT" | tee -a "$LOG"
fi
