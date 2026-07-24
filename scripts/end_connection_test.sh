#!/bin/bash
# 接続テストの終了処理を一括で行う。
#
#   bash scripts/end_connection_test.sh
#
# やること:
#  1. リアルタイム監査モニターの停止 (起動していれば)
#  2. アドバイザー(8000)+フロントエンド(3000) の停止
#     (Showdownは学習が使うため止めない)
#  3. 学習ループの再開 (launchd)
#  4. 今回の対戦の簡易サマリー表示 (敗因分析 直近10戦)
cd "$(dirname "$0")/.." || exit 1

echo "=== 接続テスト終了処理 ==="

# 1. 監査モニター停止
pkill -f tools.audit_monitor 2>/dev/null && echo "監査モニター: 停止" \
  || echo "監査モニター: 未起動"

# 2. アドバイザー/フロントエンド停止
pkill -f "uvicorn server:app_asgi" 2>/dev/null && echo "アドバイザー(8000): 停止" \
  || echo "アドバイザー(8000): 未起動"
pkill -f "http.server 3000" 2>/dev/null && echo "フロントエンド(3000): 停止" \
  || echo "フロントエンド(3000): 未起動"

# 3. 学習ループ再開
launchctl load -w ~/Library/LaunchAgents/com.championsadviser.train.plist 2>/dev/null
sleep 3
if pgrep -f train_forever >/dev/null; then
  echo "学習ループ: 再開 (launchd)"
else
  echo "⚠ 学習ループが起動していません。手動確認:"
  echo "  launchctl load -w ~/Library/LaunchAgents/com.championsadviser.train.plist"
fi

# 4. 今回の対戦サマリー
echo ""
echo "=== 直近10戦のサマリー ==="
source .venv/bin/activate 2>/dev/null
python -m tools.analyze_battles --last 10 2>/dev/null \
  || echo "(対戦ログの集計に失敗。scripts/run_test.sh 環境を確認)"

echo ""
echo "誤認識に気づいた場合: python -m tools.audit_subtask --battle <対戦ログ>"
echo "対戦レビュー:         python -m tools.review_battle"
