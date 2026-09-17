#!/usr/bin/env bash
# ゲーム内使用率順位 (championsbattledata の column_position) を過去スナップショットの pokemon_usage.rank に入れる (2026-09-18)。
#   bash scripts/repair_usage_ranks.sh          # dry-run (差分表示のみ)
#   bash scripts/repair_usage_ranks.sh apply    # DB を書き換える
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs/repair
LOG="logs/repair/usage_ranks_$(date +%Y%m%d_%H%M).log"
if [ "${1:-}" = "apply" ]; then
  .venv/bin/python -m tools.repair_usage_ranks --apply > "$LOG" 2>&1
else
  .venv/bin/python -m tools.repair_usage_ranks > "$LOG" 2>&1
fi
grep -v "NotOpenSSL\|warnings.warn" "$LOG" | tail -40
echo "全文: $LOG"
