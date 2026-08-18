"""スナップショット可視性 (meta_sets 生成前の窓) のテスト。

    python -m tests.test_snapshot_visibility

ingest のコミットから build_meta のコミットまでの数秒間、最新スナップショットは
meta_sets が空になる。2026-08-16、この窓を掴んだチーム生成が
「候補数0 < パーティサイズ」で例外を投げ、隔離学習が27時間ハングした
(docs/incidents/reports/2026-08-16-isolated-training-hang.md)。

latest_snapshot_id は既定で「meta_sets が生成済みのスナップショット」だけを
返し、meta_sets を作る側 (build_meta) だけが require_meta=False で
生のままの最新を見る、という契約を検証する。
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from champions_agent.config import SCHEMA_PATH
from champions_agent.data import database as db


def _make_test_db() -> sqlite3.Connection:
    """schema.sql を適用した使い捨てDBを作る。

    snapshot 1: meta_sets あり (通常状態)
    snapshot 2: meta_sets なし (ingest 直後〜build_meta 完了までの窓を再現)
    """
    path = Path(tempfile.mkdtemp()) / "test.sqlite3"
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))

    for fetched_at in ("2026-08-05 12:00:00", "2026-08-16 10:41:13"):
        conn.execute(
            "INSERT INTO usage_snapshot (source, format, fetched_at) "
            "VALUES ('championsbattledata+pokedb', 'champions-singles', ?)",
            (fetched_at,))
    conn.execute(
        "INSERT INTO meta_sets (snapshot_id, pokemon_name, move1, weight) "
        "VALUES (1, 'garchomp', 'earthquake', 39.65)")
    conn.commit()
    return conn


def test_default_skips_meta_less_snapshot():
    """既定 (require_meta=True) は meta_sets の無い最新を掴まない"""
    conn = _make_test_db()
    try:
        assert db.latest_snapshot_id(conn) == 1, db.latest_snapshot_id(conn)
        assert db.latest_snapshot_id(conn, fmt="champions-singles") == 1
    finally:
        conn.close()
    print("test_default_skips_meta_less_snapshot OK")


def test_builder_sees_raw_latest():
    """build_meta 側 (require_meta=False) は出来たての最新を見る"""
    conn = _make_test_db()
    try:
        assert db.latest_snapshot_id(conn, require_meta=False) == 2
    finally:
        conn.close()
    print("test_builder_sees_raw_latest OK")


def test_meta_completion_promotes_snapshot():
    """build_meta 完了 (meta_sets 投入) と同時に最新へ切り替わる"""
    conn = _make_test_db()
    try:
        conn.execute(
            "INSERT INTO meta_sets (snapshot_id, pokemon_name, move1, weight) "
            "VALUES (2, 'garchomp', 'earthquake', 48.65)")
        conn.commit()
        assert db.latest_snapshot_id(conn) == 2
    finally:
        conn.close()
    print("test_meta_completion_promotes_snapshot OK")


def test_no_snapshot_returns_none():
    """meta_sets 付きが1件も無ければ None (呼び出し側の既存ガードに乗る)"""
    conn = _make_test_db()
    try:
        conn.execute("DELETE FROM meta_sets")
        conn.commit()
        assert db.latest_snapshot_id(conn) is None
        assert db.latest_snapshot_id(conn, require_meta=False) == 2
    finally:
        conn.close()
    print("test_no_snapshot_returns_none OK")


def test_build_meta_call_site_uses_raw():
    """build_meta の呼び出しが require_meta=False を指定している (退行防止)"""
    import inspect
    from champions_agent.data import build_meta
    src = inspect.getsource(build_meta.build_meta_sets)
    assert "require_meta=False" in src, "build_meta が生の最新を見ていない"
    print("test_build_meta_call_site_uses_raw OK")


if __name__ == "__main__":
    test_default_skips_meta_less_snapshot()
    test_builder_sees_raw_latest()
    test_meta_completion_promotes_snapshot()
    test_no_snapshot_returns_none()
    test_build_meta_call_site_uses_raw()
    print("\nALL OK")
