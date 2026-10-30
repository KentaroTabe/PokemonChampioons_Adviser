#!/usr/bin/env bash
# 構築記事の定期調査 (GitHub の別リポジトリの一覧 CSV) → logs/articles/blogs.csv → manifest.json の同期 (2026-10-30)。
#   bash scripts/articles_sync.sh            # dry-run: 差分の表と件数を出すだけ (何も書かない)
#   bash scripts/articles_sync.sh --apply    # blogs.csv を更新 (控え .bak.YYYYMMDD)、manifest.json を作り直し、sync.jsonl に 1 行
# 引数はそのまま tools.team_build.articles_sync に渡す (--source-file / --out-dir ほか)。読むのは GitHub の CSV と state.json だけで、
# 記事の本文は取得しない。gh (GitHub CLI) の認証が要る。定期実行の例は scripts/com.championsadviser.articles-sync.plist (dry-run)。
set -euo pipefail
cd "$(dirname "$0")/.."
echo "=== articles_sync $(date '+%Y-%m-%d %H:%M:%S %z') ==="
.venv/bin/python -m tools.team_build.articles_sync "$@" 2>&1 | grep -v "NotOpenSSL\|warnings.warn"
