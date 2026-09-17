"""ゲーム内使用率順位を過去の champions スナップショットの pokemon_usage.rank に入れる (2026-09-18)。

2026-09-18 まで rank は「pokedb 上位ランカー構築の使用率% の順」だった。championsbattledata の生データ
(champions_agent/data/archive/cbd_<season>_singles_<取得日>.json.gz) の各ポケモンの column_position は
ゲーム内バトルデータの使用率順位で、上位ランカー構築に載らない種 (M-C 序盤のボーマンダ 1 位 / グソクムシャ 5 位)
もそこには載る。各スナップショットの取得日 (fetched_at は UTC → JST の日付) に対応するアーカイブから順位を読み、
usage_scraper.assign_ranks と同じ規則 (順位が無い種は使用率% 順で後置) で rank を置き換える。
usage_percent / meta_sets / move_usage 等は触らない。アーカイブが無いスナップショットは対象外として報告する。

使い方:
    python -m tools.repair_usage_ranks            # 差分の確認のみ (dry-run)
    python -m tools.repair_usage_ranks --apply    # DB を書き換える
"""
from __future__ import annotations

import argparse
import gzip
import json
from datetime import datetime, timedelta
from pathlib import Path

from champions_agent.data import database as db
from champions_agent.data.sources.championsbattledata import ARCHIVE_DIR, parse_battle_rows
from champions_agent.data.sources.usage_scraper import assign_ranks

JST_OFFSET_HOURS = 9
MIN_ROWS = 50            # これ未満の行数のスナップショット (テスト取り込みの残骸) は触らない


def ranks_from_archive(payload: dict) -> dict:
    """cbd 生データ (fetch_all の raw_all) → {species_id: ゲーム内順位}。column_position が無い種は含めない。純粋"""
    out = {}
    for sid, doc in (payload.get("data") or {}).items():
        r = parse_battle_rows(doc).get("ingame_rank")
        if r is not None:
            out[sid] = int(r)
    return out


def new_ranks(existing: list, ingame: dict) -> dict:
    """existing: [(pokemon_name, usage_percent)] → {name: rank}。順位がある種はそれ、無い種は使用率% 降順で後置。純粋"""
    per = {name: {"ingame_rank": ingame.get(name)} for name, _u in existing}
    order = [name for name, _u in sorted(existing, key=lambda kv: (-float(kv[1] or 0.0), kv[0]))]
    return assign_ranks(per, order)


def jst_date(fetched_at: str) -> str:
    """usage_snapshot.fetched_at (sqlite の datetime('now') = UTC) → JST の日付 YYYY-MM-DD"""
    dt = datetime.strptime(str(fetched_at)[:19], "%Y-%m-%d %H:%M:%S")
    return (dt + timedelta(hours=JST_OFFSET_HOURS)).strftime("%Y-%m-%d")


def _archives_by_date() -> dict:
    out: dict = {}
    for p in sorted(ARCHIVE_DIR.glob("cbd_*_singles_*.json.gz")):
        day = p.name.replace(".json.gz", "").split("_")[-1]
        out.setdefault(day, []).append(p)
    return out


def repair(apply: bool = False) -> None:
    archives = _archives_by_date()
    with db.get_connection() as conn:
        snaps = conn.execute("SELECT id, fetched_at FROM usage_snapshot WHERE source LIKE 'championsbattledata%' "
                             "ORDER BY id").fetchall()
        for snap_id, fetched_at in snaps:
            day = jst_date(fetched_at)
            paths = archives.get(day) or []
            if not paths:
                print(f"snap{snap_id} ({day}): cbd アーカイブ無し — 対象外")
                continue
            payload = json.loads(gzip.open(paths[-1], "rt", encoding="utf-8").read())
            ingame = ranks_from_archive(payload)
            if not ingame:
                print(f"snap{snap_id} ({day}): column_position 無し — 対象外")
                continue
            rows = conn.execute("SELECT pokemon_name, usage_percent, rank FROM pokemon_usage WHERE snapshot_id=?",
                                (snap_id,)).fetchall()
            if len(rows) < MIN_ROWS:
                # 数行だけのテスト取り込み (snap3) 等、母集団が別物の残骸は触らない (repair_usage_forms と同じ)
                print(f"snap{snap_id} ({day}): 使用率行が {len(rows)} 行のみ — 対象外")
                continue
            existing = [(r[0], r[1]) for r in rows]
            current = {r[0]: r[2] for r in rows}
            target = new_ranks(existing, ingame)
            changed = [(n, current.get(n), target[n]) for n in target if current.get(n) != target[n]]
            top5 = sorted(((r, n) for n, r in target.items()))[:5]
            print(f"snap{snap_id} ({day}, {paths[-1].name}): 順位あり {len(ingame)} 種 / 変更 {len(changed)} 行 / "
                  f"上位 5: {[n for _r, n in top5]}")
            if apply and changed:
                conn.executemany("UPDATE pokemon_usage SET rank=? WHERE snapshot_id=? AND pokemon_name=?",
                                 [(new, snap_id, n) for n, _old, new in changed])
                conn.commit()
                print(f"  → 書き換え {len(changed)} 行")
    if not apply:
        print("dry-run (書き換えていません)。適用は --apply")


def main() -> None:
    ap = argparse.ArgumentParser(description="pokemon_usage.rank をゲーム内使用率順位 (cbd の column_position) に置き換える")
    ap.add_argument("--apply", action="store_true", help="DB を書き換える (既定は差分表示のみ)")
    args = ap.parse_args()
    repair(apply=args.apply)


if __name__ == "__main__":
    main()
