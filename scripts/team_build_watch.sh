#!/bin/bash
# 構築 run の進捗を run.log から抽出して 1 行ずつ流す (Monitor / 端末での監視用)。
#
#   scripts/team_build_watch.sh <run_id>
#
# 段階の開始/終了、racing の判定、エラーだけを出力し、run プロセスが
# 終了したらその旨を出力して抜ける (ログが静かでも取り残さない)。
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
run_id="${1:?run_id を指定}"
log="logs/build_search/runs/$run_id/run.log"
if [ ! -f "$log" ]; then
  echo "[watch] $log が無い"
  exit 2
fi
pattern='after [0-9]+:|round offset|S[0-9]+[- :]|verdict|holdout|adapt|Traceback|[Ee]rror|完了|終了|FAIL|PASS|INCONCLUSIVE'
tail -n 0 -F "$log" | grep -E --line-buffered "$pattern" &
tail_pid=$!
while pgrep -f "tools.team_build.run --run-id $run_id" > /dev/null; do
  sleep 20
done
sleep 2
kill "$tail_pid" 2>/dev/null
echo "[watch] run $run_id のプロセスが終了 (最終行: $(tail -n 1 "$log"))"
