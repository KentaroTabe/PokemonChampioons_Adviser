#!/usr/bin/env bash
# 行動方策のチェックポイント (各性格の 本体 / _ema / _best) の観測次元を末尾追記ぶんゼロ拡張する。
#   bash scripts/migrate_policy_obs.sh <新しい次元> [styles...]
#   例: bash scripts/migrate_policy_obs.sh 436 balance offense cycle
# - 事前に学習を止めること (scripts/stop_training.sh)。学習中のファイルを書き換えない
# - 拡張前のファイルは checkpoints/obs<旧次元>_backup_<日時>/ に退避する
# - 拡張は champions_agent/train/migrate_obs.py (拡張前後で同じ盤面の出力が一致することを検査)
set -uo pipefail
cd "$(dirname "$0")/.."
DIM="${1:?新しい観測次元を指定 (例 436)}"
shift
STYLES="${*:-balance offense cycle}"
CKPT="champions_agent/train/checkpoints"
STAMP="$(date +%Y%m%d_%H%M)"
BACKUP="$CKPT/obs_backup_$STAMP"
if pgrep -fl "train_forever|train_nightly|smoke_train|train_battle" >/dev/null; then
  echo "[migrate_policy_obs] 学習プロセスが動いています。先に scripts/stop_training.sh を実行してください"
  exit 1
fi
mkdir -p "$BACKUP"
fail=0
for style in $STYLES; do
  for suffix in "" "_ema" "_best"; do
    f="$CKPT/battle_policy_${style}${suffix}.zip"
    if [ ! -f "$f" ]; then
      echo "[migrate_policy_obs] なし: $f (とばす)"
      continue
    fi
    cp "$f" "$BACKUP/"
    echo "[migrate_policy_obs] $f"
    if ! .venv/bin/python -m champions_agent.train.migrate_obs --src "$f" --dst "$f" --dim "$DIM"; then
      echo "[migrate_policy_obs] 失敗: $f (退避から戻します)"
      cp "$BACKUP/$(basename "$f")" "$f"
      fail=1
    fi
  done
done
echo "[migrate_policy_obs] 退避先: $BACKUP"
exit "$fail"
