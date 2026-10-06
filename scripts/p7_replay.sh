#!/usr/bin/env bash
# P7 測定2: 決定再生ハーネスで点推定 (K=0) と多世界 (K=8) の推奨反転率を比べる。
#   bash scripts/p7_replay.sh [logs]   (既定3ログ。K=8は探索が数倍遅い)
set -euo pipefail
cd "$(dirname "$0")/.."
N="${1:-3}"
OUT=logs/p7_verdict
mkdir -p "$OUT"
for k in 0 8; do
  .venv/bin/python -m tools.advice_replay --logs "$N" --belief-k "$k" \
    --out "$OUT/replay_k${k}.json" > "$OUT/replay_k${k}.log" 2>&1
  echo "=== K=$k ==="; grep -A 12 "摂動" "$OUT/replay_k${k}.log" | head -14
done
