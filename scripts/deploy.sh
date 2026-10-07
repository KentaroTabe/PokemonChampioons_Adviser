#!/bin/bash
# 更新の反映 (アドバイザーサーバーの安全な再起動)。
#
# 再起動で反映されるもの:
#  - コード修正 (vision/advisor/server)
#  - 最新の学習チェックポイント (policy_battleは起動時に読み込む)
#  - config/my_team.json はホットリロードなので再起動不要
#
# 対戦中 (直近3分以内に対戦ログが更新) はスキップする。--force で強制。
# ポートは前回の起動で決めたもの (logs/ports.env、既定 config/ports.env) を使い、自分たちのプロセスかどうかで
# 稼働中を判定する (別のプロセスが同じポートを使っていても稼働中と誤認しない)。
cd "$(dirname "$0")/.." || exit 1
. scripts/lib/ports.sh
load_ports

if [ "$1" != "--force" ]; then
  # mtimeではなくログ内容で判定する (対戦の合間もメニュー誤分類の
  # シーンレコードが書き込まれ続け、mtimeでは静かにならないため)
  if python3 -m tools.check_battle_active 1 >/dev/null 2>&1; then
    echo "対戦中のシグナル (events/hp/コマンド画面) を検知したため中止しました"
    echo "強制する場合: bash scripts/deploy.sh --force"
    exit 1
  fi
fi

# もともと起動していなければ、反映のために起動はしない (2026-08-05指示)。
# 停止中 = 意図的に止めている状態であり、deployが勝手に起こすと
# テスト終了後などに常駐へ戻ってしまう。次回の起動時に最新コードが載る
set -- $(choose_port "$ADVISOR_PORT_DEFAULT" "$ADVISOR_PORT" "$ADVISOR_PATTERN")
ADVISOR_PORT="$1"; ADV_STATE="$2"
if [ "$ADV_STATE" != "ours" ]; then
  echo "アドバイザー($ADVISOR_PORT_DEFAULT / 前回 $ADVISOR_PORT) は停止中のため起動しません"
  echo "(次回 start_connection_test.sh / start_all_nohup.sh 起動時に最新コードが反映されます)"
  exit 0
fi

pkill -f "uvicorn server:app_asgi" 2>/dev/null
sleep 2
mkdir -p logs
# RL_ADVICE_STYLE=balance: 正直な再測定 (2026-07-26、評価凍結バグ修正後)
# で balance が最強 (ベンチ0.70)。_best昇格も同測定に基づく
nohup bash -c "source .venv/bin/activate && PYTHONUNBUFFERED=1 DEBUG_DUMP_FRAMES=1 RL_ADVICE_STYLE=balance uvicorn server:app_asgi --host 0.0.0.0 --port $ADVISOR_PORT" \
  > logs/server_nohup.log 2>&1 & disown

for _ in $(seq 1 30); do
  if [ "$(port_state "$ADVISOR_PORT" "$ADVISOR_PATTERN")" = ours ]; then
    echo "反映完了: アドバイザー($ADVISOR_PORT) 再起動済み ($(date '+%H:%M:%S'))"
    # フロントエンドが落ちていたらついでに起動 (別のプロセスがポートを使っていれば次の空きへ)
    set -- $(choose_port "$FRONTEND_PORT_DEFAULT" "$FRONTEND_PORT" "$FRONTEND_PATTERN")
    FRONTEND_PORT="$1"; FRONT_STATE="$2"
    if [ "$FRONT_STATE" = "start" ]; then
      nohup python3 -m "$FRONTEND_MODULE" "$FRONTEND_PORT" > logs/frontend_nohup.log 2>&1 & disown
      echo "フロントエンド($FRONTEND_PORT) も起動しました"
    elif [ "$FRONT_STATE" = "none" ]; then
      echo "⚠ フロントエンドの空きポートが無い ($FRONTEND_PORT_DEFAULT 〜 +$PORT_SEARCH_RANGE)"
    fi
    save_ports "$ADVISOR_PORT" "$FRONTEND_PORT"
    exit 0
  fi
  sleep 1
done
echo "起動確認に失敗しました。logs/server_nohup.log を確認してください"
exit 1
