#!/usr/bin/env bash
# 報酬スイープ本体 (reward_sweep_bg.sh から切り離して呼ばれる)。
# 本番の学習ループを止めて回し、終了時に必ず戻す。
set -euo pipefail
cd "$(dirname "$0")/.."

STEPS="${1:-100000}"
ROUNDS="${2:-6}"
BATTLES="${3:-600}"
STYLES="${4:-balance}"
JOB="com.championsadviser.train"

restore() {
  echo "[sweep] 本番の学習ループを再開します"
  launchctl start "$JOB" 2>/dev/null || true
}
trap restore EXIT

echo "[sweep] 開始 $(date '+%H:%M:%S') / ${ROUNDS}回 x ${STEPS}ステップ"
echo "[sweep] 本番の学習ループを一時停止します ($JOB)"
launchctl stop "$JOB" 2>/dev/null || true
sleep 5

.venv/bin/python -m tools.reward_sweep \
  --steps "$STEPS" --rounds "$ROUNDS" --battles "$BATTLES" \
  --styles "$STYLES" 2>/dev/null

echo "[sweep] 完了 $(date '+%H:%M:%S')"
