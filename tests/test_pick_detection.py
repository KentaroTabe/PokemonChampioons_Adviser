"""自選出検出 (白リボン方式) の実フレーム検証。

使い方: python -m tests.test_pick_detection
sel_*.png が debug_frames にない場合はスキップする。
"""
import glob

import cv2

from vision.extractors import extract_selection, _is_picked_panel
from vision import zones
from vision.normalize import NameResolver
from vision.state import BattleStateV2

CASES = [
    # (フレーム, 期待される選出済みスロット)
    ("debug_frames/sel_1784518310.png", {0, 1}),
    ("debug_frames/sel_1784518319.png", {0, 1, 3}),
    ("debug_frames/sel_1784518283.png", set()),
]


def test_ribbon_detection():
    ran = 0
    for path, expected in CASES:
        files = glob.glob(path)
        if not files:
            continue
        img = cv2.imread(files[0])
        if img is None:
            continue
        got = {i for i, z in enumerate(zones.SELECTION_MY)
               if _is_picked_panel(img, z["panel"])}
        assert got == expected, f"{path}: {got} != {expected}"
        ran += 1
    if ran == 0:
        print("test_ribbon_detection SKIP (sel_*.png が掃除済み)")
        return
    print(f"test_ribbon_detection OK ({ran}フレーム)")


def test_pick_order_tracking():
    # 0/3 -> 2/3 -> 3/3 の順に流し、選出順が1,2,3と付くことを確認
    seq = ["debug_frames/sel_1784518283.png",
           "debug_frames/sel_1784518310.png",
           "debug_frames/sel_1784518319.png"]
    if not all(glob.glob(f) for f in seq):
        print("test_pick_order_tracking SKIP (フレーム不足)")
        return
    state = BattleStateV2()
    r = NameResolver()
    for f in seq:
        img = cv2.imread(f)
        if img is not None:
            extract_selection(img, state, r)
    picked = [(i, p.species_ja, p.pick_order)
              for i, p in enumerate(state.player.party) if p.is_picked]
    assert len(picked) == 3, picked
    orders = sorted(o for _, _, o in picked if o)
    assert orders == [1, 2, 3], picked
    # slot3 (ブリジュラス) が最後に選ばれたので3番
    assert [o for i, _, o in picked if i == 3] == [3], picked
    print(f"test_pick_order_tracking OK: {picked}")


if __name__ == "__main__":
    test_ribbon_detection()
    test_pick_order_tracking()
    print("ALL OK")
