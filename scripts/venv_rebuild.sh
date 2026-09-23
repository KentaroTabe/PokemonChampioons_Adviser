#!/usr/bin/env bash
# venv を作り直す (Python の版の移行、壊れたときの復旧)。使用中の .venv は触らず、指定した場所に作る。
#   bash scripts/venv_rebuild.sh /opt/homebrew/bin/python3.12 .venv312
# 作った後は scripts/ci_tests.sh <venv>/bin/python で確認し、切替は docs/OPERATIONS.md「Python 環境」の手順で行う。
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${1:?python の実行体 (例 /opt/homebrew/bin/python3.12)}"
DIR="${2:?作る venv のパス (例 .venv312)}"
if [ -e "$DIR" ]; then
  echo "[venv_rebuild] $DIR は既にあります。消してから実行してください"
  exit 1
fi
"$PY" -m venv "$DIR"
"$DIR/bin/python" -m pip install --upgrade pip wheel
"$DIR/bin/python" -m pip install -r requirements-full.txt
echo "=== 作成した環境 ==="
"$DIR/bin/python" --version
"$DIR/bin/python" -m pip list
