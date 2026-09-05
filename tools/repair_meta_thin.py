"""過去スナップショットの meta_sets を、技データ欠落の引き継ぎ保護つきで作り直す。

championsbattledata の書き出しが 2026-08-18 18:12Z 生成分から、日替わりの
28〜48種で技の上位5行を落としていた (docs/incidents/reports/
2026-08-19-cbd-missing-top-move-rows.md)。build_meta の引き継ぎ保護
(META_THIN_MOVE_PCT) は最新スナップショットにしか効かないので、過去分を
古い順に作り直す (古い順でないと引き継ぎ元が欠落のまま)。

    python -m tools.repair_meta_thin --from 9 --to 26 --skip 24 [--dry-run]

META_PIN (評価軸) のスナップショットは --skip で必ず除外する。
"""
from __future__ import annotations

import argparse

from champions_agent.data import database as db
from champions_agent.data.build_meta import build_meta_sets, is_thin_moveset
from champions_agent.env.ranked_teams import pinned_meta_snapshot_id


def junk_species(conn, snapshot_id: int) -> list[str]:
    """寄せ集め型の種: 代表型の move1 が当日の生データに存在し、その採用率が閾値未満。

    引き継ぎ後の型は move1 (例: じしん) が当日の生データに無い (上位行が落ちている)
    ので、この指標では健全側に数えられる。生データの最多を見る is_thin_moveset とは
    区別すること (生データは修復しないので変わらない)。
    """
    rows = conn.execute(
        """SELECT m.pokemon_name,
                  (SELECT usage_percent FROM move_usage u
                    WHERE u.snapshot_id=m.snapshot_id AND u.pokemon_name=m.pokemon_name
                      AND u.move_name=m.move1) AS p1
           FROM meta_sets m WHERE m.snapshot_id=? AND m.move1 IS NOT NULL""",
        (snapshot_id,)).fetchall()
    return [r["pokemon_name"] for r in rows
            if r["p1"] is not None and is_thin_moveset(r["p1"])]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--from", dest="start", type=int, required=True)
    ap.add_argument("--to", dest="end", type=int, required=True)
    ap.add_argument("--skip", type=int, nargs="*", default=[])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    pin = pinned_meta_snapshot_id()
    skip = set(args.skip)
    if pin is not None and pin not in skip:
        print(f"[repair_meta_thin] META_PIN={pin} は評価軸なので自動的に除外します")
        skip.add(pin)
    for sid in range(args.start, args.end + 1):
        if sid in skip:
            print(f"snapshot {sid}: skip")
            continue
        with db.get_connection() as conn:
            exists = conn.execute(
                "SELECT COUNT(*) FROM meta_sets WHERE snapshot_id=?", (sid,)).fetchone()[0]
            before = junk_species(conn, sid) if exists else []
        if not exists:
            print(f"snapshot {sid}: meta_sets なし (skip)")
            continue
        if args.dry_run:
            print(f"snapshot {sid}: 寄せ集め型 {len(before)}種 (dry-run)")
            continue
        build_meta_sets(snapshot_id=sid)
        with db.get_connection() as conn:
            after = junk_species(conn, sid)
        print(f"snapshot {sid}: 寄せ集め型 {len(before)} → {len(after)}種")


if __name__ == "__main__":
    main()
