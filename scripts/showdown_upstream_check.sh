#!/usr/bin/env bash
# ローカル pokemon-showdown と上流 (smogon/pokemon-showdown) の champions 関連差分を確認する (読み取りのみ)。
#   bash scripts/showdown_upstream_check.sh
set -euo pipefail
cd "$(dirname "$0")/.."
SD=pokemon-showdown
echo "== ローカル: ブランチ / 先頭コミット =="
git -C "$SD" branch --show-current
git -C "$SD" log -3 --format='%h %ad %s' --date=short
echo "== 上流 fetch =="
git -C "$SD" fetch --quiet origin master
echo "origin/master: $(git -C "$SD" log -1 --format='%h %ad %s' --date=short origin/master)"
echo "== ローカルに無い上流コミット (champions 関連, 直近40) =="
git -C "$SD" log --format='%h %ad %s' --date=short HEAD..origin/master -- \
  data/mods/champions data/mods/championsregma data/mods/championsregmb config/formats.ts \
  | head -40
echo "== 上流 formats.ts の Champions フォーマット =="
git -C "$SD" show origin/master:config/formats.ts | grep -n "Gen 9 Champions\]" | head -20
echo "== 上流 data/mods 配下の champions ディレクトリ =="
git -C "$SD" ls-tree --name-only origin/master:data/mods | grep -i champ
echo "== ローカル未コミット変更 =="
git -C "$SD" status --short | head -10
