"""build_meta の薄い技データ保護 (2026-09-05)。

championsbattledata のページは日によって一部の種で主要技を落とす
(カバルドン: じしん98% → 翌日は まもる12% が最多)。最多技の採用率が
META_THIN_MOVE_PCT 未満の種は、直近の『技データが健全な』スナップショットの
代表型を引き継ぐ。学習の相手プールと型予測が寄せ集め型を読まないための保護。
"""
from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path

from champions_agent.config import META_THIN_MOVE_PCT, SCHEMA_PATH, USAGE_TARGET_FORMAT
from champions_agent.data import build_meta

FMT = USAGE_TARGET_FORMAT


def _memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(Path(SCHEMA_PATH).read_text(encoding="utf-8"))
    return conn


def _add_snapshot(conn, sid: int) -> None:
    conn.execute(
        "INSERT INTO usage_snapshot (id, source, format, number_of_battles) "
        "VALUES (?, 'championsbattledata', ?, 223)", (sid, FMT))


def _add_species(conn, sid: int, name: str, usage: float, moves: dict,
                 item="leftovers", ability="sandstream", nature="impish",
                 evs="32/0/32/0/0/0") -> None:
    conn.execute("INSERT INTO pokemon_usage (snapshot_id, pokemon_name, usage_percent) "
                 "VALUES (?, ?, ?)", (sid, name, usage))
    for mv, pct in moves.items():
        conn.execute("INSERT INTO move_usage (snapshot_id, pokemon_name, move_name, usage_percent) "
                     "VALUES (?, ?, ?, ?)", (sid, name, mv, pct))
    conn.execute("INSERT INTO item_usage (snapshot_id, pokemon_name, item_name, usage_percent) "
                 "VALUES (?, ?, ?, 60)", (sid, name, item))
    conn.execute("INSERT INTO ability_usage (snapshot_id, pokemon_name, ability_name, usage_percent) "
                 "VALUES (?, ?, ?, 90)", (sid, name, ability))
    conn.execute("INSERT INTO spread_usage (snapshot_id, pokemon_name, nature, evs, usage_percent) "
                 "VALUES (?, ?, ?, NULL, 50)", (sid, name, nature))
    conn.execute("INSERT INTO spread_usage (snapshot_id, pokemon_name, nature, evs, usage_percent) "
                 "VALUES (?, ?, NULL, ?, 40)", (sid, name, evs))


HEALTHY = {"earthquake": 98.0, "stealthrock": 70.0, "slackoff": 65.0, "yawn": 40.0}
THIN = {"protect": 12.0, "roar": 9.0, "icefang": 8.0, "stoneedge": 7.0}


class FakeConnection:
    """db.get_connection() の差し替え (with 文で同じ in-memory 接続を返す)"""

    def __init__(self, conn):
        self.conn = conn

    def __call__(self):
        return self

    def __enter__(self):
        return self.conn

    def __exit__(self, *exc):
        return False


