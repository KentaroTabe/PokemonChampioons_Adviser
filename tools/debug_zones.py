"""ゾーン定義を画像に描画して確認するデバッグツール。

    python -m tools.debug_zones <画像> <出力先> [scene]

scene: battle / selection / watch / message / all (省略時 all)
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2

from vision import zones


def draw_zone(img, zone, label, color=(0, 255, 0)):
    h, w = img.shape[:2]
    p1 = (int(zone["x0"] * w), int(zone["y0"] * h))
    p2 = (int(zone["x1"] * w), int(zone["y1"] * h))
    cv2.rectangle(img, p1, p2, color, 2)
    cv2.putText(img, label, (p1[0], max(12, p1[1] - 4)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)


def main():
    img_path = sys.argv[1]
    out_path = sys.argv[2]
    scene = sys.argv[3] if len(sys.argv) > 3 else "all"

    img = cv2.imread(img_path)
    assert img is not None, f"cannot read {img_path}"

    if scene in ("battle", "all"):
        for name, z in zones.BATTLE.items():
            draw_zone(img, z, name, (0, 255, 0))
        for i, row in enumerate(zones.MOVE_ROWS):
            for key, z in row.items():
                draw_zone(img, z, f"m{i}_{key}", (0, 200, 255))
    if scene in ("selection", "all"):
        for name, z in zones.SELECTION.items():
            draw_zone(img, z, name, (255, 0, 0))
        for i, row in enumerate(zones.SELECTION_MY):
            draw_zone(img, row["name"], f"my{i}n", (255, 255, 0))
            draw_zone(img, row["item"], f"my{i}i", (255, 200, 0))
        for i, row in enumerate(zones.SELECTION_OPP):
            draw_zone(img, row["type1"], f"o{i}t1", (0, 0, 255))
            draw_zone(img, row["type2"], f"o{i}t2", (0, 100, 255))
    if scene in ("watch", "all"):
        for name, z in zones.WATCH.items():
            draw_zone(img, z, name, (255, 0, 255))
        for i, row in enumerate(zones.WATCH_MOVES):
            draw_zone(img, row["name"], f"wm{i}", (200, 0, 200))
            draw_zone(img, row["pp"], f"wp{i}", (200, 100, 200))
        for i, row in enumerate(zones.WATCH_MY):
            draw_zone(img, row["name"], f"wl{i}n", (255, 100, 100))
            draw_zone(img, row["hp"], f"wl{i}h", (255, 150, 100))
        for i, row in enumerate(zones.WATCH_OPP):
            draw_zone(img, row["hp_text"], f"wo{i}", (100, 100, 255))
    if scene in ("message", "all"):
        for name, z in zones.MESSAGE.items():
            draw_zone(img, z, name, (0, 255, 255))

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(out_path, img)
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
