#!/bin/bash
# メモリ実測の一括スナップショット:
#   - システム全体 (スワップ・メモリ圧・圧縮メモリ)
#   - RSS上位プロセス
#   - 本プロジェクト関連プロセス (学習python / Showdown node / サーバー類) の内訳
#
#   bash scripts/mem_snapshot.sh
set -uo pipefail

echo "=== $(date '+%F %T') システム ==="
sysctl vm.swapusage
echo "メモリ圧レベル (1=normal 2=warn 4=critical): $(sysctl -n kern.memorystatus_vm_pressure_level 2>/dev/null || echo 取得不可)"
vm_stat | awk '
  /page size/    {ps=$8}
  /Pages free/   {free=$3}
  /Pages active/ {act=$3}
  /Pages inactive/ {inact=$3}
  /Pages wired/  {wired=$4}
  /occupied by compressor/ {comp=$5}
  END {
    gsub(/\./,"",free); gsub(/\./,"",act); gsub(/\./,"",inact);
    gsub(/\./,"",wired); gsub(/\./,"",comp);
    gb=ps/1073741824.0;
    printf "free=%.2fGB active=%.2fGB inactive=%.2fGB wired=%.2fGB compressor=%.2fGB\n",
      free*gb, act*gb, inact*gb, wired*gb, comp*gb
  }'

echo ""
echo "=== RSS上位15プロセス (全体) ==="
ps axo rss,pid,etime,pcpu,command | sort -rn | head -15 | \
  awk '{printf "%7.2fGB pid=%-6s et=%-11s cpu=%-5s ", $1/1048576, $2, $3, $4;
        for(i=5;i<=NF&&i<12;i++) printf "%s ", $i; print ""}'

echo ""
echo "=== プロジェクト関連の内訳 ==="
ps axo rss,pid,ppid,etime,command | \
  grep -E "smoke_train|train_nightly|train_forever|champions_agent|caffeinate|pokemon-showdown|uvicorn|next dev|auto_tune" | \
  grep -v grep | sort -rn | \
  awk '{printf "%7.2fGB pid=%-6s ppid=%-6s et=%-11s ", $1/1048576, $2, $3, $4;
        for(i=5;i<=NF&&i<13;i++) printf "%s ", $i; print ""}'

echo ""
ps axo rss,command | grep -E "smoke_train|caffeinate" | grep -v grep | \
  awk '{s+=$1} END {printf "学習python系の合計RSS: %.2fGB\n", s/1048576}'
ps axo rss,command | grep -E "pokemon-showdown|team-validator|room-battle|sockets\.js|verifier\.js|friends\.js|artemis|battlesearch|datasearch" | grep -v grep | \
  awk '{s+=$1} END {printf "Showdown node系の合計RSS: %.2fGB\n", s/1048576}'
