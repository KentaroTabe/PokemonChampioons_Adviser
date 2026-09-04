#!/usr/bin/env bash
# P8判定 (センサ不確実性を探索へ伝播) の測定: 表示HP固着ノイズ下で
# 「気づかない探索 (q=0)」と「世界を持つ探索 (q=noise)」を同一相手列で対に測る。
# 事前登録: champions_agent/train/training_changes.json (P8、起動時に登録)
#   bash scripts/p8_measure.sh [battles] [belief_k] [value:on|off] [prior_mix] [noise]
set -euo pipefail
cd "$(dirname "$0")/.."
# RLモデルを測定開始時点にピン止め (腕の起動時刻差で後の腕が有利になるのを防ぐ、9/5)
if [ -z "${CHAMPIONS_MODELS_DIR:-}" ]; then
  export CHAMPIONS_MODELS_DIR="$(bash scripts/pin_models.sh)"
  echo "[pin] CHAMPIONS_MODELS_DIR=$CHAMPIONS_MODELS_DIR"
fi
N="${1:-600}"; K="${2:-0}"; VALUE="${3:-off}"; MIX="${4:-0.0}"; NOISE="${5:-0.3}"
SEED=20260904
OUT=logs/p8_verdict
LOG="$OUT/p8_$(date +%Y%m%d_%H%M).log"
mkdir -p "$OUT"
VFLAG="--no-value"; [ "$VALUE" = "on" ] && VFLAG=""
run_arm() {  # name noise q
  echo "=== [$1] noise=$2 q=$3 K=$K value=$VALUE mix=$MIX $(date '+%F %T') ===" >> "$LOG"
  .venv/bin/python -m tools.check_search_expert --battles "$N" --depth 2 $VFLAG \
    --belief "$K" --opp-prior-mix "$MIX" --opp-seed "$SEED" --skip-random \
    --sensor-noise "$2" --sensor-q "$3" --json "$OUT/p8_$1.json" >> "$LOG" 2>&1
}
run_arm noise_unaware "$NOISE" 0.0
run_arm noise_aware   "$NOISE" "$NOISE"
run_arm clean_unaware 0.0 0.0
run_arm clean_aware   0.0 "$NOISE"
echo "=== done $(date '+%F %T') ===" >> "$LOG"
.venv/bin/python -m tools.p8_verdict "$OUT" | tee -a "$LOG"
