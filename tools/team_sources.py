"""選出データ収集に使える「チーム構築」の在庫を数える。

    python -m tools.team_sources

選出モデルの汎化はチームの「種類」で頭打ちになるため、
どのソースから何チーム引けるかを把握するのに使う。
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path

from champions_agent.env.ranked_teams import ARCHIVE_DIR as ARCHIVE


def _peek(path: Path) -> None:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        payload = json.load(f)
    if isinstance(payload, dict):
        keys = list(payload.keys())
        print(f"  {path.name}: dict keys={keys[:8]}")
        for k in keys:
            v = payload[k]
            if isinstance(v, list):
                print(f"    {k}: {len(v)} 件")
                if v and isinstance(v[0], dict):
                    print(f"      先頭のキー: {list(v[0].keys())[:10]}")
    elif isinstance(payload, list):
        print(f"  {path.name}: list {len(payload)} 件")
        if payload and isinstance(payload[0], dict):
            print(f"    先頭のキー: {list(payload[0].keys())[:10]}")


def main() -> None:
    print("■ アーカイブの中身")
    for p in sorted(ARCHIVE.glob("*.json.gz")):
        _peek(p)

    print("\n■ 構築プール (RankedTeambuilder が使える形)")
    from champions_agent.env import ranked_teams as rt
    ladder_only = rt.build_ranked_teams(include_external=False)
    teams = rt.build_ranked_teams()
    print(f"  ラダー構築  : {len(ladder_only)}")
    print(f"  外部取り込み: {len(teams) - len(ladder_only)}")
    print(f"  合計        : {len(teams)}")
    raw = rt._load_ladder_teams()
    print(f"  (ラダー生データ: {len(raw)})")
    # 種族集合として何種類あるか (並び違い・型違いを同一視)
    uniq = set()
    for t in raw:
        key = tuple(sorted(m.get("pokemon", "") for m in t.get("team", [])))
        uniq.add(key)
    print(f"  種族集合として重複を除くと: {len(uniq)}")


if __name__ == "__main__":
    main()
