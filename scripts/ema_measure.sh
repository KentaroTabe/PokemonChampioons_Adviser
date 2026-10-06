#!/usr/bin/env bash
# EMA配布判定 (P5) の測定ラウンド。
# 事前登録: champions_agent/train/training_changes.json 2026-08-25 20:45
#
#   bash scripts/ema_measure.sh round1
#   bash scripts/ema_measure.sh round2 [sleep_sec]   # 3時間以上空けて
#
# 各ラウンドで current と EMA の h2h vs 3性格_best (各3,000戦) を
# 同一条件・逐次で測る (currentも同時に測るのは、学習が進み続けるため
# 「後から測った方が有利」の時刻バイアスを対にして消すため)。
# すべて --no-save (昇格判定とプール抽選を汚さない)。
set -euo pipefail
cd "$(dirname "$0")/.."

ROUND="${1:?round1|round2 を指定}"
SLEEP_SEC="${2:-0}"
SEED=20260730
OUTDIR=logs/ema_verdict
LOG="$OUTDIR/ema_$(date +%Y%m%d)_${ROUND}.log"
PY=.venv/bin/python
mkdir -p "$OUTDIR"

if [ "$SLEEP_SEC" -gt 0 ]; then
  echo "$ROUND を $SLEEP_SEC 秒後に開始します ($(date '+%H:%M:%S'))"
  sleep "$SLEEP_SEC"
fi

stamp() {
  echo "=== [$1] $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$LOG"
}

stamp "${ROUND}_start"
for ckpt in current ema; do
  for style in balance offense cycle; do
    stamp "${ckpt}_h2h_${style}"
    "$PY" -m champions_agent.train.evaluate \
      --play-style balance --battles 3000 --opponent agents \
      --agents-style "$style" --checkpoint "$ckpt" \
      --selection matchup --opp-seed "$SEED" --no-save >> "$LOG" 2>&1
  done
done
stamp "${ROUND}_done"
echo "完了: $LOG"
