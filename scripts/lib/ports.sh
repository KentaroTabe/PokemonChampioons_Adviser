#!/usr/bin/env bash
# 常駐 (アドバイザー uvicorn / フロント http.server) のポート解決。scripts から `. scripts/lib/ports.sh` で読む。
#
# - 既定は config/ports.env (ADVISOR_PORT_DEFAULT / FRONTEND_PORT_DEFAULT / PORT_SEARCH_RANGE)
# - 待ち受け中のポートが「自分たち」(コマンドが一致し、作業ディレクトリがこのリポジトリ) なら稼働中として使う
# - 他のプロセスが使っていれば +1 ずつ PORT_SEARCH_RANGE まで空きを探す
#   (2026-09-16: 別プロジェクトの Next.js が 3000 を使っていて、起動スクリプトが「稼働中」と誤認した)
# - 決めたポートは logs/ports.env (シェル用) と config/ports.local.js (フロントが読む) に書く
#
#   port_state PORT PATTERN           → free / ours / foreign
#   pick_free_port PORT               → PORT から上へ最初の空きポート (無ければ戻り値 1)
#   choose_port DEFAULT LAST PATTERN  → "PORT STATE" (STATE: ours=稼働中 / start=このポートで起動 / none=空き無し)
#   load_ports                        → ADVISOR_PORT / FRONTEND_PORT (前回の logs/ports.env、無ければ既定)
#   save_ports ADV FRONT              → logs/ports.env と config/ports.local.js を書く
#   port_owner PORT                   → 表示用 "pid コマンド (cwd ...)"
# 呼び出し側が set -e / pipefail でも落ちないよう、失敗し得るパイプには || true を付けている。

PORTS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
. "$PORTS_ROOT/config/ports.env"
PORTS_STATE="${PORTS_STATE:-$PORTS_ROOT/logs/ports.env}"
PORTS_JS="${PORTS_JS:-$PORTS_ROOT/config/ports.local.js}"
ADVISOR_PATTERN="uvicorn server:app_asgi"
FRONTEND_PATTERN="http.server"

port_listener_pid() {   # $1=port → 待ち受けている pid (無ければ空)
  lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null | head -n 1 || true
}

pid_command() { ps -o command= -p "$1" 2>/dev/null || true; }

pid_cwd() { lsof -a -p "$1" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -n 1 || true; }

port_owner() {   # $1=port → 表示用
  local pid
  pid="$(port_listener_pid "$1")"
  if [ -z "$pid" ]; then
    echo "(未使用)"
    return 0
  fi
  # 実行ファイルのディレクトリは落として 80 文字まで (Python の絶対パスだけで 80 文字を超え、引数が見えなくなる)
  echo "pid $pid $(pid_command "$pid" | sed 's#^[^ ]*/##' | cut -c1-80) (cwd $(pid_cwd "$pid"))"
}

port_state() {   # $1=port $2=自分たちのコマンドのパターン → free / ours / foreign
  local pid
  pid="$(port_listener_pid "$1")"
  if [ -z "$pid" ]; then
    echo free
    return 0
  fi
  if pid_command "$pid" | grep -q -- "$2" && [ "$(pid_cwd "$pid")" = "$PORTS_ROOT" ]; then
    echo ours
  else
    echo foreign
  fi
}

pick_free_port() {   # $1=開始ポート → 最初の空きポート (PORT_SEARCH_RANGE 以内)。無ければ戻り値 1
  local p="$1" i=0
  while [ "$i" -le "$PORT_SEARCH_RANGE" ]; do
    if [ -z "$(port_listener_pid "$p")" ]; then
      echo "$p"
      return 0
    fi
    p=$((p + 1))
    i=$((i + 1))
  done
  return 1
}

choose_port() {   # $1=既定 $2=前回使ったポート (空でも可) $3=パターン → "PORT STATE"
  local p
  for p in "$2" "$1"; do
    [ -n "$p" ] || continue
    if [ "$(port_state "$p" "$3")" = ours ]; then
      echo "$p ours"
      return 0
    fi
  done
  if [ -z "$(port_listener_pid "$1")" ]; then
    echo "$1 start"
    return 0
  fi
  if p="$(pick_free_port "$1")"; then
    echo "$p start"
  else
    echo "0 none"
  fi
  return 0
}

load_ports() {
  ADVISOR_PORT="$ADVISOR_PORT_DEFAULT"
  FRONTEND_PORT="$FRONTEND_PORT_DEFAULT"
  if [ -f "$PORTS_STATE" ]; then
    . "$PORTS_STATE"
  fi
  return 0
}

save_ports() {   # $1=advisor $2=frontend
  mkdir -p "$(dirname "$PORTS_STATE")" "$(dirname "$PORTS_JS")"
  printf 'ADVISOR_PORT=%s\nFRONTEND_PORT=%s\n' "$1" "$2" > "$PORTS_STATE"
  printf '// 起動スクリプトが生成する (コミットしない)。フロントが接続するアドバイザーのポート\nwindow.ADVISOR_PORT = %s;\n' "$1" > "$PORTS_JS"
}
