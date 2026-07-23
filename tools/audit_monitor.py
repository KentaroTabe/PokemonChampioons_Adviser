"""リアルタイム抽出監査モニター。

logs/battles/ を監視し、対戦中レコード (events/hp/対戦シーン) の書き込みが
30秒止まったら対戦終了とみなして tools.audit_subtask の sonnet 監査を
自動実行し、レポートを生成する。判定はレコード内容ベースのため、レート・
選出・メニュー等の対戦外の書き込みが続いていても対戦終了と判定できる。

    python -m tools.audit_monitor                 # フォアグラウンド常駐 (Ctrl+Cで停止)
    python -m tools.audit_monitor --backfill 2    # 起動時に直近2対戦も監査
    python -m tools.audit_monitor --max-frames 10 # 1対戦あたりのフレーム上限

- 起動前から存在する対戦は既定でスキップ (起動後に終わった対戦のみ監査)
- 監査後に同じログへ対戦中レコードが追記された場合 (早期判定だった場合) は
  静止後に再監査してレポートを上書きする
"""
from __future__ import annotations

import argparse
import glob
import json
import time
from pathlib import Path

from tools.audit_subtask import REPORT_DIR, run

REPO = Path(__file__).resolve().parent.parent
BATTLE_DIR = REPO / "logs" / "battles"
POLL_S = 10       # 監視間隔
QUIET_S = 30      # 対戦中レコードがこの秒数途絶えたら対戦終了とみなす

# 対戦中とみなすシーン (vision/scenes.py)。selection/standby/unknown は対戦外
BATTLE_SCENES = {"command", "move_select", "watch",
                 "field_check", "battle_hud", "field"}


def _has_report(log: Path) -> bool:
    return (REPORT_DIR / (log.stem + ".md")).exists()


def _last_battle_t(log: Path) -> float | None:
    """ログ中の最後の対戦中レコードの時刻 (無ければ None)"""
    t = None
    for line in open(log):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        typ = d.get("type")
        if typ in ("events", "hp") or \
                (typ == "scene" and d.get("scene") in BATTLE_SCENES):
            t = d.get("t", t)
    return t


def monitor(max_frames: int, timeout: int, backfill: int) -> None:
    audited: dict[str, float] = {}   # ログ名 → 監査済みの最終対戦活動時刻
    cache: dict[str, tuple] = {}     # ログ名 → (mtime, 最終対戦活動時刻)

    def t_act(log: Path) -> float:
        m = log.stat().st_mtime
        c = cache.get(log.name)
        if c and c[0] == m:          # 追記が無ければ再パースしない
            return c[1]
        v = _last_battle_t(log) or m
        cache[log.name] = (m, v)
        return v

    logs = [Path(p) for p in sorted(glob.glob(str(BATTLE_DIR / "*.jsonl")))]
    fresh = [lg for lg in logs if not _has_report(lg)]
    keep = set(lg.name for lg in fresh[-backfill:]) if backfill else set()
    for lg in logs:
        if lg.name not in keep:      # 起動前の対戦は backfill 分を除きスキップ
            audited[lg.name] = t_act(lg)
    print(f"[audit_monitor] 監視開始: {BATTLE_DIR} "
          f"(poll={POLL_S}s quiet={QUIET_S}s 既存スキップ{len(audited)}件)",
          flush=True)
    while True:
        for p in sorted(glob.glob(str(BATTLE_DIR / "*.jsonl"))):
            log = Path(p)
            ta = t_act(log)
            if audited.get(log.name, -1.0) >= ta:
                continue
            if time.time() - ta < QUIET_S:
                continue
            print(f"[audit_monitor] 対戦終了を検知 → 監査: {log.name}",
                  flush=True)
            try:
                run(str(log), max_frames, timeout)
            except SystemExit as e:  # ペアなし/claude失敗は記録して継続
                print(f"[audit_monitor] スキップ: {e}", flush=True)
            except Exception as e:
                print(f"[audit_monitor] 監査失敗 ({log.name}): {e}", flush=True)
            audited[log.name] = ta
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
