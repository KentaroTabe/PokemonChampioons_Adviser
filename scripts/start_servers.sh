#!/bin/bash
# アドバイザーの起動スクリプト (バックエンド + フロントエンド配信、前面で動かす開発用)
#
#   bash scripts/start_servers.sh          # 通常起動
#   DEBUG_DUMP_FRAMES=1 bash scripts/start_servers.sh   # 受信フレームを保存しながら起動
#
# Ctrl+C で両方まとめて停止する。ポートは config/ports.env の既定、別のプロセスが使っていれば次の空きへ
# (scripts/lib/ports.sh)。決めたポートは logs/ports.env と config/ports.local.js に書く。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
. scripts/lib/ports.sh
source .venv/bin/activate
export PYTHONUNBUFFERED=1  # サーバーログを即時書き出す

set -- $(choose_port "$ADVISOR_PORT_DEFAULT" "" "$ADVISOR_PATTERN")
ADVISOR_PORT="$1"; ADV_STATE="$2"
set -- $(choose_port "$FRONTEND_PORT_DEFAULT" "" "$FRONTEND_PATTERN")
FRONTEND_PORT="$1"; FRONT_STATE="$2"
if [ "$ADV_STATE" != "start" ] || [ "$FRONT_STATE" != "start" ]; then
  echo "起動できません: アドバイザー $ADVISOR_PORT ($ADV_STATE) / フロントエンド $FRONTEND_PORT ($FRONT_STATE)"
  echo "(ours = 既に自分たちが稼働中、none = 空きポートが無い。常駐は bash scripts/start_all_nohup.sh)"
  exit 1
fi
save_ports "$ADVISOR_PORT" "$FRONTEND_PORT"

cleanup() {
  echo "stopping..."
  kill 0
}
trap cleanup EXIT INT TERM

python3 -m http.server "$FRONTEND_PORT" >/dev/null 2>&1 &
echo "[start] フロントエンド: http://localhost:$FRONTEND_PORT"
echo "[start] バックエンドを起動します (ポート $ADVISOR_PORT。準備完了の表示までお待ちください)"
uvicorn server:app_asgi --host 0.0.0.0 --port "$ADVISOR_PORT"
