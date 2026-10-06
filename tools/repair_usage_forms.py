"""フォルム丸めインシデント (2026-09-02) の過去スナップショット修復。

pokedbオープンデータのフォルム表記が全てベース種に丸められていたため、
champions系スナップショットの pokemon_usage (usage_percent/rank) と
teammate_usage が誤っている。各スナップショットの取得日に対応する
アーカイブ (champions_agent/data/archive/pokedb_s*_single_*.json.gz) を
修正済みの _species_id で再集計し、両テーブルを置き換える。

usage_percent と teammate_usage は 100% pokedb集計由来のため、
置き換えは完全な再現になる (cbd由来の meta_sets/move_usage 等は触らない)。
アーカイブが見つからないスナップショットは修復不能として報告する。

使い方:
    python -m tools.repair_usage_forms            # 差分の確認のみ (dry-run)
    python -m tools.repair_usage_forms --apply    # DBを書き換える
"""
from __future__ import annotations

import argparse
import gzip
import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from champions_agent.data.sources.pokedb_opendata import (
    ARCHIVE_DIR, aggregate_teams)

DB_PATH = (Path(__file__).resolve().parent.parent
           / "champions_agent" / "data" / "db" / "champions.sqlite3")
# 擬似使用率の床値 (usage_scraper.fetch_champions_usage と同じ意味)
PSEUDO_USAGE = 0.1


def _archives_by_date() -> dict:
    """取得日 (YYYY-MM-DD) -> [(n_teams, path)] の索引"""
    out: dict = {}
    for p in sorted(ARCHIVE_DIR.glob("pokedb_s*_single_*.json.gz")):
        day = p.stem.replace(".json", "").split("_")[-1]
        out.setdefault(day, []).append(p)
    return out


def _load_agg(path: Path) -> dict:
    payload = json.loads(gzip.open(path, "rt", encoding="utf-8").read())
    return aggregate_teams(payload)


def repair(apply: bool = False) -> None:
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    snaps = cur.execute(
        "SELECT id, fetched_at, number_of_battles FROM usage_snapshot "
        "WHERE source LIKE '%pokedb%' ORDER BY id").fetchall()
    archives = _archives_by_date()
    unfixable = []

    for snap_id, fetched_at, n_battles in snaps:
        rows = cur.execute(
            "SELECT pokemon_name, usage_percent FROM pokemon_usage "
            "WHERE snapshot_id=?", (snap_id,)).fetchall()
        if len(rows) < 50:
            # 5行だけのテスト取り込み (snap3) 等、母集団が別物の残骸は触らない
            print(f"snap{snap_id}: 使用率行が{len(rows)}行のみ — 対象外")
            continue
        existing = {name: pct for name, pct in rows}

        day = str(fetched_at)[:10]
        cands = archives.get(day, [])
        # 同日に複数シーズンのアーカイブがある場合は構築数が一致するものを選ぶ。
        # 同日1件のみなら構築数が違っても採用する (取得は同一ジョブのため)
        agg, approx = None, ""
        for p in cands:
            a = _load_agg(p)
            if n_battles is None or a["n_teams"] == n_battles:
                agg = a
                break
        if agg is None and len(cands) == 1:
            agg = _load_agg(cands[0])
            approx = f" (構築数不一致: DB{n_battles} vs アーカイブ{agg['n_teams']})"
        if agg is None:
            # 同日アーカイブなし: ±2日以内の最近傍で補正 (近似であることを明示)
            near = None
            for delta in (1, -1, 2, -2):
                d2 = str(date.fromisoformat(day) + timedelta(days=delta))
                if archives.get(d2):
                    near = (d2, archives[d2][0])
                    break
            if near:
                agg = _load_agg(near[1])
                approx = f" (同日欠落のため {near[0]} のアーカイブで近似)"
            else:
                unfixable.append((snap_id, day, len(cands)))
                continue

        usage = {sid: u["percent"] for sid, u in agg["usage"].items()}

        # 差分レポート
        changed = []
        for name, old in sorted(existing.items()):
            new = usage.get(name, PSEUDO_USAGE)
            if abs(new - old) >= 0.05:
                changed.append((name, old, new))
        missing = [sid for sid in usage if sid not in existing]
        print(f"snap{snap_id} ({day}, {agg['n_teams']}構築): "
              f"更新{len(changed)}種 / 追加{len(missing)}種{approx}")
        for name, old, new in changed:
            print(f"    {name:<18} {old:5.1f}% -> {new:5.1f}%")
        for sid in missing:
            print(f"    {sid:<18} (行なし) -> {usage[sid]:5.1f}%")

        if not apply:
            continue

        # pokemon_usage: 既存行の usage_percent を置き換え、無い種は追加
        for name in existing:
            cur.execute(
                "UPDATE pokemon_usage SET usage_percent=? "
                "WHERE snapshot_id=? AND pokemon_name=?",
                (usage.get(name, PSEUDO_USAGE), snap_id, name))
        for sid in missing:
            cur.execute(
                "INSERT INTO pokemon_usage (snapshot_id, pokemon_name, "
                "usage_percent, rank) VALUES (?, ?, ?, NULL)",
                (snap_id, sid, usage[sid]))
        # rank を使用率降順で振り直す
        ranked = cur.execute(
            "SELECT pokemon_name FROM pokemon_usage WHERE snapshot_id=? "
            "ORDER BY usage_percent DESC, pokemon_name", (snap_id,)).fetchall()
        for rank, (name,) in enumerate(ranked, start=1):
            cur.execute(
                "UPDATE pokemon_usage SET rank=? "
                "WHERE snapshot_id=? AND pokemon_name=?", (rank, snap_id, name))

        # teammate_usage: スナップショット全体を再集計値で置き換え
        cur.execute("DELETE FROM teammate_usage WHERE snapshot_id=?", (snap_id,))
        for sid, mates in agg["teammates"].items():
            for mate, pct in sorted(mates.items(), key=lambda kv: -kv[1])[:8]:
                cur.execute(
                    "INSERT INTO teammate_usage (snapshot_id, pokemon_name, "
                    "teammate_name, usage_percent) VALUES (?, ?, ?, ?)",
                    (snap_id, sid, mate, pct))
        con.commit()

    if unfixable:
        print("\n修復不能 (対応するアーカイブなし):")
        for snap_id, day, n in unfixable:
            print(f"  snap{snap_id} ({day}) 候補{n}件")
    print("\n適用済み" if apply else "\ndry-run (--apply で書き換え)")
    con.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    repair(apply=args.apply)
