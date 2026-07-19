"""フィールドシーン中の自分側HP読取のデバッグ。

使い方: python -m tools.check_field_my_hp <frame.png> [...]
各フレームの my_hp_bar 画素数 / my_hp_text OCR結果 / parse_fraction を表示する。
"""
import sys

import cv2

from vision import ocr, zones
from vision.scenes import _hp_bar_pixels
from vision.zones import crop


def main(paths):
    for path in paths:
        img = cv2.imread(path)
        if img is None:
            print(f"{path}: 読み込み失敗")
            continue
        bar_px = _hp_bar_pixels(crop(img, zones.BATTLE["my_hp_bar"]))
        text = ocr.read_zone_text(img, zones.BATTLE["my_hp_text"], mode="panel",
                                  allowlist="0123456789/")
        frac = ocr.parse_fraction(text)
        print(f"{path.split('/')[-1]}: bar_px={bar_px} text={text!r} frac={frac}")


if __name__ == "__main__":
    main(sys.argv[1:])
