#!/usr/bin/env bash
# 終盤限定ハイブリッド操縦の並行A/B比較 (事前登録)。
#   bash scripts/endgame_bench.sh [対戦数/条件] [性格] [相手シード]
#
# ■ 事前登録 (2026-08-07, 測定前に固定):
#   - 背景: 弱点分析 (3,000戦) で負けの49%が詰め損ね (相手残り1体)。
#     終盤 (残数合計≤3) のみ方策→深さ2探索へ切り替える仮説を測る
#   - 条件: agent=policy vs agent=endgame。checkpoint=best、
#     相性選出、同一シードの同じ相手列、同時実行
#   - 戦数: 既定 各10,000戦 (δ=0.02, α=0.05, 検出力0.80 の必要数9,800を充足)
#   - 判定 (tools/two_prop_verdict, δ=0.02):
#       採用: 差>=+0.02 かつ 95%CI下限>0 / 棄却: CI上限<+0.02
#       判定不能: 戦数を倍増して1回だけ再測定。なお不能なら棄却
set -uo pipefail
cd "$(dirname "$0")/.."

BATTLES="${1:-10000}"
STYLE="${2:-balance}"
SEED="${3:-20260807}"
OUTDIR="logs/endgame_bench"
mkdir -p "$OUTDIR"

echo "[endgame_bench] policy vs endgame 並行実行 / 各${BATTLES}戦 / 相手シード${SEED}"
for AGENT in policy endgame; do
  .venv/bin/python -m champions_agent.train.evaluate \
    --play-style "$STYLE" --battles "$BATTLES" --opponent benchmark \
    --checkpoint best --agent "$AGENT" --opp-seed "$SEED" --no-save \
    > "$OUTDIR/$AGENT.log" 2>/dev/null &
  eval "PID_$AGENT=$!"
done
wait "$PID_policy" "$PID_endgame"

WINS_P=$(grep -o "'wins': [0-9]*" "$OUTDIR/policy.log" | grep -o "[0-9]*")
WINS_E=$(grep -o "'wins': [0-9]*" "$OUTDIR/endgame.log" | grep -o "[0-9]*")
if [ -z "$WINS_P" ] || [ -z "$WINS_E" ]; then
  echo "[endgame_bench] NG: 評価結果を読めませんでした ($OUTDIR/*.log)"
  exit 1
fi
echo "[endgame_bench] policy=${WINS_P}/${BATTLES} endgame=${WINS_E}/${BATTLES}"
.venv/bin/python -m tools.two_prop_verdict \
  "$WINS_E" "$BATTLES" "$WINS_P" "$BATTLES" --delta 0.02
