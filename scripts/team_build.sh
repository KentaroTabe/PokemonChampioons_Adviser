#!/usr/bin/env bash
# 構築システムの実行ラッパー。
#   bash scripts/team_build.sh <run_id> [run.py の引数...]
# 例: bash scripts/team_build.sh r_0907 --favorites ドドゲザン --banned ハッサム --style offense --profile fast --stages search
#     bash scripts/team_build.sh r_0907 --stages measure --parallel 5
set -euo pipefail
cd "$(dirname "$0")/.."
RUN="$1"; shift
.venv/bin/python -m tools.team_build.run --run-id "$RUN" "$@" 2>&1 | grep -v "NotOpenSSL\|warnings.warn\|champions_dex_patch"
