"""prune_finished_battles (学習中のBattleオブジェクト蓄積対策) のテスト。

    scripts/run_test.sh test_battle_prune

2026-08-19 メモリ枯渇対策: poke-env の Player は対戦オブジェクトを close まで
解放しないため、長時間学習でワーカーRSSが対戦数に比例して増えていた。
学習環境のみ、終了済みバトルを定期破棄する。
"""
from __future__ import annotations

from champions_agent.env.showdown_env import prune_finished_battles


class _FakeBattle:
    def __init__(self, finished: bool):
        self.finished = finished


class _FakePlayer:
    def __init__(self, battles: dict):
        self._battles = battles

    @property
    def battles(self) -> dict:
        return self._battles


def _player(n_finished: int, n_active: int) -> _FakePlayer:
    battles = {}
    for i in range(n_finished):
        battles[f"done-{i}"] = _FakeBattle(finished=True)
    for i in range(n_active):
        battles[f"live-{i}"] = _FakeBattle(finished=False)
    return _FakePlayer(battles)


def test_keeps_recent_finished_and_all_active():
    pl = _player(n_finished=10, n_active=2)
    removed = prune_finished_battles([pl], keep=3)
    assert removed == 7, removed
    # 新しい順に3件残る (dict挿入順 = 時系列)
    assert [t for t in pl.battles if t.startswith("done-")] == \
        ["done-7", "done-8", "done-9"], list(pl.battles)
    # 進行中は必ず残る
    assert [t for t in pl.battles if t.startswith("live-")] == \
        ["live-0", "live-1"], list(pl.battles)
    print("test_keeps_recent_finished_and_all_active OK")


def test_keep_zero_removes_all_finished():
    pl = _player(n_finished=4, n_active=1)
    removed = prune_finished_battles([pl], keep=0)
    assert removed == 4, removed
    assert list(pl.battles) == ["live-0"], list(pl.battles)
    print("test_keep_zero_removes_all_finished OK")


def test_none_player_and_empty_are_skipped():
    pl = _player(n_finished=6, n_active=0)
    removed = prune_finished_battles([None, _FakePlayer({}), pl], keep=5)
    assert removed == 1, removed
    assert len(pl.battles) == 5, list(pl.battles)
    print("test_none_player_and_empty_are_skipped OK")


def test_fewer_finished_than_keep_removes_nothing():
    pl = _player(n_finished=2, n_active=1)
    removed = prune_finished_battles([pl], keep=5)
    assert removed == 0, removed
    assert len(pl.battles) == 3
    print("test_fewer_finished_than_keep_removes_nothing OK")


def main() -> None:
    test_keeps_recent_finished_and_all_active()
    test_keep_zero_removes_all_finished()
    test_none_player_and_empty_are_skipped()
    test_fewer_finished_than_keep_removes_nothing()
    print("\nALL OK")


if __name__ == "__main__":
    main()
