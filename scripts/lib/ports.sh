#!/usr/bin/env bash
# 常駐 (アドバイザー uvicorn / フロント tools.frontend_server) のポート解決。scripts から `. scripts/lib/ports.sh` で読む。
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
#                                       (ports.local.js には配信する版の git commit window.FRONTEND_GIT_COMMIT も書く)
#   port_owner PORT                   → 表示用 "pid コマンド (cwd ...)"
#   launch_state PID LPID TABLE ENV   → 起動の確認の判定 (純粋、2026-10-23): ok / no_pid / no_listener / other_process / no_dump
#   advisor_launch_wait PID PORT SEC  → 今回起動した PID について待受と DEBUG_DUMP_FRAMES=1 を最大 SEC 秒待つ (結果は LAUNCH_STATE)
# 呼び出し側が set -e / pipefail でも落ちないよう、失敗し得るパイプには || true を付けている。

PORTS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)"
. "$PORTS_ROOT/config/ports.env"
PORTS_STATE="${PORTS_STATE:-$PORTS_ROOT/logs/ports.env}"
PORTS_JS="${PORTS_JS:-$PORTS_ROOT/config/ports.local.js}"
ADVISOR_PATTERN="uvicorn server:app_asgi"
# フロントの配信 (index.html)。Cache-Control: no-store を付ける (2026-10-07: http.server ではブラウザがキャッシュの古い
# index.html を使った疑い)。起動は `python3 -m "$FRONTEND_MODULE" PORT`、判定 (port_state) と停止 (pkill) は FRONTEND_PATTERN。
# tools/control_panel.py もこの行を読む (パターンの定義はここだけ)
FRONTEND_MODULE="tools.frontend_server"
FRONTEND_PATTERN="$FRONTEND_MODULE"

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

# ---- 起動の確認 (2026-10-23): 今回起動したプロセスが待ち受け、DEBUG_DUMP_FRAMES=1 で動いているか
# 純粋: $1=起動した pid, $2=調べる pid, $3="pid ppid" の行の表 → $2 が $1 そのものかその子孫なら 0
# (nohup bash -c "... uvicorn ..." の pid と、待ち受ける python の pid は違うことがある)
pid_is_descendant() {
  local target="$1" pid="$2" table="$3" n=0
  [ -n "$target" ] && [ -n "$pid" ] || return 1
  while [ -n "$pid" ] && [ "$pid" != 0 ] && [ "$n" -lt 64 ]; do
    [ "$pid" = "$target" ] && return 0
    pid="$(printf '%s\n' "$table" | awk -v p="$pid" '$1 == p { print $2; exit }')"
    n=$((n + 1))
  done
  return 1
}

# 純粋: $1=起動した pid, $2=ポートで待ち受けている pid (空可), $3="pid ppid" の表, $4=待ち受けている pid の ps eww の出力
#   → ok (今回起動したプロセスが待ち受け、DEBUG_DUMP_FRAMES=1) / no_pid (起動した pid が分からない) / no_listener (まだ待ち受けていない) /
#     other_process (待ち受けているのは今回起動したプロセスではない) / no_dump (DEBUG_DUMP_FRAMES=1 が無い)
launch_state() {
  if [ -z "$1" ]; then echo no_pid; return 0; fi
  if [ -z "$2" ]; then echo no_listener; return 0; fi
  if ! pid_is_descendant "$1" "$2" "$3"; then echo other_process; return 0; fi
  if printf '%s\n' "$4" | grep -Eq '(^|[[:space:]])DEBUG_DUMP_FRAMES=1([[:space:]]|$)'; then echo ok; else echo no_dump; fi
}

# $1=起動した pid, $2=ポート, $3=最大秒数 → 1 秒ごとに確かめ、ok になれば 0。時間切れは 1。最後の判定は LAUNCH_STATE
advisor_launch_wait() {
  local i=0 lpid table envtxt
  LAUNCH_STATE=no_pid
  [ -n "$1" ] || return 1
  while [ "$i" -lt "${3:-30}" ]; do
    lpid="$(port_listener_pid "$2")"
    table="$(ps -A -o pid=,ppid= 2>/dev/null || true)"
    envtxt=""
    [ -n "$lpid" ] && envtxt="$(ps eww "$lpid" 2>/dev/null || true)"
    LAUNCH_STATE="$(launch_state "$1" "$lpid" "$table" "$envtxt")"
    [ "$LAUNCH_STATE" = ok ] && return 0
    sleep 1
    i=$((i + 1))
  done
  return 1
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

git_commit_js() {   # → 配信する作業ツリーの git commit (短縮) を JS の文字列で。git が無ければ null
  local c
  c="$(git -C "$PORTS_ROOT" rev-parse --short HEAD 2>/dev/null || true)"
  if [ -n "$c" ]; then printf '"%s"' "$c"; else printf 'null'; fi
}

save_ports() {   # $1=advisor $2=frontend
  mkdir -p "$(dirname "$PORTS_STATE")" "$(dirname "$PORTS_JS")"
  printf 'ADVISOR_PORT=%s\nFRONTEND_PORT=%s\n' "$1" "$2" > "$PORTS_STATE"
  # フロントが接続するアドバイザーのポートと、配信する版の git commit (ページが client_hello の served_commit で返す。2026-10-07)
  printf '// 起動スクリプトが生成する (コミットしない)。フロントが接続するアドバイザーのポートと配信する版の git commit\nwindow.ADVISOR_PORT = %s;\nwindow.FRONTEND_GIT_COMMIT = %s;\n' \
    "$1" "$(git_commit_js)" > "$PORTS_JS"
}
