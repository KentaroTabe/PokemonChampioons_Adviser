#!/usr/bin/env bash
# CIと同じ条件 (コミット済みファイルのみ・未コミットの実データなし) で
# CIテストサブセットを回す。push前の検証用。
#   bash scripts/ci_local.sh [python実行体]   (既定: .venv/bin/python)
#
# 2026-09-02: ローカルでは使用率DBが埋めて緑になる隠れ依存が2テストにあり、
# push後に初めてCIで失敗した。ローカル緑≠CI緑なので、CIテストを足したら
# 必ずこれを通してからpushする。
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${1:-$PWD/.venv/bin/python}"
CLONE="$(mktemp -d)/ci_clone"
trap 'rm -rf "$(dirname "$CLONE")"' EXIT

git clone --quiet --depth 1 "file://$PWD" "$CLONE"
echo "[ci_local] クローン: $CLONE (HEAD=$(git -C "$CLONE" rev-parse --short HEAD))"
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "[ci_local] 注意: 未コミットの変更はクローンに含まれません"
fi
bash "$CLONE/scripts/ci_tests.sh" "$PY"
