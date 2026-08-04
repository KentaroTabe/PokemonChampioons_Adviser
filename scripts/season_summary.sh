#!/usr/bin/env bash
# シーズン全体の対戦ログを統合分析し、セッション別レポートと監査所見を
# 1つのMDにまとめる。
#   bash scripts/season_summary.sh [対象日数]
set -euo pipefail
cd "$(dirname "$0")/.."

DAYS="${1:-30}"
OUT="logs/battle_analysis/season_summary_$(date +%Y%m%d).md"

{
  echo "# シーズン統合分析 ($(date '+%Y-%m-%d') 作成 / 過去${DAYS}日)"
  echo
  echo "## 全期間の統合集計"
  echo
  .venv/bin/python -m tools.analyze_battles --days "$DAYS" 2>/dev/null
  echo
  echo "---"
  echo
  echo "## セッション別レポート (時系列)"
  for f in logs/battle_analysis/analysis_*.md; do
    echo
    echo "### $(basename "$f")"
    echo
    cat "$f"
    echo
  done
  echo "---"
  echo
  echo "## 読み取り監査の所見 (乖離と修正)"
  for f in logs/audit_reports/session_*.md; do
    echo
    echo "### $(basename "$f")"
    echo
    cat "$f"
    echo
  done
} > "$OUT"

echo "統合レポート: $OUT"
wc -l "$OUT"
