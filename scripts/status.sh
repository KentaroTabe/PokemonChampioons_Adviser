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
echo "=== ポート (既定 + 前回起動時に決めたもの。[別プロセス] は自分たちの常駐ではない) ==="
. "$ROOT/scripts/lib/ports.sh"
load_ports
for PORT in $(printf '%s\n' "$ADVISOR_PORT_DEFAULT" "$ADVISOR_PORT" 8100 "$FRONTEND_PORT_DEFAULT" "$FRONTEND_PORT" | awk '!seen[$0]++'); do
  OUT="$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | tail -n +2 | head -1)"
  if [ -z "$OUT" ]; then
    echo "$PORT: (未使用)"
  elif [ "$PORT" = "$ADVISOR_PORT" ] || [ "$PORT" = "$ADVISOR_PORT_DEFAULT" ]; then
    echo "$PORT: $OUT $([ "$(port_state "$PORT" "$ADVISOR_PATTERN")" = ours ] && echo "[アドバイザー]" || echo "[別プロセス: $(port_owner "$PORT")]")"
  elif [ "$PORT" = "$FRONTEND_PORT" ] || [ "$PORT" = "$FRONTEND_PORT_DEFAULT" ]; then
    echo "$PORT: $OUT $([ "$(port_state "$PORT" "$FRONTEND_PATTERN")" = ours ] && echo "[フロントエンド]" || echo "[別プロセス: $(port_owner "$PORT")]")"
  else
    echo "$PORT: $OUT"
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
