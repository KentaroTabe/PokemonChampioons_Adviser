#!/usr/bin/env bash
# CI用テストサブセット (Linux・実データなしで完結するテストのみ)。
#   bash scripts/ci_tests.sh [python実行体]
#
# フルスイート (scripts/run_test.sh all) はローカル専用:
# - Apple Vision OCR (macOS内蔵) 依存の画像読取テスト
# - 未コミットの実データ (使用率DB / RLチェックポイント / config/my_team.json /
#   debug_frames) 依存のテスト
# はここに含めない。CIは「純粋ロジック+モックで閉じるテスト」だけを回す。
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${1:-python}"
TESTS=(
  test_meta_axis_guard
  test_advice_replay
  test_anchor_pool
  test_battle_active
  test_battle_outcome_infer
  test_events
  test_team_proposal
  test_team_menu
  test_rescue
  test_hp_settle
  test_hud_attribution
  test_pokedb_forms
  test_meta_snapshot_filter
  test_set_coherence
)

fail=0
for t in "${TESTS[@]}"; do
  echo "===== tests.$t ====="
  if ! "$PY" -m "tests.$t"; then
    echo "✗ tests.$t 失敗"
    fail=1
  fi
done
exit "$fail"
