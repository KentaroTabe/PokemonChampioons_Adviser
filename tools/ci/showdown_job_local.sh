#!/usr/bin/env bash
# GitHub CI の job showdown-validator (.github/workflows/ci.yml) の手順を、この Mac の一時ディレクトリで模擬する。
# GitHub 上では push しないと実行できないため、取得・build・R1 の厳格モードが通ることをローカルで先に確かめる。
#
#   bash tools/ci/showdown_job_local.sh [python 実行体] [取得元の pokemon-showdown]
#
#   python 実行体 : 既定は本体の作業ツリーの .venv/bin/python
#   取得元         : 既定は本体の作業ツリーの pokemon-showdown/ (読み取りのみ。git clone の元にするだけで書き込まない)
#   環境変数 KEEP_TMP=1 で一時ディレクトリを残す (既定は終了時に消す)
#
# CI との違い (それ以外は同じ手順・同じ tools/ci/showdown_prepare.sh):
#   - 取得: CI は GitHub から固定の commit を fetch、ここはローカル clone から git clone して同じ commit を checkout
#   - npm ci: ここは --offline (npm の cache だけを使い、外へ取得しない)。cache に無ければ失敗する
#   - Node: CI は setup-node の 26 系列、ここは PATH の node (系列は showdown_prepare.sh が config と照合)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
MAIN="$(cd "$(git -C "$ROOT" rev-parse --path-format=absolute --git-common-dir)/.." && pwd)"
PY="${1:-$MAIN/.venv/bin/python}"
SRC="${2:-$MAIN/pokemon-showdown}"

COMMIT="$(cd "$ROOT" && "$PY" -B -c "from champions_agent.config import INH_R1_SHOWDOWN_COMMIT as c; print(c)")"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/showdown_job.XXXXXX")"
if [ "${KEEP_TMP:-}" != "1" ]; then
  trap 'rm -rf "$TMP"' EXIT
fi
SD="$TMP/pokemon-showdown"
echo "[showdown_job_local] 取得元 $SRC → $SD (commit $COMMIT)"

t0=$(date +%s)
git clone -q --no-checkout "$SRC" "$SD"
git -C "$SD" checkout -q --detach "$COMMIT"
t1=$(date +%s)
NPM_CI_EXTRA="--offline" bash "$ROOT/tools/ci/showdown_prepare.sh" "$SD" "$PY"
t2=$(date +%s)
(cd "$ROOT" && INH_R1_REQUIRE_SHOWDOWN=1 SHOWDOWN_DIR="$SD" "$PY" -B -m tests.test_inheritance_setlint)
t3=$(date +%s)
echo "[showdown_job_local] OK (取得 $((t1 - t0)) 秒, 依存と build と形式の確認 $((t2 - t1)) 秒, R1 厳格モード $((t3 - t2)) 秒)"
