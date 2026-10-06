#!/usr/bin/env bash
# P6-b判定 (相手行動の事前分布に自己対戦方策を混ぜる) の測定: λ=0 vs λ=0.5。
# 事前登録: champions_agent/train/training_changes.json (P6-b、起動時に登録)
#   bash scripts/p6b_measure.sh [battles] [belief_k] [value:on|off]
# belief_k と葉評価は P7 / P6-a の判定結果の構成で固定して渡す。
set -euo pipefail
cd "$(dirname "$0")/.."
# RLモデルを測定開始時点にピン止め (腕の起動時刻差で後の腕が有利になるのを防ぐ、9/5)
if [ -z "${CHAMPIONS_MODELS_DIR:-}" ]; then
  export CHAMPIONS_MODELS_DIR="$(bash scripts/pin_models.sh)"
  echo "[pin] CHAMPIONS_MODELS_DIR=$CHAMPIONS_MODELS_DIR"
fi
N="${1:-600}"; K="${2:-0}"; VALUE="${3:-off}"
SEED=20260904
OUT=logs/p6b_verdict
LOG="$OUT/p6b_$(date +%Y%m%d_%H%M).log"
mkdir -p "$OUT"
VFLAG="--no-value"; [ "$VALUE" = "on" ] && VFLAG=""
for arm in mix0:0.0 mix05:0.5; do
  name="${arm%%:*}"; lam="${arm##*:}"
  echo "=== [$name] λ=$lam K=$K value=$VALUE $(date '+%F %T') ===" >> "$LOG"
  .venv/bin/python -m tools.check_search_expert --battles "$N" --depth 2 $VFLAG \
    --belief "$K" --opp-prior-mix "$lam" --opp-seed "$SEED" --skip-random \
    --json "$OUT/p6b_${name}.json" >> "$LOG" 2>&1
done
echo "=== done $(date '+%F %T') ===" >> "$LOG"
.venv/bin/python -m tools.p6b_verdict "$OUT" | tee -a "$LOG"
