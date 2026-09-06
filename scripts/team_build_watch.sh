#!/bin/bash
# 構築系のバックグラウンド処理の進捗をログから抽出して 1 行ずつ流す (Monitor / 端末での監視用)。
#
#   scripts/team_build_watch.sh <run_id>                      # run.log と tools.team_build.run を監視
#   scripts/team_build_watch.sh <run_id> <log_path> <pgrep_pattern>
#     例: scripts/team_build_watch.sh chat_0906 \
#           logs/build_search/runs/chat_0906/evaluation/pick_ablation/ablation.log \
#           "tools.team_build.pick_ablation --run-id chat_0906"
#
# 段階の開始/終了、racing の判定、エラーだけを出力し、対象プロセスが
# 終了したらその旨を出力して抜ける (ログが静かでも取り残さない)。
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
run_id="${1:?run_id を指定}"
log="${2:-logs/build_search/runs/$run_id/run.log}"
proc="${3:-tools.team_build.run --run-id $run_id}"
if [ ! -f "$log" ]; then
  echo "[watch] $log が無い"
  exit 2
fi
pattern='after [0-9]+:|round offset|S[0-9]+[- :]|verdict|holdout|\[adapt|\[ablation\]|measure jobs|done|Traceback|[Ee]rror|failed|完了|終了|FAIL|PASS|INCONCLUSIVE'
tail -n 0 -F "$log" | grep -E --line-buffered "$pattern" &
tail_pid=$!
while pgrep -f "$proc" > /dev/null; do
  sleep 20
done
sleep 2
kill "$tail_pid" 2>/dev/null
echo "[watch] $proc のプロセスが終了 (最終行: $(tail -n 1 "$log"))"
