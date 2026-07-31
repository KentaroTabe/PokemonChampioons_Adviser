#!/usr/bin/env bash
# 選出データセットの再構築パイプライン (収集 → 学習)。
#
# 2026-07-30の列追加事故で272チーム分の収集データが失われた (残っているのは
# 60チーム時代の25,000件のみ)。272チームプールで積み増し、あわせて
# - 対応のある収集 (--paired): 同条件での選出比較 (ペアワイズ損失の材料)
# - opp_sel (相手の実選出3体): 読み合いの条件付きモデルの材料
# を初めて収録する。終了後に汎用/配布モデルと条件付きモデルを学習する
# (学習には平均予測を超えない場合の保存中止ガードが入っている)。
#
# 本番の学習ループは止めない (収集の勝敗ラベルは競合で偏らない。遅くなるだけ)。
set -uo pipefail
cd "$(dirname "$0")/.."

echo "===== 再収集開始: $(date '+%m-%d %H:%M:%S') ====="

echo "--- フェーズ1: 多チーム (272プール) 4回 x 4000戦 ---"
bash scripts/collect_selection.sh 4 4000 ranked

echo "--- フェーズ2: 対応のある収集 2回 x 3000戦 ---"
bash scripts/collect_selection.sh 2 3000 ranked paired

echo "--- フェーズ3: 自チーム 2回 x 2500戦 ---"
bash scripts/collect_selection.sh 2 2500 myteam

echo "--- 学習: 汎用+配布 (ペアワイズ損失込み) ---"
bash scripts/train_selection.sh

echo "--- 学習: 条件付きモデル (読み合い用) ---"
bash scripts/train_selection.sh --cond-sel

echo "===== 再収集完了: $(date '+%m-%d %H:%M:%S') ====="
