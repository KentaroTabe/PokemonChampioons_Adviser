#!/usr/bin/env bash
# Showdown (既定ポート8100) が起動していなければ、切り離して起動する。
#   bash scripts/ensure_showdown.sh [ポート]
#
# Showdown は本番の学習ジョブ (train_nightly.sh) の子プロセスとして起動される。
# そのため本番を止めるとサーバーごと落ちる。2026-07-31、報酬スイープが本番を
# 止めた結果すべての学習が「8100へ接続できない」で即死した
# (以前は launchctl stop が効かず本番が10秒で復帰していたため偶然生きていた)。
#
# ここで起動したものは切り離すので、本番の学習ジョブの停止に巻き込まれない。
# train_nightly.sh は「起動済みなら起動しない・自分で起動していなければ殺さない」
# ので、本番側はそのまま再利用する。
set -uo pipefail
cd "$(dirname "$0")/.."

PORT="${1:-8100}"

if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "[showdown] ポート${PORT} は起動済み"
  exit 0
fi

echo "[showdown] ポート${PORT} で起動します"
.venv/bin/python -m tools.spawn_detached --cwd pokemon-showdown \
  logs/showdown_detached.log \
  node pokemon-showdown start "$PORT" --no-security > /dev/null

for _ in $(seq 1 30); do
  if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "[showdown] 起動しました"
    exit 0
  fi
  sleep 2
done

echo "[showdown] NG: 60秒待っても起動しませんでした (logs/showdown_detached.log)"
exit 1
