#!/usr/bin/env bash
# 報酬スイープの再評価本体 (学習なし。学習済みの条件を測り直す)。
# 事前登録した判定手順の「判定不能→戦数を倍増して1回だけ再測定」で使う。
# 本番の学習ループを止めて回し、終了時に必ず戻す。
set -euo pipefail
cd "$(dirname "$0")/.."

BATTLES="${1:-3000}"
STYLES="${2:-balance}"
ARMS="${3:-control,ko}"
SEEDS="${4:-3}"
JOB="com.championsadviser.train"

restore() {
  echo "[sweep-eval] 本番の学習ループを再開します"
  rm -f logs/PAUSE_TRAINING
  launchctl start "$JOB" 2>/dev/null || true
}
trap restore EXIT

echo "[sweep-eval] 開始 $(date '+%H:%M:%S') / 条件=${ARMS} シード=${SEEDS} 評価=${BATTLES}戦"
echo "[sweep-eval] 本番の学習ループを一時停止します (logs/PAUSE_TRAINING)"
mkdir -p logs
touch logs/PAUSE_TRAINING
launchctl stop "$JOB" 2>/dev/null || true
sleep 30

# 本番を止めると子プロセスの Showdown も落ちるため自前で確保する
bash scripts/ensure_showdown.sh 8100

.venv/bin/python -m tools.reward_sweep \
  --eval-only --battles "$BATTLES" \
  --styles "$STYLES" --seeds "$SEEDS" --arms "$ARMS" 2>/dev/null

echo "[sweep-eval] 完了 $(date '+%H:%M:%S')"
