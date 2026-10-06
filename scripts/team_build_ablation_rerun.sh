#!/usr/bin/env bash
# 終わった run の ablation で失敗した腕 (Showdown の接続断など) だけを測り直し、分解を計算し直す。
#   bash scripts/team_build_ablation_rerun.sh <run_id> [--parallel 2]
set -euo pipefail
cd "$(dirname "$0")/.."
RUN="$1"; shift
mkdir -p "logs/build_search/runs/$RUN"
LOG="logs/build_search/runs/$RUN/ablation_rerun.log"
.venv/bin/python -m tools.team_build.ablation --run-id "$RUN" "$@" > "$LOG" 2>&1
grep -v "NotOpenSSL\|warnings.warn\|champions_dex_patch" "$LOG" | tail -20
echo "全文: $LOG"
