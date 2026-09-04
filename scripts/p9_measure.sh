#!/usr/bin/env bash
# P9判定 (探索値の助言スコア統合) の測定: advisor-as-player で基準 (A) と統合腕 (B、
# P7採用時は C=K付き) を同一相手列で対に測る。
# 事前登録: champions_agent/train/training_changes.json 2026-09-04 20:30 / 22:10 (倍増)
#   bash scripts/p9_measure.sh [battles] [belief_k_for_C|0] [sensor_q] [workers]
# 例: bash scripts/p9_measure.sh 600 0 0.0 1      # A/B のみ (P7 棄却時)
#     bash scripts/p9_measure.sh 600 8 0.3 4      # A/B/C (P7 採用・P8 採用時)
set -euo pipefail
cd "$(dirname "$0")/.."
# RLモデルを測定開始時点にピン止め (腕の起動時刻差で後の腕が有利になるのを防ぐ、9/5)
if [ -z "${CHAMPIONS_MODELS_DIR:-}" ]; then
  export CHAMPIONS_MODELS_DIR="$(bash scripts/pin_models.sh)"
  echo "[pin] CHAMPIONS_MODELS_DIR=$CHAMPIONS_MODELS_DIR"
fi
N="${1:-600}"; KC="${2:-0}"; Q="${3:-0.0}"; W="${4:-1}"
SEED=20260904
OUT=logs/p9_verdict
LOG="$OUT/p9_$(date +%Y%m%d_%H%M).log"
mkdir -p "$OUT"
run_arm() {  # name belief_k blend
  echo "=== [$1] K=$2 blend=$3 q=$Q workers=$W $(date '+%F %T') ===" >> "$LOG"
  .venv/bin/python -m tools.check_advisor_player --battles "$N" --opp-seed "$SEED" \
    --skip-random --belief-k "$2" --search-blend "$3" --sensor-q "$Q" --workers "$W" \
    --json "$OUT/p9_$1.json" >> "$LOG" 2>&1
}
run_arm A 0 0
run_arm B 0 40
ARGS=("$OUT/p9_A.json" "$OUT/p9_B.json")
if [ "$KC" != "0" ]; then
  run_arm C "$KC" 40
  ARGS+=("$OUT/p9_C.json")
fi
echo "=== done $(date '+%F %T') ===" >> "$LOG"
.venv/bin/python -m tools.p9_verdict "${ARGS[@]}" | tee -a "$LOG"
