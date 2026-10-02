#!/usr/bin/env bash
# Showdown の技データから advisor/data/move_effects.json と move_priority.json を起こす (tools/build_move_data.py)。
#   scripts/build_move_data.sh            # 生成
#   scripts/build_move_data.sh --dry-run  # 要約だけ
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/python -m tools.build_move_data "$@"
