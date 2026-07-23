"""リアルタイム抽出監査モニター。

logs/battles/ を監視し、対戦ログの書き込みが止まったら (=対戦終了とみなし)
tools.audit_subtask で sonnet 監査を自動実行してレポートを生成する。

    python -m tools.audit_monitor                 # フォアグラウンド常駐 (Ctrl+Cで停止)
    python -m tools.audit_monitor --backfill 2    # 起動時に直近2対戦も監査
    python -m tools.audit_monitor --max-frames 10 # 1対戦あたりのフレーム上限

- 起動前から存在する対戦は既定でスキップ (起動後に終わった対戦のみ監査)
- レポート (logs/audit_reports/<battle名>.md) が既にある対戦は再監査しない
"""
from __future__ import annotations

import argparse
import glob
import time
from pathlib import Path

from tools.audit_subtask import REPORT_DIR, run

REPO = Path(__file__).resolve().parent.parent
BATTLE_DIR = REPO / "logs" / "battles"
POLL_S = 30       # 監視間隔
QUIET_S = 120     # ログ更新がこの秒数止まったら対戦終了とみなす


def _has_report(log: Path) -> bool:
    rep = REPORT_DIR / (log.stem + ".md")
    return rep.exists() and rep.stat().st_mtime >= log.stat().st_mtime


def _quiescent_logs() -> list:
    now = time.time()
    out = []
    for p in sorted(glob.glob(str(BATTLE_DIR / "*.jsonl"))):
        log = Path(p)
        if now - log.stat().st_mtime >= QUIET_S:
            out.append(log)
    return out


def monitor(max_frames: int, timeout: int, backfill: int) -> None:
    done = set()
    for log in _quiescent_logs():
        if _has_report(log):
            done.add(log.name)
    unaudited = [lg for lg in _quiescent_logs() if lg.name not in done]
    for log in (unaudited[:-backfill] if backfill else unaudited):
        done.add(log.name)  # 起動前の対戦は backfill 指定分を除きスキップ
    print(f"[audit_monitor] 監視開始: {BATTLE_DIR} "
          f"(poll={POLL_S}s quiet={QUIET_S}s 既存スキップ{len(done)}件)",
          flush=True)
    while True:
        for log in _quiescent_logs():
            if log.name in done:
                continue
            done.add(log.name)
            print(f"[audit_monitor] 対戦終了を検知 → 監査: {log.name}",
                  flush=True)
            try:
                run(str(log), max_frames, timeout)
            except SystemExit as e:  # ペアなし/claude失敗は記録して継続
                print(f"[audit_monitor] スキップ: {e}", flush=True)
            except Exception as e:
                print(f"[audit_monitor] 監査失敗 ({log.name}): {e}", flush=True)
        time.sleep(POLL_S)


def main() -> None:
    ap = argparse.ArgumentParser(description="リアルタイム抽出監査モニター")
    ap.add_argument("--max-frames", type=int, default=20,
                    help="1対戦あたり監査するフレーム数の上限")
    ap.add_argument("--timeout", type=int, default=1200)
    ap.add_argument("--backfill", type=int, default=0,
                    help="起動時に直近N対戦 (未監査分) も監査する")
    args = ap.parse_args()
    try:
        monitor(args.max_frames, args.timeout, args.backfill)
    except KeyboardInterrupt:
        print("\n[audit_monitor] 停止")


if __name__ == "__main__":
    main()
