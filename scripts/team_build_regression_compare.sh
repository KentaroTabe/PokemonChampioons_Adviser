#!/usr/bin/env bash
# 回帰測定の腕の比較表 (logs/build_search/regression/<run_id>/compare.md にも書く)。
#   bash scripts/team_build_regression_compare.sh <run_id>
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/python -m tools.team_build.llm.regression --run-id "$1" --compare 2>&1 | grep -v "NotOpenSSL\|warnings.warn"
