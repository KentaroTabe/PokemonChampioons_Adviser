#!/usr/bin/env bash
# 報酬スイープを呼び出し元から切り離して回す (長時間でも打ち切られない)。
#   bash scripts/reward_sweep_bg.sh [1回のステップ数] [回数] [評価戦数] [性格]
#   tail -f logs/reward_sweep/run.log   で進捗を見る
#
# train_battle は学習の最後にしか保存しないため、実行が途中で打ち切られると
# その回の学習が丸ごと失われる (2026-07-30に発生し、同一モデルを4回測って
# 「差がない」という無意味な結論を出した)。切り離して確実に完走させる。
set -euo pipefail
cd "$(dirname "$0")/.."

STEPS="${1:-100000}"
ROUNDS="${2:-6}"
BATTLES="${3:-600}"
STYLES="${4:-balance}"
ARMS="${5:-}"
SEEDS="${6:-1}"
LOG="logs/reward_sweep/run.log"

mkdir -p logs/reward_sweep
# setsid で新しいセッションに切り離す。nohup だけだと SIGHUP しか無視できず、
# 呼び出し元のプロセスグループごと落とされると道連れになる
# (2026-07-30: 起動直後に落ちて1回目の保存にも届かなかった)
setsid nohup bash scripts/reward_sweep_run.sh "$STEPS" "$ROUNDS" "$BATTLES" \
  "$STYLES" "$ARMS" "$SEEDS" > "$LOG" 2>&1 < /dev/null &
echo "[sweep] 切り離して開始しました (PID $!)"
echo "[sweep] 進捗: tail -f $LOG"
echo "[sweep] 停止: bash scripts/reward_sweep_stop.sh"
