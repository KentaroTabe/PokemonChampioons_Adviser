#!/usr/bin/env bash
# 目標勝率の認定測定 (既存ベンチとは別枠。学習は止めない)
#   bash scripts/certify.sh [対戦数] [性格]
set -euo pipefail
cd "$(dirname "$0")/.."
BATTLES="${1:-400}"
STYLES="${2:-balance,offense,cycle}"
exec .venv/bin/python -m tools.certify --battles "$BATTLES" --styles "$STYLES" 2>/dev/null
