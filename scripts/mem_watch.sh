#!/bin/bash
# 学習ジョブ (train_forever 配下のプロセス木) と Showdown の RSS を定期記録する。
#
#   bash scripts/mem_watch.sh <label> [duration_sec] [interval_sec]
#
# 出力: logs/mem_watch/<label>.tsv (追記)
#   epoch  swap_used_MB  pressure  group  pid  rss_kb  pcpu  cmd
# group: train=木の個々のプロセス / train_total=木の合計 / showdown_total
set -uo pipefail

LABEL="${1:?usage: mem_watch.sh <label> [duration_sec] [interval_sec]}"
DUR="${2:-3600}"
INT="${3:-20}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT_DIR="$ROOT/logs/mem_watch"
mkdir -p "$OUT_DIR"
OUT="$OUT_DIR/${LABEL}.tsv"

END=$((SECONDS + DUR))
printf "epoch\tswap_mb\tpressure\tgroup\tpid\trss_kb\tpcpu\tcmd\n" >> "$OUT"

while [ "$SECONDS" -lt "$END" ]; do
  EPOCH=$(date +%s)
  SWAP=$(sysctl -n vm.swapusage | awk '{gsub(/M/,"",$6); print $6}')
  PRES=$(sysctl -n kern.memorystatus_vm_pressure_level 2>/dev/null || echo -)
  ps axo pid,ppid,rss,pcpu,command | awk -v e="$EPOCH" -v s="$SWAP" -v p="$PRES" '
    NR>1 {
      pid[$1]=1; par[$1]=$2; rss[$1]=$3; cpu[$1]=$4;
      c=""; for(i=5;i<=NF&&i<=13;i++) c=c" "$i; cmd[$1]=c;
    }
    END {
      root=0;
      for (i in pid) if (cmd[i] ~ /train_forever\.sh/ && cmd[i] !~ / awk/) root=i;
      if (root) {
        tree[root]=1; changed=1;
        while (changed) {
          changed=0;
          for (i in pid) if (!(i in tree) && (par[i] in tree)) {tree[i]=1; changed=1}
        }
        tsum=0;
        for (i in tree) {
          tsum+=rss[i];
          if (rss[i] > 10240)
            printf "%s\t%s\t%s\ttrain\t%s\t%s\t%s\t%s\n",
                   e, s, p, i, rss[i], cpu[i], substr(cmd[i],2,70);
        }
        printf "%s\t%s\t%s\ttrain_total\t-\t%s\t-\t-\n", e, s, p, tsum;
      }
      ssum=0;
      for (i in pid)
        if (cmd[i] ~ /pokemon-showdown|team-validator|room-battle|sockets\.js|verifier\.js|friends\.js|artemis|battlesearch|datasearch/ && cmd[i] !~ / awk/)
          ssum+=rss[i];
      printf "%s\t%s\t%s\tshowdown_total\t-\t%s\t-\t-\n", e, s, p, ssum;
    }' >> "$OUT"
  sleep "$INT"
done
echo "[mem_watch] 完了: $OUT"
