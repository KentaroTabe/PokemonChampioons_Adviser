#!/usr/bin/env bash
# 進化探索を測定目的で1回まわす (読み負荷と勝率の相関測定など)。
#   bash scripts/evolve_measure.sh [集団サイズ] [世代数] [戦数/個体]
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python -m tools.evolve_teams \
  --population "${1:-12}" --generations "${2:-1}" --battles "${3:-80}" \
  2>/dev/null
