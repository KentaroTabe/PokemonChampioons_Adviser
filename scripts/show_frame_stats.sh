#!/bin/bash
# 直近のフレーム受信統計 (受信/処理/破棄) を表示する。
#
#   bash scripts/show_frame_stats.sh
#
# サーバーは5秒毎に「受信= 処理= 破棄=」をログへ出す。取りこぼし率は
# アドバイスの反映速度とメッセージ検出率に直結するため、接続テスト後に
# 前回と比較する (2026-07-27の改善前は62%破棄)。
cd "$(dirname "$0")/.." || exit 1

LINE=$(grep -oE "受信=[0-9]+ 処理=[0-9]+ 破棄=[0-9]+" logs/server_nohup.log 2>/dev/null | tail -1)
if [ -z "$LINE" ]; then
  echo "フレーム統計: ログに記録がありません (logs/server_nohup.log)"
  exit 0
fi

RECV=$(echo "$LINE" | sed -E 's/受信=([0-9]+).*/\1/')
PROC=$(echo "$LINE" | sed -E 's/.*処理=([0-9]+).*/\1/')
DROP=$(echo "$LINE" | sed -E 's/.*破棄=([0-9]+)/\1/')

echo ""
echo "=== フレーム受信統計 ==="
echo "$LINE"
if [ "$RECV" -gt 0 ]; then
  awk -v r="$RECV" -v p="$PROC" -v d="$DROP" 'BEGIN {
    printf "取りこぼし率: %.0f%% (処理率 %.0f%%)\n", d * 100 / r, p * 100 / r
    printf "実効処理レート: 約%.1f fps (送信は10fps)\n", p / (r / 10.0)
  }'
  echo "※ 2026-07-27の改善前は62%破棄・3.8fps。これを下回れば改善している"
fi
