"""学習パイプラインの頭打ち対策のテスト。

    python -m tests.test_training_pipeline

- opponent_pool: ベンチ勝率による抽選重み (強い世代を優先)、旧エントリ互換
- best_checkpoint: 最良更新の判定 (更新/据え置き/対戦数不足)
"""
from __future__ import annotations

import json
import random
import tempfile
from pathlib import Path
from unittest import mock


def test_pool_sampling_weights():
    from champions_agent.train import opponent_pool as op
    pool = op.OpponentPool.__new__(op.OpponentPool)  # ディレクトリ作成を回避
    # 旧エントリ (bench_rate無し) と新エントリの混在
    pool.state = {"entries": [
        {"file": "a.zip", "style": "balance", "win_rate": 0.8,
         "added_at": "20260722_000001"},                      # 旧: 0.4扱い
        {"file": "b.zip", "style": "balance", "win_rate": 0.8,
         "bench_rate": 0.83, "added_at": "20260722_000002"},  # 強い
        {"file": "c.zip", "style": "balance", "win_rate": 0.8,
         "bench_rate": 0.10, "added_at": "20260722_000003"},  # 弱い
    ]}
    rng = random.Random(42)
    counts = {"a.zip": 0, "b.zip": 0, "c.zip": 0}
    for _ in range(3000):
        p = pool.sample(rng)
        counts[p.name] += 1
    # 強い世代 (b) は弱い最新世代 (c) より優先されるはず
    # (重み: b=2*(0.3+0.83)=2.26, c=3*(0.3+0.10)=1.20, a=1*(0.3+0.4)=0.70)
    assert counts["b.zip"] > counts["c.zip"] > counts["a.zip"], counts
    print("test_pool_sampling_weights OK", counts)


def test_best_checkpoint_update():
    from champions_agent.train import best_checkpoint as bc
    tmp = Path(tempfile.mkdtemp())
    models = tmp / "models"
    logs = tmp / "logs"
    models.mkdir()
    logs.mkdir()
    ckpt = models / "battle_policy_balance.zip"
    ckpt.write_bytes(b"model-v1")

    with mock.patch.object(bc, "MODELS_DIR", models), \
         mock.patch.object(bc, "EVAL_DIR", logs), \
         mock.patch.object(bc, "STATE_PATH", models / "best_state.json"):
        # 評価なし -> 見送り
        assert bc.update_from_eval("balance") is False
        # 初回 (0.60, 50戦) -> 最良更新
        (logs / "last_eval_balance_benchmark.json").write_text(json.dumps(
            {"win_rate": 0.60, "n_battles": 50}))
        assert bc.update_from_eval("balance") is True
        assert (models / "battle_policy_balance_best.zip").read_bytes() == b"model-v1"
        # 劣化 (0.40) -> 据え置き (bestはv1のまま)
        ckpt.write_bytes(b"model-v2")
        (logs / "last_eval_balance_benchmark.json").write_text(json.dumps(
            {"win_rate": 0.40, "n_battles": 50}))
        assert bc.update_from_eval("balance") is False
        assert (models / "battle_policy_balance_best.zip").read_bytes() == b"model-v1"
        # 更新 (0.72) -> v2がbestに
        (logs / "last_eval_balance_benchmark.json").write_text(json.dumps(
            {"win_rate": 0.72, "n_battles": 50}))
        assert bc.update_from_eval("balance") is True
        assert (models / "battle_policy_balance_best.zip").read_bytes() == b"model-v2"
        # 対戦数不足 (0.90だが10戦) -> 見送り
        (logs / "last_eval_balance_benchmark.json").write_text(json.dumps(
            {"win_rate": 0.90, "n_battles": 10}))
        assert bc.update_from_eval("balance") is False
    print("test_best_checkpoint_update OK")


if __name__ == "__main__":
    test_pool_sampling_weights()
    test_best_checkpoint_update()
    print("\nALL OK")
