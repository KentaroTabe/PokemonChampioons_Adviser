#!/usr/bin/env bash
# 決定再生ハーネス: 認識誤りの推奨反転率をフィールド別に測る。
#   bash scripts/advice_replay.sh [ログ数]
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python -m tools.advice_replay --logs "${1:-12}" 2>/dev/null
