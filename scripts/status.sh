#!/usr/bin/env bash
# 常駐プロセスとポートの実測。稼働状況の報告は必ずこれを使う。
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "=== 常駐プロセス ==="
if ! pgrep -fl "audit_monitor|train_forever|train_battle|smoke_train|uvicorn|human_battle|pokemon-showdown|tools.control_panel"; then
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
for PORT in $(printf '%s\n' "$ADVISOR_PORT_DEFAULT" "$ADVISOR_PORT" "$SHOWDOWN_PORT_DEFAULT" "$FRONTEND_PORT_DEFAULT" "$FRONTEND_PORT" "$CONTROL_PORT_DEFAULT" | awk '!seen[$0]++'); do
  OUT="$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | tail -n +2 | head -1)"
  if [ -z "$OUT" ]; then
    echo "$PORT: (未使用)"
  elif [ "$PORT" = "$ADVISOR_PORT" ] || [ "$PORT" = "$ADVISOR_PORT_DEFAULT" ]; then
    echo "$PORT: $OUT $([ "$(port_state "$PORT" "$ADVISOR_PATTERN")" = ours ] && echo "[アドバイザー]" || echo "[別プロセス: $(port_owner "$PORT")]")"
  elif [ "$PORT" = "$FRONTEND_PORT" ] || [ "$PORT" = "$FRONTEND_PORT_DEFAULT" ]; then
    echo "$PORT: $OUT $([ "$(port_state "$PORT" "$FRONTEND_PATTERN")" = ours ] && echo "[フロントエンド]" || echo "[別プロセス: $(port_owner "$PORT")]")"
  elif [ "$PORT" = "$CONTROL_PORT_DEFAULT" ]; then
    echo "$PORT: $OUT $([ "$(port_state "$PORT" "tools.control_panel")" = ours ] && echo "[操作パネル]" || echo "[別プロセス: $(port_owner "$PORT")]")"
  else
    echo "$PORT: $OUT"
  fi
done

echo
echo "=== launchctl ==="
if ! launchctl list 2>/dev/null | grep championsadviser; then
  echo "(登録なし)"
fi

# 日次定点 (tools.track_progress) の経過時間。launchd の calendar ジョブは前回が生きている間は再発火せず、
# 評価がハングすると定点が黙って欠測する (2026-09-18〜23、docs/incidents/reports/2026-09-18-track-progress-hang-no-timeout.md)
etime_minutes() {
  local et="$1" days=0 rest="$1" a b c h m
  case "$et" in *-*) days="${et%%-*}"; rest="${et#*-}";; esac
  IFS=: read -r a b c <<< "$rest"
  if [ -n "$c" ]; then h="$a"; m="$b"; else h=0; m="$a"; fi
  echo $(( (10#$days * 24 + 10#$h) * 60 + 10#$m ))
}
echo
echo "=== 日次定点 (tools.track_progress) ==="
. "$ROOT/config/jobs.env"
TP_PID="$(pgrep -f 'tools.track_progress' | head -1)"
if [ -z "$TP_PID" ]; then
  echo "(停止中) 日次ログの最終行: $(tail -n 1 "$ROOT/logs/track_progress_daily.log" 2>/dev/null)"
else
  TP_ET="$(ps -o etime= -p "$TP_PID" | tr -d ' ')"
  TP_MIN="$(etime_minutes "$TP_ET")"
  if [ "$TP_MIN" -ge "${TRACK_PROGRESS_MAX_MINUTES:-240}" ]; then
    echo "⚠ pid $TP_PID が $TP_ET ($TP_MIN 分) 走り続けている (上限 ${TRACK_PROGRESS_MAX_MINUTES:-240} 分)。評価のハングを疑う:"
    pgrep -fl "champions_agent.train.evaluate" || echo "  (evaluate プロセスなし)"
  else
    echo "実行中: pid $TP_PID 経過 $TP_ET"
  fi
fi

echo
echo "=== 最新の対戦ログ ==="
# pipefail 下では head が先に閉じると ls が SIGPIPE で非 0 になり、結果の後に "(なし)" が出ることがあった → 変数に受ける
LATEST_BATTLES="$(ls -lt "$ROOT/logs/battles"/*.jsonl 2>/dev/null | head -3)"
if [ -n "$LATEST_BATTLES" ]; then
  echo "$LATEST_BATTLES"
else
  echo "(なし)"
fi
