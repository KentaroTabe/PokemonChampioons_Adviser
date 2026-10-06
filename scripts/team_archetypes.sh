#!/usr/bin/env bash
# 構築の軸 (archetype) の一覧と、run の判定結果 (s03_archetypes.json) の要約を表示する。
#   bash scripts/team_archetypes.sh --list                       # 一覧 (docs/TEAM_BUILD_ARCHETYPES.md §1 と同期する表)
#   bash scripts/team_archetypes.sh --report --run-id <run_id>   # 軸 × 分岐の環境適合・役割の候補・core (日本語名)
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/python -m tools.team_build.archetypes "$@" 2>&1 | grep -v "NotOpenSSL\|warnings.warn\|champions_dex_patch"
