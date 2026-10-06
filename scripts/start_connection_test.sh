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
# 学習ループ: 2026-09-29 第16回で、学習 (4 環境) + Showdown + 助言サーバーの同時実行が 1 時間続くと熱圧迫 (thermal-pressure
# Heavy) で CPU が絞られ、フレーム処理率が 9% まで落ちて助言が止まった (7/27 の「学習 ON/OFF で差なし」はもう当てはまらない)。
# TRAINING_MODE=on_demand (必要時のみ回す) のときに動いていれば、テストのために止める (2026-09-29 ユーザー決定。
# 終了処理は on_demand では再開しない)。continuous のときは止めずに警告だけ出す。
cd "$(dirname "$0")/.." || exit 1
mkdir -p logs
. scripts/lib/ports.sh
load_ports

echo "=== 接続テスト開始準備 ==="
date +%s > logs/.connection_test_start
TRAINING_MODE=continuous
[ -f config/training.env ] && . config/training.env
if pgrep -f train_forever >/dev/null; then
  if [ "$TRAINING_MODE" = "on_demand" ]; then
    echo "学習ループ: 稼働中だが TRAINING_MODE=on_demand → テスト中の熱圧迫を避けるため止める (再開は bash scripts/start_training.sh)"
    bash scripts/stop_training.sh
  else
    echo "学習ループ: 稼働中 (TRAINING_MODE=$TRAINING_MODE)"
    echo "  ⚠ 学習と同時のテストは 1 時間ほどで熱圧迫により助言が止まることがある (2026-09-29 第16回: 処理率 9%)。"
    echo "    止めるなら: bash scripts/stop_training.sh  (テスト後の再開: bash scripts/start_training.sh)"
  fi
else
  echo "学習ループ: 停止中 (再開は bash scripts/start_training.sh)"
fi

# 1. Showdown の確保
if lsof -nP -iTCP:8100 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Showdown(8100): 稼働中"
else
  nohup node pokemon-showdown/pokemon-showdown start 8100 --no-security \
    > logs/showdown_nohup.log 2>&1 & disown
  echo "Showdown(8100): 起動"
fi

# 2. アドバイザー+フロントエンド (最新コードで起動)
# ⚠ deploy.sh は「停止中なら起動しない」(2026-08-05の仕様変更)。
# テスト開始時はまさに停止中なので、ここでは start_all_nohup.sh で
# 明示的に起動する。deploy.sh に任せると起動されないまま
# 「準備完了」と表示され、DEBUG_DUMP_FRAMES 無しの手動起動を招いて
# 終了時の視覚監査ができなくなる (2026-08-11に発生)
# 「稼働中」は自分たちのプロセスが待ち受けているときだけ (別プロジェクトが同じポートを使っていても起動側で空きへずらす)
set -- $(choose_port "$ADVISOR_PORT_DEFAULT" "$ADVISOR_PORT" "$ADVISOR_PATTERN")
if [ "$2" = "ours" ]; then
  bash scripts/deploy.sh || {
    echo "deploy.sh が失敗しました (対戦中判定の可能性)。--force が必要か確認してください"
    exit 1
  }
else
  bash scripts/start_all_nohup.sh
fi
load_ports

# フレーム保存の実測確認 (視覚監査の前提。無ければ終了時に監査できない)
if ! ps eww "$(pgrep -f 'uvicorn server:app_asgi' | head -1)" 2>/dev/null \
     | grep -q "DEBUG_DUMP_FRAMES=1"; then
  echo "⚠ アドバイザーが DEBUG_DUMP_FRAMES=1 で起動していません。"
  echo "  このままだと終了時の視覚監査 (誤認一覧) が作れません。"
  echo "  一度止めて再実行してください: pkill -f 'uvicorn server:app_asgi'"
fi

IP=$(ipconfig getifaddr en0 2>/dev/null || echo "localhost")
echo ""
echo "=== 準備完了 ==="
echo "フロントエンド:   http://${IP}:${FRONTEND_PORT}  (このMacなら http://localhost:${FRONTEND_PORT}、アドバイザー ${ADVISOR_PORT})"
echo "human_battle用:   https://play.pokemonshowdown.com/~~localhost:8100/"
echo "チェックリスト:   docs/CONNECTION_TEST_CHECKLIST.md"
echo "終了時:           bash scripts/end_connection_test.sh"
