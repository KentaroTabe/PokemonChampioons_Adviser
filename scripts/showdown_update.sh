#!/usr/bin/env bash
# ローカル pokemon-showdown を上流 master に追従させて再ビルドする (レギュレーション切替用)。
#   bash scripts/showdown_update.sh [--force]
# 学習・測定 (check_advisor_player / check_search_expert / evaluate) が動いている間は
# シミュレータが差し替わって結果が混ざるため、既定では中断する。
set -euo pipefail
cd "$(dirname "$0")/.."
SD=pokemon-showdown
FORCE="${1:-}"
busy="$(pgrep -fl 'train_forever|tools.check_advisor_player|tools.check_search_expert|champions_agent.train' || true)"
if [ -n "$busy" ] && [ "$FORCE" != "--force" ]; then
  echo "[showdown_update] 学習/測定プロセスが稼働中のため中断します (--force で強行):"
  echo "$busy"
  exit 2
fi
if [ -n "$(git -C "$SD" status --short)" ]; then
  echo "[showdown_update] ローカルに未コミット変更があります。先に退避してください:"
  git -C "$SD" status --short | head -20
  exit 2
fi
echo "[showdown_update] before: $(git -C "$SD" log -1 --format='%h %ad %s' --date=short)"
git -C "$SD" fetch --quiet origin master
git -C "$SD" merge --ff-only origin/master
echo "[showdown_update] after:  $(git -C "$SD" log -1 --format='%h %ad %s' --date=short)"
echo "[showdown_update] build (node build) ..."
( cd "$SD" && node build )
echo "[showdown_update] Champions フォーマット:"
grep -n "Gen 9 Champions\] BSS" "$SD/config/formats.ts"
echo "[showdown_update] champions mod:"
ls "$SD/data/mods" | grep -i champ
echo "[showdown_update] 完了。Showdown サーバーは再起動が必要です (scripts/ensure_showdown.sh は起動済みなら何もしない)。"
