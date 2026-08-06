"""選出モデルの埋め込みピン止めのテスト。

    python -m tests.test_embedding_pin

埋め込みは使用率DBから日次で再構築され、基底が変わると座標の意味が
変わる (2026-08-06: 配布選出が0.609→0.442に崩落)。モデルは学習時の
埋め込みをピンし、推論はピン側を読む。
"""
from __future__ import annotations

import json

import numpy as np


def test_pin_takes_precedence(tmp_dir=None):
    import champions_agent.agent.selection_model as sm

    # ピンファイルを一時的に差し替えて、_embがピン側を読むことを確認
    orig_path = sm.EMB_PIN_PATH
    orig_store = sm._pinned_store
    orig_cache = dict(sm._emb_cache)
    try:
        import tempfile
        from pathlib import Path
        d = Path(tempfile.mkdtemp())
        pin = d / "selection_model_emb.json"
        marker = [9.0] * sm.EMB_DIM
        pin.write_text(json.dumps(
            {"functional": {"vectors": {"garchomp": marker}}}),
            encoding="utf-8")
        sm.EMB_PIN_PATH = pin
        sm._pinned_store = None
        sm._emb_cache.clear()

        v = sm._emb("garchomp")
        assert np.allclose(v, 9.0), v
        # ピンに無い種族はゼロ (生きた埋め込みへ黙って混ざらない)
        v2 = sm._emb("greninja")
        assert np.allclose(v2, 0.0), v2
    finally:
        sm.EMB_PIN_PATH = orig_path
        sm._pinned_store = orig_store
        sm._emb_cache.clear()
        sm._emb_cache.update(orig_cache)
    print("test_pin_takes_precedence OK")


def test_fallback_to_live_when_no_pin():
    import champions_agent.agent.selection_model as sm
    orig_path = sm.EMB_PIN_PATH
    orig_store = sm._pinned_store
    orig_cache = dict(sm._emb_cache)
    try:
        from pathlib import Path
        sm.EMB_PIN_PATH = Path("/nonexistent/pin.json")
        sm._pinned_store = None
        sm._emb_cache.clear()
        v = sm._emb("garchomp")   # 生きた埋め込みから読める
        assert v.shape == (sm.EMB_DIM,)
        assert float(np.abs(v).sum()) > 0, "生きた埋め込みへのフォールバック失敗"
    finally:
        sm.EMB_PIN_PATH = orig_path
        sm._pinned_store = orig_store
        sm._emb_cache.clear()
        sm._emb_cache.update(orig_cache)
    print("test_fallback_to_live_when_no_pin OK")


if __name__ == "__main__":
    test_pin_takes_precedence()
    test_fallback_to_live_when_no_pin()
    print("\nALL OK")
