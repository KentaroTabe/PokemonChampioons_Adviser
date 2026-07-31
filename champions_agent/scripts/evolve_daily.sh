#!/bin/bash
# 構築の進化探索を1日1回まわす (launchd から呼ばれる)。
#
#   bash champions_agent/scripts/evolve_daily.sh
#
# 方針 (2026-07-28):
#  - 見つけた構築を「性格ごとの固定チーム」にはしない。訓練分布を狭めると
#    アドバイザーの用途 (ユーザーの任意チームを操縦する) と食い違うため。
#    成果は推奨構築レポート + 相手アーカイブ (PSRO) として使う
#  - 1チームあたりの戦数を増やして日替わりの揺れを抑える (40→120戦)
#  - 対戦中は実行しない (アドバイザーと競合させない)
cd "$(dirname "$0")/../.." || exit 1
source .venv/bin/activate

mkdir -p logs/team_evolution
LOG="logs/team_evolution/daily_$(date +%Y-%m-%d).log"

if python -m tools.check_battle_active 1 >/dev/null 2>&1; then
  echo "[evolve_daily] 対戦中のため中止: $(date)" >> "$LOG"
  exit 0
fi

# 報酬スイープ等がCPUを空けるために立てるフラグを尊重する
# (2026-07-31、スイープの最中に13時の本ジョブが走り測定と競合していた)
if [ -f logs/PAUSE_TRAINING ]; then
  echo "[evolve_daily] PAUSE_TRAINING があるため中止: $(date)" >> "$LOG"
  exit 0
fi

{
  echo "===== 進化探索 (日次): $(date) ====="
  # 埋め込みは使用率DBとメタに依存するので毎回作り直す (数秒)
  python -m tools.species_embedding --build
  # --update-archive でPSROの反復を進める (次回以降の相手分布に混ざる)
  python -m tools.evolve_teams \
    --population 12 --generations 3 --battles 120 \
    --archive-mix 0.2 --update-archive
  echo "===== done: $(date) ====="
} >> "$LOG" 2>&1
