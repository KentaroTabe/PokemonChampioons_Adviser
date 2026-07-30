#!/usr/bin/env bash
# 選出データセットをバックアップから復旧する。
#   bash scripts/restore_selection_data.sh [バックアップのnpz]
#
# 2026-07-30に列追加 (group) の際、スキーマ不一致で25,000件が破棄され、
# 直後の36件だけのデータで選出モデルが上書きされた。
# バックアップ (selection_data_60teams.npz) には25,000件が残っている。
# group列は load_dataset 側で -1 (対応なし) に補完されるため、
# そのまま復旧して以降の収集でマージできる。
set -euo pipefail
cd "$(dirname "$0")/.."

SRC="${1:-champions_agent/train/logs/selection_data_60teams.npz}"
DST="champions_agent/train/logs/selection_data.npz"
STAMP="$(date '+%Y%m%d_%H%M%S')"

if [ ! -f "$SRC" ]; then
  echo "[restore] バックアップがありません: $SRC"
  exit 1
fi

if [ -f "$DST" ]; then
  ARCHIVE="champions_agent/train/logs/selection_data_before_restore_${STAMP}.npz"
  cp "$DST" "$ARCHIVE"
  echo "[restore] 現在のデータを退避しました: $ARCHIVE"
fi

cp "$SRC" "$DST"
echo "[restore] 復旧しました: $SRC -> $DST"
bash scripts/selection_stats.sh
