#!/bin/bash
# 選出学習データの収集をまとめて回す。
#
#   bash scripts/collect_selection.sh              # 既定: 6回 x 4000戦 (多チーム)
#   bash scripts/collect_selection.sh 3 2000       # 回数と1回あたりの戦数
#   bash scripts/collect_selection.sh 3 2000 myteam  # 自分のチーム固定で収集
#   bash scripts/collect_selection.sh 2 3000 ranked paired  # 対応のある収集
#
# データは追記されるので分割して積み増せる。多チーム (ranked) 収集は
# 他プレイヤーの実構築を毎バトル引き直すため、モデルが「チーム一般の
# 選出判断」を学べる (単一チームだとそのチーム専用になる)。
# paired は同じ (自チーム, 相手チーム) の組で複数の選出を試し、
# 選出間の差が相手の引き運に埋もれないデータを作る。
cd "$(dirname "$0")/.." || exit 1
source .venv/bin/activate

ROUNDS="${1:-6}"
BATTLES="${2:-4000}"
TEAMS="${3:-ranked}"
MODE="${4:-}"
EXTRA=()
if [ "$MODE" = "paired" ]; then
  EXTRA=(--paired)
fi
LOG="logs/collect_selection.log"
mkdir -p logs

echo "===== 選出データ収集: ${ROUNDS}回 x ${BATTLES}戦 (teams=${TEAMS} ${MODE}) =====" \
  >> "$LOG"
for i in $(seq 1 "$ROUNDS"); do
  echo "--- ラウンド ${i}/${ROUNDS}: $(date '+%H:%M:%S') ---"
  python -m tools.collect_selection_data --battles "$BATTLES" \
    --explore 0.85 --teams "$TEAMS" "${EXTRA[@]}" 2>>"$LOG" \
    | grep -E "今回|累計|勝率の幅"
done
echo "完了: $(date '+%H:%M:%S')"
