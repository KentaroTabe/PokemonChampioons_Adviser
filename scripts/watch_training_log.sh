#!/bin/bash
# 常時学習のログ (logs/train_forever.log) から、サイクルの開始/完了・再開・評価・失敗/タイムアウト・チーム拒否だけを
# 1 行ずつ流す (Monitor / 端末での監視用)。Ctrl+C で止める。
#   bash scripts/watch_training_log.sh
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
pattern='サイクル [0-9]+ (開始|完了)|チェックポイントから再開|非互換|\[evaluate\]|学習が失敗|TIMEOUT|Popup message received|Traceback'
tail -n 0 -F logs/train_forever.log | grep -E --line-buffered "$pattern"
