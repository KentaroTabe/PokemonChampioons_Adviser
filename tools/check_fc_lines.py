"""場の状況画面のOCR行ダンプ (extract_field_check のデバッグ用)。

    python -m tools.check_fc_lines <frame...>
"""
from __future__ import annotations

import sys

import cv2

from vision import ocr


def main() -> None:
    for path in sys.argv[1:]:
        img = cv2.imread(path)
        if img is None:
            print(f"{path}: 読み込み失敗")
            continue
        lines = ocr.apple_ocr_lines(img, scale=1.0)
        print(f"=== {path}: {len(lines)}行")
        for t, (x0, y0, x1, y1) in lines:
            print(f"  ({x0:.2f},{y0:.2f})-({x1:.2f},{y1:.2f}) {t!r}")


if __name__ == "__main__":
    main()
