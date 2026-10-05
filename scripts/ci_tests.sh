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
  test_meta_pin
  test_belief_search
  test_advisor_player
  test_hp_freshness
  test_meta_thin_guard
  test_my_team_manual
  test_team_build_core
  test_team_build_opponents
  test_team_build_player
  test_team_build_interaction
  test_team_build_sets
  test_team_build_candidates
  test_team_build_concepts
  test_team_build_racing
  test_team_build_loss_stats
  test_team_build_interventions
  test_team_build_real_eval
  test_team_build_invariants
  test_team_build_sources
  test_team_build_articles
  test_team_build_report
  test_team_build_reference_adapt
  test_team_build_action_adapt
  test_team_build_pick_ablation
  test_team_build_pipeline
  test_team_build_ablation
  test_team_build_finalists
  test_ports_lib
  test_battle_end_signals
  test_my_roster_resolution
  test_current_party_roster
  test_team_build_rules
  test_team_build_gen_sets
  test_move_data
  test_ability_data
  test_effects
  test_role_sets
  test_lineup_search
  test_team_build_repair
  test_plan_prior
  test_pilot
  test_set_lint
  test_theme_check
  test_articles_ingest
  test_env_match
  test_team_build_experiments
  test_team_build_archetypes
  test_team_build_banned
  test_ja_names
  test_gimmick
  test_usage_ingame_rank
  test_selection_v3
  test_migrate_obs
  test_env_legality
  test_search
  test_battle_prune
  test_party_improvements
  test_real_opponents
  test_control_panel
  test_team_build_regression
  test_track_progress
  test_selection_experiment
  test_session_split
  test_claude_cli
  test_mega_stone_names
  test_opp_roster_guess
  test_mega_evolve_attribution
  test_end_notice
  test_outcome_correction
  test_team_build_ace
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
