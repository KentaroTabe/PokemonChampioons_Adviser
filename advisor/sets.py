"""相手ポケモンの型 (技/持ち物/特性) 予測。

champions_agent/data/db/champions.sqlite3 の使用率統計 (Smogon由来) から、
その種族の採用率上位の技・持ち物・特性を取得する。
DBが無い/種族が未収録の場合は空を返し、呼び出し側でタイプ一致技などにフォールバックする。

注: 現状のDBは gen9ou のデータ。ポケモンチャンピオンズ用の使用率ソース
(champs.pokedb.tokyo 等) が整備されたら ingest 側を差し替えるだけでよい。
"""
from __future__ import annotations

import re
import sqlite3
import threading
from functools import lru_cache
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).resolve().parent.parent / "champions_agent" / "data" / "db" / "champions.sqlite3"


def _slug_to_id(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


class SetPredictor:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._conn = None
        self._snapshot_id = None
        # サーバーはフレーム処理をスレッドプールで回すため、predict() が
        # 接続を作ったスレッドと別のスレッドから呼ばれる (選出評価が
        # "SQLite objects created in a thread..." で落ちた実績)。
        # 読み取り専用DBなので check_same_thread=False + ロック直列化で守る
        self._lock = threading.Lock()

    def _connect(self) -> Optional[sqlite3.Connection]:
        if self._conn is not None:
            return self._conn
        if not self.db_path.exists():
            return None
        try:
            self._conn = sqlite3.connect(str(self.db_path),
                                         check_same_thread=False)
            row = self._conn.execute(
                "SELECT id FROM usage_snapshot ORDER BY id DESC LIMIT 1").fetchone()
            self._snapshot_id = row[0] if row else None
        except Exception:
            self._conn = None
        return self._conn

    def _find_usage_name(self, conn, table: str, species_id: str) -> Optional[str]:
        rows = conn.execute(
            f"SELECT DISTINCT pokemon_name FROM {table} WHERE snapshot_id=?",
            (self._snapshot_id,)).fetchall()
        for (name,) in rows:
            if _slug_to_id(name) == species_id:
                return name
        return None

    @lru_cache(maxsize=256)
    def predict(self, species_id: str) -> dict:
        """種族IDから予測セットを返す。

        戻り値: {"moves": [(move_id, pct)], "items": [(item_id, pct)],
                 "abilities": [(ability_id, pct)], "found": bool}
        """
        empty = {"moves": [], "items": [], "abilities": [], "found": False}
        with self._lock:
            conn = self._connect()
            if conn is None or self._snapshot_id is None:
                return empty

            name = self._find_usage_name(conn, "move_usage", species_id)
            if name is None:
                return empty

            def top(table: str, col: str, limit: int):
                rows = conn.execute(
                    f"SELECT {col}, usage_percent FROM {table} "
                    f"WHERE snapshot_id=? AND pokemon_name=? "
                    f"ORDER BY usage_percent DESC LIMIT ?",
                    (self._snapshot_id, name, limit)).fetchall()
                total = sum(r[1] for r in rows) or 1.0
                return [(_slug_to_id(r[0]), round(100.0 * r[1] / total, 1))
                        for r in rows]

            return {
                "moves": top("move_usage", "move_name", 8),
                "items": top("item_usage", "item_name", 4),
                "abilities": top("ability_usage", "ability_name", 3),
                "found": True,
            }


_predictor = None


def get_predictor() -> SetPredictor:
    global _predictor
    if _predictor is None:
        _predictor = SetPredictor()
    return _predictor
