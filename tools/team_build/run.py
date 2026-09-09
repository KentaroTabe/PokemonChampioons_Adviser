"""構築システムのオーケストレータ (S0〜S6、探索段)。測定段 (S7〜S13) は M2 で接続する。

    python -m tools.team_build.run --run-id R --spec spec.json [--profile fast|medium|full] [--llm none|headless|mock]
                                   [--top-n 200] [--seed 20260906] [--width 8] [--n-lineups 16]

run ディレクトリ (logs/build_search/runs/<run_id>/) に manifest / request / meta_snapshot / opponent_families /
species_features / s04_concepts / s05_candidates / s06_sets を保存する。LLM 無し (--llm none) でもルール生成の
コンセプトだけで一気通貫に動く (LLM は探索ヒューリスティックの一つ)。
"""
from __future__ import annotations

import argparse
import json
import random
import time
from pathlib import Path
from typing import Optional

from champions_agent.config import BUILD_POOL_TOP_N
from champions_agent.data import database as db
from tools.team_build import candidates as C
from tools.team_build import concepts as K
from tools.team_build import sets as S
from tools.team_build.features import save_features, species_features
from tools.team_build.manifest import build_manifest, write_manifest
from tools.team_build.meta_snapshot import build_snapshot, save_snapshot, threat_sets
from tools.team_build.opponents import build_split
from tools.team_build.spec import (BuildSpec, legal_species_ids, load_spec, parse_form, save_spec,
                                   validate_spec)

REPO = Path(__file__).resolve().parent.parent.parent
RUNS_DIR = REPO / "logs" / "build_search" / "runs"
# 測定段の規模 (§5 時間軸プロファイル): fast は探索のみ (draft)、medium は候補 Package (provisional)、full は validated
PROFILE_MEASURE = {
    "fast": {"race_max": 300, "stress_n": 100, "ablation_n": 100, "max_candidates": 6, "screen_adapt": 300},
    "medium": {"race_max": 600, "stress_n": 150, "ablation_n": 150, "max_candidates": 8, "screen_adapt": None},
    "full": {"race_max": None, "stress_n": None, "ablation_n": None, "max_candidates": None, "screen_adapt": None},
}
PROFILE_DEFAULTS = {
    "fast": {"width": 6, "n_lineups": 8, "threats": 20, "quotas": {"best": 2, "coverage": 1, "roles": 1, "novelty": 1}},
    "medium": {"width": 8, "n_lineups": 16, "threats": 30, "quotas": {"best": 3, "coverage": 2, "roles": 2, "synergy": 1, "novelty": 2}},
    "full": {"width": 10, "n_lineups": 24, "threats": 30, "quotas": {"best": 4, "coverage": 3, "roles": 3, "synergy": 2, "novelty": 3}},
}


