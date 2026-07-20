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
from advisor.team_advice import champions_usable

DB_PATH = (Path(__file__).resolve().parent.parent
           / "champions_agent" / "data" / "db" / "champions.sqlite3")


def _to_id(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def cooccurrence(sid: str) -> dict:
    db = sqlite3.connect(str(DB_PATH))
    # チャンピオンズ実データに限定 (Smogon SVの共起を混ぜない)。
    # champions_usableでchampionsに存在する種族だけを候補にする
    from advisor.team_advice import _champions_filter
    flt = _champions_filter(db)
    out = {}
    for name, w in db.execute(
            f"SELECT teammate_name, SUM(usage_percent) FROM teammate_usage "
            f"WHERE REPLACE(REPLACE(pokemon_name,'-',''),' ','') = ? AND {flt} "
            f"GROUP BY teammate_name", (sid,)):
        cand = _to_id(name)
        if get_dex().species(cand) and champions_usable(cand):
            out[cand] = out.get(cand, 0) + (w or 0)
    db.close()
    return out


def team_score(team: list, meta_views: list) -> float:
    """構築スコア: メタ上位への1v1カバレッジ (主) + 多様性/対面優位 (タイブレーク)。

    カバレッジが飽和 (100%) すると6体目だけ変わる縮退が起きるため、
    タイプ多様性と平均対面スコアを微小重みで加えて候補を多様化する。
    """
    from advisor.endgame import duel_score
    covered = 0
    margin_sum = 0.0
    for _sid, _u, ov, omoves in meta_views:
        best = None
        for _s, v, moves in team:
            sc = duel_score(v, 1.0, moves, ov, 1.0, omoves)
            if sc is not None and (best is None or sc > best):
                best = sc
        if best is not None:
            if best > 0:
                covered += 1
            margin_sum += best   # 各メタへの最良対面 (勝ちの余裕)
    coverage = covered / max(1, len(meta_views))
    # 防御タイプ多様性 (受けの範囲) + 攻撃余裕。カバレッジを主軸に微小加算
    def_types = set()
    for _s, v, _m in team:
        for t in v.types:
            def_types.add(t)
    diversity = len(def_types) / 18.0
    avg_margin = margin_sum / max(1, len(meta_views))
    # メガ枠は1試合1回。メガストーン持ちが複数いると枠を食い合うので減点
    n_mega = sum(1 for _s, v, _m in team if _is_mega_holder(v))
    mega_penalty = 0.15 * max(0, n_mega - 1)
    return coverage + 0.02 * diversity + 0.03 * avg_margin - mega_penalty


def _is_mega_holder(view) -> bool:
    item = (getattr(view, "item", None) or "").lower()
    return item.endswith("ite") and item != "eviolite"


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
    # 表示用にカバレッジ (整数%) と多様性込みスコアを分離
    from tools.team_report import meta_top as _mt
    from tools.team_report import build_meta_view as _bmv
    metas = [(s, u, *(_bmv(s))) for s, u in _mt(15)]
    metas = [(s, u, v, m) for s, u, v, m in metas if v is not None]

    def _coverage(team):
        from advisor.endgame import duel
        c = sum(1 for _s, _u, ov, om in metas
                if any(duel(v, 1.0, mv, ov, 1.0, om) for _, v, mv in team))
        return round(c / max(1, len(metas)), 3)

    candidates = [{"names": [species_ja_name(s) for s, _, _ in team],
                   "coverage": _coverage(team)} for team, cov in results]
    _p(f"候補{len(candidates)}件を生成。実対戦で選抜します…")

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
        weakness = analyze_weakness(best_names)
        out["best"] = {"names": best_names, "win_rate": round(best_rate, 3),
                       "team_text": team_text, "weakness": weakness}
        _p(f"完了。最有力: {'・'.join(best_names)} (勝率{best_rate:.0%})")
    return out


def analyze_weakness(team_names: list, top_n: int = 20) -> dict:
    """構築の苦手ポケモン (対面が不利な相手) と苦手構築傾向を返す。

    - weak_mons: メタ上位でチームの最良対面スコアが低い順のポケモン
    - weak_teammates: 苦手ポケモンとよく組まれる相方 (=苦手な構築の軸)
    """
    from advisor.endgame import duel_score
    resolver = NameResolver()
    team = []
    for ja in team_names:
        r = resolver.resolve_species(ja, cutoff=0.85)
        if not r:
            continue
        v, m = build_meta_view(r[1])
        if v is not None:
            team.append((r[1], v, m))
    metas = [(s, u, *(build_meta_view(s))) for s, u in meta_top(top_n)]
    metas = [(s, u, v, m) for s, u, v, m in metas if v is not None]

    team_ids = {t[0] for t in team}
    scored = []
    for sid, usage, ov, om in metas:
        if sid in team_ids:
            continue   # 自チームと同種 (ミラー) は構築の弱点ではないので除外
        best = None
        for _s, v, mv in team:
            sc = duel_score(v, 1.0, mv, ov, 1.0, om)
            if sc is not None and (best is None or sc > best):
                best = sc
        if best is not None:
            scored.append((best, species_ja_name(sid), sid, usage))
    scored.sort()   # 最良対面スコアが低い=苦手な順
    weak_mons = [{"name": n, "margin": round(b, 2), "usage": round(u, 1)}
                 for b, n, s, u in scored[:5]]

    # 苦手ポケモンの共起相方 = 苦手な構築傾向
    weak_ids = [s for _b, _n, s, _u in scored[:3]]
    teammate_score = {}
    for wid in weak_ids:
        for cand, w in cooccurrence(wid).items():
            if cand not in {t[0] for t in team}:
                teammate_score[cand] = teammate_score.get(cand, 0) + w
    weak_teammates = [species_ja_name(s) for s, _w in
                      sorted(teammate_score.items(), key=lambda kv: -kv[1])[:4]]
    return {"weak_mons": weak_mons, "weak_teammates": weak_teammates}


def generate(core_ja: str, beam: int = 6, n_out: int = 5,
             team_size: int = 6):
    results = generate_candidates(core_ja, beam=beam, n_out=n_out,
                                  team_size=team_size)
    if not results:
        print(f"種族を解決できません/候補生成不可: {core_ja}")
        return []
    metas = [(s, u, *(build_meta_view(s))) for s, u in meta_top(15)]
    metas = [(s, u, v, m) for s, u, v, m in metas if v is not None]

    def _cov(team):
        c = sum(1 for _s, _u, ov, om in metas
                if any(duel(v, 1.0, mv, ov, 1.0, om) for _, v, mv in team))
        return c / max(1, len(metas))

    print(f"# {core_ja}軸の候補構築 (メタ上位{len(metas)}体への1v1カバレッジ+多様性順)\n")
    for i, (team, score) in enumerate(results, 1):
        names = "・".join(species_ja_name(s) for s, _, _ in team)
        print(f"{i}. カバレッジ{_cov(team):.0%} (総合{score:.2f}): {names}")

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
            w = analyze_weakness(best[1])
            if w["weak_mons"]:
                print("⚠ 苦手なポケモン (対面が薄い順): " + " / ".join(
                    f"{m['name']}({m['margin']:+.2f})" for m in w["weak_mons"]))
            if w["weak_teammates"]:
                print(f"⚠ 苦手な構築の傾向: {'・'.join(w['weak_teammates'])} 軸")
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
