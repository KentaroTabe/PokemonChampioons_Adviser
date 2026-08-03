#!/usr/bin/env bash
# 日次の進捗トラッキング (定点測定 + 停滞の簡易判定)
#   bash scripts/track_progress.sh [対戦数]
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python -m tools.track_progress --battles "${1:-3000}" 2>/dev/null
