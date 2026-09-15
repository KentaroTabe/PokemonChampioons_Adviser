#!/bin/bash
# 全常駐プロセスのセッション非依存起動 (docs/OPERATIONS.md 参照)。
# 既に動いているものはスキップする。アドバイザー/フロントのポートは config/ports.env の既定を使い、
# 別のプロセスが使っていれば次の空きポートへずらす (scripts/lib/ports.sh)。決めたポートは logs/ports.env と
# config/ports.local.js (フロントが読む) に残るので、表示された URL を開く。
cd "$(dirname "$0")/.." || exit 1
mkdir -p logs
. scripts/lib/ports.sh
load_ports

set -- $(choose_port "$ADVISOR_PORT_DEFAULT" "$ADVISOR_PORT" "$ADVISOR_PATTERN")
ADVISOR_PORT="$1"; ADV_STATE="$2"
case "$ADV_STATE" in
  ours) echo "アドバイザー($ADVISOR_PORT): 稼働中";;
  none) echo "アドバイザー: 空きポートが無い ($ADVISOR_PORT_DEFAULT 〜 +$PORT_SEARCH_RANGE)"; exit 1;;
  *)
    if [ "$ADVISOR_PORT" != "$ADVISOR_PORT_DEFAULT" ]; then
      echo "アドバイザー: $ADVISOR_PORT_DEFAULT は別のプロセスが使用中 [$(port_owner "$ADVISOR_PORT_DEFAULT")] → $ADVISOR_PORT を使う"
    fi
    nohup bash -c "source .venv/bin/activate && PYTHONUNBUFFERED=1 DEBUG_DUMP_FRAMES=1 RL_ADVICE_STYLE=balance uvicorn server:app_asgi --host 0.0.0.0 --port $ADVISOR_PORT" \
      > logs/server_nohup.log 2>&1 & disown
    echo "アドバイザー($ADVISOR_PORT): 起動";;
esac

set -- $(choose_port "$FRONTEND_PORT_DEFAULT" "$FRONTEND_PORT" "$FRONTEND_PATTERN")
FRONTEND_PORT="$1"; FRONT_STATE="$2"
case "$FRONT_STATE" in
  ours) echo "フロントエンド($FRONTEND_PORT): 稼働中";;
  none) echo "フロントエンド: 空きポートが無い ($FRONTEND_PORT_DEFAULT 〜 +$PORT_SEARCH_RANGE)"; exit 1;;
  *)
    if [ "$FRONTEND_PORT" != "$FRONTEND_PORT_DEFAULT" ]; then
      echo "フロントエンド: $FRONTEND_PORT_DEFAULT は別のプロセスが使用中 [$(port_owner "$FRONTEND_PORT_DEFAULT")] → $FRONTEND_PORT を使う"
    fi
    nohup python3 -m http.server "$FRONTEND_PORT" > logs/frontend_nohup.log 2>&1 & disown
    echo "フロントエンド($FRONTEND_PORT): 起動";;
esac
# フロントは config/ports.local.js からアドバイザーのポートを読む (稼働中でも書き直して整合させる)
save_ports "$ADVISOR_PORT" "$FRONTEND_PORT"
echo "フロントエンド URL: http://localhost:$FRONTEND_PORT (アドバイザー $ADVISOR_PORT)"

if lsof -nP -iTCP:8100 -sTCP:LISTEN >/dev/null 2>&1; then echo "Showdown(8100): 稼働中"; else
  nohup node pokemon-showdown/pokemon-showdown start 8100 --no-security \
    > logs/showdown_nohup.log 2>&1 & disown
  echo "Showdown(8100): 起動"
fi

if pgrep -f train_forever >/dev/null; then echo "学習ループ: 稼働中"; else
  nohup bash champions_agent/scripts/train_forever.sh \
    > champions_agent/train/logs/train_forever_nohup.log 2>&1 & disown
  echo "学習ループ: 起動"
fi
