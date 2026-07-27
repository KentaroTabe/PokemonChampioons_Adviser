#!/bin/bash
# 更新の反映 (アドバイザーサーバーの安全な再起動)。
#
# 再起動で反映されるもの:
#  - コード修正 (vision/advisor/server)
#  - 最新の学習チェックポイント (policy_battleは起動時に読み込む)
#  - config/my_team.json はホットリロードなので再起動不要
#
# 対戦中 (直近3分以内に対戦ログが更新) はスキップする。--force で強制。
cd "$(dirname "$0")/.." || exit 1

if [ "$1" != "--force" ]; then
  # mtimeではなくログ内容で判定する (対戦の合間もメニュー誤分類の
  # シーンレコードが書き込まれ続け、mtimeでは静かにならないため)
  if python3 -m tools.check_battle_active 1 >/dev/null 2>&1; then
    echo "対戦中のシグナル (events/hp/コマンド画面) を検知したため中止しました"
    echo "強制する場合: bash scripts/deploy.sh --force"
    exit 1
  fi
fi

pkill -f "uvicorn server:app_asgi" 2>/dev/null
sleep 2
mkdir -p logs
# RL_ADVICE_STYLE=balance: 正直な再測定 (2026-07-26、評価凍結バグ修正後)
# で balance が最強 (ベンチ0.70)。_best昇格も同測定に基づく
nohup bash -c 'source .venv/bin/activate && PYTHONUNBUFFERED=1 DEBUG_DUMP_FRAMES=1 RL_ADVICE_STYLE=balance uvicorn server:app_asgi --host 0.0.0.0 --port 8000' \
  > logs/server_nohup.log 2>&1 & disown

for _ in $(seq 1 30); do
  if lsof -nP -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "反映完了: アドバイザー(8000) 再起動済み ($(date '+%H:%M:%S'))"
    # フロントエンドが落ちていたらついでに起動
    if ! lsof -nP -iTCP:3000 -sTCP:LISTEN >/dev/null 2>&1; then
      nohup python3 -m http.server 3000 > logs/frontend_nohup.log 2>&1 & disown
      echo "フロントエンド(3000) も起動しました"
    fi
    exit 0
  fi
  sleep 1
done
echo "起動確認に失敗しました。logs/server_nohup.log を確認してください"
exit 1
