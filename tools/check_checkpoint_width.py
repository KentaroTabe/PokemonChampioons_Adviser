"""チェックポイントのネット幅と観測次元を表示する。

ネット幅を変更した直後の確認用。train_battle は希望幅と違う
チェックポイントを「非互換」として退避し新規学習を始めるため、
学習を再開する前にここで一致を確かめる。

    python -m tools.check_checkpoint_width
    python -m tools.check_checkpoint_width --want 512
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from champions_agent.config import MODELS_DIR


def width_of(path: Path):
    """(ネット幅, 観測次元)。読めなければ (None, None)"""
    from sb3_contrib import MaskablePPO
    try:
        model = MaskablePPO.load(str(path), device="cpu")
    except Exception:
        return None, None
    try:
        w = model.policy.mlp_extractor.policy_net[0].out_features
    except Exception:
        w = None
    try:
        obs = int(model.observation_space.shape[0])
    except Exception:
        obs = None
    return w, obs


def main() -> None:
    ap = argparse.ArgumentParser(description="チェックポイントのネット幅確認")
    ap.add_argument("--want", type=int,
                    default=int(os.environ.get("TRAIN_NET_WIDTH", "512")),
                    help="期待するネット幅 (既定: TRAIN_NET_WIDTH)")
    args = ap.parse_args()

    print(f"期待するネット幅: {args.want} (不一致だと学習再開時に破棄される)")
    ng = 0
    for path in sorted(MODELS_DIR.glob("battle_policy_*.zip")):
        w, obs = width_of(path)
        mark = "OK" if w == args.want else "⚠不一致"
        if w != args.want and not path.name.endswith("_best.zip"):
            ng += 1
        print(f"  {path.name}: 幅={w} 観測={obs} {mark}")
    print("\n※ _best は配布用スナップショットなので幅が違っても問題ない "
          "(BattlePolicyは保存時の構成で読む)")
    raise SystemExit(1 if ng else 0)


if __name__ == "__main__":
    main()
