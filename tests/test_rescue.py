"""破棄フレーム救出バッファ (rescue_scan / _drain_rescue) のテスト。

    scripts/run_test.sh test_rescue

2026-08-31 設計変更「キャプチャと処理の分離」: 処理落ちで破棄される
フレームのメッセージ/ポップアップ域を軽量判定で退避し、後から非同期に
OCRして取り逃しを防ぐ。実フレーム検証 (第11回のすなおこしポップアップ・
キラフロルひんし) はスクラッチで実施済み。ここではOCRをモックして
ゲート・重複抑制・消化の論理を回帰化する。
"""
from __future__ import annotations

import numpy as np

from vision import ocr
from vision.pipeline import VisionPipeline
from vision.state import PokemonState


def _img():
    return np.zeros((1080, 1920, 3), dtype=np.uint8)


def test_rescue_requires_battle_context():
    """対戦文脈外 (メニュー等) では退避しない"""
    orig = ocr.outlined_text_mask
    ocr.outlined_text_mask = lambda c, **kw: np.full((10, 10), 128, np.uint8)
    try:
        p = VisionPipeline()
        p.rescue_scan(_img())
        assert p.rescue_stats["stashed"] == 0
        p.state.battle_active = True
        p.rescue_scan(_img())
        assert p.rescue_stats["stashed"] == 3   # message + popup×2
    finally:
        ocr.outlined_text_mask = orig
    print("test_rescue_requires_battle_context OK")


def test_rescue_dedups_same_display():
    """同一表示 (同じマスク署名) の連続退避は0.4秒抑制される"""
    orig = ocr.outlined_text_mask
    ocr.outlined_text_mask = lambda c, **kw: np.full((10, 10), 128, np.uint8)
    try:
        p = VisionPipeline()
        p.state.battle_active = True
        p.rescue_scan(_img())
        p.rescue_scan(_img())   # 直後の同一表示
        assert p.rescue_stats["stashed"] == 3, p.rescue_stats
        # 文字なし (mask None) は退避しない
        ocr.outlined_text_mask = lambda c, **kw: None
        p.rescue_scan(_img())
        assert p.rescue_stats["stashed"] == 3
    finally:
        ocr.outlined_text_mask = orig
    print("test_rescue_dedups_same_display OK")


def test_drain_fires_events_from_rescued_crops():
    """退避クロップのOCR結果がイベント解析へ流れ、firedに合流する"""
    orig_mask = ocr.outlined_text_mask
    orig_read = ocr.read_crop_direct
    ocr.outlined_text_mask = lambda c, **kw: np.full((10, 10), 128, np.uint8)
    ocr.read_crop_direct = lambda c, **kw: "相手は リザードンを 繰り出した!"
    try:
        p = VisionPipeline()
        p.state.battle_active = True
        p.state.opponent.party = [PokemonState(species_ja="リザードン",
                                               species_id="charizard")]
        p.rescue_scan(_img())
        fired: list = []
        p._drain_rescue(fired)
        assert any(f.startswith("switch_opponent") for f in fired), fired
        assert p.rescue_stats["events"] >= 1
        # 1回のdrainは最大3枚 (処理予算の保護) + バッファ上限24
        for _ in range(40):
            p._rescue_buf.append(("message", _img()[:10, :10]))
        assert len(p._rescue_buf) <= 24
        before = p.rescue_stats["ocr"]
        p._drain_rescue([])
        assert p.rescue_stats["ocr"] - before <= 3
    finally:
        ocr.outlined_text_mask = orig_mask
        ocr.read_crop_direct = orig_read
    print("test_drain_fires_events_from_rescued_crops OK")


def main() -> None:
    test_rescue_requires_battle_context()
    test_rescue_dedups_same_display()
    test_drain_fires_events_from_rescued_crops()
    print("\nALL OK")


if __name__ == "__main__":
    main()
