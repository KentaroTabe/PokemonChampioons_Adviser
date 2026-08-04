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


_CHAMPIONS_IDS = None


def champions_usable(species_id: str) -> bool:
    """championsの使用率データに存在する種族か (SV専用ポケモンを除外)。

    championsのpokemon_usage/teammate_usageに一度でも現れた種族IDの集合で判定。
    """
    global _CHAMPIONS_IDS
    if _CHAMPIONS_IDS is None:
        db = sqlite3.connect(str(DB_PATH))
        flt = _champions_filter(db)
        ids = set()
        for table in ("pokemon_usage", "teammate_usage"):
            col = "pokemon_name"
            for (name,) in db.execute(
                    f"SELECT DISTINCT {col} FROM {table} WHERE {flt}"):
                ids.add(_to_id(name))
            if table == "teammate_usage":
                for (name,) in db.execute(
                        f"SELECT DISTINCT teammate_name FROM {table} WHERE {flt}"):
                    ids.add(_to_id(name))
        db.close()
        _CHAMPIONS_IDS = ids
    return _to_id(species_id) in _CHAMPIONS_IDS


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


def _my_views(resolver, party_ja: list | None = None) -> list:
    """診断対象の自分側ビューを作る。

    my_team.json は「もっと見る」自動登録の蓄積で7体以上残る (旧チームは
    型ライブラリとして保持)。全登録を診断すると「13体のパーティ」の
    ような結果になるため、現在のパーティ6体に絞る:
    party_ja (対戦から判明した実際の6体) 優先、無ければ登録から推定。
    """
    team = load_my_team()
    if party_ja:
        picked = {ja: team[ja] for ja in party_ja if ja in team}
        if picked:
            team = picked
    if len(team) > 6:
        try:
            from tools.evaluate_team import current_team_entries
            team = current_team_entries()
        except Exception:
            pass
    out = []
    for ja, entry in team.items():
        r = resolver.resolve_species(ja, cutoff=0.9)
        if not r:
            continue
        base_sid = r[1]
        sid, sp = base_sid, get_dex().species(base_sid)
        b = get_my_build(ja)
        if not (sp and b):
            continue
        # メガストーン所持なら診断はメガ後の性能で行う (1戦1回の制約は
        # あるが、1v1対面の実力はメガ前提が実態に近い。ライチュウ等の
        # メガ進化前の種族値で過小評価される問題の対策)
        item_ja = b.get("item_ja") or ""
        if item_ja.endswith(("ナイト", "ナイトX", "ナイトY")):
            suffix = "x" if item_ja.endswith("X") else \
                ("y" if item_ja.endswith("Y") else "")
            cands = [base_sid + "mega" + suffix] if suffix else \
                [base_sid + "mega", base_sid + "megax", base_sid + "megay"]
            for cand in cands:   # 表記ゆれ (X/Y未記載の登録) はX優先で補完
                msp = get_dex().species(cand)
                if msp:
                    sid, sp = cand, msp
                    break
        view = MonView(species_id=sid, name_ja=ja, types=sp["types"],
                       base=sp["baseStats"], ev=b["ev"], nature=b["nature"])
        moves = []
        for mj in (entry.get("技") or entry.get("moves") or []):
            rm = resolver.resolve(mj, "moves", cutoff=0.8)
            if rm:
                moves.append(rm[1])
        if not moves:
            # 技の使用率はベース種族で引く (メガIDはDB未収録で空になる)
            moves = [m for m, _ in
                     get_predictor().predict(base_sid)["moves"][:4]]
        out.append((ja, view, moves))
    return out


