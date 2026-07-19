"""アクションマスクの動作確認ツール。

環境を1つ作ってreset直後のマスクを検査する:
- Zワザ/ダイマックス/テラスタル枠 (14-25) が常に無効であること
- 技/交代の合法手が妥当な数であること

    python -m tools.check_action_mask
"""
from __future__ import annotations

import signal
import sys

import numpy as np


def main() -> None:
    signal.signal(signal.SIGALRM,
                  lambda s, f: (print("[check_action_mask] TIMEOUT"), sys.exit(1)))
    signal.alarm(120)

    from champions_agent.env.showdown_env import make_training_env

    env = make_training_env()
    obs, _info = env.reset()
    mask = env.action_masks()
    env.close()

    print(f"mask: {mask.astype(int).tolist()}")
    n_switch = int(mask[:6].sum())
    n_move = int(mask[6:10].sum())
    n_mega = int(mask[10:14].sum())
    n_gimmick = int(mask[14:].sum())
    print(f"交代={n_switch} 技={n_move} メガ={n_mega} 禁止ギミック={n_gimmick}")

    assert mask.shape == (26,)
    assert n_gimmick == 0, "Z/ダイマ/テラス枠が有効になっています"
    assert 1 <= n_move <= 4, f"技の合法手が異常: {n_move}"
    assert n_switch <= 5
    print("check_action_mask OK")


if __name__ == "__main__":
    main()
