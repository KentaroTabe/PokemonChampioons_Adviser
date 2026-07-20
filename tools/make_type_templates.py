"""実フレームからタイプアイコンのテンプレートを切り出して保存するツール。

選出画面のフレームと、6枠x2列のタイプ正解ラベルを与えると、
images/type_templates_real/{タイプ}_{連番}.png に保存する。
vision/typeicons.py はこのディレクトリを自動で読み込む。

使い方:
    python -m tools.make_type_templates <フレーム> "ノーマル,ひこう/むし,はがね/ほのお,かくとう/みず,フェアリー/こおり,フェアリー/ドラゴン,じめん"

ラベルは上の枠から順に「type1,type2」を「/」区切りで6枠分。タイプが1つの枠は「みず,」のように空にする。
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2

from vision import zones
from vision.zones import crop

OUT_DIR = Path(__file__).resolve().parent.parent / "images" / "type_templates_real"


def main():
    frame_path = sys.argv[1]
    labels = sys.argv[2]

    img = cv2.imread(frame_path)
    assert img is not None, f"cannot read {frame_path}"

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    rows = labels.split("/")
    saved = 0
    for i, row in enumerate(rows):
        if i >= len(zones.SELECTION_OPP):
            break
        parts = (row.split(",") + ["", ""])[:2]
        for key, label in zip(("type1", "type2"), parts):
            label = label.strip()
            if not label:
                continue
            c = crop(img, zones.SELECTION_OPP[i][key])
            if c is None or c.size == 0:
                continue
            n = 0
            while (OUT_DIR / f"{label}_{n}.png").exists():
                n += 1
            out = OUT_DIR / f"{label}_{n}.png"
            cv2.imwrite(str(out), c)
            print(f"row{i} {key} -> {out.name} ({c.shape[1]}x{c.shape[0]})")
            saved += 1
    print(f"saved {saved} templates to {OUT_DIR}")


if __name__ == "__main__":
    main()
