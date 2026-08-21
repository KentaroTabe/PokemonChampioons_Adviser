#!/bin/bash
# 現在のパーティの選出データ収集→選出モデル微調整の一括実行。
# パーティ変更 (構築提案の採用等) 後に回す運用手順の自動化
# (docs/TEAM_PROPOSAL_DESIGN.md §4 の手順2-3)。
#
#   bash scripts/refresh_selection_myteam.sh [rounds] [battles]
#   既定: 2回 x 2500戦 (train_selection の運用指針どおり)
cd "$(dirname "$0")/.." || exit 1

ROUNDS="${1:-2}"
BATTLES="${2:-2500}"

bash scripts/collect_selection.sh "$ROUNDS" "$BATTLES" myteam

source .venv/bin/activate
python -m champions_agent.train.train_selection
