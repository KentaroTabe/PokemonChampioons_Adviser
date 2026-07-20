"""実フレームで選出アドバイザーのE2E確認をするツール。

    python -m tools.check_selection_frame <選出画面フレーム>
"""
from __future__ import annotations

import sys

import cv2

from vision.pipeline import VisionPipeline
from advisor.service import Advisor


def main() -> None:
    path = sys.argv[1]
    img = cv2.imread(path)
    assert img is not None, f"cannot read {path}"

    pipe = VisionPipeline()
    state, _ = pipe.process(img, single_shot=True)
    print(f"scene={state['scene']} selection_picked={state['selection_picked']}")
    for i, p in enumerate(state["player"]["party"]):
        if p["species_ja"]:
            mark = "✔" if p.get("is_picked") else " "
            print(f"  [{mark}] {p['species_ja']} @{p['item_ja'] or '?'}")

    advisor = Advisor(resolver=pipe.resolver)
    advice = advisor.advise_selection(state)
    print("\n" + advice["text"])


if __name__ == "__main__":
    main()
