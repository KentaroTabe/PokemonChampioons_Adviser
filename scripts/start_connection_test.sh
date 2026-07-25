#!/bin/bash
# 接続テストの開始準備を一括で行う (docs/CONNECTION_TEST_CHECKLIST.md 参照)。
#
#   bash scripts/start_connection_test.sh
#
# やること:
#  1. 学習ループを一時停止 (CPU競合を避ける。終了時スクリプトで再開)
#  2. Showdown(8100) を確保 (分析パネル・human_battle が使う)
#  3. deploy.sh で最新コードのアドバイザー(8000)+フロント(3000) を起動
#     (DEBUG_DUMP_FRAMES=1 はdeploy.sh側で常時有効)
#  4. セッション開始マーカーを記録 (終了時の一括監査 audit_session が
#     このマーカー以降の対戦をまとめてsonnet 1回で検証する)
cd "$(dirname "$0")/.." || exit 1
mkdir -p logs

echo "=== 接続テスト開始準備 ==="
date +%s > logs/.connection_test_start

# 1. 学習ループの一時停止
launchctl unload ~/Library/LaunchAgents/com.championsadviser.train.plist 2>/dev/null \
  && echo "学習ループ(launchd): 停止" || echo "学習ループ(launchd): 未ロード"
for pat in train_forever train_nightly tools.smoke_train champions_agent.train.evaluate; do
  pkill -f "$pat" 2>/dev/null && echo "  残プロセス停止: $pat"
done
sleep 1

# 2. Showdown の確保
if lsof -nP -iTCP:8100 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Showdown(8100): 稼働中"
else
  nohup node pokemon-showdown/pokemon-showdown start 8100 --no-security \
    > logs/showdown_nohup.log 2>&1 & disown
  echo "Showdown(8100): 起動"
fi

# 3. アドバイザー+フロントエンド (最新コードで再起動)
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
