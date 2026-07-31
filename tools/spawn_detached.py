"""コマンドを新しいセッションに切り離して起動する。

    python -m tools.spawn_detached [--cwd DIR] <ログのパス> <コマンド...>
    -> 起動したPIDを標準出力に1行で返す

macOS には setsid が無い (util-linux のコマンド)。2026-07-31、これを知らずに
setsid を使った起動スクリプトを検証せずに入れ、比較の起動が丸ごと失敗した。
Python の start_new_session で同じこと (os.setsid) を移植性のある形で行う。

呼び出し元のシェルが終了しても、プロセスグループの巻き添えで落ちない。
ただし OS の再起動 (自動アップデート等) はこれでも防げないため、
長時間の学習は途中保存に頼る (train_battle の TRAIN_SAVE_EVERY)。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> None:
    argv = sys.argv[1:]
    cwd = None
    if argv[:1] == ["--cwd"]:
        cwd = argv[1]
        argv = argv[2:]
    if len(argv) < 2:
        print(__doc__)
        raise SystemExit(2)
    log = Path(argv[0])
    cmd = argv[1:]

    log.parent.mkdir(parents=True, exist_ok=True)
    # ログのファイルハンドルは子プロセスへ引き継がれる
    handle = log.open("w")
    proc = subprocess.Popen(
        cmd, cwd=cwd,
        stdout=handle, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        start_new_session=True, close_fds=True,
    )
    print(proc.pid)


if __name__ == "__main__":
    main()
