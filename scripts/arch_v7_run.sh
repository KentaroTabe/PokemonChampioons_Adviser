#!/usr/bin/env bash
# 新アーキテクチャ (観測v7) の隔離学習パイプライン。
# docs/RL_V7_SET_ENCODER_DESIGN.md の移行計画。本番には一切触れない。
#   bash scripts/arch_v7_run.sh [ラウンド数] [1回のステップ数] [BC対戦数] [DIR] [ARCH]
#   ARCH: set (Set Encoder) / mlp (従来MLP。v7観測の寄与を分離する実験)
#
# 1. 本番の_bestと相手プールを隔離ディレクトリへ配布 (教師と対戦相手)
# 2. BC蒸留: 現行方策(388)を教師に、v7観測(420)の生徒を初期化
# 3. ko報酬・n_envs=2 で自己対戦を継続 (本番 n_envs=4 と並走)
set -uo pipefail
cd "$(dirname "$0")/.."

ROUNDS="${1:-40}"
STEPS="${2:-100000}"
BC_BATTLES="${3:-400}"
DIR="${4:-logs/arch_v7/checkpoints}"
ARCH="${5:-set}"
SRC="champions_agent/train/checkpoints"
# 1ラウンドの時間上限 (秒)。本番は tools.smoke_train の signal.alarm で
# デッドロックを打ち切れるが、当初この隔離経路は train_battle 直呼びで
# watchdog が無く、2026-08-16 に27時間ハングした
# (docs/incidents/reports/2026-08-16-isolated-training-hang.md)。
# 実測 17〜31分/ラウンド (100kステップ, n_envs=2) の約2〜3倍を既定にする。
# 途中保存があるため打ち切ってもそのラウンドの進捗は失われない。
ROUND_TIMEOUT="${ROUND_TIMEOUT:-3600}"

export CHAMPIONS_MODELS_DIR="$PWD/$DIR"
export TRAIN_OBS=v7
export TRAIN_ARCH="$ARCH"
export N_ENVS=2
export REWARD_SHAPE_SCALE=0.15
export REWARD_OVERRIDE="hp_diff_weight=0.3,faint_bonus=4.0,fainted_penalty=4.0"
export TRAIN_ENT_COEF=0.03
export TRAIN_LR=3e-4

echo "===== arch_v7 開始: $(date '+%m-%d %H:%M:%S') ====="
mkdir -p "$DIR"

if [ ! -f "$DIR/battle_policy_balance_best.zip" ]; then
  echo "--- 準備: 教師(_best)と相手プールを配布 ---"
  cp "$SRC/battle_policy_balance_best.zip" "$DIR/"
  if [ -d "$SRC/pool" ]; then
    cp -R "$SRC/pool" "$DIR/pool"
  fi
fi

bash scripts/ensure_showdown.sh 8100

if [ ! -f "$DIR/battle_policy_balance.zip" ]; then
  echo "--- BC蒸留: 現行方策を教師に生徒を初期化 (${BC_BATTLES}戦) ---"
  .venv/bin/python -m champions_agent.train.bc_pretrain \
    --style balance --teacher policy --battles "$BC_BATTLES" --epochs 3
fi
if [ ! -f "$DIR/battle_policy_balance.zip" ]; then
  echo "NG: BC初期化に失敗しました"
  exit 1
fi

for i in $(seq 1 "$ROUNDS"); do
  echo "--- 自己対戦 第${i}/${ROUNDS}回 x ${STEPS}ステップ: $(date '+%m-%d %H:%M') ---"
  # smoke_train 経由 = 本番と同じ watchdog (signal.alarm) 付きで学習する
  .venv/bin/python -m tools.smoke_train \
    --timesteps "$STEPS" --play-style balance --resume --n-envs "$N_ENVS" \
    --timeout "$ROUND_TIMEOUT" \
    || echo "--- 第${i}回 打ち切り/異常終了 (続行): $(date '+%m-%d %H:%M') ---"
done
echo "===== arch_v7 完了: $(date '+%m-%d %H:%M:%S') ====="
