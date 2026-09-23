#!/usr/bin/env bash
# 接続テストの操作パネル (tools/control_panel) を前景で起動する。
# launchd (com.championsadviser.control-panel、scripts/control_panel_install.sh で登録) がこれを実行する。
# 手動の動作確認にも使える (Ctrl+C で停止)。
#   bash scripts/control_panel.sh [--port N] [--bind ADDR]
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
mkdir -p logs/control_panel
source .venv/bin/activate
exec python -m tools.control_panel "$@"
