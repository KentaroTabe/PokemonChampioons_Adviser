#!/usr/bin/env bash
# 外部構築テキストをチームプールへ取り込む
#   bash scripts/import_teams.sh <テキストファイル...>
#   bash scripts/import_teams.sh --list
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python -m tools.import_teams "$@" 2>/dev/null
