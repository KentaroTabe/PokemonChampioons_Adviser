#!/usr/bin/env bash
# 構築システムの run をセッション非依存 (nohup + disown) で回す。数時間かかる full run 用。
#   bash scripts/team_build_nohup.sh <run_id> [run.py の引数...]
# 進捗: logs/build_search/runs/<run_id>/run.log、停止: pkill -f "tools.team_build.run --run-id <run_id>"
set -euo pipefail
cd "$(dirname "$0")/.."
RUN="$1"; shift
mkdir -p "logs/build_search/runs/$RUN"
LOG="logs/build_search/runs/$RUN/nohup.log"
nohup .venv/bin/python -m tools.team_build.run --run-id "$RUN" "$@" > "$LOG" 2>&1 &
disown
echo "[team_build] 起動: run=$RUN pid=$! log=$LOG"
