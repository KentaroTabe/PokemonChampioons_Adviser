#!/usr/bin/env bash
# スイープが実際に学習結果を保存し始めたかを見届ける。
#   bash scripts/sweep_watch_divergence.sh [待つ秒数] [性格]
#
# 起動しただけで「走っているつもり」になると、全条件が種チェックポイントの
# ままの状態を測って「差がない」という無意味な結論を出す (2026-07-30に2回)。
# 条件ごとのチェックポイントのハッシュが割れたら保存が効いている証拠。
set -uo pipefail
cd "$(dirname "$0")/.."

DEADLINE=$(( $(date +%s) + ${1:-900} ))
STYLE="${2:-balance}"

while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  # 起動直後は本番ループの終了待ち (sleep 30) で tools.reward_sweep がまだ
  # 存在しない。ラッパー (reward_sweep_run.sh) も含めて見る
  if ! pgrep -f reward_sweep > /dev/null 2>&1; then
    echo "[watch] NG: スイープのプロセスが見当たりません (異常終了した可能性)"
    exit 1
  fi
  # 保存先は <条件>/checkpoints/ 配下 (CHAMPIONS_MODELS_DIR の直下ではない)
  files=$(ls logs/reward_sweep/*_s*/checkpoints/battle_policy_"${STYLE}".zip \
    2>/dev/null | wc -l | tr -d ' ')
  uniq_n=$(md5 -q logs/reward_sweep/*_s*/checkpoints/battle_policy_"${STYLE}".zip \
    2>/dev/null | sort -u | wc -l | tr -d ' ')
  if [ "${files:-0}" -eq 0 ]; then
    echo "[watch] NG: チェックポイントが1つも見つかりません (パスの指定を確認)"
    exit 1
  fi
  if [ "${uniq_n:-0}" -ge 2 ]; then
    echo "[watch] OK: 条件ごとのチェックポイントが分岐しました (${uniq_n}種)"
    exit 0
  fi
  sleep 20
done

echo "[watch] NG: 制限時間内にチェックポイントが分岐しませんでした"
exit 1