def log(run_dir: Path, msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with (run_dir / "run.log").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def mega_capable_ids(owned: list) -> set:
    """メガ石を持てる所持種 (図鑑に <id>mega/megax/megay がある)"""
    from advisor.dex import get_dex
    dex = get_dex()
    return {s for s in owned if any(dex.species(s + suf) for suf in ("mega", "megax", "megay"))}


def stage_s0(run_dir: Path, spec: BuildSpec, legal: set) -> BuildSpec:
    problems = validate_spec(spec, legal)
    if problems:
        raise SystemExit("BuildSpec の問題: " + "; ".join(problems))
    save_spec(spec, run_dir)
    log(run_dir, f"S0 spec: objective={spec.objective} style={spec.style} owned={len(spec.owned)} "
                 f"favorites={spec.favorites} banned={spec.banned} rules={spec.rules} profile={spec.profile}")
    return spec


def stage_s1_s3(run_dir: Path, spec: BuildSpec, prof: dict, seed: int, top_n: int,
                extra_threats: Optional[list] = None) -> tuple:
    doc = build_snapshot()
    save_snapshot(doc, run_dir)
    log(run_dir, f"S1 meta snapshot id={doc['snapshot']['id']} top={len(doc['top'])} "
                 f"local_battles={doc['local_meta'].get('n_battles')}")
    from champions_agent.env.ranked_teams import pinned_meta_snapshot_id
    split = build_split(run_dir.name, run_dir, seed=seed, top_n=top_n,
                        meta_snapshot_id=pinned_meta_snapshot_id())
    log(run_dir, f"S2 opponents: teams={split['n_teams']} families={split['n_families']} "
                 f"split={split['summary']} sealed={split['sealed_id']}")
    tv = threat_sets(doc, prof["threats"])
    # セッションの相手など、脅威リストに無い種を代表型で足す (改善案の測定: 動きづらかった相手を脅威に含める)
    added = []
    if extra_threats:
        from tools.team_build.interaction import view_from_set
        with db.get_connection() as conn:
            for sid in extra_threats:
                if sid in tv:
                    continue
                rep = S.representative_set(conn, doc["snapshot"]["id"], sid)
                if rep is None:
                    continue
                try:
                    tv[sid] = view_from_set(sid, {"item": rep.item, "ability": rep.ability, "nature": rep.nature,
                                                  "evs": rep.evs, "moves": list(rep.moves)})
                    added.append(sid)
                except Exception:
                    continue
        log(run_dir, f"S3 extra threats: +{len(added)} {added}")
    owned = [s for s in spec.owned if s not in set(spec.banned)]
    res = species_features(owned, doc, tv)
    save_features(res, run_dir)
    log(run_dir, f"S3 features: {len(res['features'])} species, missing={res['missing']}")
    return doc, split, tv, res["features"]


def rule_context(run_dir: Path, spec: BuildSpec, feats: dict, snapshot_id: int) -> Optional[dict]:
    """S0 の rules → 種ごとの判定材料 (代表型 + 図鑑 + champions mod の learnset) → 設置役/エースの集合。
    規則が無ければ None。満たせる個体がプールに足りなければ止まる (勝手に緩めない)"""
    if not spec.rules:
        return None
    from advisor.dex import get_dex
    from tools.check_mega_items import mega_stones
    from tools.team_build import rules as RU
    from tools.team_build.learnsets import can_learn
    dex = get_dex()
    stone_form = {item_id: sid for (sid, _n, _r, item_id) in mega_stones() if item_id}
    moves_needed = sorted({RU.RULES[n]["setter_move"] for n in spec.rules})
    infos = {}
    with db.get_connection() as conn:
        for sid in feats:
            rep = S.representative_set(conn, snapshot_id, sid)
            if rep is None:
                continue
            form = stone_form.get(rep.item or "", sid)          # メガ石を持つ型はメガ後の種族値・タイプで判定
            sp = dex.species(form) or dex.species(sid) or {}
            bs = sp.get("baseStats") or {}
            infos[sid] = RU.RuleInfo(sid, int(bs.get("spe") or 0), int(bs.get("def") or 0),
                                     tuple(sp.get("types") or ()), rep.ability or "", rep.item or "",
                                     {m: can_learn(sid, m) for m in moves_needed})
    ctx = RU.build_context(spec.rules, infos)
    (run_dir / "s03_rules.json").write_text(json.dumps(
        {"rules": ctx["llm"],
         "infos": {s: {"spe": i.spe, "def": i.dfn, "types": list(i.types), "ability": i.ability, "item": i.item,
                       "can_learn": i.can_learn} for s, i in infos.items()}},
        ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for p in ctx["per_rule"]:
        log(run_dir, f"S3 rule {p['name']}: 設置役={sorted(p['setters'])} エース={sorted(p['aces'])}")
        if not p["setters"] or not p["aces"] or len(p["setters"] | p["aces"]) < 2:
            raise SystemExit(f"規則 {p['name']} を満たす個体がプールに足りない "
                             f"(設置役 {sorted(p['setters'])} / エース {sorted(p['aces'])})")
    return ctx


def stage_s4(run_dir: Path, spec: BuildSpec, feats: dict, threats: list, legal: set,
             llm_mode: str, threat_weights: Optional[dict] = None, rule_ctx: Optional[dict] = None) -> list:
    mega = mega_capable_ids(list(feats))
    provider = None
    if llm_mode == "headless":
        from tools.team_build.llm.provider import ClaudeCLIProvider
        provider = ClaudeCLIProvider(run_dir / "llm")
    res = K.generate_concepts(spec, feats, threats, legal, mega, provider=provider,
                              log=lambda m: log(run_dir, m), threat_weights=threat_weights,
                              rules=(rule_ctx or {}).get("llm"))
    # 候補源の多系統化: 上位実構築の所持部分集合 (historical) も軸として加える
    try:
        from tools.team_build.opponents import pool_teams
        from tools.team_build.sources import historical_cores
        teams, _ = pool_teams()
        hist = historical_cores(teams, set(feats))
        hist = [h for h in hist if not any(set(h["core_ids"]) <= set(spec.banned) for _ in [0])]
        before = len(res["families"])
        res["families"] = K.cluster_concepts([dict(f) for f in res["families"]] + hist)
        res["historical_added"] = len(res["families"]) - before
    except Exception as e:
        res["historical_error"] = repr(e)
    if rule_ctx:
        # 規則の軸 (設置役 × エース) を先頭に置く (系統の代表になる)。LLM/ルール/historical の軸で規則を満たさない
        # ものは S5 で機械的に落ちる
        from tools.team_build import rules as RU
        cores = RU.context_cores(rule_ctx, feats, threats)
        before = len(res["families"])
        res["families"] = K.cluster_concepts(cores + [dict(f) for f in res["families"]])
        res["rule_cores_added"] = len(res["families"]) - before
    (run_dir / "s04_concepts.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    log(run_dir, f"S4 concepts: families={len(res['families'])} rounds={res['rounds']} stop={res['stop_reason']} "
                 f"historical=+{res.get('historical_added', 0)} rules=+{res.get('rule_cores_added', 0)}")
    return res["families"]


def incumbent_branch(ids: list, pool: list, feats: dict, threats: list, style: str, banned: set, favorites: set,
                     threat_weights: Optional[dict], n_neighbors: int) -> tuple:
    """現行チーム (登録 6 体の id) とその近傍 (1 枠入替) を作る (純粋)。

    戻り値: (現行の Lineup or None, 近傍の Lineup 列)。現行は全員が所持プールにあり除外に当たらないときだけ。
    近傍は固定枠を入替えず、除外種を入れず、入替枠を散らして (枠ごとの最良を先に) スコア順に n_neighbors まで
    """
    ids = sorted(ids)
    if len(ids) != 6 or any(s not in feats for s in ids):
        return None, []
    inc = None
    if not (set(ids) & set(banned)):
        sc, parts = C.lineup_score(tuple(ids), feats, threats, style, threat_weights=threat_weights)
        inc = C.Lineup(tuple(ids), "INC", sc, parts, tag="incumbent")
    per_slot = {}
    banned_in = set(ids) & set(banned)      # 現行に除外種がいれば、その枠の入替だけが有効な近傍
    for out_m in ids:
        if out_m in favorites or (banned_in and out_m not in banned_in):
            continue
        for in_m in pool:
            if in_m in ids or in_m in banned or in_m not in feats:
                continue
            new = tuple(sorted([in_m if m == out_m else m for m in ids]))
            sc, parts = C.lineup_score(new, feats, threats, style, threat_weights=threat_weights)
            per_slot.setdefault(out_m, []).append(C.Lineup(new, "INC", sc, parts, tag="incumbent_mut"))
    for lst in per_slot.values():
        lst.sort(key=lambda x: -x.score)
    # 枠ごとの最良を先に (入替枠を散らす)、残りはスコア順
    first = sorted((lst[0] for lst in per_slot.values() if lst), key=lambda x: -x.score)
    rest = sorted((l for lst in per_slot.values() for l in lst[1:]), key=lambda x: -x.score)
    neigh, seen = [], set()
    for l in first + rest:
        if l.members in seen:
            continue
        seen.add(l.members)
        neigh.append(l)
        if len(neigh) >= n_neighbors:
            break
    return inc, neigh


def registered_team() -> tuple:
    """config/my_team.json の登録チーム → (Showdown 本文, 種族 id 列, メガ軸の種族 id)。読めなければ ("", [], None)"""
    try:
        from tools.evaluate_team import build_myteam_text
        from tools.team_build.opponents import parse_team_text
        text = build_myteam_text()
        ids, mega = parse_team_text(text)
        return text, sorted(ids), mega
    except Exception:
        return "", [], None


def stage_s5(run_dir: Path, spec: BuildSpec, fams: list, feats: dict, threats: list, prof: dict,
             threat_weights: Optional[dict] = None, only_incumbent: bool = False,
             n_neighbors: Optional[int] = None, rule_ctx: Optional[dict] = None) -> list:
    pool = list(feats)
    banned = set(spec.banned)
    all_lineups = []
    if only_incumbent:
        # 改善案の測定: 探索はせず、現行チーム + 近傍 (セッションの相手を重みに含めた被覆で選ぶ) だけを候補にする
        from champions_agent.config import BUILD_INCUMBENT_NEIGHBORS
        _text, reg_ids, _mega = registered_team()
        inc, neigh = incumbent_branch(reg_ids, pool, feats, threats, spec.style, banned, set(spec.favorites),
                                      threat_weights, n_neighbors or BUILD_INCUMBENT_NEIGHBORS) if reg_ids else (None, [])
        chosen = ([inc] if inc else []) + neigh
        (run_dir / "s05_candidates.json").write_text(
            json.dumps({"n_generated": len(chosen), "lineups": [l.to_dict() for l in chosen], "only_incumbent": True},
                       ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        log(run_dir, f"S5 incumbent only: 現行={'あり' if inc else 'なし'} 近傍={len(neigh)} (登録 {len(reg_ids)} 体)")
        return chosen
    for fam in fams:
        # 再利用した系統 (--reuse-concepts) や historical のコアには、今回の
        # 除外種やプール外の種族が残ることがある → コアから外し、空なら捨てる
        core_ids = [c for c in fam["core_ids"] if c in feats and c not in banned]
        if not core_ids:
            continue
        core = tuple(sorted(set(core_ids) | set(spec.favorites)))
        if len(core) > 6:
            continue
        lineups = C.beam_complete(core, pool, feats, threats, spec.style, width=prof["width"],
                                  banned=banned, concept=fam["family_id"], threat_weights=threat_weights)
        all_lineups.extend(lineups)
    # 探索の近傍 (mutation) と交差 (crossover) も候補に加える (LLM は探索ヒューリスティックの一つ)
    try:
        from tools.team_build.sources import crossovers, mutations
        top = sorted(all_lineups, key=lambda l: -l.score)[:8]
        all_lineups.extend(mutations(top, pool, feats, threats, spec.style, banned=banned, keep=set(spec.favorites)))
        all_lineups.extend(crossovers(top, feats, threats, spec.style))
    except Exception as e:
        log(run_dir, f"S5 sources error: {e!r}")
    # 固定枠は hard constraint: mutation / crossover で落ちた並びは候補にしない
    fav = set(spec.favorites)
    if fav:
        all_lineups = [l for l in all_lineups if fav <= set(l.members)]
    if rule_ctx:
        # コンセプト規則も hard constraint: 設置役 + エース (別個体) を含まない並びは候補にしない
        from tools.team_build import rules as RU
        n_before = len(all_lineups)
        all_lineups = [l for l in all_lineups if RU.satisfies(l.members, rule_ctx)]
        log(run_dir, f"S5 rules {rule_ctx['names']}: {n_before} → {len(all_lineups)} 並び")
    chosen = C.select_with_quotas(all_lineups, prof["quotas"])
    rest = sorted((l for l in all_lineups if l not in chosen), key=lambda l: -l.score)
    for l in rest:
        if len(chosen) >= prof["n_lineups"]:
            break
        if all(C.distance(l.members, c.members) >= C.MIN_DISTANCE for c in chosen):
            l.tag = "fill"
            chosen.append(l)
    if rule_ctx:
        # 規則つきの構築では現行チーム枝 (規則を満たさない) を候補にしない。参照 (登録チーム) との比較は測定段で行う
        log(run_dir, "S5 incumbent branch: 規則つきのため入れない (参照との比較は S8a/S8b で行う)")
    else:
        # exploitation pool: 現行チーム (較正点) と近傍を quota とは別枠で必ず入れる (2026-09-07)
        from champions_agent.config import BUILD_INCUMBENT_NEIGHBORS
        _text, reg_ids, _mega = registered_team()
        inc, neigh = incumbent_branch(reg_ids, pool, feats, threats, spec.style, banned, set(spec.favorites),
                                      threat_weights, n_neighbors or BUILD_INCUMBENT_NEIGHBORS) if reg_ids else (None, [])
        existing = {tuple(l.members) for l in chosen}
        branch = [l for l in ([inc] if inc else []) + neigh if tuple(l.members) not in existing]
        chosen = branch + chosen
        log(run_dir, f"S5 incumbent branch: 現行={'あり' if inc else 'なし (除外/プール外/未登録)'} 近傍={len(neigh)} "
                     f"(登録 {len(reg_ids)} 体)")
    (run_dir / "s05_candidates.json").write_text(
        json.dumps({"n_generated": len(all_lineups), "lineups": [l.to_dict() for l in chosen]},
                   ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    log(run_dir, f"S5 candidates: generated={len(all_lineups)} kept={len(chosen)}")
    return chosen


def stage_s6(run_dir: Path, spec: BuildSpec, lineups: list, snapshot_id: int, tv: dict,
             concept_mega: Optional[dict] = None, rule_ctx: Optional[dict] = None) -> list:
    """各並びの型を型ライブラリから決め、メガ枠 1 体・クローズ・合法性を通した Showdown 本文を保存する。
    規則つきなら設置役の型に技を保証する (持ち物・メガ枠の解決後に差し込み、validate-team で合法性を確認)"""
    out_dir = run_dir / "s06_sets"
    out_dir.mkdir(exist_ok=True)
    results = []
    concept_mega = concept_mega or {}
    rule_kw = None
    if rule_ctx:
        from advisor.dex import get_dex
        from advisor.search import SETUP_MOVES
        from tools.team_build.interaction import _mega_stone_ids
        dex = get_dex()
        rule_kw = {"category_of": lambda m: str((dex.move(m) or {}).get("category") or "").lower(),
                   "setup_moves": SETUP_MOVES, "stones": _mega_stone_ids()}
    # 現行チームとその近傍: 登録済み個体は登録の型を使い、メガ枠は登録のメガに合わせる
    reg_text, _reg_ids, reg_mega = registered_team()
    with db.get_connection() as conn:
        item_map = S.item_usage_map(conn, snapshot_id, sorted({m for l in lineups for m in l.members}))
        for idx, l in enumerate(lineups):
            team, alternatives = [], {}
            for sid in l.members:
                cands = S.enumerate_sets(conn, snapshot_id, sid)
                if not cands:
                    team = []
                    break
                ranked = S.rank_sets(cands, tv)
                alternatives[sid] = ranked
                team.append(ranked[0] if ranked else cands[0])
            if not team:
                results.append({"index": idx, "members": list(l.members), "ok": False, "errors": ["型が無い種を含む"]})
                continue
            is_inc = l.tag in ("incumbent", "incumbent_mut") and bool(reg_text)
            keep_mega = (reg_mega if is_inc and reg_mega in l.members else None) or concept_mega.get(l.concept)
            if is_inc:
                # 登録個体を先頭に置き持ち物を登録に合わせる (クローズ解決で新規個体側が譲る)
                team = S.prefer_registered(team, S.registered_items(reg_text))
            team = S.enforce_single_mega(team, alternatives, keep=keep_mega)
            team = S.resolve_item_clause(team, item_map)
            rule_setter, rule_notes = None, []
            if rule_kw and not is_inc:
                from tools.team_build import rules as RU
                team, rule_setter, rule_notes = RU.apply_to_team(team, rule_ctx, alternatives=alternatives, **rule_kw)
            text = S.to_showdown_text(team)
            registered = []
            if is_inc:
                text, registered = S.splice_registered_sets(text, reg_text)
            ok, errs = S.validate_team_text(text, spec.regulation)
            cid = f"L{idx:02d}_{l.concept}"
            (out_dir / f"{cid}.txt").write_text(text, encoding="utf-8")
            results.append({"index": idx, "candidate_id": cid, "members": list(l.members), "ok": ok,
                            "errors": errs[:5], "tag": l.tag, "score": round(l.score, 4),
                            "registered_sets": registered, "rule_setter": rule_setter, "rule_notes": rule_notes,
                            "sets": [{"species": c.species_id, "item": c.item, "nature": c.nature,
                                      "evs": c.evs, "moves": c.moves, "source": c.source,
                                      "coverage": round(c.score, 3), "notes": c.notes} for c in team]})
    (run_dir / "s06_sets.json").write_text(json.dumps(results, ensure_ascii=False, indent=1) + "\n",
                                           encoding="utf-8")
    n_ok = sum(1 for r in results if r["ok"])
    log(run_dir, f"S6 sets: {n_ok}/{len(results)} 並びが合法 (validate-team)")
    return results


def main() -> None:
    ap = argparse.ArgumentParser(description="構築システム (探索段 S0〜S6)")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--spec", help="BuildSpec JSON (request.json 形式 or フォーム形式)")
    ap.add_argument("--favorites", default="", help="固定枠 (カンマ区切り、日本語名可)")
    ap.add_argument("--banned", default="", help="除外 (カンマ区切り)")
    ap.add_argument("--rules", default="",
                    help="コンセプト規則 (カンマ区切り、tools/team_build/rules.py の RULES)。例: psychic_terrain_priority_ace")
    ap.add_argument("--style", default="any")
    ap.add_argument("--objective", default="max_wr")
    ap.add_argument("--profile", choices=list(PROFILE_DEFAULTS), default="fast")
    ap.add_argument("--llm", choices=["none", "headless"], default="none")
    ap.add_argument("--top-n", type=int, default=BUILD_POOL_TOP_N)
    ap.add_argument("--seed", type=int, default=20260906)
    ap.add_argument("--stages", choices=["search", "measure", "all"], default="search",
                    help="search=S0〜S6 / measure=S7〜S13 (既存の run に対して) / all")
    ap.add_argument("--reuse-concepts", action="store_true",
                    help="既存の s04_concepts.json を再利用して S5〜S6 だけやり直す (LLM を呼ばない)")
    ap.add_argument("--only-incumbent", action="store_true",
                    help="探索せず、現行チーム + 近傍だけを候補にする (接続テスト後の改善案の測定)")
    ap.add_argument("--incumbent-neighbors-s5", type=int, default=None,
                    help="S5 で候補に入れる現行チームの近傍の数 (既定 config BUILD_INCUMBENT_NEIGHBORS)")
    ap.add_argument("--threat-weights-file", default=None,
                    help="脅威の追加重み JSON {species_id: 0..1} (セッションの動きづらかった相手)。脅威リストに無い種は代表型で追加")
    ap.add_argument("--race-steps", default=None, help="racing の戦数段階 (例 100,300,600)。既定は config")
    ap.add_argument("--race-max", type=int, default=None)
    ap.add_argument("--adapt-min", type=int, default=None)
    ap.add_argument("--adapt-chunk", type=int, default=None)
    ap.add_argument("--adapt-max", type=int, default=None)
    ap.add_argument("--stress-n", type=int, default=None)
    ap.add_argument("--ablation-n", type=int, default=None)
    ap.add_argument("--parallel", type=int, default=None)
    ap.add_argument("--max-candidates", type=int, default=None,
                    help="S7 で収束まで適応する候補数 (screening 生存の Δ 上位)。screening 自体は全候補")
    ap.add_argument("--screen-adapt", type=int, default=None, help="screening 用 cheap adaptation の戦数 (既定 config)")
    ap.add_argument("--screen-margin", type=float, default=None, help="screening の脱落 margin (既定 config)")
    ap.add_argument("--screen-steps", default=None, help="screening の戦数段階 (例 100,300)")
    ap.add_argument("--screen-max", type=int, default=None)
    ap.add_argument("--candidates", default=None, help="測定するチームを candidate_id のカンマ区切りで限定")
    ap.add_argument("--strata", default=None,
                    help="探索候補を S5 スコア順に並べた順位 (1 始まり) のカンマ区切りで限定 (例 1,2,5,10,20,40)")
    ap.add_argument("--include-incumbent", action="store_true", help="--strata/--candidates に現行チームと近傍を加える")
    ap.add_argument("--incumbent-neighbors", type=int, default=2, help="--include-incumbent で加える近傍の数")
    ap.add_argument("--stop-after", choices=["s08a", "s08b"], default=None,
                    help="この段で止める (ablation 拡張: S8a/S8b の結果だけ取る)")
    ap.add_argument("--s08b-seed-offset", type=int, default=1,
                    help="S8b の相手列 seed のオフセット (既定 1 = S8a と別の列。ablation 拡張では 0 で同一列)")
    ap.add_argument("--s11", choices=["on", "off"], default="off",
                    help="S11 (勝者の SEARCH+SELECTION 再学習)。既定 off = S7 の検証済み checkpoint を最終モデルにする")
    ap.add_argument("--resume", action="store_true",
                    help="途中で落ちた run の続き: S8a の結果と完了済みの適応 (adapt_result.json) を再利用する")
    ap.add_argument("--validate-n", type=int, default=None, help="S7 の checkpoint 検証の戦数 (既定 config)")
    ap.add_argument("--validate-max", type=int, default=None, help="S7 で検証する checkpoint 数 (既定 config)")
    ap.add_argument("--repairs", type=int, default=0)
    ap.add_argument("--registry", default=None, help="registry のディレクトリ (既定 logs/registry)")
    ap.add_argument("--adapt-action", choices=["auto", "on", "off"], default="auto",
                    help="行動方策 adapter (S11b)。auto = full プロファイルのみ")
    ap.add_argument("--action-steps", type=int, default=None, help="adapter の 1 chunk 学習ステップ (既定 100k)")
    ap.add_argument("--action-eval", type=int, default=None, help="adapter の chunk ごとの対応比較戦数 (既定 100)")
    ap.add_argument("--article-file", default=None,
                    help="構築記事の本文 (ユーザーが貼ったもの)。LLM で structured claims にして軸の候補に加える (要 --llm headless)")
    args = ap.parse_args()

    run_dir = RUNS_DIR / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    if args.stages == "measure":
        _measure(run_dir, args)
        return
    legal = legal_species_ids()
    if args.spec:
        raw = json.loads(Path(args.spec).read_text(encoding="utf-8"))
        spec = load_spec(Path(args.spec)) if "schema_version" in raw else parse_form(raw)
    else:
        spec = parse_form({"favorites": args.favorites, "banned": args.banned, "style": args.style,
                           "objective": args.objective, "profile": args.profile, "rules": args.rules})
    spec.profile = args.profile
    prof = PROFILE_DEFAULTS[args.profile]
    manifest = build_manifest(args.run_id, {"profile": args.profile, "llm": args.llm, "seed": args.seed,
                                            "top_n": args.top_n})
    write_manifest(run_dir, manifest)
    log(run_dir, f"run {args.run_id} start (commit {str(manifest.get('git_commit'))[:8]})")
    spec = stage_s0(run_dir, spec, legal)
    session_w = {}
    if args.threat_weights_file:
        session_w = {k: float(v) for k, v in json.loads(Path(args.threat_weights_file).read_text(encoding="utf-8")).items()}
    doc, split, tv, feats = stage_s1_s3(run_dir, spec, prof, args.seed, args.top_n, extra_threats=list(session_w))
    threats = list(tv.keys())
    rule_ctx = rule_context(run_dir, spec, feats, doc["snapshot"]["id"])
    threat_weights = {t["id"]: float(t.get("usage") or 0.0) for t in doc["top"] if t["id"] in tv}
    if session_w:
        # セッションの相手 (正規化した難易度 0..1) を重みに反映: base × (1 + BOOST × w)。脅威リストに無かった種は
        # 使用率の代わりに上位の中央値を base にする
        from champions_agent.config import BUILD_SESSION_THREAT_BOOST
        base_vals = sorted(threat_weights.values())
        median = base_vals[len(base_vals) // 2] if base_vals else 1.0
        for sid, w in session_w.items():
            if sid in tv:
                threat_weights[sid] = threat_weights.get(sid, median) * (1.0 + BUILD_SESSION_THREAT_BOOST * w)
        log(run_dir, f"S3 session threat weights: {len([s for s in session_w if s in tv])} 種に反映 (boost {BUILD_SESSION_THREAT_BOOST})")
    if args.reuse_concepts and (run_dir / "s04_concepts.json").exists():
        fams = json.loads((run_dir / "s04_concepts.json").read_text(encoding="utf-8"))["families"]
        log(run_dir, f"S4 concepts: 既存を再利用 families={len(fams)}")
    else:
        fams = stage_s4(run_dir, spec, feats, threats, legal, args.llm, threat_weights, rule_ctx=rule_ctx)
    if args.article_file and args.llm == "headless" and not args.reuse_concepts:
        try:
            from tools.team_build.articles import claims_to_cores, extract_claims
            from tools.team_build.llm.provider import ClaudeCLIProvider
            claims = extract_claims(ClaudeCLIProvider(run_dir / "llm"), Path(args.article_file).read_text(encoding="utf-8"), legal)
            cores = claims_to_cores(claims, set(feats))
            fams = K.cluster_concepts([dict(f) for f in fams] + cores)
            log(run_dir, f"S4 article: claims={len(claims)} cores={len(cores)} → families={len(fams)}")
        except Exception as e:
            log(run_dir, f"S4 article error: {e!r}")
    lineups = stage_s5(run_dir, spec, fams, feats, threats, prof, threat_weights,
                       only_incumbent=args.only_incumbent, n_neighbors=args.incumbent_neighbors_s5,
                       rule_ctx=rule_ctx)
    concept_mega = {f["family_id"]: f.get("mega_id") for f in fams}
    results = stage_s6(run_dir, spec, lineups, doc["snapshot"]["id"], tv, concept_mega, rule_ctx=rule_ctx)
    from champions_agent.env.ranked_teams import pinned_meta_snapshot_id
    manifest.update({"meta_snapshot": doc["snapshot"]["id"], "meta_pin": pinned_meta_snapshot_id(),
                     "opponent_split": {"sealed_id": split["sealed_id"], "n_teams": split["n_teams"],
                                        "n_families": split["n_families"]},
                     "n_concepts": len(fams), "n_lineups": len(lineups),
                     "n_legal_lineups": sum(1 for r in results if r["ok"])})
    write_manifest(run_dir, manifest)
    log(run_dir, "S0〜S6 完了")
    if args.stages == "all":
        _measure(run_dir, args)


def resolve_candidate_subset(rows: list, candidates: Optional[str], strata: Optional[str],
                             include_incumbent: bool, incumbent_neighbors: int) -> Optional[list]:
    """測定するチームの部分集合を決める (純粋)。None なら全候補。

    candidates: candidate_id のカンマ区切り。strata: 探索候補 (現行枝を除く) を S5 スコア順に並べた 1 始まりの順位の
    カンマ区切り (例 1,2,5,10,20)。include_incumbent なら現行 + 近傍 (incumbent_neighbors 並び) を加える
    """
    if not candidates and not strata:
        return None
    ok_rows = [r for r in rows if r.get("ok")]
    ids = []
    if candidates:
        ids += [c.strip() for c in candidates.split(",") if c.strip()]
    if strata:
        explore = sorted((r for r in ok_rows if (r.get("tag") or "") not in ("incumbent", "incumbent_mut")),
                         key=lambda r: -(r.get("score") or 0.0))
        for tok in strata.split(","):
            tok = tok.strip()
            if not tok:
                continue
            k = int(tok)
            if 1 <= k <= len(explore):
                ids.append(explore[k - 1]["candidate_id"])
    if include_incumbent:
        ids += [r["candidate_id"] for r in ok_rows if r.get("tag") == "incumbent"]
        ids += [r["candidate_id"] for r in ok_rows if r.get("tag") == "incumbent_mut"][:max(0, incumbent_neighbors)]
    seen, out = set(), []
    for c in ids:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _measure(run_dir: Path, args) -> None:
    from champions_agent.config import (BUILD_ADAPT_MIN_BATTLES, BUILD_RACE_DEFAULT_MAX, BUILD_RACE_STEPS)
    from tools.team_build import ablation as AB, adapt as AD, racing as R, stress as ST
    from tools.team_build.pipeline import run_measurement
    from tools.team_build.registry import Registry
    steps = tuple(int(x) for x in args.race_steps.split(",")) if args.race_steps else BUILD_RACE_STEPS
    reg = Registry(Path(args.registry)) if args.registry else Registry()
    rows = json.loads((run_dir / "s06_sets.json").read_text(encoding="utf-8"))
    subset = resolve_candidate_subset(rows, args.candidates, args.strata,
                                      args.include_incumbent, args.incumbent_neighbors)
    if subset is not None:
        log(run_dir, f"measure subset: {len(subset)} チーム {subset}")
    from champions_agent.config import (BUILD_ADAPT_VALIDATE_MAX_CKPTS, BUILD_ADAPT_VALIDATE_N,
                                        BUILD_SCREEN_ADAPT_BATTLES, BUILD_SCREEN_MARGIN, BUILD_SCREEN_MAX,
                                        BUILD_SCREEN_STEPS)
    pm = PROFILE_MEASURE.get(getattr(args, "profile", "full"), PROFILE_MEASURE["full"])
    for key in ("race_max", "stress_n", "ablation_n", "max_candidates", "screen_adapt"):
        if getattr(args, key, None) is None and pm.get(key) is not None:
            setattr(args, key, pm[key])
    screen_steps = tuple(int(x) for x in args.screen_steps.split(",")) if args.screen_steps else BUILD_SCREEN_STEPS
    provider = None
    if getattr(args, "llm", "none") == "headless":
        from tools.team_build.llm.provider import ClaudeCLIProvider
        provider = ClaudeCLIProvider(run_dir / "llm")
    run_measurement(run_dir, args.seed, steps=steps, max_battles=args.race_max or BUILD_RACE_DEFAULT_MAX,
                    adapt_min=args.adapt_min or BUILD_ADAPT_MIN_BATTLES, adapt_chunk=args.adapt_chunk or AD.CHUNK,
                    adapt_max=args.adapt_max or AD.MAX_BATTLES, stress_n=args.stress_n or ST.STRESS_BATTLES,
                    ablation_n=args.ablation_n or AB.ABLATION_BATTLES, parallel=args.parallel or R.PARALLEL,
                    repairs=args.repairs, max_candidates=args.max_candidates, registry=reg, llm_provider=provider,
                    adapt_action=(args.adapt_action == "on" or (args.adapt_action == "auto" and args.profile == "full")),
                    action_steps=args.action_steps or AD.ACTION_CHUNK_STEPS,
                    action_eval=args.action_eval or AD.ACTION_EVAL_BATTLES,
                    screen_adapt=args.screen_adapt or BUILD_SCREEN_ADAPT_BATTLES,
                    screen_margin=BUILD_SCREEN_MARGIN if args.screen_margin is None else args.screen_margin,
                    screen_steps=screen_steps, screen_max=args.screen_max or BUILD_SCREEN_MAX,
                    candidate_ids=subset, stop_after=args.stop_after, s08b_seed_offset=args.s08b_seed_offset,
                    s11=(args.s11 == "on"), validate_n=args.validate_n or BUILD_ADAPT_VALIDATE_N,
                    validate_max=args.validate_max or BUILD_ADAPT_VALIDATE_MAX_CKPTS, resume=args.resume)


if __name__ == "__main__":
    main()
