"""評価側 meta_sets ピン (META_PIN) のテスト (2026-09-02)。

    scripts/run_test.sh test_meta_pin

cbd由来の型は日次で回転し、評価軸 (ベンチ/h2hのチーム中身) を動かす
(2026-08-19: 127種、09-02: 67種)。META_PIN に snapshot_id を書くと
評価 (train/evaluate.py) の meta_sets がそのスナップショットに固定される。
学習側は最新を追い続ける。メモリ上のDBとテンポラリのピンで検証する。
"""
from __future__ import annotations

import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path

from champions_agent.env import ranked_teams as rt


def _memory_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE usage_snapshot (
        id INTEGER PRIMARY KEY, fetched_at TEXT, source TEXT, format TEXT)""")
    conn.execute("""CREATE TABLE meta_sets (
        snapshot_id INTEGER, pokemon_name TEXT, ability_name TEXT,
        item_name TEXT, nature TEXT, evs TEXT,
        move1 TEXT, move2 TEXT, move3 TEXT, move4 TEXT)""")
    conn.executemany("INSERT INTO usage_snapshot VALUES (?,?,?,?)", [
        (1, "2026-09-01 00:00:00", "champions", "champions-singles"),
        (2, "2026-09-02 00:00:00", "champions", "champions-singles")])
    conn.executemany("INSERT INTO meta_sets VALUES (?,?,?,?,?,?,?,?,?,?)", [
        (1, "rotomwash", "levitate", "leftovers", "bold", "32/0/32/0/2/0",
         "hydropump", "voltswitch", "willowisp", "thunderbolt"),
        (2, "rotomwash", "levitate", "leftovers", "bold", "32/0/32/0/2/0",
         "trick", "discharge", "lightscreen", "nastyplot")])
    return conn


def test_pinned_snapshot_overrides_latest():
    """ピン指定のスナップショットの型が返り、未指定なら最新の型が返る"""
    conn = _memory_db()

    @contextmanager
    def fake_connection(db_path=None):
        yield conn

    orig = rt.db.get_connection
    rt.db.get_connection = fake_connection
    try:
        latest = rt._load_meta_sets()
        pinned = rt._load_meta_sets(1)
        assert latest["rotomwash"]["move1"] == "trick", latest
        assert pinned["rotomwash"]["move1"] == "hydropump", pinned
    finally:
        rt.db.get_connection = orig
    print("test_pinned_snapshot_overrides_latest OK")


def test_meta_pin_file_parsing():
    """META_PIN は1行のsnapshot_id。無い/壊れているときは None (=最新)"""
    orig = rt.META_PIN_PATH
    try:
        with tempfile.TemporaryDirectory() as d:
            rt.META_PIN_PATH = Path(d) / "META_PIN"
            assert rt.pinned_meta_snapshot_id() is None      # ファイルなし
            rt.META_PIN_PATH.write_text("24\n", encoding="utf-8")
            assert rt.pinned_meta_snapshot_id() == 24
            rt.META_PIN_PATH.write_text("latest", encoding="utf-8")
            assert rt.pinned_meta_snapshot_id() is None      # 壊れた内容
    finally:
        rt.META_PIN_PATH = orig
    print("test_meta_pin_file_parsing OK")


def test_cache_key_includes_pin():
    """ピン違いのプールがキャッシュで混ざらない"""
    conn = _memory_db()

    @contextmanager
    def fake_connection(db_path=None):
        yield conn

    orig_conn, orig_ladder, orig_ext = (
        rt.db.get_connection, rt._load_ladder_teams, rt._load_external_teams)
    rt.db.get_connection = fake_connection
    rt._load_ladder_teams = lambda: [
        {"team": [{"pokemon": "ロトム", "form": "ウォッシュロトム",
                   "item": "たべのこし"}]}]
    rt._load_external_teams = lambda: []
    rt._cache.clear()
    try:
        latest = rt.build_ranked_teams(team_size=1, include_external=False)
        pinned = rt.build_ranked_teams(team_size=1, include_external=False,
                                       meta_snapshot_id=1)
        assert "trick" in latest[0] and "hydropump" not in latest[0], latest
        assert "hydropump" in pinned[0] and "trick" not in pinned[0], pinned
    finally:
        rt.db.get_connection = orig_conn
        rt._load_ladder_teams = orig_ladder
        rt._load_external_teams = orig_ext
        rt._cache.clear()
    print("test_cache_key_includes_pin OK")


if __name__ == "__main__":
    test_pinned_snapshot_overrides_latest()
    test_meta_pin_file_parsing()
    test_cache_key_includes_pin()
