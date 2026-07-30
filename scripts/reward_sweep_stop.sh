#!/usr/bin/env bash
# 切り離して回している報酬スイープを止め、本番の学習ループを戻す。
#   bash scripts/reward_sweep_stop.sh
set -euo pipefail
cd "$(dirname "$0")/.."

pkill -f "tools.reward_sweep" 2>/dev/null || true
pkill -f "reward_sweep_run.sh" 2>/dev/null || true
pkill -f "champions_agent.train.train_battle" 2>/dev/null || true
sleep 2
echo "[sweep] 停止しました。本番の学習ループを再開します"
launchctl start com.championsadviser.train 2>/dev/null || true
