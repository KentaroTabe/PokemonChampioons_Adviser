#!/usr/bin/env bash
# 選出データを新方策で取り直し、旧選出モデルとA/B比較する (事前登録)。
#
# ■ 背景: 選出モデルのラベル (勝敗) は収集時の操縦方策に依存する。
#   方策が強くなった (_best 0.539→0.584) ことで旧データ由来モデルの寄与が
#   +0.072→+0.028 に圧縮された。新方策で収集し直したモデルが上回るかを測る。
#
# ■ 事前登録 (2026-08-03, 測定前に固定):
#   - 収集: 新_best方策で旧データと同規模 (~49,000件: ranked 8x4000 +
#     paired 4x3000 + myteam 2x2500)。旧データは退避し混ぜない
#     (データ量とラベル品質の交絡を避ける)
#   - 学習: v1特徴量・--no-finetune。サンドボックスへ保存し配布物に触れない。
#     保存ガード (平均予測比+2%未満で中止) はそのまま適用
#   - 比較: selection=model の 旧(本番) vs 新(サンドボックス) を同時並行で
#     各10,000戦・同一相手列 (シード20260803)・操縦は同一の_best
#   - 判定 (tools/two_prop_verdict, δ=0.02):
#       採用: 差>=+0.02 かつ 95%CI下限>0 / 棄却: CI上限<+0.02
#       判定不能: 戦数を倍増して1回だけ再測定。なお不能なら棄却
#   - 採用時のみ本番へ反映 (通常経路で学習し直して配布パスへ保存)
set -uo pipefail
cd "$(dirname "$0")/.."

STAMP="$(date +%Y%m%d_%H%M%S)"
DATA="champions_agent/train/logs/selection_data.npz"
OUTDIR="logs/selection_refresh"
SANDBOX="$OUTDIR/checkpoints"
BATTLES="${1:-10000}"
SEED="${2:-20260803}"
mkdir -p "$SANDBOX"

echo "===== 選出データ更新: $(date '+%m-%d %H:%M:%S') ====="

echo "--- 0) 旧方策データを退避 ---"
if [ -f "$DATA" ]; then
  mv "$DATA" "champions_agent/train/logs/selection_data_oldpolicy_${STAMP}.npz"
  echo "退避: selection_data_oldpolicy_${STAMP}.npz"
fi

echo "--- 1) 新方策で収集 (~49,000件) ---"
bash scripts/collect_selection.sh 8 4000 ranked
bash scripts/collect_selection.sh 4 3000 ranked paired
bash scripts/collect_selection.sh 2 2500 myteam

echo "--- 2) サンドボックスへ学習 (配布物に触れない) ---"
cp champions_agent/train/checkpoints/battle_policy_balance_best.zip "$SANDBOX/"
(
  export CHAMPIONS_MODELS_DIR="$PWD/$SANDBOX"
  .venv/bin/python -m champions_agent.train.train_selection \
    --no-finetune 2>/dev/null
)
if [ ! -f "$SANDBOX/selection_model_general.pt" ]; then
  echo "NG: 新モデルが保存されていません (保存ガードで中止された可能性)"
  exit 1
fi

echo "--- 3) 旧 vs 新 並行A/B (各${BATTLES}戦 / シード${SEED}) ---"
.venv/bin/python -m champions_agent.train.evaluate \
  --play-style balance --battles "$BATTLES" --opponent benchmark \
  --checkpoint best --selection model --opp-seed "$SEED" --no-save \
  > "$OUTDIR/old.log" 2>/dev/null &
PID_OLD=$!
(
  export CHAMPIONS_MODELS_DIR="$PWD/$SANDBOX"
  .venv/bin/python -m champions_agent.train.evaluate \
    --play-style balance --battles "$BATTLES" --opponent benchmark \
    --checkpoint best --selection model --opp-seed "$SEED" --no-save \
    > "$OUTDIR/new.log" 2>/dev/null
) &
PID_NEW=$!
wait "$PID_OLD" "$PID_NEW"

WINS_OLD=$(grep -o "'wins': [0-9]*" "$OUTDIR/old.log" | grep -o "[0-9]*")
WINS_NEW=$(grep -o "'wins': [0-9]*" "$OUTDIR/new.log" | grep -o "[0-9]*")
if [ -z "$WINS_OLD" ] || [ -z "$WINS_NEW" ]; then
  echo "NG: 評価結果を読めませんでした ($OUTDIR/*.log)"
  exit 1
fi
echo "旧=${WINS_OLD}/${BATTLES} 新=${WINS_NEW}/${BATTLES}"
.venv/bin/python -m tools.two_prop_verdict \
  "$WINS_NEW" "$BATTLES" "$WINS_OLD" "$BATTLES" --delta 0.02
echo "===== 完了: $(date '+%m-%d %H:%M:%S') ====="
