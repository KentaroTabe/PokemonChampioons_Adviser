#!/usr/bin/env bash
# ローカルでPokemon Showdownサーバーを構築・起動するスクリプト。
#
# 使い方:
#   bash champions_agent/scripts/setup_showdown.sh          # clone+install(初回のみ)
#   bash champions_agent/scripts/setup_showdown.sh --start  # サーバー起動のみ(localhost:8000)
#
# 前提: node (>=18推奨) と npm がインストールされていること。
#   macOS: brew install node
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# champions_agent/ の一つ上(リポジトリルート)に pokemon-showdown/ を配置する
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SHOWDOWN_DIR="${REPO_ROOT}/pokemon-showdown"

start_server() {
  if [ ! -d "${SHOWDOWN_DIR}" ]; then
    echo "[setup_showdown] ${SHOWDOWN_DIR} が見つかりません。先にセットアップ(引数なし実行)してください。"
    exit 1
  fi
  cd "${SHOWDOWN_DIR}"
  PORT="${SHOWDOWN_PORT:-8100}"
  echo "[setup_showdown] Pokemon Showdown サーバーを起動します(localhost:${PORT}, --no-security)"
  # アドバイザーのバックエンド(8000)と常時併用するため既定は8100
  node pokemon-showdown start "${PORT}" --no-security
}

if [ "${1:-}" = "--start" ]; then
  start_server
  exit 0
fi

if ! command -v node >/dev/null 2>&1; then
  echo "[setup_showdown] node が見つかりません。'brew install node' 等でインストールしてください。"
  exit 1
fi

if [ ! -d "${SHOWDOWN_DIR}" ]; then
  echo "[setup_showdown] pokemon-showdown をclonします -> ${SHOWDOWN_DIR}"
  git clone https://github.com/smogon/pokemon-showdown.git "${SHOWDOWN_DIR}"
else
  echo "[setup_showdown] 既に ${SHOWDOWN_DIR} が存在するためcloneをスキップします"
fi

cd "${SHOWDOWN_DIR}"

echo "[setup_showdown] npm install を実行します"
npm install

if [ ! -f "config/config.js" ]; then
  echo "[setup_showdown] config/config.js を作成します"
  cp config/config-example.js config/config.js
fi

echo "[setup_showdown] セットアップ完了。サーバーを起動するには次を実行してください:"
echo "  bash ${SCRIPT_DIR}/setup_showdown.sh --start"
