#!/usr/bin/env bash
# 常駐プロセスとポートの実測。稼働状況の報告は必ずこれを使う。
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "=== 常駐プロセス ==="
if ! pgrep -fl "audit_monitor|train_forever|train_battle|smoke_train|uvicorn|human_battle|pokemon-showdown"; then
  echo "(該当なし)"
fi

echo
echo "=== 経過時間つき ==="
ps -eo pid,etime,%cpu,command \
  | grep -E "audit_monitor|train_forever|train_battle|uvicorn|pokemon-showdown" \
  | grep -v grep \
  | head -20

echo
echo "=== ポート ==="
for PORT in 8000 8100 3000; do
  OUT="$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | tail -n +2 | head -1)"
  if [ -n "$OUT" ]; then
    echo "$PORT: $OUT"
  else
    echo "$PORT: (未使用)"
  fi
done

echo
echo "=== launchctl ==="
if ! launchctl list 2>/dev/null | grep championsadviser; then
  echo "(登録なし)"
fi

echo
echo "=== 最新の対戦ログ ==="
ls -lt "$ROOT/logs/battles"/*.jsonl 2>/dev/null | head -3 || echo "(なし)"