class ThinGuardTest(unittest.TestCase):
    def test_is_thin_moveset(self):
        self.assertTrue(build_meta.is_thin_moveset(None))
        self.assertTrue(build_meta.is_thin_moveset(0.0))
        self.assertTrue(build_meta.is_thin_moveset(META_THIN_MOVE_PCT - 0.1))
        self.assertFalse(build_meta.is_thin_moveset(META_THIN_MOVE_PCT))
        self.assertFalse(build_meta.is_thin_moveset(98.0))

    def test_carry_forward_picks_newest_healthy_snapshot(self):
        conn = _memory_db()
        for sid in (1, 2, 3):
            _add_snapshot(conn, sid)
        _add_species(conn, 1, "hippowdon", 20.0, HEALTHY, item="rockyhelmet")
        _add_species(conn, 2, "hippowdon", 22.0, HEALTHY, item="leftovers")
        _add_species(conn, 3, "hippowdon", 23.0, THIN, item="smoothrock")
        for sid, item in ((1, "rockyhelmet"), (2, "leftovers"), (3, "smoothrock")):
            conn.execute(
                "INSERT INTO meta_sets (snapshot_id, pokemon_name, ability_name, item_name, "
                "nature, evs, move1, move2, move3, move4, weight) VALUES "
                "(?, 'hippowdon', 'sandstream', ?, 'impish', '32/0/32/0/0/0', "
                "?, ?, ?, ?, 20)",
                (sid, item, *(list(HEALTHY if sid < 3 else THIN))))
        prev = build_meta.carry_forward_row(conn, "hippowdon", 3)
        self.assertIsNotNone(prev)
        self.assertEqual(prev[0], 2)            # 直近の健全なスナップショット
        self.assertEqual(prev[2], "leftovers")
        self.assertEqual(prev[5], "earthquake")
        # 4 に対しては 3 が薄いので飛ばして 2 を返す
        _add_snapshot(conn, 4)
        self.assertEqual(build_meta.carry_forward_row(conn, "hippowdon", 4)[0], 2)
        # 前が全部薄い / 前が無い → None
        self.assertIsNone(build_meta.carry_forward_row(conn, "hippowdon", 1))
        self.assertIsNone(build_meta.carry_forward_row(conn, "garchomp", 4))

    def test_build_meta_sets_carries_forward_thin_species(self):
        conn = _memory_db()
        _add_snapshot(conn, 1)
        _add_snapshot(conn, 2)
        # snapshot 1: 健全 (両種)
        _add_species(conn, 1, "hippowdon", 20.0, HEALTHY, item="rockyhelmet")
        _add_species(conn, 1, "garchomp", 45.0,
                     {"earthquake": 95.0, "stealthrock": 60.0, "scaleshot": 55.0,
                      "swordsdance": 50.0},
                     item="focussash", ability="roughskin", nature="jolly",
                     evs="0/32/0/0/0/32")
        # snapshot 2: カバルドンだけ主要技が欠落 (最多 12%)
        _add_species(conn, 2, "hippowdon", 23.0, THIN, item="smoothrock")
        _add_species(conn, 2, "garchomp", 48.0,
                     {"earthquake": 96.0, "stealthrock": 58.0, "scaleshot": 57.0,
                      "swordsdance": 51.0},
                     item="focussash", ability="roughskin", nature="jolly",
                     evs="0/32/0/0/0/32")
        conn.commit()

        orig = build_meta.db.get_connection
        build_meta.db.get_connection = FakeConnection(conn)
        try:
            n1 = build_meta.build_meta_sets(fmt=FMT)
            self.assertEqual(n1, 2)
            self.assertEqual(build_meta.db.latest_snapshot_id(conn, fmt=FMT, require_meta=False), 2)
        finally:
            build_meta.db.get_connection = orig

        # 上の呼び出しは最新 (=2) を作ったので、1 の meta_sets は自前で用意して再実行
        conn.execute(
            "INSERT INTO meta_sets (snapshot_id, pokemon_name, ability_name, item_name, "
            "nature, evs, move1, move2, move3, move4, weight) VALUES "
            "(1, 'hippowdon', 'sandstream', 'rockyhelmet', 'impish', '32/0/32/0/0/0', "
            "'earthquake', 'stealthrock', 'slackoff', 'yawn', 20)")
        conn.commit()
        build_meta.db.get_connection = FakeConnection(conn)
        try:
            build_meta.build_meta_sets(fmt=FMT)
        finally:
            build_meta.db.get_connection = orig

        hippo = conn.execute(
            "SELECT * FROM meta_sets WHERE snapshot_id = 2 AND pokemon_name = 'hippowdon'"
        ).fetchone()
        self.assertEqual(hippo["move1"], "earthquake")       # 引き継いだ型
        self.assertEqual(hippo["item_name"], "rockyhelmet")
        self.assertEqual(hippo["weight"], 23.0)              # 使用率は当日の値
        garchomp = conn.execute(
            "SELECT * FROM meta_sets WHERE snapshot_id = 2 AND pokemon_name = 'garchomp'"
        ).fetchone()
        self.assertEqual(garchomp["move1"], "earthquake")    # 健全な種は当日データ
        self.assertEqual(garchomp["weight"], 48.0)
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM meta_sets WHERE snapshot_id = 2").fetchone()[0], 2)

    def test_build_meta_sets_explicit_snapshot(self):
        """snapshot_id 指定で過去スナップショットを作り直せる (最新は触らない)"""
        conn = _memory_db()
        _add_snapshot(conn, 1)
        _add_snapshot(conn, 2)
        _add_snapshot(conn, 3)
        _add_species(conn, 1, "hippowdon", 20.0, HEALTHY, item="rockyhelmet")
        _add_species(conn, 2, "hippowdon", 22.0, THIN, item="smoothrock")
        _add_species(conn, 3, "hippowdon", 23.0, HEALTHY, item="leftovers")
        conn.commit()
        orig = build_meta.db.get_connection
        build_meta.db.get_connection = FakeConnection(conn)
        try:
            build_meta.build_meta_sets(fmt=FMT, snapshot_id=1)
            build_meta.build_meta_sets(fmt=FMT, snapshot_id=2)
        finally:
            build_meta.db.get_connection = orig
        row2 = conn.execute(
            "SELECT move1, item_name FROM meta_sets WHERE snapshot_id = 2").fetchone()
        self.assertEqual((row2["move1"], row2["item_name"]), ("earthquake", "rockyhelmet"))
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM meta_sets WHERE snapshot_id = 3").fetchone()[0], 0)

    def test_thin_without_history_falls_back_to_raw(self):
        """引き継げる過去が無ければ従来通り当日の最多で組む (棄却しない)"""
        conn = _memory_db()
        _add_snapshot(conn, 1)
        _add_species(conn, 1, "hippowdon", 23.0, THIN, item="smoothrock")
        conn.commit()
        orig = build_meta.db.get_connection
        build_meta.db.get_connection = FakeConnection(conn)
        try:
            self.assertEqual(build_meta.build_meta_sets(fmt=FMT), 1)
        finally:
            build_meta.db.get_connection = orig
        row = conn.execute("SELECT move1 FROM meta_sets WHERE snapshot_id = 1").fetchone()
        self.assertEqual(row["move1"], "protect")


if __name__ == "__main__":
    unittest.main()
