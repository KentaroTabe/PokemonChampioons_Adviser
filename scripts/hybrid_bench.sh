#!/usr/bin/env bash
# RL方策単体 (policy) と探索+RL価値の複合体 (hybrid) を同じ相手列で比較する。
#   bash scripts/hybrid_bench.sh [対戦数] [性格] [相手シード]
#
# 配布アドバイザーの実体は複合体だが、従来のベンチは方策単体しか測って
# いなかった。方策のPPO積み増しは効かないと実測済みのため、複合体の
# 改善を数字で追うにはこの経路を使う。
# どちらも配布相当の _best を使い、--no-save で昇格判定を汚さない。
#
# ⚠ 報酬スイープの実行中は回さないこと (CPUを取り合い両方の測定が歪む)。
set -euo pipefail
cd "$(dirname "$0")/.."

BATTLES="${1:-400}"
STYLE="${2:-balance}"
SEED="${3:-20260731}"

echo "[hybrid_bench] policy vs hybrid / ${BATTLES}戦ずつ / 相手シード${SEED}"
for AGENT in policy hybrid; do
  echo "--- agent=${AGENT} ---"
  .venv/bin/python -m champions_agent.train.evaluate \
    --play-style "$STYLE" --battles "$BATTLES" --opponent benchmark \
    --checkpoint best --agent "$AGENT" --opp-seed "$SEED" --no-save \
    2>/dev/null
done
