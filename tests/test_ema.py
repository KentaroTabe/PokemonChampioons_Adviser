"""EMA (Polyak平均) チェックポイントのテスト。

    python -m tests.test_ema

背景 (docs/AXIS_GAP_ANALYSIS.md): current の対 _best 勝率が短時間で
大きく動く疑いがあり、振動する学習の一般的対処として平均方策を導入した。
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import torch

from champions_agent.train.ema import EMA_TAU, blend_state_dicts, update_ema


def test_blend_math():
    """浮動小数は tau*ema + (1-tau)*current、整数は current 側を採用"""
    ema = {"w": torch.tensor([1.0, 2.0]), "n": torch.tensor([10])}
    cur = {"w": torch.tensor([3.0, 4.0]), "n": torch.tensor([20])}
    out = blend_state_dicts(ema, cur, tau=0.75)
    assert torch.allclose(out["w"], torch.tensor([1.5, 2.5])), out["w"]
    assert out["n"].item() == 20, out["n"]
    print("test_blend_math OK")


def test_blend_rejects_mismatch():
    """形状・キーの不一致は黙って混ぜずに拒否する (呼び出し側でリセット)"""
    ema = {"w": torch.zeros(2)}
    try:
        blend_state_dicts(ema, {"w": torch.zeros(3)}, tau=0.5)
        raise AssertionError("形状不一致を検出できていない")
    except ValueError:
        pass
    try:
        blend_state_dicts(ema, {"v": torch.zeros(2)}, tau=0.5)
        raise AssertionError("キー不一致を検出できていない")
    except ValueError:
        pass
    print("test_blend_rejects_mismatch OK")


def test_tau_window_exceeds_oscillation_period():
    """平均窓 1/(1-τ) が観測された振動周期 (約10ラウンド) を上回る"""
    window_rounds = 1.0 / (1.0 - EMA_TAU)
    assert window_rounds >= 10, window_rounds
    print("test_tau_window_exceeds_oscillation_period OK")


def test_update_initializes_by_copy():
    """EMA が無ければ current のコピーで初期化し、current 不在なら None"""
    d = Path(tempfile.mkdtemp())
    assert update_ema("balance", models_dir=d) is None
    (d / "battle_policy_balance.zip").write_bytes(b"model-v1")
    path = update_ema("balance", models_dir=d)
    assert path is not None and path.read_bytes() == b"model-v1"
    print("test_update_initializes_by_copy OK")


def test_policy_load_failure_is_visible():
    """モデル未ロードは stats に痕跡が残る (静かな全戦ランダム化の可視化)"""
    from champions_agent.agent.policy_battle import BattlePolicy
    p = BattlePolicy(model_path=Path(tempfile.mkdtemp()) / "missing.zip")
    assert p.model is None
    assert p.stats["load_failed"] == 1, p.stats
    assert set(p.stats) == {"masked", "argmax_fallback",
                            "random_fallback", "load_failed"}, p.stats
    print("test_policy_load_failure_is_visible OK")


if __name__ == "__main__":
    test_blend_math()
    test_blend_rejects_mismatch()
    test_tau_window_exceeds_oscillation_period()
    test_update_initializes_by_copy()
    test_policy_load_failure_is_visible()
    print("\nALL OK")
