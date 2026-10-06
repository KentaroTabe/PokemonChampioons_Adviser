#!/usr/bin/env bash
# 参照チームの対照実験 (参照にも候補と同じ深さの選出モデル適応を与えて勝者と比べる) をセッション非依存で回す。
#   bash scripts/team_build_reference_adapt.sh <run_id> [reference_adapt.py の引数...]
# 進捗: logs/build_search/runs/<run_id>/run.log の [ref_full] の行、
# 監視: bash scripts/team_build_watch.sh <run_id> logs/build_search/runs/<run_id>/run.log "tools.team_build.reference_adapt --run-id <run_id>"
# 停止: pkill -f "tools.team_build.reference_adapt --run-id <run_id>"
set -euo pipefail
cd "$(dirname "$0")/.."
RUN="$1"; shift
LOG="logs/build_search/runs/$RUN/reference_full.nohup.log"
nohup .venv/bin/python -m tools.team_build.reference_adapt --run-id "$RUN" "$@" > "$LOG" 2>&1 &
disown
echo "[reference_adapt] 起動: run=$RUN pid=$! log=$LOG"
