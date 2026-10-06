#!/usr/bin/env bash
# 候補チームを日本語の表で表示する (名前は jp_names.json の逆引き。手書きの翻訳はしない)。
#   bash scripts/team_ja.sh --run-id rule_0910 --candidate L26_C003
#   bash scripts/team_ja.sh --run-id rule_0910                      # 勝者
#   bash scripts/team_ja.sh --file logs/build_search/runs/rule_0910/reference_team.txt
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/python -m tools.team_build.team_ja "$@" 2>&1 | grep -v "NotOpenSSL\|warnings.warn\|champions_dex_patch"
