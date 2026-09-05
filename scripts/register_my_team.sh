#!/usr/bin/env bash
# 推奨構築 (final_team.json) またはチーム本文から config/my_team.json を手入力ベースで更新する。
#   bash scripts/register_my_team.sh [--team-file x.txt] [--dry-run]
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/python -m tools.register_my_team "$@" 2>&1 | grep -v "NotOpenSSL\|warnings.warn"
