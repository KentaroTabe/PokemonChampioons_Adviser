#!/usr/bin/env bash
# 選出方式の比較 (相性 / モデルargmax / 読み合いの均衡解) を同じ相手列で回す。
#   bash scripts/selection_bench.sh [対戦数] [性格] [相手シード]
# 操縦は配布相当の _best 方策で固定し、差が選出方式の寄与だけになるようにする。
set -euo pipefail
cd "$(dirname "$0")/.."

BATTLES="${1:-1000}"
STYLE="${2:-balance}"
SEED="${3:-20260731}"

echo "[selection_bench] matchup vs model vs matrix / ${BATTLES}戦ずつ / 相手シード${SEED}"
for SEL in matchup model matrix; do
  echo "--- selection=${SEL} ---"
  .venv/bin/python -m champions_agent.train.evaluate \
    --play-style "$STYLE" --battles "$BATTLES" --opponent benchmark \
    --checkpoint best --selection "$SEL" --opp-seed "$SEED" --no-save \
    2>/dev/null
done
