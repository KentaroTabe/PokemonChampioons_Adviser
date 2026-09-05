#!/usr/bin/env bash
# 構築候補を「助言エンジンが操縦した勝率」(advisor-as-player・固定チーム) で比べる。
#   bash scripts/team_candidates_measure.sh [battles] [--seed N] [--pin DIR] [--out DIR] <team_a.txt> [team_b.txt ...]
# 全候補を同時起動し、RLモデルは開始時点にピン止め (--pin で既存のピンを再利用)、
# 相手列は同一 (--seed、既定 20260904)。出力は <out>/adv_<name>.json / .log。
set -euo pipefail
cd "$(dirname "$0")/.."
N=300
SEED=20260904
OUT=logs/build_search/v3
PIN=""
if [[ "${1:-}" =~ ^[0-9]+$ ]]; then N="$1"; shift; fi
while [[ "${1:-}" == --* ]]; do
  case "$1" in
    --seed) SEED="$2"; shift 2 ;;
    --pin)  PIN="$2";  shift 2 ;;
    --out)  OUT="$2";  shift 2 ;;
    *) echo "unknown option: $1"; exit 2 ;;
  esac
done
[ "$#" -ge 1 ] || { echo "usage: $0 [battles] [--seed N] [--pin DIR] [--out DIR] <team.txt>..."; exit 2; }
mkdir -p "$OUT"
if [ -n "$PIN" ]; then
  export CHAMPIONS_MODELS_DIR="$PIN"
else
  export CHAMPIONS_MODELS_DIR="$(bash scripts/pin_models.sh)"
fi
echo "[pin] $CHAMPIONS_MODELS_DIR  [seed] $SEED  [battles] $N  [out] $OUT"
pids=()
for f in "$@"; do
  name="$(basename "$f" .txt)"
  .venv/bin/python -m tools.check_advisor_player --battles "$N" --opp-seed "$SEED" \
    --skip-random --belief-k 0 --team-file "$f" --json "$OUT/adv_${name}.json" \
    > "$OUT/adv_${name}.log" 2>&1 &
  pids+=($!)
done
for p in "${pids[@]}"; do wait "$p"; done
for f in "$@"; do
  name="$(basename "$f" .txt)"
  echo "--- $name"
  grep -E "===|勝率|レイテンシ|意思決定" "$OUT/adv_${name}.log" | cut -c1-160
done
