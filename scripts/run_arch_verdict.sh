#!/bin/bash
# 新アーキ実験の事前登録判定 (docs/RL_V7_SET_ENCODER_DESIGN.md §4)。
#
#   bash scripts/run_arch_verdict.sh [実験checkpointsディレクトリ] [戦数] [delta]
#   例: bash scripts/run_arch_verdict.sh logs/arch_v7mlp/checkpoints 10000 0.02
#
# 新条件 (実験ディレクトリの current) と 本番current を、
# 同一相手列 (--opp-seed 固定)・同条件 (benchmark相手 / 相性選出) で
# 同時に測り、two_prop_verdict で採用/棄却/判定不能を出す。
# 判定規則は測定前に固定されており、結果を見てから動かさない。
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
source .venv/bin/activate

ARCH_DIR="${1:-logs/arch_v7mlp/checkpoints}"
BATTLES="${2:-10000}"
DELTA="${3:-0.02}"
OPP_SEED=20260730

OUT="logs/verdict/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$OUT"

echo "===== 事前登録判定 $(date) ====="
echo "新条件: $ARCH_DIR / 基準: 本番current / 各${BATTLES}戦 / delta=${DELTA} / opp_seed=${OPP_SEED}"

run_eval () {
  # $1=出力ファイル $2=models_dir (空なら本番)
  local out="$1" models="$2"
  if [ -n "$models" ]; then
    export CHAMPIONS_MODELS_DIR="$models"
  else
    unset CHAMPIONS_MODELS_DIR
  fi
  python -m champions_agent.train.evaluate \
    --play-style balance --battles "$BATTLES" --opponent benchmark \
    --checkpoint current --selection matchup --opp-seed "$OPP_SEED" \
    --no-save > "$out" 2>&1
}

( run_eval "$OUT/arch.log" "$ARCH_DIR" ) &
PID_A=$!
( run_eval "$OUT/base.log" "" ) &
PID_B=$!
echo "新条件 pid=$PID_A / 基準 pid=$PID_B (完了待ち)"
wait $PID_A; RC_A=$?
wait $PID_B; RC_B=$?

extract () {  # $1=ログ $2=キー
  grep "^\[evaluate\] " "$1" | tail -1 | sed -E "s/.*'$2': ([0-9]+).*/\1/"
}

WINS_A=$(extract "$OUT/arch.log" wins)
WINS_B=$(extract "$OUT/base.log" wins)

echo "--- 生の結果 ---"
grep "^\[evaluate\] " "$OUT/arch.log" | tail -1
grep "^\[evaluate\] " "$OUT/base.log" | tail -1

if [ "$RC_A" -ne 0 ] || [ "$RC_B" -ne 0 ] || [ -z "$WINS_A" ] || [ -z "$WINS_B" ]; then
  echo "✗ 評価が完了していません (rc=$RC_A/$RC_B)。$OUT のログを確認すること"
  exit 1
fi

echo "--- 判定 ---"
python -m tools.two_prop_verdict "$WINS_A" "$BATTLES" "$WINS_B" "$BATTLES" --delta "$DELTA" \
  | tee "$OUT/verdict.txt"
echo "結果一式: $OUT"
