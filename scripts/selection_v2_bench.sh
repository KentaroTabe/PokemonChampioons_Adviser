#!/usr/bin/env bash
# 選出モデル v1 vs v2 の並行A/B比較 (事前登録)。
#   bash scripts/selection_v2_bench.sh [対戦数/条件] [性格] [相手シード]
#
# ■ 事前登録 (2026-08-02, 測定前に固定):
#   - 条件: selection=model (v1汎用) vs selection=model2 (v2汎用)。
#     操縦は両条件とも _best。相手は同一シードの同じ構築列
#   - 戦数: 既定 各10,000戦 (δ=0.02, α=0.05, 検出力0.80 の必要数9,800を充足)
#   - 判定 (tools/two_prop_verdict, δ=0.02):
#       採用: 差>=+0.02 かつ 95%CI下限>0 / 棄却: CI上限<+0.02
#       判定不能: 戦数を倍増して1回だけ再測定。なお不能なら棄却
#   - オフライン前提条件: v2の未知チーム検証の改善率がv1 (+5.6%) を
#     上回っていること。下回る場合はこの測定自体を行わない
#
# 2条件は同時に実行する (アカウント名は自動で一意)。負荷は両条件に同時に
# かかるので対称。
set -uo pipefail
cd "$(dirname "$0")/.."

BATTLES="${1:-10000}"
STYLE="${2:-balance}"
SEED="${3:-20260802}"
OUTDIR="logs/selection_v2_bench"
mkdir -p "$OUTDIR"

echo "[v2bench] v1 vs v2 並行実行 / 各${BATTLES}戦 / 相手シード${SEED}"
for SEL in model model2; do
  .venv/bin/python -m champions_agent.train.evaluate \
    --play-style "$STYLE" --battles "$BATTLES" --opponent benchmark \
    --checkpoint best --selection "$SEL" --opp-seed "$SEED" --no-save \
    > "$OUTDIR/$SEL.log" 2>/dev/null &
  eval "PID_$SEL=$!"
done
wait "$PID_model" "$PID_model2"

WINS_V1=$(grep -o "'wins': [0-9]*" "$OUTDIR/model.log" | grep -o "[0-9]*")
WINS_V2=$(grep -o "'wins': [0-9]*" "$OUTDIR/model2.log" | grep -o "[0-9]*")
if [ -z "$WINS_V1" ] || [ -z "$WINS_V2" ]; then
  echo "[v2bench] NG: 評価結果を読めませんでした ($OUTDIR/*.log)"
  exit 1
fi

echo "[v2bench] v1=${WINS_V1}/${BATTLES} v2=${WINS_V2}/${BATTLES}"
.venv/bin/python -m tools.two_prop_verdict \
  "$WINS_V2" "$BATTLES" "$WINS_V1" "$BATTLES" --delta 0.02
