#!/usr/bin/env bash
# Showdown のデータから、コミットするキャッシュ (vision/data/champions_illegal_ids.json /
# tools/team_build/data/sim_species_table.json / champions_agent/data/champions_available_items.json) を作り直す
# (tools/refresh_sim_caches.py)。
# レギュレーション切替などで pokemon-showdown を更新したあとに実行してコミットする。
#   scripts/refresh_sim_caches.sh                        # 書き出し
#   scripts/refresh_sim_caches.sh --check                # 一致だけ確かめる (違えば終了コード 1)
#   scripts/refresh_sim_caches.sh --showdown-dir <dir>   # 別の場所の Showdown を読む (worktree から本体を読む等)
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/python -m tools.refresh_sim_caches "$@"
