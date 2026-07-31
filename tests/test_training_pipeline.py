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


class _FakeModel:
    """SB3モデルの保存まわりだけを模したスタブ。

    SB3のsaveは拡張子が.zipでないと.zipを付け足す。この癖を再現しないと
    「一時ファイル名を間違えて保存が消える」不具合を検出できない
    (npzで同じ罠を踏んでいる)。
    """

    def __init__(self):
        self.num_timesteps = 0
        self.logger = None
        self.saved = []

    def get_env(self):
        return None

    def save(self, path):
        p = Path(path)
        if p.suffix != ".zip":
            p = p.with_suffix(p.suffix + ".zip")
        p.write_bytes(f"model@{self.num_timesteps}".encode())
        self.saved.append(p)


def test_periodic_save():
    """途中保存: OSの自動更新等で落ちても学習が丸ごと消えないこと。

    2026-07-30、報酬スイープが起動直後に落ち、最後にしか保存しない設計だった
    ため10万ステップ x 6条件が丸ごと失われた。
    """
    from champions_agent.train import train_battle as tb
    tmp = Path(tempfile.mkdtemp())
    dest = tmp / "battle_policy_balance.zip"

    model = _FakeModel()
    cb = tb._make_periodic_save(dest, every=1000)
    cb.init_callback(model)

    # 再開時: num_timesteps を引き継いでいても即座には保存しない
    model.num_timesteps = 5_000_000
    cb.on_training_start({}, {})
    assert cb.on_step() is True
    assert not dest.exists(), "再開直後に保存してはいけない"

    # 間隔に達したら保存される
    model.num_timesteps = 5_001_000
    cb.on_step()
    assert dest.exists(), "途中保存が行われていない"
    assert dest.read_bytes() == b"model@5001000"
    # 一時ファイルが残っていない (置き換えが成立している)
    leftovers = [p.name for p in tmp.iterdir() if p.name != dest.name]
    assert not leftovers, f"一時ファイルが残っている: {leftovers}"

    # 次の保存は間隔ぶん進んでから
    model.num_timesteps = 5_001_500
    cb.on_step()
    assert dest.read_bytes() == b"model@5001000"
    model.num_timesteps = 5_002_000
    cb.on_step()
    assert dest.read_bytes() == b"model@5002000"
    print("test_periodic_save OK")


def test_atomic_save_replaces_in_place():
    """書き込み途中のzipを他プロセスに掴ませないこと。

    保存先は評価とアドバイザーが読む。一時ファイルへ書いてから置き換える。
    """
    from champions_agent.train import train_battle as tb
    tmp = Path(tempfile.mkdtemp())
    dest = tmp / "sub" / "battle_policy_balance.zip"  # 親が無くても作る

    model = _FakeModel()
    model.num_timesteps = 123
    tb._atomic_save(model, dest)
    assert dest.read_bytes() == b"model@123"
    # 保存先そのものに直接書いていない (必ず一時ファイル経由)
    assert model.saved[0] != dest, "保存先へ直接書き込んでいる"
    assert list(dest.parent.iterdir()) == [dest]
    print("test_atomic_save_replaces_in_place OK")


if __name__ == "__main__":
    test_pool_sampling_weights()
    test_best_checkpoint_update()
    test_periodic_save()
    test_atomic_save_replaces_in_place()
    print("\nALL OK")
