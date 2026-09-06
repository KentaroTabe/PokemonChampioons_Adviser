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
    "fast": {"race_max": 300, "stress_n": 100, "ablation_n": 100, "max_candidates": 6},
    "medium": {"race_max": 600, "stress_n": 150, "ablation_n": 150, "max_candidates": 8},
    "full": {"race_max": None, "stress_n": None, "ablation_n": None, "max_candidates": None},
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
                 f"favorites={spec.favorites} banned={spec.banned} profile={spec.profile}")
    return spec


def stage_s1_s3(run_dir: Path, spec: BuildSpec, prof: dict, seed: int, top_n: int) -> tuple:
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
    owned = [s for s in spec.owned if s not in set(spec.banned)]
    res = species_features(owned, doc, tv)
    save_features(res, run_dir)
    log(run_dir, f"S3 features: {len(res['features'])} species, missing={res['missing']}")
    return doc, split, tv, res["features"]


def stage_s4(run_dir: Path, spec: BuildSpec, feats: dict, threats: list, legal: set,
             llm_mode: str, threat_weights: Optional[dict] = None) -> list:
    mega = mega_capable_ids(list(feats))
    provider = None
    if llm_mode == "headless":
        from tools.team_build.llm.provider import ClaudeCLIProvider
        provider = ClaudeCLIProvider(run_dir / "llm")
    res = K.generate_concepts(spec, feats, threats, legal, mega, provider=provider,
                              log=lambda m: log(run_dir, m), threat_weights=threat_weights)
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
    (run_dir / "s04_concepts.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    log(run_dir, f"S4 concepts: families={len(res['families'])} rounds={res['rounds']} stop={res['stop_reason']} "
                 f"historical=+{res.get('historical_added', 0)}")
    return res["families"]


