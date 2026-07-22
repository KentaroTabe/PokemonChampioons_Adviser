#!/bin/bash
# テスト実行ラッパー。venv有効化とログノイズ除去を一手に引き受ける。
#
#   scripts/run_test.sh test_events                # 1件
#   scripts/run_test.sh test_events test_rl_bridge # 複数
#   scripts/run_test.sh all                        # tests/ 全件
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
source .venv/bin/activate

targets=("$@")
if [ "${1:-}" = "all" ]; then
  targets=()
  for f in tests/test_*.py; do
    targets+=("$(basename "$f" .py)")
  done
fi
if [ ${#targets[@]} -eq 0 ]; then
  echo "使い方: scripts/run_test.sh <test_名...|all>"
  exit 2
fi

fail=0
for t in "${targets[@]}"; do
  name="${t%.py}"
  name="${name#tests/}"
  echo "===== tests.$name ====="
  python -m "tests.$name" 2>&1 | grep -vE "urllib3|warnings\.warn|NotOpenSSL"
  rc=${PIPESTATUS[0]}
  if [ "$rc" -ne 0 ]; then
    echo "✗ tests.$name 失敗 (exit=$rc)"
    fail=1
  fi
done
exit $fail
