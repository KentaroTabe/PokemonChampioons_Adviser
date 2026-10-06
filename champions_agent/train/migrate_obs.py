"""行動方策 (MaskablePPO) の観測次元を末尾追記ぶんだけ拡張する (挙動は同一のまま)。

    python -m champions_agent.train.migrate_obs --src checkpoints/battle_policy_balance.zip \
        --dst checkpoints/battle_policy_balance.zip --dim 436 [--check]

観測は v1 からずっと末尾追記なので、旧チェックポイントの入力層に新しい次元ぶんの列をゼロで足せば、
新しい観測 (末尾がゼロでも値が入っても) に対して旧方策と同じ出力を返すところから続きを学習できる。
既存の「観測空間が違う → 退避して新規学習」(policy が消える) を避けるための道具。
--check は拡張前後で同じ盤面 (末尾ゼロ) の出力が一致することを確かめる。
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np


def _expand_linear(layer, new_in: int):
    """nn.Linear の in_features を new_in に広げる (追加列はゼロ)。戻り値: 新しい層"""
    import torch
    import torch.nn as nn
    old_in = layer.in_features
    if old_in == new_in:
        return layer
    if old_in > new_in:
        raise ValueError(f"縮小はできない: {old_in} → {new_in}")
    new = nn.Linear(new_in, layer.out_features, bias=layer.bias is not None)
    with torch.no_grad():
        new.weight.zero_()
        new.weight[:, :old_in] = layer.weight
        if layer.bias is not None:
            new.bias.copy_(layer.bias)
    return new


def _first_linear_paths(policy) -> list:
    """観測を直接受ける Linear 層の (親モジュール, 属性名 or インデックス) を列挙する"""
    import torch.nn as nn
    out = []
    ext = policy.mlp_extractor
    for name in ("policy_net", "value_net"):
        seq = getattr(ext, name, None)
        if isinstance(seq, nn.Sequential) and len(seq) and isinstance(seq[0], nn.Linear):
            out.append((seq, 0))
    # set encoder 等、features_extractor が Linear を持つ場合 (Flatten なら何もしない)
    fe = getattr(policy, "features_extractor", None)
    for mod_name, mod in (fe.named_children() if fe is not None else []):
        if isinstance(mod, nn.Linear):
            out.append((fe, mod_name))
    return out


def migrate(src: Path, dst: Path, dim: int, check: bool = True) -> dict:
    import torch
    from gymnasium.spaces import Box
    from sb3_contrib import MaskablePPO
    model = MaskablePPO.load(str(src), device="cpu")
    old_dim = int(model.observation_space.shape[0])
    if old_dim >= dim:
        raise SystemExit(f"拡張不要: checkpoint={old_dim} >= 希望={dim}")
    policy = model.policy
    ref = None
    if check:
        torch.manual_seed(0)
        obs_old = torch.randn(8, old_dim)
        with torch.no_grad():
            ref = policy.get_distribution(obs_old).distribution.logits.clone()
            ref_v = policy.predict_values(obs_old).clone()
    paths = _first_linear_paths(policy)
    if not paths:
        raise SystemExit("観測を受ける Linear 層が見つからない (アーキテクチャ未対応)")
    for parent, key in paths:
        layer = parent[key] if isinstance(key, int) else getattr(parent, key)
        new = _expand_linear(layer, dim)
        if isinstance(key, int):
            parent[key] = new
        else:
            setattr(parent, key, new)
    space = Box(low=-np.inf, high=np.inf, shape=(dim,), dtype=np.float32)
    model.observation_space = space
    policy.observation_space = space
    try:
        policy.features_extractor.observation_space = space   # Flatten 抽出器は shape だけ持つ
        policy.features_extractor._observation_space = space
    except Exception:
        pass
    # 最適化器は形が変わるので作り直す (学習率は保存値を引き継ぐ)
    policy.optimizer = policy.optimizer_class(policy.parameters(), lr=model.lr_schedule(1.0), **policy.optimizer_kwargs)
    info = {"src": str(src), "dst": str(dst), "old_dim": old_dim, "new_dim": dim, "expanded_layers": len(paths)}
    if check:
        obs_new = torch.zeros(8, dim)
        obs_new[:, :old_dim] = obs_old
        with torch.no_grad():
            got = policy.get_distribution(obs_new).distribution.logits
            got_v = policy.predict_values(obs_new)
        diff = float((got - ref).abs().max())
        diff_v = float((got_v - ref_v).abs().max())
        info.update({"max_logit_diff": diff, "max_value_diff": diff_v})
        if diff > 1e-5 or diff_v > 1e-5:
            raise SystemExit(f"拡張後の出力が一致しない: logits {diff:.2e} / value {diff_v:.2e}")
    model.save(str(dst))
    return info


def main() -> None:
    ap = argparse.ArgumentParser(description="行動方策の観測次元をゼロ拡張する")
    ap.add_argument("--src", required=True)
    ap.add_argument("--dst", required=True)
    ap.add_argument("--dim", type=int, required=True)
    ap.add_argument("--no-check", action="store_true")
    args = ap.parse_args()
    info = migrate(Path(args.src), Path(args.dst), args.dim, check=not args.no_check)
    print(info)


if __name__ == "__main__":
    main()
