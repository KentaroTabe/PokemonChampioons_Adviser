#!/usr/bin/env bash
# 取得済みの pokemon-showdown (git の作業ツリー) を、I-05 R1 の実経路 (validate_team_text) に使える状態にする。
# GitHub CI の job showdown-validator (.github/workflows/ci.yml) とローカルの模擬 (tools/ci/showdown_job_local.sh) の共通部分。
#
#   bash tools/ci/showdown_prepare.sh <pokemon-showdown の dir> [python 実行体]
#
# 手順 (どれか 1 つでも失敗したら終了コード 1。理由つき SKIP を合格の代わりにしない):
#   1. HEAD が config の INH_R1_SHOWDOWN_COMMIT と一致する (ブランチの最新ではなく検証対象の commit)
#   2. node の系列が config の INH_R1_NODE_MAJOR と一致する (Showdown の build は Node 22 以上を要求)
#   3. npm ci --omit=dev --ignore-scripts (package-lock.json どおり。dev と install script を入れない理由は下)
#   4. node build (package.json の scripts.build。README の手順と同じ。dist/ と config/config.js を作る)
#   5. 対象形式 (R1 が検証に使う config の TRAINING_BATTLE_FORMAT = gen9championsbssregmc) が build した Dex の formats にある
#
# --omit=dev: devDependencies は lint・型検査・試験の道具 (eslint / typescript / mocha / @types/*)。node build は esbuild で
#   transpile するだけで (tools/build-utils.js の transpile。typescript は宣言ファイルの build (node build decl) にだけ使う)、
#   validate-team も dev を使わない。
# --ignore-scripts: validate-team に要るのは esbuild (build) と ts-chacha20 等の JS だけ。install script を持つのは
#   optionalDependencies の better-sqlite3 / sqlite3 (native の build や prebuilt の download) と esbuild の postinstall
#   (platform の binary は optionalDependencies の @esbuild/<platform> で入るので無くても動く)。CI とローカルの模擬で
#   同じ条件にし、ローカルの模擬で npm の外へ download が出ないようにするため止める。
# 環境変数 NPM_CI_EXTRA: npm ci に足す引数 (ローカルの模擬は --offline。CI では空)
set -euo pipefail

SD="${1:?pokemon-showdown の dir を指定}"
PY="${2:-python}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

fail() { echo "[showdown_prepare] FAILED: $*" >&2; exit 1; }
now() { date +%s; }

read -r COMMIT NODE_MAJOR FORMAT < <(cd "$ROOT" && "$PY" -B -c \
  "from champions_agent.config import INH_R1_SHOWDOWN_COMMIT as c, INH_R1_NODE_MAJOR as n, TRAINING_BATTLE_FORMAT as f; print(c, n, f)")
[ -n "${FORMAT:-}" ] || fail "config (champions_agent/config.py) を読めない"

HEAD="$(git -C "$SD" rev-parse HEAD)" || fail "$SD が git の作業ツリーでない"
[ "$HEAD" = "$COMMIT" ] || fail "HEAD $HEAD が固定の commit $COMMIT と違う"
echo "[showdown_prepare] pokemon-showdown $HEAD ($(git -C "$SD" log -1 --format='%ci %s'))"

NODE_VER="$(node -p 'process.versions.node')" || fail "node が無い"
[ "${NODE_VER%%.*}" = "$NODE_MAJOR" ] || fail "node $NODE_VER が固定の系列 $NODE_MAJOR と違う"
echo "[showdown_prepare] node v$NODE_VER / npm $(npm --version)"

[ -f "$SD/package-lock.json" ] || fail "$SD/package-lock.json が無い (npm ci できない)"
[ ! -e "$SD/dist" ] || fail "$SD/dist が既にある (取得したての作業ツリーで build する)"

cd "$SD"
t0=$(now)
# shellcheck disable=SC2086
npm ci --omit=dev --ignore-scripts --no-audit --no-fund ${NPM_CI_EXTRA:-} || fail "npm ci"
t1=$(now)
node build || fail "node build"
t2=$(now)
[ -f dist/sim/dex.js ] || fail "build の後に dist/sim/dex.js が無い"

node -e '
const {Dex} = require("./dist/sim");
const f = Dex.formats.get(process.argv[1]);
if (!f.exists) { console.error("形式 " + process.argv[1] + " が formats に無い"); process.exit(1); }
console.log("[showdown_prepare] 形式 " + f.id + " = " + f.name + " (mod " + f.mod + ")");
' "$FORMAT" || fail "対象形式 $FORMAT の確認"

echo "[showdown_prepare] OK (npm ci $((t1 - t0)) 秒, node build $((t2 - t1)) 秒)"
