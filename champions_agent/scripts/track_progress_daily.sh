#!/bin/bash
# 日次の進捗定点 (tools.track_progress) を launchd から回すラッパー。
#
#   bash champions_agent/scripts/track_progress_daily.sh
#   定期実行: 同ディレクトリの com.championsadviser.track-progress.plist を参照
#
# 定点は 8/13〜8/15 の間、前セッションの手動実行に依存しており、
# セッション終了とともに 8/16-17 が欠測した。launchd で恒久化する。
# 評価は逐次 (単独) で走る前提 (並列評価は水準を歪める。
# docs/AXIS_GAP_ANALYSIS.md §2-1)。二重起動だけ防ぐ。
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

if pgrep -f "tools.track_progress" >/dev/null 2>&1; then
  echo "[track_progress_daily] 既に実行中のためスキップ: $(date)" \
    >> logs/track_progress_daily.log
  exit 0
fi

source .venv/bin/activate

{
  echo "===== 日次定点: $(date) ====="
  python -m tools.track_progress
  echo "===== done: $(date) ====="
} >> logs/track_progress_daily.log 2>&1
