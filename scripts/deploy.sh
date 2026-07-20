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
  recent=$(find logs/battles -name "*.jsonl" -mmin -3 2>/dev/null | head -1)
  if [ -n "$recent" ]; then
    echo "対戦中の可能性があるため中止しました ($recent が3分以内に更新)"
    echo "強制する場合: bash scripts/deploy.sh --force"
    exit 1
  fi
fi

pkill -f "uvicorn server:app_asgi" 2>/dev/null
sleep 2
mkdir -p logs
nohup bash -c 'source .venv/bin/activate && PYTHONUNBUFFERED=1 DEBUG_DUMP_FRAMES=1 uvicorn server:app_asgi --host 0.0.0.0 --port 8000' \
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
