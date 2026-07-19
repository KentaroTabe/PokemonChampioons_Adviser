"""不要ログの自動掃除。サーバー起動 (更新反映) のタイミングで実行される。

- logs/battles/: 断片ログ (対戦シーンを含まない/レコードが少なく勝敗もない)
  を削除。書き込み中ファイルを消さないよう、更新から10分以上経過したもののみ
- debug_frames/: 直近 KEEP_FRAMES 枚のみ残す (1枚≒3MBで無制限に増えるため)
- debug_frames_720_old/: 720p時代の旧デバッグ出力。存在すれば丸ごと削除

使い方:
    python -m tools.cleanup_logs            # 実行
    python -m tools.cleanup_logs --dry-run  # 削除対象の表示のみ
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BATTLE_DIR = ROOT / "logs" / "battles"
FRAME_DIR = ROOT / "debug_frames"
LEGACY_FRAME_DIR = ROOT / "debug_frames_720_old"

KEEP_FRAMES = 200
MIN_AGE_SEC = 600          # 更新から10分未満のファイルには触らない
MIN_RECORDS = 20           # これ未満で勝敗も対戦シーンもなければ断片とみなす


def _is_fragment(path: Path) -> bool:
    try:
        records = [json.loads(l) for l in path.open(encoding="utf-8")]
    except Exception:
        return False   # 壊れたファイルは判断できないので残す
    has_outcome = any(r.get("type") == "outcome" and r.get("outcome") in ("win", "loss")
                      for r in records)
    if has_outcome:
        return False
    has_battle = any(r.get("type") == "scene" and r.get("scene") in ("command", "move_select")
                     for r in records)
    return not (has_battle and len(records) >= MIN_RECORDS)


def cleanup(dry_run: bool = False) -> None:
    now = time.time()
    removed_logs = 0
    if BATTLE_DIR.exists():
        for f in sorted(BATTLE_DIR.glob("*.jsonl")):
            if now - f.stat().st_mtime < MIN_AGE_SEC:
                continue
            if _is_fragment(f):
                print(f"[cleanup] 断片ログ削除: {f.name}")
                if not dry_run:
                    f.unlink()
                removed_logs += 1

    removed_frames = 0
    if FRAME_DIR.exists():
        frames = sorted(FRAME_DIR.glob("*.png"), key=lambda p: p.stat().st_mtime)
        for f in frames[:-KEEP_FRAMES]:
            if not dry_run:
                f.unlink()
            removed_frames += 1

    if LEGACY_FRAME_DIR.exists():
        print(f"[cleanup] 旧デバッグ出力削除: {LEGACY_FRAME_DIR.name}/")
        if not dry_run:
            shutil.rmtree(LEGACY_FRAME_DIR)

    print(f"[cleanup] 断片ログ {removed_logs}件 / 古いフレーム {removed_frames}枚"
          f"{' (dry-run)' if dry_run else ''}")


if __name__ == "__main__":
    cleanup(dry_run="--dry-run" in sys.argv)
