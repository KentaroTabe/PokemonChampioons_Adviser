"""種族アイコンの自動収穫 + 実キャプチャ照合のテスト。

    scripts/run_test.sh test_species_harvest

- 手動確定時の収穫 (前景なし拒否 / 重複拒否 / 種族ごとの上限)
- 収穫済みテンプレートでの候補内照合 (同タイプ複数候補の判別)
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import cv2
import numpy as np

import vision.spriteid as S


def _icon(color, shape="circle"):
    """選出アイコン風の合成画像 (暗赤背景 + 前景ブロブ)"""
    img = np.full((100, 120, 3), (40, 20, 60), np.uint8)   # 暗い背景
    if shape == "circle":
        cv2.circle(img, (60, 50), 30, color, -1)
    else:
        cv2.rectangle(img, (30, 20), (90, 80), color, -1)
    return img


def _fresh_dir():
    tmp = Path(tempfile.mkdtemp())
    S.REAL_DIR = tmp
    S._real_cache = None
    return tmp


def test_harvest_rules():
    tmp = _fresh_dir()
    try:
        # 正常収穫
        assert S.harvest_species_icon("greninja", _icon((255, 120, 40)))
        assert len(list(tmp.glob("greninja_*.png"))) == 1
        # ほぼ同一は重複拒否
        assert not S.harvest_species_icon("greninja", _icon((255, 120, 40)))
        # 形が違えば追加収穫できる
        assert S.harvest_species_icon("greninja", _icon((255, 120, 40), "rect"))
        # 前景が無い (背景のみ) は拒否
        flat = np.full((100, 120, 3), (40, 20, 60), np.uint8)
        assert not S.harvest_species_icon("greninja", flat)
        # 上限 (MAX_REAL_PER_SPECIES) で打ち止め
        for i in range(10):
            c = _icon((10 + i * 20, 200, 100), "rect" if i % 2 else "circle")
            cv2.circle(c, (20 + i * 8, 30), 8, (0, 255, 255), -1)  # 形を変える
            S.harvest_species_icon("greninja", c)
        assert len(list(tmp.glob("greninja_*.png"))) <= S.MAX_REAL_PER_SPECIES
        print("test_harvest_rules OK")
    finally:
        shutil.rmtree(tmp)
        S._real_cache = None


def test_identify_with_real_templates():
    tmp = _fresh_dir()
    try:
        # 同タイプ2候補 (みず/あく: ゲッコウガ vs サメハダー) を色形で判別
        gren = _icon((200, 80, 30))            # 青系の円
        sharp = _icon((60, 60, 220), "rect")   # 赤系の矩形
        assert S.harvest_species_icon("greninja", gren)
        assert S.harvest_species_icon("sharpedo", sharp)
        cands = [("greninja", 0.5, "ゲッコウガ"), ("sharpedo", 0.5, "サメハダー")]
        hit = S.identify_species(gren, cands)
        assert hit and hit[0] == "greninja", hit
        hit = S.identify_species(sharp, cands)
        assert hit and hit[0] == "sharpedo", hit
        # 少し崩れたクエリ (ノイズ+シフト) でも当たる
        noisy = np.roll(gren, 3, axis=1).copy()
        noise = np.random.default_rng(7).integers(0, 25, noisy.shape,
                                                  dtype=np.uint8)
        noisy = cv2.add(noisy, noise)
        hit = S.identify_species(noisy, cands)
        assert hit and hit[0] == "greninja", hit
        print("test_identify_with_real_templates OK")
    finally:
        shutil.rmtree(tmp)
        S._real_cache = None


if __name__ == "__main__":
    test_harvest_rules()
    test_identify_with_real_templates()
    print("\nALL OK")