def stage_s5(run_dir: Path, spec: BuildSpec, fams: list, feats: dict, threats: list, prof: dict,
             threat_weights: Optional[dict] = None) -> list:
    pool = list(feats)
    banned = set(spec.banned)
    all_lineups = []
    for fam in fams:
        core = tuple(sorted(set(fam["core_ids"]) | set(spec.favorites)))
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
    chosen = C.select_with_quotas(all_lineups, prof["quotas"])
    rest = sorted((l for l in all_lineups if l not in chosen), key=lambda l: -l.score)
    for l in rest:
        if len(chosen) >= prof["n_lineups"]:
            break
        if all(C.distance(l.members, c.members) >= C.MIN_DISTANCE for c in chosen):
            l.tag = "fill"
            chosen.append(l)
    (run_dir / "s05_candidates.json").write_text(
        json.dumps({"n_generated": len(all_lineups), "lineups": [l.to_dict() for l in chosen]},
                   ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    log(run_dir, f"S5 candidates: generated={len(all_lineups)} kept={len(chosen)}")
    return chosen


def stage_s6(run_dir: Path, spec: BuildSpec, lineups: list, snapshot_id: int, tv: dict,
             concept_mega: Optional[dict] = None) -> list:
    """各並びの型を型ライブラリから決め、メガ枠 1 体・クローズ・合法性を通した Showdown 本文を保存する"""
    out_dir = run_dir / "s06_sets"
    out_dir.mkdir(exist_ok=True)
    results = []
    concept_mega = concept_mega or {}
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
            team = S.enforce_single_mega(team, alternatives, keep=concept_mega.get(l.concept))
            team = S.resolve_item_clause(team, item_map)
            text = S.to_showdown_text(team)
            ok, errs = S.validate_team_text(text, spec.regulation)
            cid = f"L{idx:02d}_{l.concept}"
            (out_dir / f"{cid}.txt").write_text(text, encoding="utf-8")
            results.append({"index": idx, "candidate_id": cid, "members": list(l.members), "ok": ok,
                            "errors": errs[:5], "tag": l.tag, "score": round(l.score, 4),
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
    ap.add_argument("--race-steps", default=None, help="racing の戦数段階 (例 100,300,600)。既定は config")
    ap.add_argument("--race-max", type=int, default=None)
    ap.add_argument("--adapt-min", type=int, default=None)
    ap.add_argument("--adapt-chunk", type=int, default=None)
    ap.add_argument("--adapt-max", type=int, default=None)
    ap.add_argument("--stress-n", type=int, default=None)
    ap.add_argument("--ablation-n", type=int, default=None)
    ap.add_argument("--parallel", type=int, default=None)
    ap.add_argument("--max-candidates", type=int, default=None)
    ap.add_argument("--repairs", type=int, default=0)
    ap.add_argument("--registry", default=None, help="registry のディレクトリ (既定 logs/registry)")
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
                           "objective": args.objective, "profile": args.profile})
    spec.profile = args.profile
    prof = PROFILE_DEFAULTS[args.profile]
    manifest = build_manifest(args.run_id, {"profile": args.profile, "llm": args.llm, "seed": args.seed,
                                            "top_n": args.top_n})
    write_manifest(run_dir, manifest)
    log(run_dir, f"run {args.run_id} start (commit {str(manifest.get('git_commit'))[:8]})")
    spec = stage_s0(run_dir, spec, legal)
    doc, split, tv, feats = stage_s1_s3(run_dir, spec, prof, args.seed, args.top_n)
    threats = list(tv.keys())
    threat_weights = {t["id"]: float(t.get("usage") or 0.0) for t in doc["top"] if t["id"] in tv}
    if args.reuse_concepts and (run_dir / "s04_concepts.json").exists():
        fams = json.loads((run_dir / "s04_concepts.json").read_text(encoding="utf-8"))["families"]
        log(run_dir, f"S4 concepts: 既存を再利用 families={len(fams)}")
    else:
        fams = stage_s4(run_dir, spec, feats, threats, legal, args.llm, threat_weights)
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
    lineups = stage_s5(run_dir, spec, fams, feats, threats, prof, threat_weights)
    concept_mega = {f["family_id"]: f.get("mega_id") for f in fams}
    results = stage_s6(run_dir, spec, lineups, doc["snapshot"]["id"], tv, concept_mega)
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


def _measure(run_dir: Path, args) -> None:
    from champions_agent.config import (BUILD_ADAPT_MIN_BATTLES, BUILD_RACE_DEFAULT_MAX, BUILD_RACE_STEPS)
    from tools.team_build import ablation as AB, adapt as AD, racing as R, stress as ST
    from tools.team_build.pipeline import run_measurement
    from tools.team_build.registry import Registry
    steps = tuple(int(x) for x in args.race_steps.split(",")) if args.race_steps else BUILD_RACE_STEPS
    reg = Registry(Path(args.registry)) if args.registry else Registry()
    pm = PROFILE_MEASURE.get(getattr(args, "profile", "full"), PROFILE_MEASURE["full"])
    for key in ("race_max", "stress_n", "ablation_n", "max_candidates"):
        if getattr(args, key, None) is None and pm.get(key) is not None:
            setattr(args, key, pm[key])
    provider = None
    if getattr(args, "llm", "none") == "headless":
        from tools.team_build.llm.provider import ClaudeCLIProvider
        provider = ClaudeCLIProvider(run_dir / "llm")
    run_measurement(run_dir, args.seed, steps=steps, max_battles=args.race_max or BUILD_RACE_DEFAULT_MAX,
                    adapt_min=args.adapt_min or BUILD_ADAPT_MIN_BATTLES, adapt_chunk=args.adapt_chunk or AD.CHUNK,
                    adapt_max=args.adapt_max or AD.MAX_BATTLES, stress_n=args.stress_n or ST.STRESS_BATTLES,
                    ablation_n=args.ablation_n or AB.ABLATION_BATTLES, parallel=args.parallel or R.PARALLEL,
                    repairs=args.repairs, max_candidates=args.max_candidates, registry=reg, llm_provider=provider)


if __name__ == "__main__":
    main()
