"""途中保存が実環境で機能するかを短時間の学習で確認する。

    python -m tools.smoke_periodic_save [ステップ数] [保存間隔]

ユニットテストはSB3の保存の癖を模したスタブで検証しているが、
本番の学習経路そのものは通していない。長時間のスイープを回す前に、
実際に学習を走らせて「学習が終わる前に保存が現れるか」を確かめる。
最後の保存だけを見ても、元の実装 (最後にしか保存しない) と区別できない。
隔離したディレクトリへ保存するので本番のチェックポイントには触れない。
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path


def main() -> None:
    steps = int(sys.argv[1]) if len(sys.argv) > 1 else 3000
    every = int(sys.argv[2]) if len(sys.argv) > 2 else 1000

    out = Path(tempfile.mkdtemp(prefix="smoke_save_"))
    os.environ["CHAMPIONS_MODELS_DIR"] = str(out)
    os.environ["TRAIN_SAVE_EVERY"] = str(every)

    # 環境変数を読むのはimport時なので、設定してから読み込む
    from champions_agent.train import train_battle as tb
    print(f"[smoke] 保存先={out} 間隔={tb.SAVE_EVERY} ステップ={steps}")
    assert tb.SAVE_EVERY == every, "TRAIN_SAVE_EVERY が反映されていない"

    dest = out / "battle_policy_balance.zip"
    done = threading.Event()
    mid_run: list[float] = []

    def watch() -> None:
        """学習中 (終了前) にチェックポイントが現れた時刻を記録する"""
        while not done.is_set():
            if dest.exists() and not mid_run:
                mid_run.append(time.time())
            time.sleep(0.5)

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()

    t0 = time.time()
    try:
        tb.train(total_timesteps=steps, play_style="balance", n_envs=1)
    finally:
        done.set()
        watcher.join(timeout=2)
    end = time.time()

    if not dest.exists():
        print("[smoke] NG: チェックポイントが作られていない")
        raise SystemExit(1)
    if not mid_run:
        print("[smoke] NG: 学習中に保存が現れなかった (最後にしか保存していない)")
        raise SystemExit(1)

    print(f"[smoke] OK: 学習開始{mid_run[0] - t0:.0f}秒で途中保存を検出 "
          f"(学習全体は{end - t0:.0f}秒)")
    # pool/ 等は学習が正常に作るもの。見るのは保存の一時ファイルだけ
    leftovers = [p.name for p in out.iterdir() if ".tmp" in p.name]
    if leftovers:
        print(f"[smoke] NG: 一時ファイルが残っている: {leftovers}")
        raise SystemExit(1)
    print(f"[smoke] 一時ファイルの残骸なし / {dest.stat().st_size}バイト")


if __name__ == "__main__":
    main()
