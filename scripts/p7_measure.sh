#!/usr/bin/env bash
# P7判定 (belief-weighted search) の測定1: 3腕を同一相手列で対に測る。
# 事前登録: champions_agent/train/training_changes.json 2026-09-04 19:40
#   bash scripts/p7_measure.sh [battles]   (既定300、逐次、約3×N戦)
# 腕: current (攻撃系252振りの単一仮定) / map (最尤仮説1つ) / belief (K=8)
# depth=2、RL価値の葉評価はOFF (P6-a で別途判定)、型は META_PIN の固定軸。
set -euo pipefail
cd "$(dirname "$0")/.."
N="${1:-300}"
SEED=20260904
OUT=logs/p7_verdict
LOG="$OUT/p7_$(date +%Y%m%d_%H%M).log"
mkdir -p "$OUT"
for arm in current:0 map:1 belief:8; do
  name="${arm%%:*}"; k="${arm##*:}"
  echo "=== [$name] belief_k=$k $(date '+%F %T') ===" >> "$LOG"
  .venv/bin/python -m tools.check_search_expert \
    --battles "$N" --depth 2 --no-value --belief "$k" \
    --opp-seed "$SEED" --skip-random --json "$OUT/p7_${name}.json" >> "$LOG" 2>&1
done
echo "=== done $(date '+%F %T') ===" >> "$LOG"
.venv/bin/python -m tools.p7_verdict "$OUT" | tee -a "$LOG"
