#!/bin/bash
# 接続テストの終了処理を一括で行う。
#
#   bash scripts/end_connection_test.sh              # 一括監査つき
#   bash scripts/end_connection_test.sh --no-audit   # 監査なし (API課金を避ける)
#
# やること:
#  1. アドバイザー(8000)+フロントエンド(3000) の停止
#     (Showdownは学習が使うため止めない)
#  2. 学習ループの再開 (launchd)
#  3. 今回の対戦の簡易サマリー表示 (敗因分析 直近10戦)
#  4. セッション一括監査 (tools/audit_session): 今回の全対戦を横断して
#     矛盾候補の機械検出+層化サンプリングで sonnet 1回にまとめて検証する
cd "$(dirname "$0")/.." || exit 1

echo "=== 接続テスト終了処理 ==="

# アドバイザー/フロントエンド停止
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

# 今回の対戦サマリー (セッション中の全対戦。マーカーが無ければ直近10戦)
echo ""
echo "=== セッションのサマリー ==="
source .venv/bin/activate 2>/dev/null
python -m tools.analyze_battles --session --last 10 2>/dev/null \
  || echo "(対戦ログの集計に失敗。scripts/run_test.sh 環境を確認)"

# セッション一括監査 (sonnet 1回。今回の全対戦を横断)
if [ "${1:-}" = "--no-audit" ]; then
  echo ""
  echo "一括監査: スキップ (--no-audit)"
else
  echo ""
  echo "=== セッション一括監査 (sonnet, 数分かかります) ==="
  python -m tools.audit_session \
    || echo "(一括監査に失敗。手動実行: python -m tools.audit_session --last 5)"
fi
rm -f logs/.connection_test_start

echo ""
echo "対戦レビュー: python -m tools.review_battle"
