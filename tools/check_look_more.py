"""もっと見る画面の読取診断: タブ/対象/実数値/能力ポイント/性格のOCR生値を表示。

    python -m tools.check_look_more <frame...>
"""
from __future__ import annotations

import sys

import cv2

from vision import ocr, zones
from vision.extractors import (
    _STAT_KEYS, _highlight_species, _watch_active_tab,
)
from vision.normalize import NameResolver


def main() -> None:
    resolver = NameResolver()
    for path in sys.argv[1:]:
        img = cv2.imread(path)
        if img is None:
            print(f"{path}: 読み込み失敗")
            continue
        tab = _watch_active_tab(img)
        hl = _highlight_species(img, resolver)
        print(f"=== {path}: tab={tab} highlight={hl}")
        if tab != "status":
            continue
        for key, z in zip(_STAT_KEYS, zones.WATCH_STATS):
            v = ocr.read_zone_text(img, z["value"], mode="panel",
                                   allowlist="0123456789")
            p = ocr.read_zone_text(img, z["points"], mode="panel",
                                   allowlist="0123456789")
            print(f"  {key}: 実数値={v!r} ポイント={p!r}")
        nt = ocr.read_zone_text(img, zones.WATCH["nature_value"], mode="panel")
        print(f"  性格テキスト={nt!r}")


if __name__ == "__main__":
    main()
