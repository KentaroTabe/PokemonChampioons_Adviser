"""パーティ診断・改善案 (構造化データ版)。

tools/team_report のロジックを、サーバー配信・フロント表示で使える
構造化データとして返す。試合終了時の自動アドバイスに使う。
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Optional

from advisor.damage import MonView
from advisor.dex import get_dex, calc_stat
from advisor.endgame import duel, _best_dmg
from advisor.ev_infer import SpreadEstimator, _nature_mult
from advisor.infer import species_ja_name
from advisor.my_team import _load as load_my_team, get_my_build
from advisor.sets import get_predictor

DB_PATH = (Path(__file__).resolve().parent.parent
           / "champions_agent" / "data" / "db" / "champions.sqlite3")

_META_CACHE: dict = {}


def _to_id(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _champions_filter(db) -> str:
    """チャンピオンズ実データのスナップショットに限定するWHERE句。

    SmogonのSV (gen9ou) データはフォールバック用で、チャンピオンズに
    存在しないポケモンを含むため、診断/推定では混ぜない。
    チャンピオンズのスナップショットが無い場合のみ全データを使う。
    """
    ids = [str(r[0]) for r in db.execute(
        "SELECT id FROM usage_snapshot WHERE format LIKE 'champions%'")]
    if not ids:
        return "1=1"
    return f"snapshot_id IN ({','.join(ids)})"


def meta_top(n: int = 20) -> list:
    db = sqlite3.connect(str(DB_PATH))
    flt = _champions_filter(db)
    rows = db.execute(
        f"SELECT pokemon_name, MAX(usage_percent) u FROM pokemon_usage "
        f"WHERE {flt} GROUP BY pokemon_name ORDER BY u DESC LIMIT ?",
        (n * 2,)).fetchall()
    db.close()
    dex = get_dex()
    out = []
    for name, u in rows:
        sid = _to_id(name)
        if dex.species(sid):
            out.append((sid, u))
        if len(out) >= n:
            break
    return out


def build_meta_view(sid: str) -> tuple:
    """メタポケモンの (MonView, moves)。DBクエリが重いのでキャッシュする"""
    if sid in _META_CACHE:
        return _META_CACHE[sid]
    est = SpreadEstimator(sid)
    dex = get_dex()
    sp = dex.species(sid)
    if sp is None:
        _META_CACHE[sid] = (None, [])
        return _META_CACHE[sid]
    b = est.best()
    ev, nature, item = {"atk": 252, "spa": 252, "spe": 252}, {}, None
    if b:
        ev, nature, item = b["evs"], _nature_mult(b["nature"]), b["item"]
    view = MonView(species_id=sid, name_ja=species_ja_name(sid),
                   types=sp["types"], base=sp["baseStats"],
                   ev=ev, nature=nature, item=item)
    moves = [m for m, _ in get_predictor().predict(sid)["moves"][:4]]
    _META_CACHE[sid] = (view, moves)
    return _META_CACHE[sid]


def _my_views(resolver) -> list:
    team = load_my_team()
    out = []
    for ja, entry in team.items():
        r = resolver.resolve_species(ja, cutoff=0.9)
        if not r:
            continue
        sid = r[1]
        sp = get_dex().species(sid)
        b = get_my_build(ja)
        if not (sp and b):
            continue
        view = MonView(species_id=sid, name_ja=ja, types=sp["types"],
                       base=sp["baseStats"], ev=b["ev"], nature=b["nature"])
        moves = []
        for mj in (entry.get("技") or entry.get("moves") or []):
            rm = resolver.resolve(mj, "moves", cutoff=0.8)
            if rm:
                moves.append(rm[1])
        if not moves:
            moves = [m for m, _ in get_predictor().predict(sid)["moves"][:4]]
        out.append((ja, view, moves))
    return out


def team_advice(resolver, top_n: int = 15, n_suggest: int = 5) -> Optional[dict]:
    """構築診断+改善案。my_teamが未登録なら None"""
    mine = _my_views(resolver)
    if not mine:
        return None
    meta_views = []
    for sid, usage in meta_top(top_n):
        v, moves = build_meta_view(sid)
        if v is not None:
            meta_views.append((sid, usage, v, moves))

    matchups = []
    for ja, mv, mmoves in mine:
        wins = [ov.name_ja for _s, _u, ov, omoves in meta_views
                if duel(mv, 1.0, mmoves, ov, 1.0, omoves)]
        matchups.append({"name": ja, "wins": len(wins),
                         "total": len(meta_views)})
    holes = []
    for sid, usage, ov, omoves in meta_views:
        if not any(duel(mv, 1.0, mmoves, ov, 1.0, omoves)
                   for _j, mv, mmoves in mine):
            holes.append({"name": ov.name_ja, "usage": round(usage, 1)})

    ohko_risks = []
    for ja, mv, _m in mine:
        hits = [f"{ov.name_ja}({_best_dmg(ov, mv, omoves):.0f}%)"
                for _s, _u, ov, omoves in meta_views
                if _best_dmg(ov, mv, omoves) >= 90]
        if len(hits) >= 3:
            ohko_risks.append({"name": ja, "hits": hits[:4]})

    # 補完候補 (共起 + 穴への解答)
    suggestions = []
    try:
        db = sqlite3.connect(str(DB_PATH))
        flt = _champions_filter(db)
        my_ids = {mv.species_id for _j, mv, _m in mine}
        cooc = {}
        for _j, mv, _m in mine:
            for name, w in db.execute(
                    f"SELECT teammate_name, SUM(usage_percent) FROM teammate_usage "
                    f"WHERE REPLACE(REPLACE(pokemon_name,'-',''),' ','') = ? "
                    f"AND {flt} GROUP BY teammate_name", (mv.species_id,)):
                cooc[_to_id(name)] = cooc.get(_to_id(name), 0) + (w or 0)
        db.close()
        cands = []
        for sid, w in sorted(cooc.items(), key=lambda kv: -kv[1])[:20]:
            if sid in my_ids or not get_dex().species(sid):
                continue
            cv, cmoves = build_meta_view(sid)
            if cv is None:
                continue
            hole_cover = [h["name"] for h in holes
                          for _s, _u, ov, omoves in meta_views
                          if ov.name_ja == h["name"]
                          and duel(cv, 1.0, cmoves, ov, 1.0, omoves)]
            cands.append((w + len(hole_cover) * 5000,
                          {"name": species_ja_name(sid),
                           "covers": hole_cover}))
        suggestions = [c for _s, c in sorted(cands, key=lambda x: -x[0])[:n_suggest]]
    except Exception:
        pass

    # 素早さ調整候補
    speed_tips = []
    for ja, mv, _m in mine:
        base = mv.base.get("spe", 80)
        my_ev = mv.ev.get("spe", 0)
        nat = mv.nature.get("spe", 1.0)
        for _s, _u, ov, _om in meta_views:
            target = ov.stat("spe")
            if mv.stat("spe") <= target and \
                    calc_stat(base, min(252, my_ev + 16), nat) > target:
                speed_tips.append(f"{ja}: +2ポイントで{ov.name_ja} (S{target}) 抜き")
                break

    # 入れ替え提案: 「誰を抜いて誰を入れるか」のカバレッジ差分評価
    swaps = []
    try:
        swaps = _swap_suggestions(mine, meta_views, suggestions)
    except Exception:
        pass

    return {"matchups": matchups, "holes": holes, "ohko_risks": ohko_risks,
            "suggestions": suggestions, "speed_tips": speed_tips,
            "swaps": swaps, "meta_n": len(meta_views)}


def _swap_suggestions(mine: list, meta_views: list,
                      suggestions: list, top_k: int = 3) -> list:
    """各メンバー×補完候補の入れ替えで、メタカバレッジが最も改善する組を返す。

    事前に「各ポケモン vs メタ各体」の勝敗ベクトルを計算しておき、
    集合演算でカバレッジ差分を高速に評価する。
    """
    def win_vec(view, moves):
        return [bool(duel(view, 1.0, moves, ov, 1.0, om))
                for _s, _u, ov, om in meta_views]

    mine_vecs = [(ja, win_vec(v, m)) for ja, v, m in mine]
    cand_views = []
    for s in suggestions:
        # suggestionsは日本語名なのでID逆引き
        for sid, _u in meta_top(40):
            if species_ja_name(sid) == s["name"]:
                cv, cm = build_meta_view(sid)
                if cv is not None:
                    cand_views.append((s["name"], win_vec(cv, cm)))
                break

    n = len(meta_views)
    base_cov = sum(1 for i in range(n) if any(vec[i] for _, vec in mine_vecs))
    results = []
    for out_i, (out_ja, _out_vec) in enumerate(mine_vecs):
        rest = [vec for j, (_, vec) in enumerate(mine_vecs) if j != out_i]
        for in_ja, in_vec in cand_views:
            cov = sum(1 for i in range(n)
                      if any(vec[i] for vec in rest) or in_vec[i])
            delta = cov - base_cov
            if delta > 0:
                newly = [meta_views[i][2].name_ja for i in range(n)
                         if in_vec[i] and not any(vec[i] for vec in rest)
                         and not any(vec[i] for _, vec in mine_vecs)]
                results.append({"out": out_ja, "in": in_ja,
                                "delta": delta, "covers": newly[:3]})
    results.sort(key=lambda r: -r["delta"])
    # 同じin/outの重複を除いて上位を返す
    seen, out = set(), []
    for r in results:
        key = (r["out"], r["in"])
        if key not in seen:
            seen.add(key)
            out.append(r)
        if len(out) >= top_k:
            break
    return out


def format_team_advice(a: Optional[dict]) -> str:
    if not a:
        return "パーティが未登録です (パーティ編集から登録すると診断できます)"
    lines = [f"📊 構築診断 (メタ上位{a['meta_n']}体基準)"]
    mus = " / ".join(f"{m['name']}:勝ち{m['wins']}/{m['total']}"
                     for m in a["matchups"])
    lines.append(f"1v1マッチアップ: {mus}")
    if a["holes"]:
        names = "・".join(f"{h['name']}(使用率{h['usage']})" for h in a["holes"])
        lines.append(f"⚠ 構築の穴 (誰も勝てない): {names}")
    else:
        lines.append("✅ メタ上位すべてに勝てる駒がいます")
    for r in a["ohko_risks"]:
        lines.append(f"⚠ {r['name']}は一撃圏が多い: {', '.join(r['hits'])}")
    for tip in a["speed_tips"][:3]:
        lines.append(f"💨 {tip}")
    if a.get("swaps"):
        for sw in a["swaps"][:2]:
            covers = f" (新規カバー: {'・'.join(sw['covers'])})" if sw["covers"] else ""
            lines.append(f"🔁 {sw['out']} → {sw['in']} で"
                         f"メタカバレッジ+{sw['delta']}{covers}")
    elif a["suggestions"]:
        sug = " / ".join(
            s["name"] + (f" (穴{len(s['covers'])}体に解答)" if s["covers"] else "")
            for s in a["suggestions"])
        lines.append(f"💡 追加候補: {sug}")
    return "\n".join(lines)
