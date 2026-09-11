"""観測次元のゼロ拡張 (champions_agent/train/migrate_obs) の純粋部分のテスト。

    python -m tests.test_migrate_obs
"""
from __future__ import annotations


def test_expand_linear_keeps_outputs():
    import torch
    import torch.nn as nn
    from champions_agent.train.migrate_obs import _expand_linear
    torch.manual_seed(0)
    layer = nn.Linear(5, 3)
    new = _expand_linear(layer, 8)
    assert new.in_features == 8 and new.out_features == 3
    x = torch.randn(4, 5)
    x8 = torch.zeros(4, 8)
    x8[:, :5] = x
    assert torch.allclose(layer(x), new(x8), atol=1e-6)          # 末尾ゼロなら出力が一致
    x8[:, 5:] = 1.0
    assert torch.allclose(layer(x), new(x8), atol=1e-6)          # 追加列の重みはゼロなので値が入っても同じ
    assert _expand_linear(layer, 5) is layer
    try:
        _expand_linear(layer, 3)
        raise AssertionError("縮小は拒否されるはず")
    except ValueError:
        pass
    print("test_expand_linear_keeps_outputs OK")


if __name__ == "__main__":
    test_expand_linear_keeps_outputs()
