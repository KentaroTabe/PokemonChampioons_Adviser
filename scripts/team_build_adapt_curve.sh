#!/usr/bin/env bash
# 選出適応の learning curve (tools/team_build/adapt_curve.py) をセッション非依存 (nohup + disown) で回す。
#   bash scripts/team_build_adapt_curve.sh <run_id> --candidates L00_C001,L02_C023 [他の引数...]
# 進捗: logs/build_search/runs/<run_id>/evaluation/adapt_curve/curve.log
# 停止: pkill -f "tools.team_build.adapt_curve --run-id <run_id>"
set -euo pipefail
cd "$(dirname "$0")/.."
RUN="$1"; shift
mkdir -p "logs/build_search/runs/$RUN/evaluation/adapt_curve"
LOG="logs/build_search/runs/$RUN/evaluation/adapt_curve/nohup.log"
nohup .venv/bin/python -m tools.team_build.adapt_curve --run-id "$RUN" "$@" > "$LOG" 2>&1 &
disown
echo "[adapt_curve] 起動: run=$RUN pid=$! log=$LOG"
