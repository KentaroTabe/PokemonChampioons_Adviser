"""シーズン切替直後の薄いオープンデータを掴まないことのテスト。

    python -m tests.test_usage_season_guard

2026-08-05、pokedb の新シーズン (M-4) が3構築しか公開されていない時刻に
日次更新が走り、285構築の前シーズンから黙って乗り換えた。その結果
使用率%が14種にしか付かず、meta_sets.weight で抽選する自己対戦の
チーム生成が実質14種に偏った (11日間気付かれなかった)。

ここでは取得側 (最小構築数に満たないシーズンを見送る) と
ingest側 (薄い集計を補完に使わない) の二重の足切りを検証する。
"""
from __future__ import annotations

from champions_agent.config import USAGE_MIN_RANKED_TEAMS


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self._payload


def _team(*species: str) -> dict:
    return {"team": [{"pokemon": s, "item": ""} for s in species]}


def _payload(season: str, n_teams: int) -> dict:
    return {
        "season": season,
        "season_number": int(season.split("-")[1]),
        "rule": "single",
        "teams": [_team("ガブリアス", "ミミッキュ") for _ in range(n_teams)],
    }


def _patch_seasons(monkeypatched: dict) -> callable:
    """{シーズン番号: teams数} を返す requests.get のスタブを作る"""
    def fake_get(url: str, **kwargs):
        n = int(url.split("/opendata/s")[1].split("_")[0])
        if n not in monkeypatched:
            return _FakeResponse(404)
        return _FakeResponse(200, _payload(f"M-{n}", monkeypatched[n]))
    return fake_get


def _with_fake_requests(seasons: dict, fn):
    from champions_agent.data.sources import pokedb_opendata as pokedb
    orig = pokedb.requests.get
    pokedb.requests.get = _patch_seasons(seasons)
    try:
        return fn(pokedb)
    finally:
        pokedb.requests.get = orig


def test_sparse_new_season_is_skipped():
    """最新シーズンが薄ければ前シーズンへ遡る (2026-08-05 の再現)"""
    seasons = {4: 3, 3: 285}
    payload, n = _with_fake_requests(
        seasons, lambda p: p.fetch_ranked_teams(archive=False))
    assert n == 3, f"薄いs4を掴んだ: s{n}"
    assert len(payload["teams"]) == 285, len(payload["teams"])
    print("test_sparse_new_season_is_skipped OK")


def test_falls_back_to_largest_when_all_sparse():
    """どのシーズンも足切り未満なら、最も構築数が多いものを使う (安全側)"""
    seasons = {4: 3, 3: 40, 2: 12}
    payload, n = _with_fake_requests(
        seasons, lambda p: p.fetch_ranked_teams(archive=False))
    assert n == 3, f"最大構築数のシーズンを選ばなかった: s{n}"
    assert len(payload["teams"]) == 40, len(payload["teams"])
    print("test_falls_back_to_largest_when_all_sparse OK")


def test_explicit_season_is_not_filtered():
    """シーズンを明示した場合は足切りせずそのシーズンを返す"""
    seasons = {4: 3, 3: 285}
    payload, n = _with_fake_requests(
        seasons, lambda p: p.fetch_ranked_teams(season_number=4, archive=False))
    assert n == 4, f"明示指定が無視された: s{n}"
    assert len(payload["teams"]) == 3, len(payload["teams"])
    print("test_explicit_season_is_not_filtered OK")


def test_ingest_ignores_sparse_aggregate():
    """取得側をすり抜けた薄い集計を、使用率%/共起率の補完に使わない"""
    from champions_agent.data.sources import usage_scraper
    from champions_agent.data.sources import championsbattledata as cbd
    from champions_agent.data.sources import pokedb_opendata as pokedb

    fake_cbd = {
        "garchomp": {"abilities": {"roughskin": 100.0}, "items": {"lifeorb": 50.0},
                     "moves": {"earthquake": 90.0}, "spreads": []},
        "mimikyu": {"abilities": {"disguise": 100.0}, "items": {"lifeorb": 40.0},
                    "moves": {"shadowsneak": 80.0}, "spreads": []},
    }
    orig_fetch_all = cbd.fetch_all
    orig_ranked = pokedb.fetch_ranked_teams
    cbd.fetch_all = lambda **kwargs: (fake_cbd, {"season": "M4"})
    pokedb.fetch_ranked_teams = lambda **kwargs: (_payload("M-4", 3), 4)
    try:
        entries, meta = usage_scraper.fetch_champions_usage()
    finally:
        cbd.fetch_all = orig_fetch_all
        pokedb.fetch_ranked_teams = orig_ranked

    assert meta["source"] == "championsbattledata", meta["source"]
    assert meta["number_of_battles"] is None, meta["number_of_battles"]
    # 3構築中3件=100% のような偽の使用率が付いていないこと
    for e in entries:
        assert e.usage_percent <= 0.1, (e.pokemon_name, e.usage_percent)
        assert not e.teammates, (e.pokemon_name, e.teammates)
    print("test_ingest_ignores_sparse_aggregate OK")


def test_pool_threshold_shares_config():
    """ベンチ用チームプールの足切りが設定値と一致している (二重管理の防止)"""
    from champions_agent.env import ranked_teams
    assert ranked_teams.MIN_POOL_TEAMS == USAGE_MIN_RANKED_TEAMS, (
        ranked_teams.MIN_POOL_TEAMS, USAGE_MIN_RANKED_TEAMS)
    print("test_pool_threshold_shares_config OK")


if __name__ == "__main__":
    test_sparse_new_season_is_skipped()
    test_falls_back_to_largest_when_all_sparse()
    test_explicit_season_is_not_filtered()
    test_ingest_ignores_sparse_aggregate()
    test_pool_threshold_shares_config()
    print("\nALL OK")
