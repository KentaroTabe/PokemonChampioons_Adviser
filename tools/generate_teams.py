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


def generate_candidates(core_ja: str, beam: int = 6, n_out: int = 5,
                        team_size: int = 6) -> list:
    """コア軸の候補構築を [(team, coverage)] で返す (表示なし・純粋関数)。

    team は [(species_id, MonView, moves)]。サーバー配信・CLI双方で使う。
    """
    resolver = NameResolver()
    r = resolver.resolve_species(core_ja, cutoff=0.8)
    if not r:
        return []
    core = r[1]
    meta_views = []
    for sid, usage in meta_top(15):
        v, moves = build_meta_view(sid)
        if v is not None:
            meta_views.append((sid, usage, v, moves))

    core_view, core_moves = build_meta_view(core)
    if core_view is None:
        return []
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
        seen, uniq = set(), []
        for team, score in sorted(nxt, key=lambda x: -x[1]):
            key = frozenset(s for s, _, _ in team)
            if key not in seen:
                seen.add(key)
                uniq.append((team, score))
        beams = uniq[:beam]
        if not beams:
            break
    return beams[:n_out]


def generate_report(core_ja: str, n_eval: int = 3, n_battles: int = 12,
                    evaluate: bool = True, progress=None) -> dict:
    """フロントエンド用の構造化レポート。

    progress: 進捗コールバック (message:str) -> None。
    戻り値 {"ok", "core", "candidates":[{names,coverage}],
            "best":{names,win_rate,team_text}, "reason"}
    """
    import asyncio

    def _p(msg):
        if progress:
            try:
                progress(msg)
            except Exception:
                pass

    _p(f"{core_ja}軸の候補を共起データから探索中…")
    results = generate_candidates(core_ja)
    if not results:
        return {"ok": False, "reason": f"{core_ja} の候補を生成できません"}
    candidates = [{"names": [species_ja_name(s) for s, _, _ in team],
                   "coverage": round(cov, 3)} for team, cov in results]
    _p(f"候補{len(candidates)}件を生成。カバレッジ上位を確認中…")

    out = {"ok": True, "core": core_ja, "candidates": candidates, "best": None}
    if not evaluate:
        return out

    from tools.evaluate_team import evaluate_team, build_team_text
    ranked = []
    for i, (team, cov) in enumerate(results[:n_eval], 1):
        names = [species_ja_name(s) for s, _, _ in team]
        _p(f"実対戦評価 {i}/{min(n_eval, len(results))}: "
           f"{'・'.join(names)} を{n_battles}戦…")
        try:
            r = asyncio.run(evaluate_team(names, n_battles=n_battles))
            ranked.append((r["win_rate"], names))
        except Exception as e:
            _p(f"  評価失敗: {e}")
    if ranked:
        best_rate, best_names = max(ranked)
        team_text = ""
        try:
            team_text = build_team_text(best_names)
        except Exception:
            pass
        out["best"] = {"names": best_names, "win_rate": round(best_rate, 3),
                       "team_text": team_text}
        _p(f"完了。最有力: {'・'.join(best_names)} (勝率{best_rate:.0%})")
    return out


def generate(core_ja: str, beam: int = 6, n_out: int = 5,
             team_size: int = 6):
    results = generate_candidates(core_ja, beam=beam, n_out=n_out,
                                  team_size=team_size)
    if not results:
        print(f"種族を解決できません/候補生成不可: {core_ja}")
        return []
    print(f"# {core_ja}軸の候補構築 (メタ上位15体への1v1カバレッジ順)\n")
    for i, (team, score) in enumerate(results, 1):
        names = "・".join(species_ja_name(s) for s, _, _ in team)
        print(f"{i}. カバレッジ{score:.0%}: {names}")

    if "--evaluate" in sys.argv:
        # 上位候補を実対戦で選抜 (Phase 6.4の結線)
        import asyncio
        from tools.evaluate_team import evaluate_team
        n_b = int(sys.argv[sys.argv.index("--battles") + 1]) \
            if "--battles" in sys.argv else 12
        print(f"\n# 実対戦評価 (各{n_b}戦 vs ベンチマーク構築群)")
        ranked = []
        for team, cov in results[:3]:
            names = [species_ja_name(s) for s, _, _ in team]
            try:
                r = asyncio.run(evaluate_team(names, n_battles=n_b))
                ranked.append((r["win_rate"], names))
                print(f"  {'・'.join(names)}: 勝率{r['win_rate']:.0%}")
            except Exception as e:
                print(f"  {'・'.join(names)}: 評価失敗 ({e})")
        if ranked:
            best = max(ranked)
            print(f"\n◎ 最有力: {'・'.join(best[1])} (勝率{best[0]:.0%})")
            # 型 (特性/持ち物/性格/能力ポイント/技) つきで出力
            try:
                from tools.evaluate_team import build_team_text
                print("\n# 最有力構築の型 (meta_sets最有力セット):\n")
                print(build_team_text(best[1]))
            except Exception as e:
                print(f"(型出力失敗: {e})")
    else:
        print("\n実対戦で選抜するには: --evaluate [--battles N] を付ける")
    return results


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    core = args[0] if args else "ガブリアス"
    beam = int(sys.argv[sys.argv.index("--beam") + 1]) if "--beam" in sys.argv else 6
    n = int(sys.argv[sys.argv.index("--n") + 1]) if "--n" in sys.argv else 5
    generate(core, beam=beam, n_out=n)
