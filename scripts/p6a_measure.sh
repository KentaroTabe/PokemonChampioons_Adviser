#!/usr/bin/env bash
# P6-a判定 (RL価値の葉評価の是非) の測定: 葉評価 ON/OFF を同一相手列で対に測る。
# 事前登録: champions_agent/train/training_changes.json 2026-09-04 (P6-a)
#   bash scripts/p6a_measure.sh [battles]   (既定300、逐次)
# 探索プレイヤー depth=2、相手型は単一仮定 (K=0、P7の結果と独立に葉評価だけを測る)
set -euo pipefail
cd "$(dirname "$0")/.."
# RLモデルを測定開始時点にピン止め (腕の起動時刻差で後の腕が有利になるのを防ぐ、9/5)
if [ -z "${CHAMPIONS_MODELS_DIR:-}" ]; then
  export CHAMPIONS_MODELS_DIR="$(bash scripts/pin_models.sh)"
  echo "[pin] CHAMPIONS_MODELS_DIR=$CHAMPIONS_MODELS_DIR"
fi
N="${1:-600}"
SEED=20260904
OUT=logs/p6a_verdict
LOG="$OUT/p6a_$(date +%Y%m%d_%H%M).log"
mkdir -p "$OUT"
echo "=== [value_off] $(date '+%F %T') ===" >> "$LOG"
.venv/bin/python -m tools.check_search_expert --battles "$N" --depth 2 --no-value \
  --belief 0 --opp-seed "$SEED" --skip-random --json "$OUT/p6a_value_off.json" >> "$LOG" 2>&1
echo "=== [value_on] $(date '+%F %T') ===" >> "$LOG"
.venv/bin/python -m tools.check_search_expert --battles "$N" --depth 2 \
  --belief 0 --opp-seed "$SEED" --skip-random --json "$OUT/p6a_value_on.json" >> "$LOG" 2>&1
echo "=== done $(date '+%F %T') ===" >> "$LOG"
.venv/bin/python -m tools.p6a_verdict "$OUT" | tee -a "$LOG"
