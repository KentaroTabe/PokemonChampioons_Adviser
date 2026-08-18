"""
usage_snapshot(使用率統計)の最新スナップショットから、各ポケモンの「代表的な型(セット)」を
組み立て、meta_sets テーブルに保存する。

自己対戦(env/team_builder.py)や相手行動予測の際に、
「このポケモンなら何%の確率でこの持ち物/技構成を使うか」を引くために使用する。
"""
from __future__ import annotations

from champions_agent.config import USAGE_TARGET_FORMAT
from champions_agent.data import database as db


def _top_n(conn, table: str, col: str, snapshot_id: int, pokemon_name: str, n: int) -> list[str]:
    rows = conn.execute(
        f"""
        SELECT {col} AS name, usage_percent
        FROM {table}
        WHERE snapshot_id = ? AND pokemon_name = ?
        ORDER BY usage_percent DESC
        LIMIT ?
        """,
        (snapshot_id, pokemon_name, n),
    ).fetchall()
    return [r["name"] for r in rows]


def _top1(conn, table: str, col: str, snapshot_id: int, pokemon_name: str) -> str | None:
    names = _top_n(conn, table, col, snapshot_id, pokemon_name, 1)
    return names[0] if names else None


def build_meta_sets(fmt: str = USAGE_TARGET_FORMAT, source: str | None = None) -> int:

    """最新スナップショットからmeta_setsを再構築する。戻り値: 生成した行数。"""
    with db.get_connection() as conn:
        # require_meta=False: meta_sets を作る側なので、meta_sets が
        # まだ無い出来たてのスナップショットを対象にする必要がある
        snapshot_id = db.latest_snapshot_id(conn, source=source, fmt=fmt,
                                            require_meta=False)
        if snapshot_id is None:
            raise RuntimeError(
                f"usage_snapshot が見つかりません(source={source}, format={fmt})。"
                "先に data.ingest でingest_usage_statsを実行してください。"
            )

        # 再実行時に同一スナップショットへ行が重複しないよう作り直す
        conn.execute("DELETE FROM meta_sets WHERE snapshot_id = ?", (snapshot_id,))

        pokemon_rows = conn.execute(
            """
            SELECT DISTINCT pokemon_name FROM pokemon_usage WHERE snapshot_id = ?
            """,
            (snapshot_id,),
        ).fetchall()

        inserted = 0
        for row in pokemon_rows:
            name = row["pokemon_name"]

            ability = _top1(conn, "ability_usage", "ability_name", snapshot_id, name)
            item = _top1(conn, "item_usage", "item_name", snapshot_id, name)
            tera = _top1(conn, "tera_usage", "tera_type", snapshot_id, name)
            moves = _top_n(conn, "move_usage", "move_name", snapshot_id, name, 4)
            while len(moves) < 4:
                moves.append(None)

            # 性格とEV配分は別行に分かれて格納されることがある
            # (championsbattledata由来: stat_alignment行=natureのみ / stat_points行=evsのみ)
            # ため、それぞれ「値が入っている行の中の最上位」を独立に取得して合成する
            nature_row = conn.execute(
                """
                SELECT nature FROM spread_usage
                WHERE snapshot_id = ? AND pokemon_name = ? AND nature IS NOT NULL
                ORDER BY usage_percent DESC LIMIT 1
                """,
                (snapshot_id, name),
            ).fetchone()
            evs_row = conn.execute(
                """
                SELECT evs FROM spread_usage
                WHERE snapshot_id = ? AND pokemon_name = ? AND evs IS NOT NULL
                ORDER BY usage_percent DESC LIMIT 1
                """,
                (snapshot_id, name),
            ).fetchone()
            nature = nature_row["nature"] if nature_row else None
            evs = evs_row["evs"] if evs_row else None

            usage_row = conn.execute(
                """
                SELECT usage_percent FROM pokemon_usage
                WHERE snapshot_id = ? AND pokemon_name = ?
                """,
                (snapshot_id, name),
            ).fetchone()
            weight = usage_row["usage_percent"] if usage_row else 0.0

            conn.execute(
                """
                INSERT INTO meta_sets
                    (snapshot_id, pokemon_name, ability_name, item_name, tera_type,
                     nature, evs, move1, move2, move3, move4, weight)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (snapshot_id, name, ability, item, tera, nature, evs,
                 moves[0], moves[1], moves[2], moves[3], weight),
            )
            inserted += 1

        conn.commit()

    print(f"[build_meta] meta_sets 生成完了: snapshot_id={snapshot_id}, rows={inserted}")
    return inserted


if __name__ == "__main__":
    build_meta_sets()
