#!/usr/bin/env bash
# 常時学習 (launchd: com.championsadviser.train) を安全に止める。
#   bash scripts/stop_training.sh            # 学習だけ止める (Showdownは残す)
#   bash scripts/stop_training.sh showdown   # Showdown (8100) も止める
#
# - KeepAlive=true のため launchctl stop では10秒後に復帰する。bootout で外す
#   (再開は scripts/start_training.sh)
# - チェックポイントは2万ステップごとの原子的保存なので、途中終了の損失は
#   最大でその区間ぶん
# - 短時間 (40分未満) の一時停止なら logs/PAUSE_TRAINING の touch でもよい
#   (train_forever.sh が鮮度40分で自動解除する)
# - Showdown は scripts/ensure_showdown.sh 経由なら切り離されており、
#   学習を止めても残る (2026-09-02 以前は学習サイクルの子で巻き添えになった)
set -uo pipefail
cd "$(dirname "$0")/.."

launchctl bootout "gui/$(id -u)/com.championsadviser.train" 2>/dev/null \
  && echo "[stop_training] launchd ジョブを外しました" \
  || echo "[stop_training] launchd ジョブは登録されていませんでした"

sleep 2
if pgrep -fl "train_forever|train_nightly|smoke_train|train_battle" >/dev/null; then
  echo "[stop_training] 残存プロセスに TERM を送ります"
  pkill -f "train_forever|train_nightly|smoke_train|train_battle" || true
  sleep 3
fi

if [ "${1:-}" = "showdown" ]; then
  pkill -f "pokemon-showdown start" && echo "[stop_training] Showdown を止めました" \
    || echo "[stop_training] Showdown は起動していませんでした"
fi

echo "=== 実測 ==="
pgrep -fl "train_forever|train_nightly|smoke_train|train_battle" \
  || echo "学習系プロセス: なし"
lsof -nP -iTCP:8100 -sTCP:LISTEN >/dev/null 2>&1 \
  && echo "Showdown 8100: 稼働中" || echo "Showdown 8100: 停止"
