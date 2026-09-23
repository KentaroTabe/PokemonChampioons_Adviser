#!/usr/bin/env bash
# 固定入力での LLM 段の回帰測定を 1 腕、セッション非依存 (nohup) で回す。
#   bash scripts/team_build_regression.sh <run_id> <label> [regression.py の引数...]
#   例: bash scripts/team_build_regression.sh arch_0918 opus55 --model-opus claude-opus-5-5
#       bash scripts/team_build_regression.sh arch_0918 run --replay        # run の記録を再生 (費用なし、基準)
#   比較表: bash scripts/team_build_regression_compare.sh arch_0918
set -euo pipefail
cd "$(dirname "$0")/.."
RUN="$1"; LABEL="$2"; shift 2
DIR="logs/build_search/regression/$RUN"
mkdir -p "$DIR"
nohup .venv/bin/python -m tools.team_build.llm.regression --run-id "$RUN" --label "$LABEL" "$@" > "$DIR/$LABEL.log" 2>&1 &
disown
echo "[regression] 起動: run=$RUN label=$LABEL pid=$! log=$DIR/$LABEL.log"
