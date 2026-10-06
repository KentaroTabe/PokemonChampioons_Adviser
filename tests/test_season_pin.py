"""季節 (規制) ごとの固定 (tools/team_build/season_pin) のテスト: 分割の seed・使用率スナップショット・実在の構築の区切りと、鮮度。

    python -m tests.test_season_pin
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

from tools.team_build import season_pin as SP


def test_resolve_pure():
    # 表に無い規制: run の seed、最新のスナップショット、今の時刻を登録して使う (fixed:new)
    pin, src, table = SP.resolve_pin("gen9championsbssregmb", 20260906, 57, 1_700_000_000.0, {}, fixed=True)
    assert pin == {"seed": 20260906, "pool_snapshot_id": 57, "roster_until": 1_700_000_000.0} and src == "fixed:new"
    assert table["gen9championsbssregmb"]["seed"] == 20260906 and table["gen9championsbssregmb"]["source"] == "first_run"
    # 表にある規制: 表の固定 (run の seed や最新のスナップショットが違っても)
    pin2, src2, table2 = SP.resolve_pin("gen9championsbssregmb", 777, 61, 1_800_000_000.0, table, fixed=True)
    assert pin2 == pin and src2 == "fixed:gen9championsbssregmb" and table2 == table
    # 別の規制は別の項
    pin3, src3, table3 = SP.resolve_pin("gen9championsbssregmc", 777, 61, 1_800_000_000.0, table2, fixed=True)
    assert pin3["seed"] == 777 and pin3["pool_snapshot_id"] == 61 and src3 == "fixed:new" and set(table3) == {"gen9championsbssregmb", "gen9championsbssregmc"}
    # 固定しない: run の seed と最新 (None)、表は触らない
    pin4, src4, table4 = SP.resolve_pin("gen9championsbssregmb", 777, 61, 1.0, table3, fixed=False)
    assert pin4 == {"seed": 777, "pool_snapshot_id": None, "roster_until": None} and src4 == "run" and table4 == table3
    # seed の上書き (--split-seed) は表を変えず由来に +arg
    pin5, src5, table5 = SP.resolve_pin("gen9championsbssregmb", 777, 61, 1.0, table3, fixed=True, override_seed=5)
    assert pin5["seed"] == 5 and pin5["pool_snapshot_id"] == 57 and src5 == "fixed:gen9championsbssregmb+arg" and table5 == table3
    # スナップショットが取れないときは None (最新を使う) で登録される
    pin6, _s6, _t6 = SP.resolve_pin("", 1, None, 2.0, {}, fixed=True)
    assert pin6["pool_snapshot_id"] is None and pin6["seed"] == 1
    print("test_resolve_pure OK")


def test_overlap_pure():
    a = [("garchomp", 1), ("primarina", 2), ("dragonite", 3)]
    assert SP.weighted_overlap(a, a) == 1.0
    assert SP.weighted_overlap(a, [("x", 1), ("y", 2), ("z", 3)]) == 0.0
    # 1 位が同じで残りが違う: 重みは 3/6, 2/6, 1/6 → 1 位の 0.5 だけ重なる
    assert SP.weighted_overlap(a, [("garchomp", 1), ("y", 2), ("z", 3)]) == 0.5
    # 順位が入れ替わると重みの小さい方
    assert SP.weighted_overlap(a, [("primarina", 1), ("garchomp", 2), ("dragonite", 3)]) == round(2 / 6 + 2 / 6 + 1 / 6, 4)
    assert SP.weighted_overlap([], a) is None and SP.rank_weights([]) == {}
    row = SP.freshness_row(57, 61, 0.75, 30, warn_below=0.8)
    assert row["warn"] is True and row["same"] is False and row["overlap"] == 0.75
    assert SP.freshness_row(57, 57, None, 30)["overlap"] == 1.0 and not SP.freshness_row(57, 57, None, 30)["warn"]
    assert SP.freshness_row(57, None, None, 30)["warn"] is False
    print("test_overlap_pure OK")


def test_file_roundtrip_and_repin():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "registry" / "season_pins.json"
        assert SP.load_table(path) == {}
        pin, src = SP.pin_for("regA", 11, path=path, fixed=True, latest_snapshot_id=57, now=1000.0)
        assert (pin["seed"], pin["pool_snapshot_id"], pin["roster_until"], src) == (11, 57, 1000.0, "fixed:new")
        saved = json.loads(path.read_text(encoding="utf-8"))
        assert saved["regA"]["pool_snapshot_id"] == 57 and saved["regA"]["created_at"]
        pin2, src2 = SP.pin_for("regA", 22, path=path, fixed=True, latest_snapshot_id=61, now=2000.0)
        assert pin2 == pin and src2 == "fixed:regA"
        # 固定し直し: 新しいスナップショット、区切りは今、前の固定は previous に残る
        entry = SP.repin("regA", snapshot_id=61, path=path, now=3000.0)
        assert entry["seed"] == 11 and entry["pool_snapshot_id"] == 61 and entry["roster_until"] == 3000.0 and entry["source"] == "repin"
        assert entry["previous"]["pool_snapshot_id"] == 57 and entry["previous"]["roster_until"] == 1000.0
        pin3, src3 = SP.pin_for("regA", 33, path=path, fixed=True, now=4000.0)
        assert pin3 == {"seed": 11, "pool_snapshot_id": 61, "roster_until": 3000.0} and src3 == "fixed:regA"
        # 壊れた表は空として扱う。固定しない設定は run の seed
        path.write_text("not json", encoding="utf-8")
        assert SP.load_table(path) == {}
        assert SP.pin_for("regA", 44, path=path, fixed=False)[1] == "run"
    print("test_file_roundtrip_and_repin OK")


def test_freshness_with_db():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE usage_snapshot (id INTEGER PRIMARY KEY, format TEXT, number_of_battles INTEGER)")
    conn.execute("CREATE TABLE pokemon_usage (snapshot_id INTEGER, pokemon_name TEXT, usage_percent REAL)")
    conn.executemany("INSERT INTO usage_snapshot VALUES (?, ?, ?)", [(1, "f", 100), (2, "f", 100)])
    rows = [(1, "garchomp", 50.0), (1, "primarina", 40.0), (1, "dragonite", 30.0), (1, "gengar", 20.0),
            (2, "garchomp", 55.0), (2, "primarina", 35.0), (2, "mimikyu", 33.0), (2, "gengar", 10.0)]
    conn.executemany("INSERT INTO pokemon_usage VALUES (?, ?, ?)", rows)
    assert SP.top_species(conn, 1, 3) == [("garchomp", 1), ("primarina", 2), ("dragonite", 3)]
    f = SP.freshness(1, 2, top_n=3, warn_below=0.9, conn=conn)
    # 上位 3: {garchomp 1, primarina 2, dragonite 3} vs {garchomp 1, primarina 2, mimikyu 3} → 3/6 + 2/6 = 0.8333
    assert f["overlap"] == round(5 / 6, 4) and f["warn"] is True and f["pinned"] == 1 and f["latest"] == 2
    assert SP.freshness(1, 1, top_n=3, conn=conn)["overlap"] == 1.0
    assert SP.freshness(None, 2, conn=conn)["note"] == "no_pin"
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "season_pins.json"
        SP.pin_for("regA", 1, path=path, fixed=True, latest_snapshot_id=1, now=1.0)
        res = SP.check(path=path, top_n=3, warn_below=0.9, conn=conn, latest_snapshot_id=2)
        assert res["any_warn"] is True and res["regulations"]["regA"]["overlap"] == round(5 / 6, 4) and res["regulations"]["regA"]["seed"] == 1
    print("test_freshness_with_db OK")


def main() -> None:
    test_resolve_pure()
    test_overlap_pure()
    test_file_roundtrip_and_repin()
    test_freshness_with_db()
    print("ALL OK")


if __name__ == "__main__":
    main()
