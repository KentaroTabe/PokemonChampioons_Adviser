#!/usr/bin/env bash
# 操作パネル (tools/control_panel) を launchd に登録・解除し、稼働を実測する。
#   bash scripts/control_panel_install.sh install     # 登録 (ログイン時に自動起動、落ちても復帰)。plist を変えたときも install で入れ直す
#   bash scripts/control_panel_install.sh uninstall   # 解除 (パネルを止める)
#   bash scripts/control_panel_install.sh status      # 実測だけ
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
. scripts/lib/ports.sh
LABEL=com.championsadviser.control-panel
SRC="scripts/$LABEL.plist"
DST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"

case "${1:-status}" in
  install)
    mkdir -p "$(dirname "$DST")" logs/control_panel
    cp "$SRC" "$DST"
    if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
      launchctl bootout "$DOMAIN/$LABEL" && echo "[control_panel] 旧登録を外しました"
      sleep 1
    fi
    launchctl bootstrap "$DOMAIN" "$DST" \
      && echo "[control_panel] launchd に登録しました ($DST)" \
      || { echo "[control_panel] 登録に失敗しました"; exit 1; }
    sleep 2
    ;;
  uninstall)
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null \
      && echo "[control_panel] launchd から外しました" \
      || echo "[control_panel] 登録されていませんでした"
    rm -f "$DST"
    sleep 1
    ;;
  status) ;;
  *) echo "使い方: $0 install|uninstall|status"; exit 2 ;;
esac

echo "=== 実測 ==="
if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
  echo "launchd: 登録あり ($LABEL)"
else
  echo "launchd: 登録なし"
fi
PID="$(lsof -nP -iTCP:"$CONTROL_PORT_DEFAULT" -sTCP:LISTEN -t 2>/dev/null | head -n 1)"
if [ -n "$PID" ]; then
  IP=$(ipconfig getifaddr en0 2>/dev/null || echo localhost)
  echo "操作パネル($CONTROL_PORT_DEFAULT): 稼働中 (pid $PID)"
  echo "  このMac: http://localhost:$CONTROL_PORT_DEFAULT/   LAN: http://$IP:$CONTROL_PORT_DEFAULT/"
else
  echo "操作パネル($CONTROL_PORT_DEFAULT): 停止 (ログ: logs/control_panel.log)"
fi
