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


def _make_strip_img(hue, sat, val):
    """単色のHSVからBGR画像を作る (合成ストリップ)"""
    import numpy as np
    hsv = np.full((40, 20, 3), (hue, sat, val), dtype=np.uint8)
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)


FULL_ZONE = {"x0": 0.1, "y0": 0.0, "x1": 0.9, "y1": 1.0}


def test_cursor_lime_strip_is_unknown():
    """白リボンが読めずライム優勢のストリップは None (判定不能) を返す。

    2026-08-18 欠陥#8: カーソル行で選出済みが巻き戻る一因。
    """
    lime = _make_strip_img(40, 200, 230)
    assert _is_picked_panel(lime, FULL_ZONE) is None
    purple = _make_strip_img(123, 122, 236)   # 実測の通常行HSV平均
    assert _is_picked_panel(purple, FULL_ZONE) is False
    white = _make_strip_img(0, 10, 240)
    assert _is_picked_panel(white, FULL_ZONE) is True
    print("test_cursor_lime_strip_is_unknown OK")


def test_cursor_row_real_frame_and_scene():
    """実フレーム: カーソル行でも picked が壊れず、シーンは selection。

    sel_1787037329.png = row0(サザンドラ,選出2番目)にカーソル (白光縁あり)、
    row2(ミミッキュ)が白リボン表示、2/3選出済み。
    修正前はこのフレームが scene=field に誤分類されていた (欠陥#9)。
    """
    files = glob.glob("debug_frames/sel_1787037329.png")
    if not files:
        print("test_cursor_row_real_frame_and_scene SKIP (フレーム無し)")
        return
    img = cv2.imread(files[0])

    # 白リボン行は True、通常行は False。カーソル行は True か None
    # (白光縁の残り方で揺れるため False にだけはならないこと)
    assert _is_picked_panel(img, zones.SELECTION_MY[2]["panel"]) is True
    assert _is_picked_panel(img, zones.SELECTION_MY[1]["panel"]) is False
    assert _is_picked_panel(img, zones.SELECTION_MY[0]["panel"]) is not False

    # 前フレームで選出済みだった row0 は維持される
    state = BattleStateV2()
    from vision.state import PokemonState
    for ja in ("サザンドラ", "ライチュウ", "ミミッキュ",
               "ペロリーム", "ムクホーク", "キラフロル"):
        state.player.party.append(PokemonState(species_ja=ja))
    state.player.party[0].is_picked = True
    state.player.party[0].pick_order = 2
    extract_selection(img, state, NameResolver())
    assert state.player.party[0].is_picked is True, "カーソル行で巻き戻った"
    assert state.player.party[2].is_picked is True
    assert state.player.party[1].is_picked is False

    # このフレームがシーン分類で selection になる (欠陥#9の回帰)
    from vision.scenes import SCENE_SELECTION, classify
    assert classify(img)["scene"] == SCENE_SELECTION, classify(img)
    print("test_cursor_row_real_frame_and_scene OK")


def test_unpick_needs_consecutive_false():
    """選出解除は連続Falseでのみ確定する (1フレームの読み損ねで巻き戻らない)"""
    from vision import extractors as ex
    from vision.state import PokemonState

    state = BattleStateV2()
    for ja in ("サザンドラ", "ライチュウ", "ミミッキュ",
               "ペロリーム", "ムクホーク", "キラフロル"):
        state.player.party.append(PokemonState(species_ja=ja))
    state.player.party[0].is_picked = True
    state.player.party[0].pick_order = 1

    import numpy as np
    black = np.zeros((720, 1280, 3), dtype=np.uint8)
    orig_panel, orig_read = ex._is_picked_panel, ex.ocr.read_zone_text
    ex._is_picked_panel = lambda img, z: False   # 全行False (読み損ね相当)
    ex.ocr.read_zone_text = lambda *a, **k: ""
    try:
        ex.extract_selection(black, state, NameResolver())
        assert state.player.party[0].is_picked is True, "1回のFalseで巻き戻った"
        ex.extract_selection(black, state, NameResolver())
        assert state.player.party[0].is_picked is False, "2回連続Falseで解除されるべき"
    finally:
        ex._is_picked_panel = orig_panel
        ex.ocr.read_zone_text = orig_read
    print("test_unpick_needs_consecutive_false OK")


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
    test_cursor_lime_strip_is_unknown()
    test_cursor_row_real_frame_and_scene()
    test_unpick_needs_consecutive_false()
    test_pick_order_tracking()
    print("ALL OK")
