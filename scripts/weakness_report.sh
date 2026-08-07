#!/usr/bin/env bash
# 操縦の弱点分析 (負けの型分類)。介入選択の材料に使う。
#   bash scripts/weakness_report.sh [対戦数]
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python -m tools.weakness_report --battles "${1:-3000}" 2>/dev/null
