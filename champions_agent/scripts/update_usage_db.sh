#!/bin/bash
# 使用率DBの日次更新スクリプト
#
# championsbattledata.com (ゲーム内バトルデータ由来) + champs.pokedb.tokyo
# オープンデータから最新の採用率を取得し、meta_sets / role_tags まで再構築する。
# 取得失敗時は Smogon gen9ou にフォールバックし、既存スナップショットは保持される。
#
# 手動実行:  bash champions_agent/scripts/update_usage_db.sh
# 定期実行:  同ディレクトリの com.championsadviser.usage-update.plist を参照
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

if [ -f .venv/bin/activate ]; then
  source .venv/bin/activate
fi

LOG_DIR="$REPO_ROOT/champions_agent/data/archive"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/update_$(date +%Y-%m-%d).log"

{
  echo "===== usage DB update: $(date) ====="
  python -m champions_agent.data.ingest --skip-static --source auto
  python -m champions_agent.data.build_meta
  python -m champions_agent.data.role_tagger
  # 90日より古いアーカイブ/ログを削除
  find "$LOG_DIR" -name "*.json.gz" -mtime +90 -delete
  find "$LOG_DIR" -name "update_*.log" -mtime +90 -delete
  echo "===== done: $(date) ====="
} >> "$LOG_FILE" 2>&1
