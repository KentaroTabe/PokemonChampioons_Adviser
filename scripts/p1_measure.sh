#!/usr/bin/env bash
# P1判定 (アンカー常駐の採否) の測定ラウンド。
# 事前登録: champions_agent/train/training_changes.json 2026-08-18 01:25 / 02:05 / 2026-08-24 12:40
#
#   bash scripts/p1_measure.sh round1   # 主要指標1回目 + 軸対照 + ベンチガードレール (逐次・約60分)
#   bash scripts/p1_measure.sh round2   # 主要指標2回目 (round1の主要指標から3時間以上空けて)
#   bash scripts/p1_measure.sh round2 <sleep_sec>  # 指定秒待ってからround2を実行
#
# すべて逐次・opp_seed固定・--no-save (昇格判定とプール抽選を汚さない)。
# 結果は logs/p1_verdict/p1_<日付>_<round>.log に追記される。
set -euo pipefail
cd "$(dirname "$0")/.."

ROUND="${1:?round1|round2 を指定}"
SLEEP_SEC="${2:-0}"
SEED=20260730
OUTDIR=logs/p1_verdict
LOG="$OUTDIR/p1_$(date +%Y%m%d)_${ROUND}.log"
ANCHOR_DIR="$PWD/$OUTDIR/anchor_models"
CKPT=champions_agent/train/checkpoints
PY=.venv/bin/python
mkdir -p "$OUTDIR"

if [ "$SLEEP_SEC" -gt 0 ]; then
  echo "round2 を $SLEEP_SEC 秒後に開始します ($(date '+%H:%M:%S'))"
  sleep "$SLEEP_SEC"
fi

stamp() {
  echo "=== [$1] $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$LOG"
}

# 主要指標: current の h2h vs 3性格_best (各3,000戦・逐次)
h2h_current() {
  for style in balance offense cycle; do
    stamp "current_h2h_${style}"
    "$PY" -m champions_agent.train.evaluate \
      --play-style balance --battles 3000 --opponent agents \
      --agents-style "$style" --checkpoint current \
      --selection matchup --opp-seed "$SEED" --no-save >> "$LOG" 2>&1
  done
}

# 軸対照: P1適用直前 (2026-08-18 00:46) の凍結アンカーで同じ h2h を測る。
# CHAMPIONS_MODELS_DIR でアンカーを current として読ませ、相手の _best は本物を複製する
setup_anchor_dir() {
  mkdir -p "$ANCHOR_DIR"
  cp "$CKPT/anchors/balance_20260818.zip" "$ANCHOR_DIR/battle_policy_balance.zip"
  for style in balance offense cycle; do
    cp "$CKPT/battle_policy_${style}_best.zip" "$ANCHOR_DIR/"
  done
}

h2h_anchor() {
  for style in balance offense cycle; do
    stamp "anchor_h2h_${style}"
    CHAMPIONS_MODELS_DIR="$ANCHOR_DIR" "$PY" -m champions_agent.train.evaluate \
      --play-style balance --battles 3000 --opponent agents \
      --agents-style "$style" --checkpoint current \
      --selection matchup --opp-seed "$SEED" --no-save >> "$LOG" 2>&1
  done
}

# ベンチガードレール: 同日・同軸のペア比較 (アンカー vs current、各10,000戦)
bench_pair() {
  stamp "anchor_bench_10000"
  CHAMPIONS_MODELS_DIR="$ANCHOR_DIR" "$PY" -m champions_agent.train.evaluate \
    --play-style balance --battles 10000 --opponent benchmark \
    --checkpoint current --selection matchup --opp-seed "$SEED" --no-save \
    >> "$LOG" 2>&1
  stamp "current_bench_10000"
  "$PY" -m champions_agent.train.evaluate \
    --play-style balance --battles 10000 --opponent benchmark \
    --checkpoint current --selection matchup --opp-seed "$SEED" --no-save \
    >> "$LOG" 2>&1
}

case "$ROUND" in
  round1)
    stamp "round1_start"
    h2h_current
    stamp "round1_main_metric_done"   # round2 はここから3時間以上空ける
    setup_anchor_dir
    h2h_anchor
    bench_pair
    stamp "round1_done"
    ;;
  round2)
    stamp "round2_start"
    h2h_current
    stamp "round2_done"
    ;;
  *)
    echo "不明なラウンド: $ROUND" >&2
    exit 1
    ;;
esac

echo "完了: $LOG"
