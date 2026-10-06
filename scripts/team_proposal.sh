#!/bin/bash
# 構築提案 (段階ゲート付き) のラッパー。
#
#   bash scripts/team_proposal.sh --check                 # 運用可否 (両段階)
#   bash scripts/team_proposal.sh --propose --stage 1     # 制約付き改善 (±2枠)
#   bash scripts/team_proposal.sh --propose --stage 2     # 一般提案
#
# 実対戦評価を行うため、ローカルShowdown(8100) が必要
# (未起動なら scripts/ensure_showdown.sh)。
cd "$(dirname "$0")/.." || exit 1
source .venv/bin/activate
python -m tools.team_proposal "$@"
