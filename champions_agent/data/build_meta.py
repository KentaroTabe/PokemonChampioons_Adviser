"""
usage_snapshot(使用率統計)の最新スナップショットから、各ポケモンの「代表的な型(セット)」を
組み立て、meta_sets テーブルに保存する。

自己対戦(env/team_builder.py)や相手行動予測の際に、
「このポケモンなら何%の確率でこの持ち物/技構成を使うか」を引くために使用する。
"""
from __future__ import annotations

from champions_agent.config import (
    META_SET_CHANGE_WARN, NATURE_ALIGN_MIN_POINTS, OFFENSIVE_ITEM_IDS,
    SPREAD_OFFENSE_MIN_POINTS, USAGE_TARGET_FORMAT)
from champions_agent.data import database as db

# 性格 -> (補正先, 補正元)。無補正性格は含めない (整合チェック不要)
_NATURE_STATS = {
    "lonely": ("atk", "def"), "brave": ("atk", "spe"),
    "adamant": ("atk", "spa"), "naughty": ("atk", "spd"),
    "bold": ("def", "atk"), "relaxed": ("def", "spe"),
    "impish": ("def", "spa"), "lax": ("def", "spd"),
    "timid": ("spe", "atk"), "hasty": ("spe", "def"),
    "jolly": ("spe", "spa"), "naive": ("spe", "spd"),
    "modest": ("spa", "atk"), "mild": ("spa", "def"),
    "quiet": ("spa", "spe"), "rash": ("spa", "spd"),
    "calm": ("spd", "atk"), "gentle": ("spd", "def"),
    "sassy": ("spd", "spe"), "careful": ("spd", "spa"),
}
_STAT_KEYS = ("hp", "atk", "def", "spa", "spd", "spe")


def parse_points(evs: str | None) -> dict | None:
    """"2/0/0/32/0/32" -> {"hp":2, "atk":0, ...} (不正な形式はNone)"""
    if not evs:
        return None
    parts = evs.split("/")
    if len(parts) != 6:
        return None
    try:
        return dict(zip(_STAT_KEYS, (int(p) for p in parts)))
    except ValueError:
        return None


def nature_fits(nature: str | None, evs: str | None,
                move_categories: list) -> bool:
    """性格が配分・技構成と整合するか (純粋関数)。

    - 補正元 (下降) が投資されていたら不整合
    - 補正先 (上昇) は NATURE_ALIGN_MIN_POINTS 以上の投資を要求。
      ただし攻撃/特攻補正は該当分類の技があれば無振りでも整合
      (種族値受けのいじっぱりハッサム等の実在型)。
    - 配分不明・無補正性格は整合扱い (棄却する根拠がない)
    """
    if not nature:
        return True
    stats = _NATURE_STATS.get(str(nature).lower())
    if stats is None:
        return True
    pts = parse_points(evs)
    if pts is None:
        return True
    plus, minus = stats
    if pts[minus] >= NATURE_ALIGN_MIN_POINTS:
        return False
    if pts[plus] >= NATURE_ALIGN_MIN_POINTS:
        return True
    if plus == "atk" and "physical" in move_categories:
        return True
    if plus == "spa" and "special" in move_categories:
        return True
    return False


def _is_offensive_spread(evs: str | None) -> bool:
    pts = parse_points(evs)
    return bool(pts and (pts["atk"] >= SPREAD_OFFENSE_MIN_POINTS
                         or pts["spa"] >= SPREAD_OFFENSE_MIN_POINTS))


def choose_coherent_spread(natures: list, spreads: list,
                           move_categories: list, item: str | None) -> tuple:
    """(性格候補, 配分候補, 技分類, 持ち物) -> 整合する (nature, evs)。

    natures/spreads は (値, 使用率%) を使用率降順で並べたリスト。
    整合する (性格, 配分) ペアのうち使用率の積が最大のものを選ぶ。
    最多同士の独立合成だと、受け型は配分の票が細かく割れるため
    「最多性格 (受け) + 最多配分 (攻撃)」の実在しない縫い合わせになる。
    ペアの積最大化なら「ずぶとい47%+HB9.5%」が「ひかえめ24.5%+CS13.8%」に
    勝ち、受け型が正しく復元される。
    攻撃的持ち物 (OFFENSIVE_ITEM_IDS) のときは攻撃的配分
    (atk/spa >= SPREAD_OFFENSE_MIN_POINTS) に候補を絞る (絞って全滅なら解除)。
    整合ペアが無ければ最多同士 (棄却より実測値を残す方が安全)。
    """
    spread_pool = spreads
    if item and item in OFFENSIVE_ITEM_IDS:
        offensive = [(v, u) for v, u in spreads if _is_offensive_spread(v)]
        if offensive:
            spread_pool = offensive
    best, best_score = None, 0.0
    for nv, nu in natures:
        for ev, eu in spread_pool:
            if not nature_fits(nv, ev, move_categories):
                continue
            score = max(nu, 0.0) * max(eu, 0.0)
            if score > best_score:
                best, best_score = (nv, ev), score
    if best:
        return best
    return ((natures[0][0] if natures else None),
            (spread_pool[0][0] if spread_pool else None))


