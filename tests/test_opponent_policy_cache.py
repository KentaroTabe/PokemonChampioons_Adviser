"""相手方策キャッシュのLRU上限と割当掃除のテスト (2026-08-20 メモリ対策)。

    scripts/run_test.sh test_opponent_policy_cache

無上限キャッシュで pool/anchor 全世代の torch モデルがワーカーごとに
載っていた問題の回帰テスト。抽選分布は変えず保持数だけを絞る。
"""
from __future__ import annotations

from champions_agent.train.opponent_pool import (
    lru_get_or_load, prune_finished_assignments,
)


def test_lru_caps_and_evicts_oldest():
    cache: dict = {}
    calls: list = []

    def loader_for(k):
        def _load():
            calls.append(k)
            return f"policy-{k}"
        return _load

    for k in ("a", "b", "c"):
        got = lru_get_or_load(cache, k, loader_for(k), cap=2)
        assert got == f"policy-{k}"
    # cap=2: 最古 "a" が追い出されている
    assert list(cache) == ["b", "c"], list(cache)

    # ヒットは loader を呼ばず、参照順を最新へ動かす
    got = lru_get_or_load(cache, "b", loader_for("b"), cap=2)
    assert got == "policy-b"
    assert list(cache) == ["c", "b"], list(cache)
    assert calls == ["a", "b", "c"], calls

    # 追い出された "a" の再要求はロードし直し、最古 "c" が落ちる
    got = lru_get_or_load(cache, "a", loader_for("a"), cap=2)
    assert got == "policy-a"
    assert list(cache) == ["b", "a"], list(cache)
    assert calls == ["a", "b", "c", "a"], calls
    print("test_lru_caps_and_evicts_oldest OK")


def test_lru_cap_floor_is_one():
    cache: dict = {}
    lru_get_or_load(cache, "x", lambda: 1, cap=0)
    lru_get_or_load(cache, "y", lambda: 2, cap=0)
    assert list(cache) == ["y"], list(cache)
    print("test_lru_cap_floor_is_one OK")


class _FakeBattle:
    def __init__(self, finished: bool):
        self.finished = finished


def test_prune_finished_assignments():
    battles = {"t1": _FakeBattle(True), "t2": _FakeBattle(False),
               "t3": _FakeBattle(True)}
    assign = {"t1": "policy", "t2": "heuristic", "t3": "random",
              "t4-not-in-battles": "search"}
    prune_finished_assignments(assign, battles)
    # 終了済み t1/t3 だけ落ち、進行中と battles 不在のタグは残る
    assert set(assign) == {"t2", "t4-not-in-battles"}, assign
    print("test_prune_finished_assignments OK")


def main() -> None:
    test_lru_caps_and_evicts_oldest()
    test_lru_cap_floor_is_one()
    test_prune_finished_assignments()
    print("\nALL OK")


if __name__ == "__main__":
    main()
