#!/bin/bash
# 夜間セルフプレイ学習バッチ
#
#   bash champions_agent/scripts/train_nightly.sh                # 全性格 各50kステップ
#   TIMESTEPS=200000 STYLES="offense stall" bash champions_agent/scripts/train_nightly.sh
#
# - Showdownをポート8100で起動する (アドバイザーのバックエンド8000と併用可)
# - caffeinate でMacのスリープを抑止する
# - 既存チェックポイントがあれば継続学習 (--resume)、世代バックアップを残す
# - 学習後に vs Random の勝率評価を実行してログに残す
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"
source .venv/bin/activate

TIMESTEPS="${TIMESTEPS:-50000}"
STYLES="${STYLES:-balance offense cycle stall}"
EVAL_BATTLES="${EVAL_BATTLES:-30}"
export SHOWDOWN_PORT="${SHOWDOWN_PORT:-8100}"

LOG_DIR="$REPO_ROOT/champions_agent/train/logs"
CKPT_DIR="$REPO_ROOT/champions_agent/train/checkpoints"
mkdir -p "$LOG_DIR" "$CKPT_DIR"
LOG_FILE="$LOG_DIR/nightly_$(date +%Y-%m-%d_%H%M).log"

STARTED_SHOWDOWN=0
cleanup() {
  if [ "$STARTED_SHOWDOWN" = "1" ] && [ -n "${SHOWDOWN_PID:-}" ]; then
    echo "[nightly] Showdownを停止します (pid=$SHOWDOWN_PID)" | tee -a "$LOG_FILE"
    kill "$SHOWDOWN_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT

{
  echo "===== nightly training: $(date) ====="
  echo "timesteps=$TIMESTEPS styles=[$STYLES] port=$SHOWDOWN_PORT"

  # Showdownが起動していなければ起動する
  if ! lsof -nP -iTCP:"$SHOWDOWN_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "[nightly] Showdownをポート$SHOWDOWN_PORT で起動します"
    (cd pokemon-showdown && node pokemon-showdown start "$SHOWDOWN_PORT" --no-security) &
    SHOWDOWN_PID=$!
    STARTED_SHOWDOWN=1
    sleep 10
  fi

  for style in $STYLES; do
    echo "--- [$style] 学習開始: $(date) ---"
    # チェックポイントの世代バックアップ (直近3世代)
    ckpt="$CKPT_DIR/battle_policy_${style}.zip"
    if [ -f "$ckpt" ]; then
      cp "$ckpt" "$ckpt.prev" 2>/dev/null || true
    fi
    # スリープ抑止つきで学習 (失敗しても次の性格へ進む)
    caffeinate -i python -m champions_agent.train.train_battle \
      --play-style "$style" --timesteps "$TIMESTEPS" --resume || {
        echo "[nightly] [$style] 学習が失敗しました"; continue; }

    echo "--- [$style] 評価 (vs Random, $EVAL_BATTLES 戦) ---"
    caffeinate -i python -m champions_agent.train.evaluate \
      --play-style "$style" --battles "$EVAL_BATTLES" || \
      echo "[nightly] [$style] 評価が失敗しました"
  done

  echo "===== done: $(date) ====="
} 2>&1 | tee -a "$LOG_FILE"
