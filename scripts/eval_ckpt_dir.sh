#!/usr/bin/env bash
# 任意のチェックポイントディレクトリをベンチマーク評価する。
#   bash scripts/eval_ckpt_dir.sh <checkpointsディレクトリ|production> [対戦数] [性格] [相手シード]
#
# スイープの各条件と本番を同じ条件で測り比べるのに使う (--no-save なので
# 昇格判定やプール抽選は汚さない)。
set -euo pipefail
cd "$(dirname "$0")/.."

DIR="${1:?checkpointsディレクトリ か production を指定}"
BATTLES="${2:-1000}"
STYLE="${3:-balance}"
SEED="${4:-20260730}"

if [ "$DIR" != "production" ]; then
  export CHAMPIONS_MODELS_DIR="$(cd "$DIR" && pwd)"
  echo "[eval_ckpt] $CHAMPIONS_MODELS_DIR"
else
  echo "[eval_ckpt] 本番チェックポイント"
fi

exec .venv/bin/python -m champions_agent.train.evaluate \
  --play-style "$STYLE" --battles "$BATTLES" --opponent benchmark \
  --checkpoint current --no-save --opp-seed "$SEED" 2>/dev/null
