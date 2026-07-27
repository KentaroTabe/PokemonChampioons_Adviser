#!/bin/bash
# 接続テストの開始準備を一括で行う (docs/CONNECTION_TEST_CHECKLIST.md 参照)。
#
#   bash scripts/start_connection_test.sh
#
# やること:
#  1. Showdown(8100) を確保 (分析パネル・human_battle が使う)
#  2. deploy.sh で最新コードのアドバイザー(8000)+フロント(3000) を起動
#     (DEBUG_DUMP_FRAMES=1 はdeploy.sh側で常時有効)
#  3. セッション開始マーカーを記録 (終了時の一括監査 audit_session が
#     このマーカー以降の対戦をまとめてsonnet 1回で検証する)
#
# 学習ループは止めない: 実測 (tools/bench_pipeline) で学習ON/OFFの差は
# 平均95→102ms・予算超過率は27%で同じだった (2026-07-27)。止めると
# 30分のテストで約30万ステップを失うため、継続したまま行う。
cd "$(dirname "$0")/.." || exit 1
mkdir -p logs

echo "=== 接続テスト開始準備 ==="
date +%s > logs/.connection_test_start
if pgrep -f train_forever >/dev/null; then
  echo "学習ループ: 稼働中のまま継続 (停止不要と実測で確認済み)"
else
  echo "学習ループ: 停止中 (必要なら launchctl load -w で開始)"
fi

# 1. Showdown の確保
if lsof -nP -iTCP:8100 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Showdown(8100): 稼働中"
else
  nohup node pokemon-showdown/pokemon-showdown start 8100 --no-security \
    > logs/showdown_nohup.log 2>&1 & disown
  echo "Showdown(8100): 起動"
fi

# 2. アドバイザー+フロントエンド (最新コードで再起動)
bash scripts/deploy.sh || {
  echo "deploy.sh が失敗しました (対戦中判定の可能性)。--force が必要か確認してください"
  exit 1
}

IP=$(ipconfig getifaddr en0 2>/dev/null || echo "localhost")
echo ""
echo "=== 準備完了 ==="
echo "フロントエンド:   http://${IP}:3000  (このMacなら http://localhost:3000)"
echo "human_battle用:   https://play.pokemonshowdown.com/~~localhost:8100/"
echo "チェックリスト:   docs/CONNECTION_TEST_CHECKLIST.md"
echo "終了時:           bash scripts/end_connection_test.sh"
