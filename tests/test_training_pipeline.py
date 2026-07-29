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

    eval_path = logs / "last_eval_balance_benchmark.json"

    def feed(rate, n=50, times=1):
        eval_path.write_text(json.dumps({"win_rate": rate, "n_battles": n}))
        return [bc.update_from_eval("balance") for _ in range(times)][-1]

    with mock.patch.object(bc, "MODELS_DIR", models), \
         mock.patch.object(bc, "EVAL_DIR", logs), \
         mock.patch.object(bc, "STATE_PATH", models / "best_state.json"):
        # 評価なし -> 見送り
        assert bc.update_from_eval("balance") is False
        # 窓が埋まるまでは判定しない (単発50戦はSE 0.071で判定に使えない)
        assert feed(0.60, times=bc.WINDOW - 1) is False
        # 窓が埋まったら最良として記録
        assert feed(0.60) is True
        assert (models / "battle_policy_balance_best.zip").read_bytes() == b"model-v1"

        # ★本命: 単発の幸運では更新されない (旧実装はここで更新していた)
        ckpt.write_bytes(b"model-v2")
        assert feed(0.95) is False
        assert (models / "battle_policy_balance_best.zip").read_bytes() == b"model-v1"

        # 明確な改善が続けば更新される
        assert feed(0.95, times=bc.WINDOW - 1) is True
        assert (models / "battle_policy_balance_best.zip").read_bytes() == b"model-v2"

        # 劣化が続いても据え置き (bestはv2のまま)
        ckpt.write_bytes(b"model-v3")
        assert feed(0.40, times=bc.WINDOW) is False
        assert (models / "battle_policy_balance_best.zip").read_bytes() == b"model-v2"

        # 1回の評価の対戦数が少なすぎるものは窓に入れない
        assert feed(0.99, n=10, times=bc.WINDOW) is False
        assert (models / "battle_policy_balance_best.zip").read_bytes() == b"model-v2"
    print("test_best_checkpoint_update OK")


if __name__ == "__main__":
    test_pool_sampling_weights()
    test_best_checkpoint_update()
    print("\nALL OK")
