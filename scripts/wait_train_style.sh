#!/bin/bash
# 指定スタイルの学習プロセス (tools.smoke_train --play-style <style>) が
# 現れるまで待ち、検出したらPIDを表示して終了する。
#
#   bash scripts/wait_train_style.sh balance [timeout_sec]
set -uo pipefail

STYLE="${1:?usage: wait_train_style.sh <style> [timeout_sec]}"
TIMEOUT="${2:-3600}"
END=$((SECONDS + TIMEOUT))

while [ "$SECONDS" -lt "$END" ]; do
  PID=$(pgrep -f "smoke_train --play-style $STYLE" | head -1)
  if [ -n "$PID" ]; then
    echo "[wait] $STYLE の学習プロセスを検出: pid=$PID ($(date '+%F %T'))"
    exit 0
  fi
  sleep 5
done
echo "[wait] タイムアウト: $STYLE は ${TIMEOUT}秒以内に始まらなかった"
exit 1