def team_advice(resolver, top_n: int = 15, n_suggest: int = 5,
                party_ja: list | None = None) -> Optional[dict]:
    """構築診断+改善案。my_teamが未登録なら None。

    party_ja: 今対戦した実際の6体 (日本語名)。渡されればそれだけを診断する
    """
    mine = _my_views(resolver, party_ja=party_ja)
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
    from advisor.endgame import duel_score
    holes = []
    weak_mons = []   # 勝てるが余裕が小さい相手 (対面が薄い順)
    my_ids_set = {mv.species_id for _j, mv, _m in mine}
    for sid, usage, ov, omoves in meta_views:
        best = None
        for _j, mv, mmoves in mine:
            sc = duel_score(mv, 1.0, mmoves, ov, 1.0, omoves)
            if sc is not None and (best is None or sc > best):
                best = sc
        if best is None or best <= 0:
            holes.append({"name": ov.name_ja, "usage": round(usage, 1),
                          "sid": sid})
        elif sid not in my_ids_set and best < 0.4:
            weak_mons.append({"name": ov.name_ja, "margin": round(best, 2),
                              "usage": round(usage, 1), "sid": sid})
    weak_mons.sort(key=lambda w: w["margin"])
    weak_mons = weak_mons[:5]

    # 苦手な構築の傾向 (穴+薄い相手の共起相方)
    weak_teammates = []
    try:
        from tools.generate_teams import cooccurrence as _cooc
        threat_ids = [h["sid"] for h in holes] + \
                     [w["sid"] for w in weak_mons[:3]]
        tscore = {}
        for tid in threat_ids:
            for cand, w in _cooc(tid).items():
                if cand not in my_ids_set:
                    tscore[cand] = tscore.get(cand, 0) + w
        weak_teammates = [species_ja_name(s) for s, _w in
                          sorted(tscore.items(), key=lambda kv: -kv[1])[:4]]
    except Exception:
        pass

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
            "swaps": swaps, "weak_mons": weak_mons,
            "weak_teammates": weak_teammates, "meta_n": len(meta_views)}


def _swap_suggestions(mine: list, meta_views: list,
                      suggestions: list, top_k: int = 3) -> list:
    """各メンバー×補完候補の入れ替えで、メタカバレッジが最も改善する組を返す。

    事前に「各ポケモン vs メタ各体」の勝敗ベクトルを計算しておき、
    集合演算でカバレッジ差分を高速に評価する。
    """
    from advisor.endgame import duel_score

    def margin_vec(view, moves):
        # 各メタへの対面スコア (勝ち=正)。カバレッジだけでなく余裕も見る
        return [duel_score(view, 1.0, moves, ov, 1.0, om) or -1.0
                for _s, _u, ov, om in meta_views]

    mine_vecs = [(ja, margin_vec(v, m)) for ja, v, m in mine]
    cand_views = []
    for s in suggestions:
        for sid, _u in meta_top(40):
            if species_ja_name(sid) == s["name"]:
                cv, cm = build_meta_view(sid)
                if cv is not None:
                    cand_views.append((s["name"], margin_vec(cv, cm)))
                break

    n = len(meta_views)
    THR = 0.4   # この余裕未満は「苦手」とみなす

    def team_ok(vecs, i):
        return max((vec[i] for vec in vecs), default=-1.0) >= THR

    base_ok = sum(1 for i in range(n) if team_ok([v for _, v in mine_vecs], i))
    results = []
    for out_i, (out_ja, _out_vec) in enumerate(mine_vecs):
        rest = [vec for j, (_, vec) in enumerate(mine_vecs) if j != out_i]
        for in_ja, in_vec in cand_views:
            new_vecs = rest + [in_vec]
            ok = sum(1 for i in range(n) if team_ok(new_vecs, i))
            delta = ok - base_ok
            if delta > 0:
                # 新たに「余裕を持って対応できる」ようになった相手
                newly = [meta_views[i][2].name_ja for i in range(n)
                         if team_ok(new_vecs, i)
                         and not team_ok([v for _, v in mine_vecs], i)]
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
        lines.append("✅ メタ上位すべてに1v1で勝てる駒がいます")
    if a.get("weak_mons"):
        wm = " / ".join(f"{w['name']}({w['margin']:+.2f})" for w in a["weak_mons"])
        lines.append(f"△ 苦手なポケモン (対面が薄い順): {wm}")
    if a.get("weak_teammates"):
        lines.append(f"△ 苦手な構築の傾向: {'・'.join(a['weak_teammates'])} 軸")
    for r in a["ohko_risks"]:
        lines.append(f"⚠ {r['name']}は一撃圏が多い: {', '.join(r['hits'])}")
    for tip in a["speed_tips"][:3]:
        lines.append(f"💨 {tip}")
    if a.get("swaps"):
        for sw in a["swaps"][:2]:
            covers = f" (改善: {'・'.join(sw['covers'])})" if sw["covers"] else ""
            lines.append(f"🔁 {sw['out']} → {sw['in']} で"
                         f"苦手/穴を{sw['delta']}体解消{covers}")
    elif a["suggestions"]:
        sug = " / ".join(
            s["name"] + (f" (穴{len(s['covers'])}体に解答)" if s["covers"] else "")
            for s in a["suggestions"])
        lines.append(f"💡 追加候補: {sug}")
    return "\n".join(lines)
