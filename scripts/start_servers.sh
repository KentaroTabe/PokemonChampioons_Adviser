#!/bin/bash
# アドバイザーの起動スクリプト (バックエンド + フロントエンド配信)
#
#   bash scripts/start_servers.sh          # 通常起動
#   DEBUG_DUMP_FRAMES=1 bash scripts/start_servers.sh   # 受信フレームを保存しながら起動
#
# Ctrl+C で両方まとめて停止する。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
source .venv/bin/activate

cleanup() {
  echo "stopping..."
  kill 0
}
trap cleanup EXIT INT TERM

python3 -m http.server 3000 >/dev/null 2>&1 &
echo "[start] フロントエンド: http://localhost:3000"
echo "[start] バックエンドを起動します (準備完了の表示までお待ちください)"
uvicorn server:app_asgi --host 0.0.0.0 --port 8000
