#!/bin/bash
# 接続テストの開始準備を一括で行う (docs/CONNECTION_TEST_CHECKLIST.md 参照)。
#
#   bash scripts/start_connection_test.sh             # 通常 (監査モニター込み)
#   bash scripts/start_connection_test.sh --no-audit  # 監査なし (API課金を避ける)
#
# やること:
#  1. 学習ループを一時停止 (CPU競合を避ける。終了時スクリプトで再開)
#  2. Showdown(8100) を確保 (分析パネル・human_battle が使う)
#  3. deploy.sh で最新コードのアドバイザー(8000)+フロント(3000) を起動
#     (DEBUG_DUMP_FRAMES=1 はdeploy.sh側で常時有効)
#  4. リアルタイム監査モニターを起動 (対戦ごとに抽出ミスをsonnet監査。
#     1対戦あたり数分+API課金。終了時スクリプトが停止する)
cd "$(dirname "$0")/.." || exit 1
mkdir -p logs

echo "=== 接続テスト開始準備 ==="

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

# 4. リアルタイム監査 (既定で起動。--no-audit で無効化)
if [ "${1:-}" = "--no-audit" ]; then
  echo "監査モニター: 起動しない (--no-audit)"
elif pgrep -f tools.audit_monitor >/dev/null; then
  echo "監査モニター: 稼働中"
else
  nohup bash -c 'source .venv/bin/activate && python -m tools.audit_monitor' \
    > logs/audit_monitor.log 2>&1 & disown
  echo "監査モニター: 起動 (レポート: logs/audit_reports/ / ログ: logs/audit_monitor.log)"
fi

IP=$(ipconfig getifaddr en0 2>/dev/null || echo "localhost")
echo ""
echo "=== 準備完了 ==="
echo "フロントエンド:   http://${IP}:3000  (このMacなら http://localhost:3000)"
echo "human_battle用:   https://play.pokemonshowdown.com/~~localhost:8100/"
echo "チェックリスト:   docs/CONNECTION_TEST_CHECKLIST.md"
echo "終了時:           bash scripts/end_connection_test.sh"
