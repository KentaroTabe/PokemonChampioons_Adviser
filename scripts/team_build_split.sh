#!/usr/bin/env bash
# 相手構築の系統化と階層分割 (SEARCH/SELECTION/HOLDOUT) を作る。
#   bash scripts/team_build_split.sh <run_id> <out_dir> [seed] [top_n]
set -euo pipefail
cd "$(dirname "$0")/.."
RUN="$1"; OUT="$2"; SEED="${3:-0}"; TOPN="${4:-200}"
.venv/bin/python -m tools.team_build.opponents --run-id "$RUN" --out "$OUT" --seed "$SEED" --top-n "$TOPN" \
  2>&1 | grep -v "NotOpenSSL\|warnings.warn"
