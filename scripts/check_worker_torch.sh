#!/bin/bash
# 学習ワーカーのプロセスに torch (libtorch) がマップされているかを実測する。
#
#   bash scripts/check_worker_torch.sh <pid> [pid...]
set -uo pipefail

for PID in "$@"; do
  N=$(lsof -p "$PID" 2>/dev/null | grep -c "libtorch\|site-packages/torch")
  RSS=$(ps -o rss= -p "$PID" | awk '{printf "%.0f", $1/1024}')
  if [ "$N" -gt 0 ]; then
    echo "pid=$PID rss=${RSS}MB torch=マップ済み (${N}ファイル)"
  else
    echo "pid=$PID rss=${RSS}MB torch=なし"
  fi
done
