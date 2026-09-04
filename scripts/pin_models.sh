#!/usr/bin/env bash
# 測定用にRLモデル (チェックポイント) を開始時点のスナップショットへピン止めする。
#   PIN_DIR="$(bash scripts/pin_models.sh)"; export CHAMPIONS_MODELS_DIR="$PIN_DIR"
# advisor-as-player と葉評価ONの探索プレイヤーは起動時に最新のEMA/_bestを読むため、
# 腕の起動時刻がずれると学習の進行で後の腕が有利になる (2026-09-05 P9' で判明:
# 同一設定の基準腕が 0.587→0.593→0.634 と上昇)。全腕で同じスナップショットを使う。
set -euo pipefail
cd "$(dirname "$0")/.."
SRC=champions_agent/train/checkpoints
DST="logs/pinned_models/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$DST"
cp "$SRC"/battle_policy_*.zip "$DST"/ 2>/dev/null || true
if [ -d "$SRC/anchors" ]; then
  mkdir -p "$DST/anchors"
  cp "$SRC"/anchors/*.zip "$DST/anchors/" 2>/dev/null || true
fi
n=$(ls "$DST"/*.zip 2>/dev/null | wc -l | tr -d ' ')
if [ "$n" = "0" ]; then
  echo "pin_models: チェックポイントが見つかりません ($SRC)" >&2
  exit 1
fi
echo "$PWD/$DST"
