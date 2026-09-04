#!/usr/bin/env bash
# 構築決定用の進化探索 (段階2相当・大きめ予算)。
#   bash scripts/build_search.sh [population] [generations] [battles]
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs/build_search
LOG="logs/build_search/evolve_$(date +%Y%m%d_%H%M).log"
.venv/bin/python -m tools.evolve_teams \
  --population "${1:-14}" --generations "${2:-4}" --battles "${3:-100}" \
  --concurrency 4 --forecast-mix 0.3 --archive-mix 0.2 \
  --set-mut 0.5 --seed 20260901 > "$LOG" 2>&1
echo "完了: $LOG"
