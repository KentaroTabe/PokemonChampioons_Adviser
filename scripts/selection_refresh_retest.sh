#!/usr/bin/env bash
# 選出データ更新A/Bの再測定 (事前登録の「判定不能→倍増して1回だけ」用)。
#   bash scripts/selection_refresh_retest.sh [戦数/条件] [相手シード]
# 収集と学習はやり直さず、比較だけを回す。
set -uo pipefail
cd "$(dirname "$0")/.."

BATTLES="${1:-20000}"
SEED="${2:-20260803}"
OUTDIR="logs/selection_refresh"
SANDBOX="$OUTDIR/checkpoints"

if [ ! -f "$SANDBOX/selection_model_general.pt" ]; then
  echo "NG: 新モデルがありません ($SANDBOX)"
  exit 1
fi

echo "--- 再測定: 旧 vs 新 並行A/B (各${BATTLES}戦 / シード${SEED}) ---"
.venv/bin/python -m champions_agent.train.evaluate \
  --play-style balance --battles "$BATTLES" --opponent benchmark \
  --checkpoint best --selection model --opp-seed "$SEED" --no-save \
  > "$OUTDIR/old_retest.log" 2>/dev/null &
PID_OLD=$!
(
  export CHAMPIONS_MODELS_DIR="$PWD/$SANDBOX"
  .venv/bin/python -m champions_agent.train.evaluate \
    --play-style balance --battles "$BATTLES" --opponent benchmark \
    --checkpoint best --selection model --opp-seed "$SEED" --no-save \
    > "$OUTDIR/new_retest.log" 2>/dev/null
) &
PID_NEW=$!
wait "$PID_OLD" "$PID_NEW"

WINS_OLD=$(grep -o "'wins': [0-9]*" "$OUTDIR/old_retest.log" | grep -o "[0-9]*")
WINS_NEW=$(grep -o "'wins': [0-9]*" "$OUTDIR/new_retest.log" | grep -o "[0-9]*")
if [ -z "$WINS_OLD" ] || [ -z "$WINS_NEW" ]; then
  echo "NG: 評価結果を読めませんでした ($OUTDIR/*_retest.log)"
  exit 1
fi
echo "旧=${WINS_OLD}/${BATTLES} 新=${WINS_NEW}/${BATTLES}"
.venv/bin/python -m tools.two_prop_verdict \
  "$WINS_NEW" "$BATTLES" "$WINS_OLD" "$BATTLES" --delta 0.02
