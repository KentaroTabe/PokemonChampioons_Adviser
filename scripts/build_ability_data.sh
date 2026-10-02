#!/usr/bin/env bash
# Showdown の特性データ + 参戦種から advisor/data/ability_effects.json を起こす (tools/build_ability_data.py)。
#   scripts/build_ability_data.sh            # 生成
#   scripts/build_ability_data.sh --dry-run  # 要約だけ
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/python -m tools.build_ability_data "$@"