def count_substantive_changes(prev: dict, new: dict) -> int:
    """2スナップショットのmeta_setsで「実質変化」した種の数を返す。

    prev/new: pokemon_name -> (ability, item, nature, evs, 技のfrozenset)。
    技の並び順だけの違いは変化に数えない (frozensetで吸収)。
    評価軸のチーム中身はここから補完されるため、この数が大きい日は
    ベンチ絶対値の前後比較が壊れる (2026-08-19 インシデント)。
    """
    return sum(1 for k in prev.keys() & new.keys() if prev[k] != new[k])


def _set_signature_rows(conn, snapshot_id: int) -> dict:
    rows = conn.execute(
        """
        SELECT pokemon_name, ability_name, item_name, nature, evs,
               move1, move2, move3, move4
        FROM meta_sets WHERE snapshot_id = ?
        """,
        (snapshot_id,),
    ).fetchall()
    return {
        r["pokemon_name"]: (
            r["ability_name"], r["item_name"], r["nature"], r["evs"],
            frozenset(m for m in (r["move1"], r["move2"],
                                  r["move3"], r["move4"]) if m),
        )
        for r in rows
    }


def _report_axis_drift(conn, snapshot_id: int) -> None:
    """前スナップショットからのセット回転量を日次更新ログに残す。"""
    prev_row = conn.execute(
        "SELECT DISTINCT snapshot_id FROM meta_sets WHERE snapshot_id < ? "
        "ORDER BY snapshot_id DESC LIMIT 1",
        (snapshot_id,),
    ).fetchone()
    if prev_row is None:
        return
    prev_id = prev_row["snapshot_id"]
    changed = count_substantive_changes(
        _set_signature_rows(conn, prev_id),
        _set_signature_rows(conn, snapshot_id))
    mark = "⚠ " if changed >= META_SET_CHANGE_WARN else ""
    print(f"[build_meta] {mark}実質セット変化: snapshot {prev_id}→{snapshot_id} "
          f"で {changed}種 (警告閾値 {META_SET_CHANGE_WARN})。"
          + ("この日を跨ぐベンチ絶対値の比較は不可" if mark else ""))


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

            # 性格とEV配分は別行に分かれて格納される
            # (championsbattledata由来: stat_alignment行=natureのみ /
            #  stat_points行=evsのみ)。それぞれの最多を独立に貼り合わせると
            # 「ずぶとい+CS極振り」のような実在しない型が合成されるため、
            # 持ち物の系統で配分を選び、配分と整合する性格を使用率順に選ぶ
            natures = [(r["nature"], r["usage_percent"]) for r in conn.execute(
                """
                SELECT nature, usage_percent FROM spread_usage
                WHERE snapshot_id = ? AND pokemon_name = ? AND nature IS NOT NULL
                ORDER BY usage_percent DESC
                """, (snapshot_id, name))]
            spreads = [(r["evs"], r["usage_percent"]) for r in conn.execute(
                """
                SELECT evs, usage_percent FROM spread_usage
                WHERE snapshot_id = ? AND pokemon_name = ? AND evs IS NOT NULL
                ORDER BY usage_percent DESC
                """, (snapshot_id, name))]
            move_categories = [r["category"] for r in conn.execute(
                f"""
                SELECT category FROM moves
                WHERE name IN ({",".join("?" * len([m for m in moves if m]))})
                """, [m for m in moves if m])] if any(moves) else []
            nature, evs = choose_coherent_spread(
                natures, spreads, move_categories, item)

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
        _report_axis_drift(conn, snapshot_id)

    print(f"[build_meta] meta_sets 生成完了: snapshot_id={snapshot_id}, rows={inserted}")
    return inserted


if __name__ == "__main__":
    build_meta_sets()
