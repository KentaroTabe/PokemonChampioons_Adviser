"""
SQLiteデータベースへの接続・スキーマ初期化・簡易CRUDユーティリティ。
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Sequence

from champions_agent.config import DB_PATH, SCHEMA_PATH


def ensure_db_dir() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def init_db(db_path: Path = DB_PATH, schema_path: Path = SCHEMA_PATH) -> None:
    """スキーマファイルを読み込みDBを初期化(既存テーブルはCREATE IF NOT EXISTSのため保持)。"""
    ensure_db_dir()
    schema_sql = schema_path.read_text(encoding="utf-8")
    with get_connection(db_path) as conn:
        conn.executescript(schema_sql)
        conn.commit()


@contextmanager
def get_connection(db_path: Path = DB_PATH) -> Iterator[sqlite3.Connection]:
    ensure_db_dir()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
    finally:
        conn.close()


def upsert_pokemon(conn: sqlite3.Connection, row: dict) -> int:
    """pokemonテーブルへupsert。idはPokeAPI由来のidをそのまま使う。"""
    conn.execute(
        """
        INSERT INTO pokemon (id, name, display_name, hp, attack, defense,
                              sp_attack, sp_defense, speed, type1, type2, updated_at)
        VALUES (:id, :name, :display_name, :hp, :attack, :defense,
                :sp_attack, :sp_defense, :speed, :type1, :type2, datetime('now'))
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name,
            display_name=excluded.display_name,
            hp=excluded.hp,
            attack=excluded.attack,
            defense=excluded.defense,
            sp_attack=excluded.sp_attack,
            sp_defense=excluded.sp_defense,
            speed=excluded.speed,
            type1=excluded.type1,
            type2=excluded.type2,
            updated_at=datetime('now')
        """,
        row,
    )
    return row["id"]


def upsert_ability(conn: sqlite3.Connection, row: dict) -> int:
    conn.execute(
        """
        INSERT INTO abilities (id, name, display_name, effect_text, updated_at)
        VALUES (:id, :name, :display_name, :effect_text, datetime('now'))
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name,
            display_name=excluded.display_name,
            effect_text=excluded.effect_text,
            updated_at=datetime('now')
        """,
        row,
    )
    return row["id"]


def upsert_move(conn: sqlite3.Connection, row: dict) -> int:
    conn.execute(
        """
        INSERT INTO moves (id, name, display_name, type, category, power,
                            accuracy, pp, priority, effect_text, updated_at)
        VALUES (:id, :name, :display_name, :type, :category, :power,
                :accuracy, :pp, :priority, :effect_text, datetime('now'))
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name,
            display_name=excluded.display_name,
            type=excluded.type,
            category=excluded.category,
            power=excluded.power,
            accuracy=excluded.accuracy,
            pp=excluded.pp,
            priority=excluded.priority,
            effect_text=excluded.effect_text,
            updated_at=datetime('now')
        """,
        row,
    )
    return row["id"]


def upsert_item(conn: sqlite3.Connection, row: dict) -> int:
    conn.execute(
        """
        INSERT INTO items (id, name, display_name, effect_text, updated_at)
        VALUES (:id, :name, :display_name, :effect_text, datetime('now'))
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name,
            display_name=excluded.display_name,
            effect_text=excluded.effect_text,
            updated_at=datetime('now')
        """,
        row,
    )
    return row["id"]


def link_pokemon_ability(conn: sqlite3.Connection, pokemon_id: int, ability_id: int,
                          is_hidden: bool, slot: int) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO pokemon_abilities (pokemon_id, ability_id, is_hidden, slot)
        VALUES (?, ?, ?, ?)
        """,
        (pokemon_id, ability_id, int(is_hidden), slot),
    )


def link_pokemon_move(conn: sqlite3.Connection, pokemon_id: int, move_id: int,
                       learn_method: str) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO pokemon_moves (pokemon_id, move_id, learn_method)
        VALUES (?, ?, ?)
        """,
        (pokemon_id, move_id, learn_method),
    )


def create_usage_snapshot(conn: sqlite3.Connection, source: str, fmt: str,
                           rating_cutoff: int | None = None, note: str = "",
                           source_month: str | None = None,
                           number_of_battles: int | None = None,
                           source_url: str | None = None) -> int:
    cur = conn.execute(
        """
        INSERT INTO usage_snapshot
            (source, format, rating_cutoff, note, source_month, number_of_battles, source_url)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (source, fmt, rating_cutoff, note, source_month, number_of_battles, source_url),
    )
    return cur.lastrowid



def bulk_insert(conn: sqlite3.Connection, table: str, columns: Sequence[str],
                 rows: Sequence[Sequence]) -> None:
    if not rows:
        return
    placeholders = ", ".join(["?"] * len(columns))
    col_sql = ", ".join(columns)
    conn.executemany(
        f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders})",
        rows,
    )


def latest_snapshot_id(conn: sqlite3.Connection, source: str | None = None,
                        fmt: str | None = None) -> int | None:
    """最新のスナップショットIDを返す。source/fmt は指定時のみ絞り込む。"""
    conditions, params = [], []
    if source:
        conditions.append("source = ?")
        params.append(source)
    if fmt:
        conditions.append("format = ?")
        params.append(fmt)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    row = conn.execute(
        f"""
        SELECT id FROM usage_snapshot
        {where}
        ORDER BY fetched_at DESC, id DESC
        LIMIT 1
        """,
        params,
    ).fetchone()
    return row["id"] if row else None
