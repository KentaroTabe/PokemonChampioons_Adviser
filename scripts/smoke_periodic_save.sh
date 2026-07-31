#!/usr/bin/env bash
# 途中保存が実環境で機能するかを短時間の学習で確認する。
#   bash scripts/smoke_periodic_save.sh [ステップ数] [保存間隔]
# Showdown (8100) が起動している必要がある。
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python -m tools.smoke_periodic_save "${1:-3000}" "${2:-1000}"
