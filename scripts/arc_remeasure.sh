#!/usr/bin/env bash
# 2026-09-04〜05 の信念つき探索アークの判定 (advisor-as-player の P9 系 3 本 + advisor baseline) を、
# 正しい自チームの型で再測定する (docs/incidents/reports/2026-09-04-sim-team-registration-off-by-one.md、2026-10-09)。
#   bash scripts/arc_remeasure.sh all --dry-run                 # コマンド列と見積もり時間だけ (ピンを作らない・起動しない)
#   bash scripts/arc_remeasure.sh all                           # 本番 (開始時に scripts/pin_models.sh でピン)
#   bash scripts/arc_remeasure.sh p9_600 [--jobs 3] [--format mb|mc] [--pin-dir DIR] [--use-my-team]
#   bash scripts/arc_remeasure.sh p9_300 --battles 2 --pin-dir DIR   # 煙試験 (各腕 2 戦)
# 判定: all / p9_300 / p9_600 / p9b_1500 / baseline (= p9_300 の A)。探索プレイヤーの判定 (P6-a/P6-b/P7/P7'/P8/P10) は
# 型の登録を通らないので対象外。出力: logs/arc_remeasure_<YYYYMMDD_HHMM>/<判定>/<腕>.json|.log|.battles.jsonl と manifest.json。
# 集計: .venv/bin/python -m tools.arc_remeasure_summary logs/arc_remeasure_<stamp>
# 開始時刻の注意 (強制しない): track-progress (毎日 21:50、17〜34 分) の後に始め、06:30 の usage-update の前に終える。
set -euo pipefail
cd "$(dirname "$0")/.."
PY="${PYTHON:-.venv/bin/python}"
exec "$PY" -m tools.arc_remeasure "$@"
