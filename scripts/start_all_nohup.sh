#!/bin/bash
# 全常駐プロセスのセッション非依存起動 (docs/OPERATIONS.md 参照)。
# 既に動いているものはスキップする。
cd "$(dirname "$0")/.." || exit 1
mkdir -p logs

up() { lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }

if up 8000; then echo "アドバイザー(8000): 稼働中"; else
  nohup bash -c 'source .venv/bin/activate && PYTHONUNBUFFERED=1 DEBUG_DUMP_FRAMES=1 uvicorn server:app_asgi --host 0.0.0.0 --port 8000' \
    > logs/server_nohup.log 2>&1 & disown
  echo "アドバイザー(8000): 起動"
fi

if up 3000; then echo "フロントエンド(3000): 稼働中"; else
  nohup python3 -m http.server 3000 > logs/frontend_nohup.log 2>&1 & disown
  echo "フロントエンド(3000): 起動"
fi

if up 8100; then echo "Showdown(8100): 稼働中"; else
  nohup node pokemon-showdown/pokemon-showdown start 8100 --no-security \
    > logs/showdown_nohup.log 2>&1 & disown
  echo "Showdown(8100): 起動"
fi

if pgrep -f train_forever >/dev/null; then echo "学習ループ: 稼働中"; else
  nohup bash champions_agent/scripts/train_forever.sh \
    > champions_agent/train/logs/train_forever_nohup.log 2>&1 & disown
  echo "学習ループ: 起動"
fi
