#!/usr/bin/env bash
# 報酬設計の並列スイープ。本番の学習ループを一時停止して回し、必ず戻す。
#   bash scripts/reward_sweep.sh [ステップ数] [評価戦数] [性格]
#
# 学習ループを止める理由: 8コアに対し本番(N_ENVS=4)と4条件が同時に走ると
# 取り合いになり、条件間の学習量が揃わず比較にならないため。
set -euo pipefail
cd "$(dirname "$0")/.."

STEPS="${1:-400000}"
BATTLES="${2:-400}"
STYLES="${3:-balance}"
JOB="com.championsadviser.train"

restore() {
  echo "[sweep] 本番の学習ループを再開します"
  launchctl start "$JOB" 2>/dev/null || true
}
trap restore EXIT

echo "[sweep] 本番の学習ループを一時停止します ($JOB)"
launchctl stop "$JOB" 2>/dev/null || true
sleep 5

.venv/bin/python -m tools.reward_sweep \
  --steps "$STEPS" --battles "$BATTLES" --styles "$STYLES" 2>/dev/null
