#!/usr/bin/env bash
# P6-b判定 (相手行動の事前分布に自己対戦方策を混ぜる) の測定: λ=0 vs λ=0.5。
# 事前登録: champions_agent/train/training_changes.json (P6-b、起動時に登録)
#   bash scripts/p6b_measure.sh [battles] [belief_k] [value:on|off]
# belief_k と葉評価は P7 / P6-a の判定結果の構成で固定して渡す。
set -euo pipefail
cd "$(dirname "$0")/.."
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
