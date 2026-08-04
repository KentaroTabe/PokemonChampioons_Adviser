#!/usr/bin/env bash
# 実対戦ログの読み負荷 (重い択) レポート
#   bash scripts/read_burden_report.sh [対象日数]
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python -m tools.read_burden_report --days "${1:-30}" 2>/dev/null
