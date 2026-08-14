#!/bin/bash
# arch_v7 隔離学習の停止見張り。
#
#   bash scripts/arch_v7_watchdog.sh [チェック間隔秒=300] [停止判定秒=1800]
#
# チェックポイント (途中保存は約2分毎) が停止判定秒より古いのに
# arch_v7 の train_battle プロセスが生きている場合、デッドロックと
# みなして kill する。ラッパー (arch_v7_run.sh) のループが次ラウンドを
# チェックポイントから再開する。
#
# 背景 (2026-08-12): Showdown のチーム拒否popupを契機に poke-env が
# 応答待ちのままデッドロックし、約46時間気づかれずに学習が止まった。
# 異常時のみ標準出力に1行出す (Monitor 連携用)。
cd "$(dirname "$0")/.." || exit 1

INTERVAL="${1:-300}"
STALE_SEC="${2:-1800}"
CKPT="${3:-logs/arch_v7/checkpoints}/battle_policy_balance.zip"

while true; do
  sleep "$INTERVAL"
  if ! pgrep -f "arch_v7_run.sh" >/dev/null; then
    echo "[watchdog] arch_v7_run.sh が動いていません (完了または異常終了)"
    exit 0
  fi
  [ -f "$CKPT" ] || continue
  now=$(date +%s)
  mtime=$(stat -f %m "$CKPT")
  age=$((now - mtime))
  if [ "$age" -lt "$STALE_SEC" ]; then
    continue
  fi
  # arch_v7 側の train_battle を環境変数で特定する (本番と区別)
  for pid in $(pgrep -f "train_battle"); do
    if ps eww "$pid" 2>/dev/null | grep -q "CHAMPIONS_MODELS_DIR=.*arch_v7"; then
      echo "[watchdog] チェックポイントが${age}秒更新されていないため" \
           "停止中の学習プロセス (PID $pid) を再起動します"
      kill "$pid"
    fi
  done
done
