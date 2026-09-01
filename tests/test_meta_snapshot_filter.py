"""診断用スナップショット選別のテスト (2026-09-02)。

    scripts/run_test.sh test_meta_snapshot_filter

meta_top 等は全スナップショット横断の MAX(usage_percent) を使うため、
集計母数が極端に小さいスナップショット (2026-08-05 の3構築等) が
「使用率66.7%の脅威」を診断に混入させていた。_champions_filter が
母数の足切りをかけることを、メモリ上のDBで検証する。
"""
from __future__ import annotations

import sqlite3

from advisor.team_advice import _champions_filter
from champions_agent.config import USAGE_MIN_RANKED_TEAMS


def _make_db():
    db = sqlite3.connect(":memory:")
    db.execute("""CREATE TABLE usage_snapshot (
        id INTEGER PRIMARY KEY, format TEXT, number_of_battles INTEGER)""")
    db.execute("""CREATE TABLE pokemon_usage (
        snapshot_id INTEGER, pokemon_name TEXT, usage_percent REAL)""")
    rows = [
        (1, "champions-singles", 223),                        # 正常
        (2, "champions-singles", 3),                          # 薄い (8/5型)
        (3, "champions-singles", None),                       # 母数不明は許容
        (4, "gen9ou", 999999),                                # smogonフォールバック
    ]
    db.executemany("INSERT INTO usage_snapshot VALUES (?,?,?)", rows)
    db.executemany(
        "INSERT INTO pokemon_usage VALUES (?,?,?)",
        [(1, "garchomp", 48.9), (2, "samurotthisui", 66.7),
         (3, "primarina", 26.5), (4, "greatTusk", 40.0)])
    return db


def test_thin_snapshot_excluded():
    """母数が足切り未満のスナップショットは診断対象から外れる"""
    assert 3 < USAGE_MIN_RANKED_TEAMS <= 223
    db = _make_db()
    flt = _champions_filter(db)
    names = {r[0] for r in db.execute(
        f"SELECT pokemon_name FROM pokemon_usage WHERE {flt}")}
    assert "garchomp" in names          # 正常スナップショットは残る
    assert "primarina" in names         # 母数不明 (NULL) は許容
    assert "samurotthisui" not in names  # 3構築の66.7%は除外
    assert "greatTusk" not in names     # smogonは champions がある限り除外
    print("test_thin_snapshot_excluded OK")


def test_no_champions_snapshots_falls_back_to_all():
    """championsスナップショットが無ければ全データを使う (従来どおり)"""
    db = sqlite3.connect(":memory:")
    db.execute("""CREATE TABLE usage_snapshot (
        id INTEGER PRIMARY KEY, format TEXT, number_of_battles INTEGER)""")
    db.execute("INSERT INTO usage_snapshot VALUES (1, 'gen9ou', 100000)")
    assert _champions_filter(db) == "1=1"
    print("test_no_champions_snapshots_falls_back_to_all OK")


if __name__ == "__main__":
    test_thin_snapshot_excluded()
    test_no_champions_snapshots_falls_back_to_all()
