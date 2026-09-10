#!/usr/bin/env bash
# Showdown の champions mod から種族/技データを champions_agent/data/champions_dex.json へ書き出す
# (レギュレーション切替や上流更新の後に実行。事前に pokemon-showdown がビルド済みであること)。
#   bash scripts/export_champions_dex.sh
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=champions_agent/data/champions_dex.json
TMP="$OUT.tmp"
node tools/export_champions_dex.js > "$TMP"
.venv/bin/python -c "import json,sys; d=json.load(open('$TMP')); print('species', len(d.get('species', {})), 'moves', len(d.get('moves', {})))"
mv "$TMP" "$OUT"
echo "[export_champions_dex] 書き出し: $OUT"
