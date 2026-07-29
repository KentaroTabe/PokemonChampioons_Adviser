#!/usr/bin/env bash
# 選出データセットの要約を表示する (収集の進捗確認用)
#   bash scripts/selection_stats.sh
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python -m tools.selection_stats 2>/dev/null
