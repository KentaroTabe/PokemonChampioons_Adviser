#!/bin/bash
# 接続テストの終了処理を一括で行う。
#
#   bash scripts/end_connection_test.sh              # 一括監査つき
#   bash scripts/end_connection_test.sh --no-audit   # 監査なし (API課金を避ける)
#
# やること:
#  1. アドバイザー(8000)+フロントエンド(3000) の停止
#     (Showdownは学習が使うため止めない)
#  2. 学習ループの確認 (テスト中も止めていないので、落ちていた場合のみ再開)
#  3. 今回の対戦の簡易サマリー表示 (敗因分析: セッション全体)
#  4. セッション一括監査 (tools/audit_session): 今回の全対戦を横断して
#     矛盾候補の機械検出+層化サンプリングで sonnet 1回にまとめて検証する
#  5. フレーム取りこぼし率の表示 (受信/処理/破棄。改善効果の確認用)
#  6. 試用中 Package (experiment ラベル) があれば実戦サマリー (scripts/canary_summary.sh --session)
#
# ターミナルを使わずに実行するなら操作パネル (tools/control_panel、docs/OPERATIONS.md) の「終了」ボタン。
cd "$(dirname "$0")/.." || exit 1
. scripts/lib/ports.sh
load_ports

echo "=== 接続テスト終了処理 ==="

# アドバイザー/フロントエンド停止 (ポートは起動時に決めたもの: logs/ports.env)
pkill -f "uvicorn server:app_asgi" 2>/dev/null && echo "アドバイザー($ADVISOR_PORT): 停止" \
  || echo "アドバイザー($ADVISOR_PORT): 未起動"
pkill -f "http.server $FRONTEND_PORT" 2>/dev/null && echo "フロントエンド($FRONTEND_PORT): 停止" \
  || echo "フロントエンド($FRONTEND_PORT): 未起動"

# 学習ループの確認。TRAINING_MODE=continuous のときだけ止まっていれば再開する
# (2026-09-17: 常時学習は停止 (on_demand)。必要なときだけ scripts/start_training.sh で回す)
TRAINING_MODE=continuous
[ -f config/training.env ] && . config/training.env
if pgrep -f train_forever >/dev/null; then
  echo "学習ループ: 稼働中 (テスト中も継続)"
elif [ "$TRAINING_MODE" != "continuous" ]; then
  echo "学習ループ: 停止中 (TRAINING_MODE=$TRAINING_MODE、自動再開しない)"
else
  launchctl load -w ~/Library/LaunchAgents/com.championsadviser.train.plist 2>/dev/null
  sleep 3
  if pgrep -f train_forever >/dev/null; then
    echo "学習ループ: 停止していたため再開しました"
  else
    echo "⚠ 学習ループが起動していません。手動確認:"
    echo "  launchctl load -w ~/Library/LaunchAgents/com.championsadviser.train.plist"
  fi
fi

# フレーム取りこぼし率 (改善効果の確認用)
bash scripts/show_frame_stats.sh

# 今回の対戦サマリー (セッション中の全対戦。マーカーが無ければ直近10戦)
echo ""
echo "=== セッションのサマリー ==="
source .venv/bin/activate 2>/dev/null
python -m tools.analyze_battles --session --last 10 2>/dev/null \
  || echo "(対戦ログの集計に失敗。scripts/run_test.sh 環境を確認)"

# 決定監査 (テストA: 助言の欠落/遅延/不一致と大失点の抽出。ローカル・無課金)
echo ""
echo "=== 決定監査 (テストAの合格判定) ==="
python -m tools.decision_audit --session \
  || echo "(決定監査に失敗。手動実行: python -m tools.decision_audit)"

# 試用中の Package (experiment ラベル) があれば、その実戦サマリー (勝敗・勝率の CI・遵守率) を出す
# (canary の smoke 20 戦の確認用。ラベルは自動では外さない: bash scripts/experiment_label.sh off か操作パネルの OFF)
if [ -f logs/.experiment_package ]; then
  echo ""
  echo "=== 試用中 Package の実戦サマリー (experiment ラベル: $(cat logs/.experiment_package)) ==="
  bash scripts/canary_summary.sh --session \
    || echo "(サマリーに失敗。手動実行: bash scripts/canary_summary.sh --session)"
fi

# 実戦の相手バンク (相手の実際の選出・先発・判明した型) を更新 → 学習環境の相手プールと選出助言の条件づけに使う
echo ""
echo "=== 実戦の相手バンクを更新 ==="
python -m tools.real_opponents --build 2>/dev/null \
  || echo "(バンクの更新に失敗。手動実行: python -m tools.real_opponents --build)"

# 自パーティ改善案: 動きづらかった相手・相手の良い動き (概念) を出し、現行 + 近傍の測定 run を起動する
# (未測定の案は載せない。測定結果は python -m tools.party_improvements --report <run_id>)
echo ""
echo "=== 自パーティ改善案 (セッションの相手パーティから。測定を起動) ==="
python -m tools.party_improvements --session --measure 2>/dev/null \
  || echo "(改善案の生成に失敗。手動実行: python -m tools.party_improvements --session --measure)"

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
