#!/usr/bin/env bash
# 試用中 (experiment ラベル) の Package の実戦サマリー: 由来ラベルつき対戦ログから
# 勝敗・勝率の CI・決定監査の集計 (助言あり / 一致 = 遵守率 / 時間内)。
#   bash scripts/canary_summary.sh                     # ラベルの Package (logs/.experiment_package)
#   bash scripts/canary_summary.sh --session           # + 接続テストのマーカー以降だけの集計も出す
#   bash scripts/canary_summary.sh --package <id>      # Package を指定
#   bash scripts/canary_summary.sh --json
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
.venv/bin/python -m tools.team_build.real_eval "$@" 2>&1 | grep -v "NotOpenSSL\|warnings.warn"
