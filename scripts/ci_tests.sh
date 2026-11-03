#!/usr/bin/env bash
# CI用テストサブセット (Linux・実データなしで完結するテストのみ)。
#   bash scripts/ci_tests.sh [python実行体]
#
# フルスイート (scripts/run_test.sh all) はローカル専用:
# - Apple Vision OCR (macOS内蔵) 依存の画像読取テスト
# - 未コミットの実データ (使用率DB / RLチェックポイント / config/my_team.json /
#   debug_frames) 依存のテスト
# はここに含めない。CIは「純粋ロジック+モックで閉じるテスト」だけを回す。
#
# CI の依存は requirements-ci.txt (numpy / opencv-python-headless / requests) だけ。それ以外のライブラリが要るテストは
# ここに入れず、ローカル専用の一覧 (scripts/run_test.sh local の LOCAL_ONLY_TESTS) に置く (2026-10-06):
# - test_migrate_obs: torch が要る。検証の対象が nn.Linear の入力次元のゼロ拡張そのもので、torch なしでは確かめられない。
#   torch は RL モデル用の重い依存 (requirements-full.txt で固定) で、requirements-ci.txt の「RL モデル非依存のテストだけを
#   回すので画像処理の土台だけで足りる」という方針に合わないため、CI の最小依存に入れない
# - test_replay_path_server: server.py (socketio / fastapi) を import して実際の入口 (_handle_one_frame) を動かす (2026-10-21)。
#   CI で回る部分 (server.py の構文木での確認・録画再生の経路・記録 → 再生) は test_replay_path に分けて CI に残した
# - test_inputs_pin_propagation_server: 同じく server._handle_one_frame で固定入力の異常の停止・記録を確かめる (2026-10-31)。
#   CI で回る部分 (engine・advice_key の判断・real_accept・advice_replay) は test_inputs_pin_propagation
# - poke_env / sb3_contrib (sim・RL) 依存のテストは local (2026-10-31、docs/KNOWN_ISSUES.md E-ci-1。この job は 10/5〜10/31 これで
#   失敗していた)。依存する関数だけを tests/<元の名前>_local に分けて LOCAL_ONLY_TESTS に置き、残りはここに残した:
#   test_advice_m2d / test_advisor_team_validate / test_inputs_pin_propagation / test_sim_legal_inputs / test_sim_mega_form /
#   test_sim_revive_select。import を握りつぶして skip にしない (環境の欠如で黙って緑にしない)
# - test_inh_fixes (2026-10-31): Showdown の validate-team (node + pokemon-showdown) と本物の RL 方策 (sb3) を使う部分は、
#   無い環境 (CI) では点数の単体検査と固定の止まる側だけを確かめる (どちらも同じテストの中で分岐する)
# - test_pin_bundle (2026-10-31 版の固定の bundle): torch が無い環境 (CI) では選出モデルの推論の値の比較を省き、固定したモデルを
#   読み込めないときに止まることを確かめる (同じテストの中で分岐する。合成のファイルだけで閉じる)
# - test_model_kind (2026-11-02 選出モデルの種別を学習履歴で決める): 同じく torch が無ければ推論・微調整の見送りの確認を省く
# - test_model_provenance (2026-11-02 既存の選出モデルの学習履歴の復元、tools/inheritance_eval/reconstruct_provenance.py): 合成の
#   ツリー・記録・モデルだけで閉じる (本体のモデル・記録は読まない)
# test_battle_prune は検証する関数を champions_agent/env/showdown_env_pure.py に分けて CI に残した (showdown_env は
# poke-env を先頭で読み、poke-env 0.10 は numpy>=2.0.2 を要求して CI の numpy<2 と両立しない)。
# Showdown のデータ (pokemon-showdown/、未コミット) の読み手は、コミット済みのキャッシュを先に読むので CI でも同じ結果になる
# (champions_agent/data/sim_cache.py、作り直しは scripts/refresh_sim_caches.sh)。
# 引き継ぎ評価 段階 A の 5 本 (test_inheritance_*、2026-10-31 レビュー判断 5): 合成データと一時ファイルだけで閉じる。
# test_inheritance_setlint の test_r1_real_path_with_showdown はローカル Showdown の validate-team が要り、CI には
# pokemon-showdown/ が無いので理由つきで SKIP と出る (合法とは扱わない。検証の前の段は test_r1_gate_stage が CI で回る)
# この job では SKIP のまま。実経路は別の job showdown-validator (.github/workflows/ci.yml) が固定 commit の Showdown を
# build して厳格モード (SKIP を fail にする) で実行する (2026-10-31 追加レビュー判断 3)
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${1:-python}"
TESTS=(
  test_meta_axis_guard
  test_advice_replay
  test_advice_key
  test_state_schema_roundtrip
  test_transform_input
  test_version_split
  test_advice_m1a
  test_advice_m2a
  test_transition
  test_valuation
  test_scoring_transition
  test_advice_m2c
  test_advice_m2d
  test_advice_m2e
  test_mega_registration_forms
  test_anchor_pool
  test_battle_active
  test_battle_outcome_infer
  test_events
  test_field_extender
  test_screen_turns
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
  test_my_team
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
  test_articles_sync
  test_article_parse
  test_article_bank
  test_article_aliases
  test_article_units
  test_articles_process
  test_article_manual
  test_adapter_gamewith
  test_articles_fetch
  test_host_policy_suffix
  test_env_match
  test_advice_trace
  test_advice_variant
  test_team_build_experiments
  test_team_build_archetypes
  test_team_build_banned
  test_ja_names
  test_gimmick
  test_usage_ingame_rank
  test_selection_v3
  test_env_legality
  test_search
  test_battle_prune
  test_party_improvements
  test_real_opponents
  test_control_panel
  test_advice_mode
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
  test_undefined_names
  test_rate_outcome_attribution
  test_stale_notice
  test_type_inference_prior
  test_win_lose_screen
  test_rate_chain
  test_season_pin
  test_cheap_drift
  test_register_selection
  test_sim_caches
  test_stage0_logger
  test_selection_record
  test_pick_labels
  test_frame_burst
  test_client_state
  test_hp_intake
  test_hp_bar_estimate
  test_hp_paths
  test_hp_fraction_ocr
  test_watch_opp_color
  test_watch_right_candidates
  test_hp_low_digits
  test_roster_retention
  test_type_recognition
  test_roster_cap
  test_dmg_compare
  test_scene_samples
  test_usefulness_p1
  test_usefulness_verdict
  test_arc_remeasure
  test_advisor_team_validate
  test_diag_scoring_compare
  test_sim_picked_roster
  test_sim_legal_inputs
  test_pick_state
  test_sim_mega_form
  test_sim_revive_select
  test_diag_scenes
  test_real_accept
  test_display_search_gate
  test_transition_profile
  test_sensitivity_off
  test_first_pick_mega
  test_replay_path
  test_residuals_1023
  test_inputs_pin
  test_restore_team_text
  test_live_data_pin
  test_smogon_eval
  test_item_cues
  test_ev_infer_item_pin
  test_ev_infer_reeval
  test_inputs_pin_propagation
  test_inheritance_inventory
  test_inheritance_conversion
  test_inheritance_setlint
  test_inheritance_record
  test_inheritance_fsample
  test_inh_fixes
  test_pin_bundle
  test_model_kind
  test_model_provenance
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
