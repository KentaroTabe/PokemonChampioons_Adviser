#!/bin/bash
# mem_watch.sh が記録した TSV を集計する。
#
#   bash scripts/mem_watch_summary.sh logs/mem_watch/baseline.tsv [開始epoch] [終了epoch]
#
# 出力: 期間、train_total / showdown_total の最初・平均・最大・最後 (GB)、
#       スワップの推移、プロセス別の増加量上位。
set -uo pipefail

TSV="${1:?usage: mem_watch_summary.sh <tsv> [from_epoch] [to_epoch]}"
FROM="${2:-0}"
TO="${3:-9999999999}"

awk -F'\t' -v from="$FROM" -v to="$TO" '
  NR==1 {next}
  $1 < from || $1 > to {next}
  {
    if (first_t == 0) first_t = $1
    last_t = $1
    swap_last = $2; if (swap_first == "") swap_first = $2
    pres[$3]++
  }
  $4 == "train_total" {
    v = $6 / 1048576.0
    n_tr++; sum_tr += v
    if (v > max_tr) {max_tr = v; max_tr_t = $1}
    if (first_tr == "") first_tr = v
    last_tr = v
  }
  $4 == "showdown_total" {
    v = $6 / 1048576.0
    n_sd++; sum_sd += v
    if (v > max_sd) max_sd = v
    if (first_sd == "") first_sd = v
    last_sd = v
  }
  $4 == "train" {
    pid = $5
    if (!(pid in p_first)) {p_first[pid] = $6; p_first_t[pid] = $1; p_cmd[pid] = $8}
    p_last[pid] = $6; p_last_t[pid] = $1
    if ($6 > p_max[pid]) p_max[pid] = $6
  }
  END {
    if (n_tr == 0) {print "対象期間にデータがありません"; exit 1}
    dur = (last_t - first_t) / 60.0
    printf "期間: %.1f分 (サンプル%d件)  スワップ: %.1f→%.1fGB\n",
           dur, n_tr, swap_first/1024, swap_last/1024
    printf "学習プロセス木 合計RSS: 開始%.2f → 平均%.2f / 最大%.2f / 最後%.2fGB\n",
           first_tr, sum_tr/n_tr, max_tr, last_tr
    printf "Showdown node 合計RSS:  開始%.2f → 平均%.2f / 最大%.2f / 最後%.2fGB\n",
           first_sd, sum_sd/n_sd, max_sd, last_sd
    print "--- プロセス別 (観測10分以上) ---"
    for (pid in p_first) {
      mins = (p_last_t[pid] - p_first_t[pid]) / 60.0
      if (mins < 10) continue
      growth = (p_last[pid] - p_first[pid]) / 1024.0
      printf "%8.1fMB増 (%.0f分, %.0f→%.0fMB, 最大%.0fMB) pid=%s %s\n",
             growth, mins, p_first[pid]/1024, p_last[pid]/1024,
             p_max[pid]/1024, pid, substr(p_cmd[pid], 1, 46)
    }
  }' "$TSV"
