#!/usr/bin/env bash
# 助言エンジン (advisor-as-player) の基準測定: vs ベンチマーク、固定軸・同一相手列。
#   bash scripts/advisor_baseline.sh [battles] [belief_k] [extra args...]
# 探索結果は現状の推奨に未統合のため、既定は --belief-k 0 (探索コストを省く)。
set -euo pipefail
cd "$(dirname "$0")/.."
N="${1:-100}"; K="${2:-0}"; shift 2 2>/dev/null || shift $# 
SEED=20260904
OUT=logs/advisor_verdict
mkdir -p "$OUT"
STAMP="$(date +%Y%m%d_%H%M)"
.venv/bin/python -m tools.check_advisor_player --battles "$N" --opp-seed "$SEED" \
  --skip-random --belief-k "$K" --json "$OUT/advisor_${STAMP}.json" "$@" \
  > "$OUT/advisor_${STAMP}.log" 2>&1
grep -E "===|勝率|レイテンシ|意思決定|例外" "$OUT/advisor_${STAMP}.log"
