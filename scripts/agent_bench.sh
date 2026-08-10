#!/usr/bin/env bash
# 配布相当 (_best) のベンチマーク評価。選出方式を切り替えて測れる。
#   bash scripts/agent_bench.sh [対戦数] [性格] [相手シード] [選出]
# (旧: 第1引数でhybrid等を選べたが、複合体は実測棄却で削除した 2026-08-08)
set -euo pipefail
cd "$(dirname "$0")/.."

BATTLES="${1:-1000}"
STYLE="${2:-balance}"
SEED="${3:-20260731}"
SELECTION="${4:-matchup}"

exec .venv/bin/python -m champions_agent.train.evaluate \
  --play-style "$STYLE" --battles "$BATTLES" --opponent benchmark \
  --checkpoint best --opp-seed "$SEED" \
  --selection "$SELECTION" --no-save \
  2>/dev/null
