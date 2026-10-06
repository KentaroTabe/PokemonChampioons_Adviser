#!/usr/bin/env bash
# フォルム丸めインシデント (2026-09-02) の使用率DB修復ラッパー。
#   bash scripts/repair_usage_forms.sh          # dry-run (差分表示のみ)
#   bash scripts/repair_usage_forms.sh apply    # DBを書き換える
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs/repair
LOG="logs/repair/usage_forms_$(date +%Y%m%d_%H%M).log"
if [ "${1:-}" = "apply" ]; then
  .venv/bin/python -m tools.repair_usage_forms --apply > "$LOG" 2>&1
else
  .venv/bin/python -m tools.repair_usage_forms > "$LOG" 2>&1
fi
tail -30 "$LOG"
echo "全文: $LOG"
