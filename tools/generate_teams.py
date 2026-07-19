"""構築生成 (Phase 6.4): 共起データのビーム探索 + セルフプレイ評価の準備。

コア (軸にするポケモン) を起点に teammate_usage の共起率でビーム探索し、
候補構築を生成する。各構築は tools/team_report と同じマッチアップ診断で
静的スコアリングし、上位を出力する。

Showdownセルフプレイでの実対戦評価 (最終選抜) は時間がかかるため別途:
    python -m tools.generate_teams コア名 --export out.txt
    -> 出力チームで champions_agent/env/team_builder のチームを差し替えて
       python -m tools.smoke_selfplay 等で勝率評価

使い方:
    python -m tools.generate_teams ガブリアス
    python -m tools.generate_teams ガブリアス --beam 8 --n 5
"""
from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

from advisor.dex import get_dex
from advisor.endgame import duel
from advisor.infer import species_ja_name
from vision.normalize import NameResolver
from tools.team_report import build_meta_view, meta_top

DB_PATH = (Path(__file__).resolve().parent.parent
           / "champions_agent" / "data" / "db" / "champions.sqlite3")


def _to_id(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def cooccurrence(sid: str) -> dict:
    db = sqlite3.connect(str(DB_PATH))
    out = {}
    for name, w in db.execute(
            "SELECT teammate_name, SUM(usage_percent) FROM teammate_usage "
            "WHERE REPLACE(REPLACE(pokemon_name,'-',''),' ','') = ? "
            "GROUP BY teammate_name", (sid,)):
        cand = _to_id(name)
        if get_dex().species(cand):
            out[cand] = out.get(cand, 0) + (w or 0)
    db.close()
    return out


def team_score(team: list, meta_views: list) -> float:
    """静的スコア: メタ上位への1v1カバレッジ (穴1つにつき大減点)"""
    covered = 0
    for _sid, _u, ov, omoves in meta_views:
        if any(duel(v, 1.0, moves, ov, 1.0, omoves)
               for _s, v, moves in team):
            covered += 1
    return covered / max(1, len(meta_views))


def generate(core_ja: str, beam: int = 6, n_out: int = 5,
             team_size: int = 6):
    resolver = NameResolver()
    r = resolver.resolve_species(core_ja, cutoff=0.8)
    if not r:
        print(f"種族を解決できません: {core_ja}")
        return []
    core = r[1]
    meta_views = []
    for sid, usage in meta_top(15):
        v, moves = build_meta_view(sid)
        if v is not None:
            meta_views.append((sid, usage, v, moves))

    core_view, core_moves = build_meta_view(core)
    beams = [([(core, core_view, core_moves)], 0.0)]
    while len(beams[0][0]) < team_size:
        nxt = []
        for team, _score in beams:
            members = {s for s, _, _ in team}
            cooc = {}
            for s, _, _ in team:
                for cand, w in cooccurrence(s).items():
                    if cand not in members:
                        cooc[cand] = cooc.get(cand, 0) + w
            for cand, _w in sorted(cooc.items(), key=lambda kv: -kv[1])[:beam]:
                cv, cmoves = build_meta_view(cand)
                if cv is None:
                    continue
                new_team = team + [(cand, cv, cmoves)]
                nxt.append((new_team, team_score(new_team, meta_views)))
        # スコア上位でビームを絞る (重複チームは除去)
        seen = set()
        uniq = []
        for team, score in sorted(nxt, key=lambda x: -x[1]):
            key = frozenset(s for s, _, _ in team)
            if key not in seen:
                seen.add(key)
                uniq.append((team, score))
        beams = uniq[:beam]
        if not beams:
            break

    results = beams[:n_out]
    print(f"# {core_ja}軸の候補構築 (メタ上位15体への1v1カバレッジ順)\n")
    for i, (team, score) in enumerate(results, 1):
        names = "・".join(species_ja_name(s) for s, _, _ in team)
        print(f"{i}. カバレッジ{score:.0%}: {names}")
    print("\n次ステップ: 上位候補を tools/team_report の形式で診断するか、")
    print("champions_agentのセルフプレイで実対戦評価してください")
    return results


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    core = args[0] if args else "ガブリアス"
    beam = int(sys.argv[sys.argv.index("--beam") + 1]) if "--beam" in sys.argv else 6
    n = int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 5
    generate(core, beam=beam, n_out=n)
