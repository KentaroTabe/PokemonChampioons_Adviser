#!/usr/bin/env bash
# experiment ラベル (接続テストで候補 Package を試用中の印。対戦ログの session 行に
# source=experiment / package_id が付く。logs/.experiment_package) の操作。
#   bash scripts/experiment_label.sh on <package_id>   # ON (registry にある Package だけ)
#   bash scripts/experiment_label.sh off               # OFF
#   bash scripts/experiment_label.sh show              # 現在の値
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
MARK=logs/.experiment_package

case "${1:-show}" in
  on)
    [ -n "${2:-}" ] || { echo "使い方: $0 on <package_id>"; exit 2; }
    .venv/bin/python -m tools.team_build.promote --experiment "$2" 2>&1 | grep -v "NotOpenSSL\|warnings.warn"
    ;;
  off)
    .venv/bin/python -m tools.team_build.promote --experiment-off 2>&1 | grep -v "NotOpenSSL\|warnings.warn"
    ;;
  show) ;;
  *) echo "使い方: $0 on <package_id> | off | show"; exit 2 ;;
esac

if [ -f "$MARK" ]; then
  echo "experiment ラベル: $(cat "$MARK")"
else
  echo "experiment ラベル: OFF"
fi
