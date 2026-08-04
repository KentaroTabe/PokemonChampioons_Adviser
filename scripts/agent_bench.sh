#!/usr/bin/env bash
# 任意のエージェント構成をベンチマーク評価する。
#   bash scripts/agent_bench.sh <policy|hybrid|search> [対戦数] [性格] [相手シード] [選出]
set -euo pipefail
cd "$(dirname "$0")/.."

AGENT="${1:?policy / hybrid / search を指定}"
BATTLES="${2:-1000}"
STYLE="${3:-balance}"
SEED="${4:-20260731}"
SELECTION="${5:-matchup}"

exec .venv/bin/python -m champions_agent.train.evaluate \
  --play-style "$STYLE" --battles "$BATTLES" --opponent benchmark \
  --checkpoint best --agent "$AGENT" --opp-seed "$SEED" \
  --selection "$SELECTION" --no-save \
  2>/dev/null
