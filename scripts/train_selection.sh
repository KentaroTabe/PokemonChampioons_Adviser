#!/usr/bin/env bash
# 選出モデル (6体->3体) を学習する。
#   bash scripts/train_selection.sh                 # 既定 (300エポック)
#   bash scripts/train_selection.sh --no-finetune   # 汎用モデルだけ見たいとき
#
# 平均予測を超えられない場合は保存を中止する安全弁が入っている
# (2026-07-30に36件で学習した無意味なモデルが配布版を上書きしたため)。
set -euo pipefail
cd "$(dirname "$0")/.."
exec .venv/bin/python -m champions_agent.train.train_selection "$@" 2>/dev/null
