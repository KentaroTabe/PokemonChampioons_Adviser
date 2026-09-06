#!/usr/bin/env bash
# 選出方策 ablation (tools/team_build/pick_ablation.py) をセッション非依存 (nohup + disown) で回す。
#   bash scripts/team_build_pick_ablation.sh <run_id> --candidates L00_C001,L02_C023 [他の引数...]
# 進捗: logs/build_search/runs/<run_id>/evaluation/pick_ablation/ablation.log
# 停止: pkill -f "tools.team_build.pick_ablation --run-id <run_id>"
set -euo pipefail
cd "$(dirname "$0")/.."
RUN="$1"; shift
mkdir -p "logs/build_search/runs/$RUN/evaluation/pick_ablation"
LOG="logs/build_search/runs/$RUN/evaluation/pick_ablation/nohup.log"
nohup .venv/bin/python -m tools.team_build.pick_ablation --run-id "$RUN" "$@" > "$LOG" 2>&1 &
disown
echo "[pick_ablation] 起動: run=$RUN pid=$! log=$LOG"
