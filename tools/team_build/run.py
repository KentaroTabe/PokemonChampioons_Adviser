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

from champions_agent.config import (BUILD_ACE_MAX_MEGA_STONES, BUILD_ARCHETYPES, BUILD_POOL_SOURCE, BUILD_POOL_TOP_N,
                                    BUILD_SEARCH_MODE)
from champions_agent.data import database as db
from tools.team_build import archetypes as ARCH
from tools.team_build import candidates as C
from tools.team_build import concepts as K
from tools.team_build import sets as S
from tools.team_build.features import save_features, species_features
from tools.team_build.manifest import build_manifest, write_manifest
from tools.team_build.meta_snapshot import build_snapshot, save_snapshot, threat_sets, threat_weight
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
    """メガ石を持てる所持種 (champions_dex の requiredItem の表 (advisor.gimmick.mega_forms) にメガ後のフォルムがある。
    無ければ図鑑の <id>mega/megax/megay/megaz で補う)"""
    from advisor.dex import get_dex
    from advisor.gimmick import mega_forms
    dex = get_dex()
    return {s for s in owned
            if mega_forms(s) or any(dex.species(s + suf) for suf in ("mega", "megax", "megay", "megaz"))}


def _assert_no_banned(run_dir: Path, stage: str, items: list, banned) -> None:
    """hard invariant: 使わないポケモン (config/banned_species.txt) が並びに入っていたら止める
    (S4 の検証・S5 の探索・持ち込みの検査をすり抜けた場合の安全装置)。items = [(id, members)]"""
    from tools.team_build.spec import banned_in_members
    bad = [(cid, banned_in_members(members, banned)) for cid, members in items if banned_in_members(members, banned)]
    if bad:
        log(run_dir, f"{stage} invariant 違反: 使わないポケモンが並びに入っている {bad}")
        raise SystemExit(f"{stage}: 使わないポケモンが並びに入っている {bad} (config/banned_species.txt)")


def ace_stone_violations(results: list, max_stones: int = BUILD_ACE_MAX_MEGA_STONES) -> list:
    """指定エース (メガ) の並びで「エースが石を持ち、石の総数が max_stones 以下」に反する行 → [(candidate_id, 石持ちの列)]。純粋"""
    bad = []
    for r in results:
        ace = r.get("ace")
        if not ace or not r.get("ok"):
            continue
        holders = [s.get("species") for s in (r.get("sets") or []) if S.has_mega_stone(s.get("item"))]
        if ace not in holders or len(holders) > max_stones:
            bad.append((r.get("candidate_id"), holders))
    return bad


def _assert_ace_mega(run_dir: Path, results: list) -> None:
    """hard invariant: 指定エース (メガ) の並びでは、エースが石を持ち、他のメンバーは石を持たない (S6 の出力で検査)"""
    bad = ace_stone_violations(results)
    if bad:
        log(run_dir, f"S6 ace: エースの石の不変条件に反する並び {bad}")
        raise SystemExit(f"S6: 指定エースの並びでエースが石を持てないか、エース以外が石を持つ {bad}")


def stage_s0(run_dir: Path, spec: BuildSpec, legal: set) -> BuildSpec:
    # 使わないポケモン (config/banned_species.txt) は parse_form / apply_banned_file で spec.banned に入っている。
    # 明示の owned (使える候補の限定) からも外す (2026-09-25 ユーザー決定: 所持リストは持たない)
    from tools.team_build.spec import banned_in_members
    removed = banned_in_members(spec.owned, spec.banned)
    spec.owned = [s for s in spec.owned if s not in set(spec.banned)]
    src = spec.banned_source or {}
    log(run_dir, f"S0 banned: {len(spec.banned)} 種 (ファイル {src.get('file_count', '?')} 種 sha={src.get('sha256', '?')}"
                 f"{', 依頼の追加 ' + str(src['extra']) if src.get('extra') else ''}) / 使える種 {len(spec.owned)}"
                 + (f" / owned から除いた {removed}" if removed else ""))
    problems = validate_spec(spec, legal)
    if problems:
        raise SystemExit("BuildSpec の問題: " + "; ".join(problems))
    save_spec(spec, run_dir)
    log(run_dir, f"S0 spec: objective={spec.objective} style={spec.style} owned={len(spec.owned)} "
                 f"favorites={spec.favorites} ace={spec.ace or '-'} banned={spec.banned} rules={spec.rules} "
                 f"profile={spec.profile}")
    return spec


def stage_s1_s3(run_dir: Path, spec: BuildSpec, prof: dict, seed: int, top_n: int,
                extra_threats: Optional[list] = None, pool_source: Optional[str] = None) -> tuple:
    doc = build_snapshot()
    save_snapshot(doc, run_dir)
    ingame_only = (doc.get("threat_source") or {}).get("ingame_only") or []
    log(run_dir, f"S1 meta snapshot id={doc['snapshot']['id']} top={len(doc['top'])} "
                 f"threats={len(doc['threats'])} ゲーム内順位だけで入った種={ingame_only} "
                 f"local_battles={doc['local_meta'].get('n_battles')}")
    from champions_agent.env.ranked_teams import pinned_meta_snapshot_id
    pool_source = pool_source or BUILD_POOL_SOURCE
    # 相手プール: latest = 最新スナップショット (この run の S1 と同じ) の全種から合成 (ピンを使わない)、ranked = POOL_PIN の上位構築
    split = build_split(run_dir.name, run_dir, seed=seed, top_n=top_n,
                        meta_snapshot_id=(None if pool_source == "latest" else pinned_meta_snapshot_id()),
                        pool_source=pool_source)
    log(run_dir, f"S2 opponents: source={pool_source} snapshot={split.get('pool_snapshot')} teams={split['n_teams']} "
                 f"families={split['n_families']} split={split['summary']} sealed={split['sealed_id']}")
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
    usage_w = {t["id"]: threat_weight(t) for t in doc["top"] if t["id"] in tv}
    gen = make_generator(tv, usage_w, doc["snapshot"]["id"])
    res = species_features(owned, doc, tv, custom_sets=custom_sets_of(spec), required_moves=spec.required_moves,
                           generator=gen)
    save_features(res, run_dir)
    msg = f"S3 features: {len(res['features'])} species, missing={res['missing']}"
    if res.get("generated"):
        msg += f" 生成型で補完={res['generated']}"
    if spec.custom_sets:
        msg += f" 型指定={sorted(spec.custom_sets)}"
    if spec.required_moves:
        msg += f" 技指定={dict(spec.required_moves)}"
    log(run_dir, msg)
    return doc, split, tv, res["features"]


def custom_sets_of(spec: BuildSpec) -> dict:
    """spec.custom_sets (request.json の行) → {species_id: SetCandidate}"""
    return {sid: S.candidate_from_row(sid, row) for sid, row in (spec.custom_sets or {}).items()}


def make_generator(tv: dict, threat_weights: Optional[dict], snapshot_id: Optional[int] = None):
    """learnset からの型生成 (gen_sets.generate_for_species) を種ごとにキャッシュする callable。
    BUILD_GEN_SETS=off なら常に空。戻り値の callable は sid → [SetCandidate]、.items(sid) → 持ち物の候補。
    snapshot_id があれば使用率 DB のその種の技の使用率を渡す (補助技は使用率の高いものを先に採る。2026-10-02)"""
    from champions_agent.config import BUILD_GEN_SETS
    from tools.team_build import gen_sets as G
    cache: dict = {}
    usage_cache: dict = {}

    def usage_of(sid: str) -> Optional[dict]:
        if snapshot_id is None:
            return None
        if sid not in usage_cache:
            try:
                with db.get_connection() as conn:
                    usage_cache[sid] = S.move_usage_pct(conn, snapshot_id, sid)
            except Exception:
                usage_cache[sid] = None
        return usage_cache[sid]

    def _run(sid: str, field: Optional[dict] = None) -> dict:
        """field = 並びの場 (設置役が張るフィールド/天候) の前提。種 × 場ごとにキャッシュ"""
        key = (sid, G.field_key(field))
        if key not in cache:
            if BUILD_GEN_SETS == "off":
                cache[key] = {"sets": [], "items": []}
            else:
                try:
                    cache[key] = G.generate_for_species(sid, tv, threat_weights, assumed_field=field,
                                                        move_pct=usage_of(sid))
                except Exception as e:  # 生成できない種は空 (使用率の型だけで進む)
                    cache[key] = {"sets": [], "items": [], "error": repr(e)}
        return cache[key]

    def gen(sid: str, field: Optional[dict] = None) -> list:
        return list(_run(sid, field).get("sets") or [])

    gen.items = lambda sid: list(_run(sid).get("items") or [])      # type: ignore[attr-defined]
    gen.info = _run                                                   # type: ignore[attr-defined]
    return gen


def set_library_kwargs(spec: BuildSpec, sid: str, gen=None, conn=None, snapshot_id: Optional[int] = None,
                       field: Optional[dict] = None) -> dict:
    """型ライブラリに渡す、その種の指定の型・必須技・生成型 (無ければ空)。
    生成型は BUILD_GEN_SETS=auto なら常に、missing なら代表型が無い種だけ渡す。field = 並びの場の前提 (生成型に渡す)"""
    from champions_agent.config import BUILD_GEN_SETS
    custom = custom_sets_of(spec).get(sid)
    required = (spec.required_moves or {}).get(sid)
    generated = None
    if gen is not None and custom is None and BUILD_GEN_SETS != "off":
        if BUILD_GEN_SETS == "auto" or conn is None or S.representative_set(conn, snapshot_id, sid) is None:
            generated = gen(sid, field) or None
    if custom is None and not required and not generated:
        return {}
    out = {"custom": custom, "required": required, "generated": generated}
    if required:
        out.update({"category_of": S.default_category_of(), "setup_moves": S.default_setup_moves()})
    return out


def species_abilities(sid: str) -> tuple:
    """その種が持ちうる特性 id (メガ後のフォルムを含む。champions_dex)。構築の軸の役割判定 (天候始動 等) に使う"""
    import re
    from advisor.gimmick import mega_forms
    from tools.team_build.interaction import _cdex_species
    cdex = _cdex_species()
    out: list = []
    for form in [sid] + list(mega_forms(sid)):
        for v in ((cdex.get(form) or {}).get("abilities") or {}).values():
            a = re.sub(r"[^a-z0-9]", "", str(v).lower())
            if a and a not in out:
                out.append(a)
    return tuple(out)


def rule_context(run_dir: Path, spec: BuildSpec, feats: dict, snapshot_id: int, tv: Optional[dict] = None,
                 gen=None, extra_moves=(), capture: Optional[dict] = None) -> Optional[dict]:
    """S0 の rules → 種ごとの判定材料 (型ライブラリ + 図鑑 + champions mod の learnset) → 設置役/エースの集合。
    エースの火力・技範囲は 代表型 + 単独入替の代替 (使用率 5% 以上) のうち門を通る最初の型で判定し、代表型と違えば
    その型を ace_sets に残して S6 で採用する (2026-09-10 ユーザー決定)。規則が無ければ None。
    満たせる個体がプールに足りなければ止まる (勝手に緩めない)。
    extra_moves / capture (2026-09-18): 構築の軸 (archetypes) も同じ判定材料を使う。capture (dict) を渡すと規則の有無に
    かかわらず種ごとの材料 (RuleInfo + 基準の型の技/持ち物/特性、持ちうる特性、積み技の有無、接地) を capture[sid] に入れる"""
    if not spec.rules and capture is None:
        return None
    from advisor.dex import get_dex
    from champions_agent.config import BUILD_SPEED_SETUP_MOVES, BUILD_TRICK_ROOM_MOVES
    from tools.check_mega_items import mega_stones
    from tools.team_build import rules as RU
    from tools.team_build.features import boost_multiplier
    from tools.team_build.learnsets import can_learn, learnset_of
    dex = get_dex()
    stone_form = {item_id: sid for (sid, _n, _r, item_id) in mega_stones() if item_id}
    moves_needed = sorted({RU.RULES[n]["setter_move"] for n in spec.rules} | set(extra_moves or ()))
    from advisor.search import SETUP_MOVES as _SETUP
    setup_all = tuple(_SETUP) + tuple(m for m in BUILD_SPEED_SETUP_MOVES if m not in _SETUP)

    def move_info(m: str):
        mv = dex.move(m)
        return (mv.get("category"), mv.get("type"), mv.get("power")) if mv else None

    from advisor.damage import FieldView
    from advisor.search import SETUP_MOVES
    from tools.team_build import gen_sets as G
    from tools.team_build.interaction import matrix, view_from_set

    # 規則が前提とする場 (サイコフィールド等): エースの型の採点と生成型の技選択にその場を使う
    rf = RU.rule_field(spec.rules)
    rule_fd = rf if G.has_field(rf) else None
    rule_fv = FieldView(terrain=rf.get("terrain"), weather=rf.get("weather")) if rule_fd else None

    def boosted_coverage(sid: str, c) -> float:
        """積み技・加速特性を持つ型は「1 回積んだ後」の被覆も計算し、大きい方を使う (自己加速型・積み型のエースは
        積む前の被覆が低いのが普通で、そのままでは門を通らない)"""
        stages = RU.setup_stages(c.moves, SETUP_MOVES, boost_multiplier(c.ability, c.item, []))
        if not stages or not tv:
            return float(c.score)
        try:
            view, mv = view_from_set(sid, c.as_row())
            view.boosts = dict(stages)
            rows = matrix({sid: (view, mv)}, tv, fieldv=rule_fv)[sid]
            return max(float(c.score), S.coverage_score(rows))
        except Exception:
            return float(c.score)

    infos, ace_sets = {}, {}
    with db.get_connection() as conn:
        for sid in feats:
            lib = set_library_kwargs(spec, sid, gen, conn, snapshot_id, field=rule_fd)
            rep = S.base_set(conn, snapshot_id, sid, lib.get("custom"), lib.get("required"),
                             lib.get("category_of"), lib.get("setup_moves", ()), generated=lib.get("generated"))
            if rep is None:
                continue
            form = stone_form.get(rep.item or "", sid)          # メガ石を持つ型はメガ後の種族値・タイプで判定
            sp = dex.species(form) or dex.species(sid) or {}
            bs = sp.get("baseStats") or {}
            roles = feats[sid].roles
            cov = feats[sid].coverage
            # 火力・技範囲: 型ライブラリ (代表型 + 単独入替の代替、adj 降順) のうち門を通る最初の型で判定。
            # 代表型が通らず代替が通れば、その型をエースの型として S6 で採用する。採点は規則の場の前提で
            ranked = S.rank_sets(list(S.enumerate_sets(conn, snapshot_id, sid, **lib)), tv, field=rule_fv) if tv else [rep]
            cov_cache: dict = {}

            def cov_of(c, sid=sid):
                key = c.key()
                if key not in cov_cache:
                    cov_cache[key] = boosted_coverage(sid, c)
                return cov_cache[key]

            chosen = RU.pick_ace_set(ranked, bs, move_info, cov_of)
            basis = chosen if chosen is not None else rep
            offense, n_atk, n_types = RU.offense_metrics(basis.moves, bs, move_info)
            cov_mean = (cov_of(chosen) if chosen is not None and tv else ((sum(cov.values()) / len(cov)) if cov else 0.0))
            if chosen is not None and chosen.source != "representative":
                ace_sets[sid] = chosen
            infos[sid] = RU.RuleInfo(sid, int(bs.get("spe") or 0), int(bs.get("def") or 0),
                                     tuple(sp.get("types") or ()), rep.ability or "", rep.item or "",
                                     {m: can_learn(sid, m) for m in moves_needed},
                                     speed_share=float(roles.get("speed", 0.0)),
                                     boost_share=float(roles.get("speed_boost", roles.get("speed", 0.0))),
                                     boost_mult=boost_multiplier(basis.ability, basis.item, basis.moves),
                                     bulk=float(roles.get("bulk", 0.0)),
                                     has_tr=any(m in BUILD_TRICK_ROOM_MOVES for m in rep.moves),
                                     offense=offense, attack_moves=n_atk, attack_types=n_types,
                                     coverage_mean=float(cov_mean))
            if capture is not None:
                ls = learnset_of(sid)
                capture[sid] = {
                    "info": infos[sid], "moves": tuple(basis.moves), "item": basis.item or "", "ability": basis.ability or "",
                    "abilities": species_abilities(sid) or ((basis.ability,) if basis.ability else ()),
                    "usage": float(feats[sid].usage), "mega": bool(feats[sid].mega), "coverage": dict(cov),
                    "has_setup": any(m in setup_all for m in basis.moves) or boost_multiplier(basis.ability, basis.item, basis.moves) > 1.0,
                    "can_setup": any(m in ls for m in setup_all),
                    "grounded": RU.grounded(infos[sid], RU.RULES["psychic_terrain_priority_ace"]),
                }
    if not spec.rules:
        return None
    ctx = RU.build_context(spec.rules, infos)
    ctx["ace_sets"] = ace_sets
    (run_dir / "s03_rules.json").write_text(json.dumps(
        {"rules": ctx["llm"],
         "ace_sets": {s: {"moves": list(c.moves), "item": c.item, "nature": c.nature, "evs": c.evs, "source": c.source,
                          "coverage": round(c.score, 3), "usage_gap": round(c.usage_gap, 3)} for s, c in ace_sets.items()},
         "infos": {s: {"spe": i.spe, "def": i.dfn, "types": list(i.types), "ability": i.ability, "item": i.item,
                       "can_learn": i.can_learn, "speed_share": i.speed_share, "boost_share": i.boost_share,
                       "boost_mult": i.boost_mult, "bulk": i.bulk, "has_tr": i.has_tr, "offense": i.offense,
                       "attack_moves": i.attack_moves, "attack_types": i.attack_types,
                       "coverage_mean": round(i.coverage_mean, 3)} for s, i in infos.items()}},
        ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    for p in ctx["per_rule"]:
        aces = {s: "/".join(t) for s, t in sorted(p["aces"].items())}
        log(run_dir, f"S3 rule {p['name']}: 設置役={sorted(p['setters'])} エース={aces} TR使い={sorted(p['tr_setters'])} "
                     f"代替型で判定={sorted(s for s in ace_sets if s in p['aces'])}")
        usable = {a for a in p["aces"] if any(t in ("fast", "boost") for t in p["aces"][a]) or p["tr_setters"]}
        if not p["setters"] or not usable or len(p["setters"] | usable) < 2:
            raise SystemExit(f"規則 {p['name']} を満たす個体がプールに足りない "
                             f"(設置役 {sorted(p['setters'])} / エース {aces} / TR使い {sorted(p['tr_setters'])})")
    return ctx


def archetype_context(run_dir: Path, spec: BuildSpec, feats: dict, threats: list, threat_weights: Optional[dict],
                      tv: dict, capture: dict, snapshot_id: int) -> dict:
    """構築の軸 (archetypes): 種ごとの判定材料 (rule_context の capture) → Caps、脅威ごとの技・特性・タイプ・素早さ・重み →
    threat_info、軸 × 分岐の 環境適合 / 役割の候補 / core を s03_archetypes.json に保存する"""
    from advisor.dex import get_dex
    from champions_agent.config import BUILD_ARCHETYPE_THREAT_MOVE_PCT
    dex = get_dex()
    caps_by = {}
    for sid, c in capture.items():
        info = c["info"]
        caps_by[sid] = ARCH.Caps(sid, tuple(c["moves"]), c["ability"], c["item"], tuple(c["abilities"]), tuple(info.types),
                                 int(info.spe), dict(info.can_learn), speed_share=info.speed_share, boost_share=info.boost_share,
                                 boost_mult=info.boost_mult, bulk=info.bulk, offense=info.offense, attack_moves=info.attack_moves,
                                 attack_types=info.attack_types, coverage_mean=info.coverage_mean, coverage=dict(c["coverage"]),
                                 usage=c["usage"], mega=c["mega"], grounded=c["grounded"], has_setup=c["has_setup"],
                                 can_setup=c["can_setup"])
    threat_info = {}
    with db.get_connection() as conn:
        for tid, (view, moves) in tv.items():
            mv = set(moves)
            for r in conn.execute("SELECT move_name, usage_percent FROM move_usage WHERE snapshot_id=? AND pokemon_name=?",
                                  (snapshot_id, tid)):
                if float(r[1]) >= BUILD_ARCHETYPE_THREAT_MOVE_PCT:
                    mv.add(r[0])
            ab = {view.ability} if view.ability else set()
            for r in conn.execute("SELECT ability_name, usage_percent FROM ability_usage WHERE snapshot_id=? AND pokemon_name=?",
                                  (snapshot_id, tid)):
                if float(r[1]) >= BUILD_ARCHETYPE_THREAT_MOVE_PCT:
                    ab.add(r[0])
            try:
                rock = float(dex.effectiveness("Rock", list(view.types)))
            except Exception:
                rock = 1.0
            threat_info[tid] = {"moves": sorted(mv), "abilities": sorted(ab), "ability": view.ability,
                                "types": list(view.types), "spe": int((view.base or {}).get("spe") or 0),
                                "weight": float((threat_weights or {}).get(tid, 0.0)), "rock_mult": rock}
    ctx = ARCH.build_context(caps_by, threat_info, list(tv), threat_weights=threat_weights,
                             coverage_fn=lambda core: C.team_coverage(tuple(core), feats, threats, threat_weights),
                             type_mult=dex.effectiveness)
    doc = ARCH.context_to_json(ctx)
    doc["threat_info"] = threat_info
    doc["caps"] = {sid: {"moves": list(c.moves), "ability": c.ability, "item": c.item, "abilities": list(c.abilities),
                         "speed_share": c.speed_share, "bulk": c.bulk, "offense": c.offense, "coverage_mean": round(c.coverage_mean, 3),
                         "usage": c.usage, "mega": c.mega, "grounded": c.grounded, "has_setup": c.has_setup, "can_setup": c.can_setup}
                   for sid, c in caps_by.items()}
    (run_dir / "s03_archetypes.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    fits = ctx["fits"]
    ok = sorted(((v["fit"], a, b) for (a, b), v in fits.items()), reverse=True)
    log(run_dir, f"S3 archetypes: core={len(ctx['cores'])} (特殊 {sum(1 for c in ctx['cores'] if c['archetype'] == 'special')}) "
                 f"見送り={len(ctx['skipped'])} 適合上位: " + ", ".join(f"{a}/{b}={f}" for f, a, b in ok[:6]))
    return ctx


def stage_s4(run_dir: Path, spec: BuildSpec, feats: dict, threats: list, legal: set,
             llm_mode: str, threat_weights: Optional[dict] = None, rule_ctx: Optional[dict] = None,
             arch_ctx: Optional[dict] = None, pool_source: Optional[str] = None, seed: int = 0) -> list:
    mega = mega_capable_ids(list(feats))
    provider = None
    if llm_mode == "headless":
        from tools.team_build.llm.provider import ClaudeCLIProvider
        provider = ClaudeCLIProvider(run_dir / "llm")
    axes = ARCH.llm_axes(arch_ctx) if arch_ctx else None
    from champions_agent.config import BUILD_ARCHETYPE_LLM_ROUNDS
    res = K.generate_concepts(spec, feats, threats, legal, mega, provider=provider,
                              log=lambda m: log(run_dir, m), threat_weights=threat_weights,
                              rules=(rule_ctx or {}).get("llm"), archetypes=axes,
                              rounds=(BUILD_ARCHETYPE_LLM_ROUNDS if axes else K.MAX_ROUNDS))
    # 候補源の多系統化: 上位実構築 (latest なら合成プール) の所持部分集合 (historical) も軸として加える
    try:
        from tools.team_build.opponents import pool_teams
        from tools.team_build.sources import historical_cores
        teams, _ = pool_teams(source=pool_source, seed=seed)
        hist = historical_cores(teams, set(feats))
        hist = [h for h in hist if not any(set(h["core_ids"]) <= set(spec.banned) for _ in [0])]
        before = len(res["families"])
        res["families"] = K.cluster_concepts([dict(f) for f in res["families"]] + hist)
        res["historical_added"] = len(res["families"]) - before
    except Exception as e:
        res["historical_error"] = repr(e)
    if arch_ctx:
        # 構築の軸 (軸 × 分岐の core、役割つき) を先頭側に置く (系統の代表になる → S5 の役割検査と S6 の型反映が効く)
        cores = ARCH.context_cores(arch_ctx)
        before = len(res["families"])
        res["families"] = K.cluster_concepts(cores + [dict(f) for f in res["families"]])
        res["archetype_cores_added"] = len(res["families"]) - before
    if rule_ctx:
        # 規則の軸 (設置役 × エース) を先頭に置く (系統の代表になる)。LLM/ルール/historical の軸で規則を満たさない
        # ものは S5 で機械的に落ちる
        from tools.team_build import rules as RU
        cores = RU.context_cores(rule_ctx, feats, threats)
        before = len(res["families"])
        res["families"] = K.cluster_concepts(cores + [dict(f) for f in res["families"]])
        res["rule_cores_added"] = len(res["families"]) - before
    if spec.ace:
        # 指定エース: 全系統の mega_id をエースにそろえる (S6 でエースだけが石を持つ)。core には S5 が固定枠として足す
        ace_mega = spec.ace in mega
        res["families"] = K.apply_ace(res["families"], spec.ace, ace_mega)
        res["ace"] = {"species_id": spec.ace, "mega": ace_mega}
        log(run_dir, f"S4 ace: {spec.ace} (メガ石を{'持てる → 全系統の mega_id をエースに' if ace_mega else '持てない種'})")
    (run_dir / "s04_concepts.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    log(run_dir, f"S4 concepts: families={len(res['families'])} rounds={res['rounds']} stop={res['stop_reason']} "
                 f"historical=+{res.get('historical_added', 0)} rules=+{res.get('rule_cores_added', 0)} "
                 f"archetypes=+{res.get('archetype_cores_added', 0)}")
    # LLM の呼び出しが失敗していたら隠さず書く (2026-09-13: claude CLI の OAuth 失効で全 18 回が失敗していたのに
    # "rounds=6 stop=max_rounds" としか出ず、規則ベースだけで進んだことが分かりにくかった)
    calls = res.get("llm_calls") or []
    failed = [c for c in calls if isinstance(c, dict) and not c.get("ok")]
    if calls and failed:
        problems = (failed[-1].get("problems") or [])[:2]
        log(run_dir, f"S4 LLM: {len(calls)} 回中 {len(failed)} 回失敗 (規則ベースの軸だけで進む)。最後の問題: {problems}")
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
             n_neighbors: Optional[int] = None, rule_ctx: Optional[dict] = None,
             arch_ctx: Optional[dict] = None) -> list:
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
    if arch_ctx:
        # 構築の軸つきの系統 (archetype / branch を持つ) の並びは、役割の最小数 (壁役 1 + 積みエース 2 等) を満たすものだけ
        fam_by = {f.get("family_id"): f for f in fams}
        q = ARCH.qualified_lookup(arch_ctx)
        n_before = len(all_lineups)
        kept = []
        for l in all_lineups:
            fam = fam_by.get(l.concept) or {}
            if fam.get("archetype") and not ARCH.lineup_ok(l.members, fam["archetype"], fam.get("branch"), q,
                                                           fam.get("special_branch")):
                continue
            kept.append(l)
        all_lineups = kept
        log(run_dir, f"S5 archetypes: 役割の最小数で {n_before} → {len(all_lineups)} 並び")
    if rule_ctx:
        # コンセプト規則も hard constraint: 設置役 + エース (別個体) を含まない並びは候補にしない
        from tools.team_build import rules as RU
        n_before = len(all_lineups)
        all_lineups = [l for l in all_lineups if RU.satisfies(l.members, rule_ctx)]
        log(run_dir, f"S5 rules {rule_ctx['names']}: {n_before} → {len(all_lineups)} 並び")
        # 対の相補性: (設置役, エース) の共通の苦手を他の 4 体が見ている割合。見ていない分を点から引く (記事にも書く)
        from champions_agent.config import BUILD_LINEUP_HOLE_THRESHOLD, BUILD_RULE_PAIR_COVER, BUILD_RULE_PAIR_WEIGHT
        pairs = rule_ctx.setdefault("pairs", {})
        for l in all_lineups:
            comp = None
            for p in rule_ctx["per_rule"]:
                pr = RU.choose_pair(l.members, p["setters"], p["aces"], p.get("tr_setters", ()))
                if pr:
                    comp = RU.pair_complementarity(l.members, pr[0], pr[1], feats, threats,
                                                   BUILD_LINEUP_HOLE_THRESHOLD, BUILD_RULE_PAIR_COVER)
                    break
            if comp:
                l.parts["pair"] = comp["score"]
                l.score -= BUILD_RULE_PAIR_WEIGHT * (1.0 - comp["score"])
                pairs[tuple(l.members)] = comp
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
    _assert_no_banned(run_dir, "S5", [(f"L{idx:02d}_{l.concept}", l.members) for idx, l in enumerate(chosen)], banned)
    (run_dir / "s05_candidates.json").write_text(
        json.dumps({"n_generated": len(all_lineups), "lineups": [l.to_dict() for l in chosen]},
                   ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    log(run_dir, f"S5 candidates: generated={len(all_lineups)} kept={len(chosen)}")
    return chosen


def restore_locked_sets(team: list, originals: dict, locked: set, stone_keeper: Optional[str] = None) -> tuple:
    """指定の型 (spec.custom_sets、locked) は S6 で変えない: 規則・軸の反映で型が変わっていたら元 (originals) に戻す (純粋)。
    元の持ち物がメガ石で、その種が石を残す側 (stone_keeper) でなければ持ち物だけは今の値を保つ (指定エースの規則で外した石を
    戻さない)。戻り値 (team, 戻した種の一覧)。2026-10-02: 指定エースの型 (ユーザー登録のミミロップ) に軸の役割の技
    (でんじは / バトンタッチ) が差し込まれていた"""
    out, restored = [], []
    for c in team:
        o = originals.get(c.species_id)
        if c.species_id not in locked or o is None or c.key() == o.key():
            out.append(c)
            continue
        item = o.item
        if S.has_mega_stone(o.item) and stone_keeper and c.species_id != stone_keeper:
            item = c.item
        out.append(S.SetCandidate(o.species_id, o.ability, item, o.nature, o.evs, list(o.moves), o.source, o.score,
                                  list(o.notes) + [f"locked:restored<-{c.source}"], o.usage_gap, o.adj))
        restored.append(c.species_id)
    return out, restored


def apply_team_field(team: list, alternatives: dict, tv: dict, gen, rule_field: Optional[dict],
                     locked: Optional[set] = None) -> tuple:
    """並びの場 (規則の前提 + メンバーの特性 (メガ後)/技で張る場) があれば、自分で張らないメンバーの型をその場の前提で
    選び直す: 生成型の場つき変種 (gen(sid, field)) を候補に足し、被覆をその場で採点し直す。
    locked (指定の型の種) は選び直さない。戻り値 (team, alternatives, team_field or None)。場が無ければそのまま"""
    from advisor.damage import FieldView
    from tools.team_build import gen_sets as G
    from tools.team_build.interaction import view_from_set
    members = []
    for c in team:
        try:
            view, _mv = view_from_set(c.species_id, c.as_row())
            members.append((view.ability, list(c.moves)))
        except Exception:
            members.append((c.ability, list(c.moves)))
    tf = G.team_field_of(members, rule_field)
    if not G.has_field(tf):
        return team, alternatives, None
    fv = FieldView(terrain=tf.get("terrain"), weather=tf.get("weather"))
    label = "/".join(v for v in (tf.get("terrain"), tf.get("weather")) if v)
    new_team = []
    for c, (ability, moves) in zip(team, members):
        own = G.own_field(ability, moves)
        if own.get("terrain") == tf.get("terrain") and own.get("weather") == tf.get("weather"):
            new_team.append(c)                      # 自分で張る側 (設置役) はそのまま
            continue
        if c.species_id in (locked or ()):
            new_team.append(c)                      # 指定の型 (custom_sets) は場の前提でも選び直さない
            continue
        cands = list(alternatives.get(c.species_id) or [c])
        keys = {x.key() for x in cands}
        for g in (gen(c.species_id, tf) if gen is not None else []):
            if g.key() not in keys:
                cands.append(g)
                keys.add(g.key())
        ranked = S.rank_sets(cands, tv, field=fv)
        if not ranked:
            new_team.append(c)
            continue
        alternatives[c.species_id] = ranked
        chosen = ranked[0]
        if chosen.key() != c.key() and f"team_field:{label}" not in chosen.notes:
            chosen.notes = list(chosen.notes) + [f"team_field:{label}"]
        new_team.append(chosen)
    return new_team, alternatives, tf


def stage_s6(run_dir: Path, spec: BuildSpec, lineups: list, snapshot_id: int, tv: dict,
             concept_mega: Optional[dict] = None, rule_ctx: Optional[dict] = None, gen=None,
             arch_ctx: Optional[dict] = None, fams: Optional[list] = None) -> list:
    """各並びの型を型ライブラリから決め、メガ枠 1 体・クローズ・合法性を通した Showdown 本文を保存する。
    規則つきなら設置役の型に技を保証する (持ち物・メガ枠の解決後に差し込み、validate-team で合法性を確認)。
    並びの場 (規則の前提 + 設置役の特性/技) があれば、他のメンバーの型をその場の前提で選び直す (apply_team_field)。
    構築の軸つきの系統 (arch_ctx) は役割の技・持ち物・特性を型に保証する (archetypes.apply_to_team)"""
    out_dir = run_dir / "s06_sets"
    out_dir.mkdir(exist_ok=True)
    results = []
    concept_mega = concept_mega or {}
    rule_kw = None
    arch_kw = None
    if rule_ctx or arch_ctx:
        from advisor.dex import get_dex
        from advisor.search import SETUP_MOVES
        from tools.team_build.interaction import _mega_stone_ids
        dex = get_dex()
        rule_kw = {"category_of": lambda m: str((dex.move(m) or {}).get("category") or "").lower(),
                   "setup_moves": SETUP_MOVES, "stones": _mega_stone_ids()}
    if arch_ctx:
        from tools.team_build.learnsets import can_learn
        arch_kw = dict(rule_kw, can_learn=can_learn, legal_item=S.legal_item, abilities_of=species_abilities)
        if not rule_ctx:
            rule_kw = None
    fam_by = {f.get("family_id"): f for f in (fams or [])}
    # 現行チームとその近傍: 登録済み個体は登録の型を使い、メガ枠は登録のメガに合わせる
    reg_text, _reg_ids, reg_mega = registered_team()
    # 指定エース (spec.ace): メガ石を持てる種なら、探索の並びではエースだけが石を持つ (config BUILD_ACE_MAX_MEGA_STONES)
    ace_sid = spec.ace or None
    ace_mega = bool(ace_sid) and ace_sid in mega_capable_ids([ace_sid])
    # 指定の型 (spec.custom_sets) は S6 で変えない: 場の前提の選び直しの対象外、規則・軸の差し込みは元に戻す、クローズでは残す側
    locked = set(spec.custom_sets or {})
    with db.get_connection() as conn:
        members_all = sorted({m for l in lineups for m in l.members})
        item_map = S.item_usage_map(conn, snapshot_id, members_all)
        usage_pct = S.item_usage_pct_map(conn, snapshot_id, members_all)
        if gen is not None:
            # 使用率の無い種 (生成型で補完) はアイテムクローズの差し替え先も生成側の候補から
            for sid in members_all:
                if not item_map.get(sid):
                    item_map[sid] = gen.items(sid)
        for idx, l in enumerate(lineups):
            team, alternatives = [], {}
            for sid in l.members:
                cands = S.enumerate_sets(conn, snapshot_id, sid, **set_library_kwargs(spec, sid, gen, conn, snapshot_id))
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
            # 指定エース (メガ): 探索の並びではエースの型を石持ちにし、エースだけが石を持つ。現行チーム枝 (登録の型) には
            # 適用しない (参照と同じ条件で測る)。型の選び直し (場の前提・役割の反映) のたびに保証し直す
            ace_on = bool(ace_sid) and ace_mega and (ace_sid in l.members) and not is_inc
            keep_mega = (ace_sid if ace_on else None) or (reg_mega if is_inc and reg_mega in l.members else None) \
                or concept_mega.get(l.concept)
            ace_notes: list = []

            def ace_fix(team_: list, alts_: dict, on: bool = ace_on, notes_: list = ace_notes) -> list:
                if not on:
                    return team_
                team_, holds = S.prefer_mega_set(team_, alts_, ace_sid)
                if not holds and "ace_mega:no_stone_set" not in notes_:
                    notes_.append("ace_mega:no_stone_set")
                return S.enforce_max_megas(team_, alts_, keep=ace_sid, max_n=BUILD_ACE_MAX_MEGA_STONES)

            prefer: dict = {}
            if is_inc:
                # 登録個体を先頭に置き持ち物を登録に合わせる (クローズ解決で新規個体側が譲る)
                reg_items = S.registered_items(reg_text)
                team = S.prefer_registered(team, reg_items)
                prefer = {c.species_id: 1 for c in team if c.species_id in reg_items}
            team = S.enforce_max_megas(team, alternatives, keep=keep_mega)
            team = ace_fix(team, alternatives)
            locked_originals = {c.species_id: c for c in team if c.species_id in locked}
            team_field = None
            if not is_inc:
                from tools.team_build import rules as RU
                rf = RU.rule_field(rule_ctx["names"]) if rule_ctx else None
                team, alternatives, team_field = apply_team_field(team, alternatives, tv, gen, rf, locked=locked)
                if team_field:
                    team = S.enforce_max_megas(team, alternatives, keep=keep_mega)
                    team = ace_fix(team, alternatives)
            rule_setter, rule_notes = None, []
            if rule_kw and not is_inc:
                # 規則: 設置役の技と、エースの持ち物 (クローズでは設置役/エースが残す側)
                from tools.team_build import rules as RU
                team, rule_setter, rule_notes, prefer = RU.apply_to_team(team, rule_ctx, usage_pct=usage_pct,
                                                                         alternatives=alternatives, **rule_kw)
            arch_info = None
            if arch_kw and not is_inc:
                # 構築の軸: 役割の技 (壁技 / トリックルーム / バトンタッチ …)・特性 (天候始動)・持ち物 (ひかりのねんど 等) を保証する。
                # 役割の持ち物はクローズで残す側
                fam = fam_by.get(l.concept) or {}
                if fam.get("archetype") and fam.get("branch"):
                    q_all = ARCH.qualified_lookup(arch_ctx)
                    q = q_all.get((fam["archetype"], fam["branch"])) or {}
                    sb = fam.get("special_branch")
                    team, assign, a_notes = ARCH.apply_to_team(team, fam["archetype"], fam["branch"], q,
                                                               arch_kw["can_learn"], arch_kw["category_of"],
                                                               arch_kw["setup_moves"], arch_kw["stones"],
                                                               legal_item=arch_kw["legal_item"], alternatives=alternatives,
                                                               abilities_of=arch_kw["abilities_of"],
                                                               special_branch=sb,
                                                               special_qualified=(q_all.get(("special", sb)) if sb else None))
                    arch_info = {"axis": fam["archetype"], "branch": fam["branch"], "special_branch": sb, "roles": assign,
                                 "notes": a_notes, "label": ARCH.label_ja(fam["archetype"], fam["branch"], sb)}
                    for sids in (assign or {}).values():
                        for s in sids:
                            prefer[s] = max(prefer.get(s, 0), 1)
            locked_restored: list = []
            if locked_originals and not is_inc:
                # 指定の型に規則・軸が技や持ち物を差し込んでいたら元に戻す (指定エースの規則で外した石は戻さない)
                team, locked_restored = restore_locked_sets(team, locked_originals, locked,
                                                            stone_keeper=(ace_sid if ace_on else keep_mega))
                for sid in locked:
                    prefer[sid] = max(prefer.get(sid, 0), 1)
            if ace_on:
                # 規則・軸の反映後にもエースの石を保証し、クローズではエースが残す側 (規則のエースと同じ優先度 2)
                team = ace_fix(team, alternatives)
                prefer[ace_sid] = 2
            team = S.resolve_item_clause(team, item_map, usage_pct, prefer=prefer)
            text = S.to_showdown_text(team)
            registered = []
            if is_inc:
                text, registered = S.splice_registered_sets(text, reg_text)
            ok, errs = S.validate_team_text(text, spec.regulation)
            cid = f"L{idx:02d}_{l.concept}"
            (out_dir / f"{cid}.txt").write_text(text, encoding="utf-8")
            results.append({"index": idx, "candidate_id": cid, "members": list(l.members), "ok": ok,
                            "errors": errs[:5], "tag": l.tag, "score": round(l.score, 4),
                            "origin": dict(getattr(l, "origin", None) or {}),
                            "registered_sets": registered, "rule_setter": rule_setter, "rule_notes": rule_notes,
                            "rule_pair": (rule_ctx or {}).get("pairs", {}).get(tuple(l.members)),
                            "team_field": team_field, "archetype": arch_info,
                            "ace": (ace_sid if ace_on else None), "ace_notes": ace_notes,
                            "locked_restored": locked_restored,
                            "sets": [{"species": c.species_id, "item": c.item, "nature": c.nature,
                                      "evs": c.evs, "moves": c.moves, "source": c.source,
                                      "coverage": round(c.score, 3), "notes": c.notes} for c in team]})
    _assert_no_banned(run_dir, "S6", [(r["candidate_id"], r["members"]) for r in results], set(spec.banned))
    _assert_ace_mega(run_dir, results)
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
    ap.add_argument("--ace", default="",
                    help="指定エース (日本語名可、1 体): 固定枠にし、メガ石を持てる種なら探索の並びではエースだけがメガ石を持つ "
                         "(config BUILD_ACE_MAX_MEGA_STONES)。S4 の concept は core_ids にエースを含み mega_id もエース。"
                         "型を固定するなら --sets-file でその種の型を渡す")
    ap.add_argument("--banned", default="", help="除外 (カンマ区切り)")
    ap.add_argument("--rules", default="",
                    help="コンセプト規則 (カンマ区切り、tools/team_build/rules.py の RULES)。例: psychic_terrain_priority_ace")
    ap.add_argument("--moves", default="",
                    help="技 + ポケモンの指定 (その種を使う型に必ず入れる)。例: 'マフォクシー:サイコフィールド, ポットデス:からをやぶる/アシストパワー'")
    ap.add_argument("--sets-file", default=None,
                    help="指定の型 (Showdown 形式の本文ファイル、EVs は能力ポイント)。その種は使用率データを見ずこの型を使う")
    ap.add_argument("--targets", default="",
                    help="想定する相手 (カンマ区切り、日本語可)。脅威に加えて重みを最大にし、型生成の技・配分と並びの被覆に効かせる")
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
    ap.add_argument("--extra-lineups", default=None,
                    help="別の run の並びを測定に持ち込む: run_id:candidate_id のカンマ区切り (例 rule_0910:L26_C003)。"
                         "本文を s06_sets に写してこの run のレギュレーションで validate-team。--candidates と併用時も自動で加える")
    ap.add_argument("--strata", default=None,
                    help="探索候補を S5 スコア順に並べた順位 (1 始まり) のカンマ区切りで限定 (例 1,2,5,10,20,40)")
    ap.add_argument("--include-incumbent", action="store_true", help="--strata/--candidates に現行チームと近傍を加える")
    ap.add_argument("--incumbent-neighbors", type=int, default=2, help="--include-incumbent で加える近傍の数")
    ap.add_argument("--stop-after", choices=["s08a", "s08b"], default=None,
                    help="この段で止める (ablation 拡張: S8a/S8b の結果だけ取る)")
    ap.add_argument("--s08b-seed-offset", type=int, default=1,
                    help="S8b の相手列 seed のオフセット (既定 1 = S8a と別の列。ablation 拡張では 0 で同一列)")
    ap.add_argument("--search-mode", choices=["joint", "legacy"], default=BUILD_SEARCH_MODE,
                    help="joint = 並びと型の同時探索 (S5 統合段: 核の型を同時に決め補完を順に足す、docs/TEAM_BUILD_REDESIGN_1002.md §5) / "
                         "legacy = 従来の S5 (種の並び) → S6 (型) (既定 config BUILD_SEARCH_MODE)")
    ap.add_argument("--s11", choices=["on", "off"], default="off",
                    help="S11 (勝者の SEARCH+SELECTION 再学習)。既定 off = S7 の検証済み checkpoint を最終モデルにする")
    ap.add_argument("--reference-adapt", choices=["on", "off"], default=None,
                    help="S7b: 参照にも S7 と同じ適応を与え fresh を参照の variant に加える (既定 config BUILD_REFERENCE_FULL_ADAPT)")
    ap.add_argument("--finalists", type=int, default=None,
                    help="方向性の違う最終候補の数 (既定 config BUILD_FINALISTS)。それぞれに S11/S11b/封印 holdout を行う")
    ap.add_argument("--resume", action="store_true",
                    help="途中で落ちた run の続き: S8a の結果と完了済みの適応 (adapt_result.json) を再利用する")
    ap.add_argument("--validate-n", type=int, default=None, help="S7 の checkpoint 検証の戦数 (既定 config)")
    ap.add_argument("--validate-max", type=int, default=None, help="S7 で検証する checkpoint 数 (既定 config)")
    ap.add_argument("--plan-prior", choices=["off", "on", "ab"], default=None,
                    help="選出計画を選出モデルの初期値にするか (既定 config BUILD_PLAN_PRIOR=off)。ab は S8b に fresh_plan の腕を足して対応比較")
    ap.add_argument("--repairs", type=int, default=None,
                    help="測定からの戻りの周回数 (S8a 後 / S8b 後の修理モード、docs/TEAM_BUILD_REDESIGN_1002.md §14)。"
                         "既定 config BUILD_REPAIR_ROUNDS。0 で無効")
    ap.add_argument("--registry", default=None, help="registry のディレクトリ (既定 logs/registry)")
    ap.add_argument("--adapt-action", choices=["auto", "on", "off"], default="off",
                    help="行動方策 adapter (S11b)。auto = full プロファイルのみ")
    ap.add_argument("--action-steps", type=int, default=None, help="adapter の 1 chunk 学習ステップ (既定 100k)")
    ap.add_argument("--action-eval", type=int, default=None, help="adapter の chunk ごとの対応比較戦数 (既定 100)")
    ap.add_argument("--article-file", default=None,
                    help="構築記事の本文 (ユーザーが貼ったもの)。LLM で structured claims にして軸の候補に加える (要 --llm headless)")
    ap.add_argument("--pool-source", choices=["ranked", "latest"], default=BUILD_POOL_SOURCE,
                    help="測定の相手プール: ranked = POOL_PIN の上位ランカー構築 / latest = 最新スナップショットの全種から合成 "
                         "(既定 config BUILD_POOL_SOURCE)")
    ap.add_argument("--archetypes", choices=["on", "off"], default=("on" if BUILD_ARCHETYPES else "off"),
                    help="構築の軸 (docs/TEAM_BUILD_ARCHETYPES.md): S3 で役割判定、S4 で軸 × 分岐の core と LLM の軸ごとの提案、"
                         "S5 で役割の最小数、S6 で役割の技・持ち物 (既定 config BUILD_ARCHETYPES)")
    args = ap.parse_args()

    run_dir = RUNS_DIR / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    if args.stages == "measure":
        _measure(run_dir, args)
        return
    legal = legal_species_ids()
    sets_text = Path(args.sets_file).read_text(encoding="utf-8") if args.sets_file else ""
    if args.spec:
        raw = json.loads(Path(args.spec).read_text(encoding="utf-8"))
        spec = load_spec(Path(args.spec)) if "schema_version" in raw else parse_form(raw, legal=legal)
        # 使わないポケモンのファイルは --spec (古い request.json) でも常に効かせる
        from tools.team_build.spec import apply_banned_file, parse_custom_sets, parse_required_moves
        apply_banned_file(spec, legal=legal)
        if args.banned:
            # --spec と併用した --banned はその run だけの追加 (extra)
            extra = [s for s in parse_form({"banned": args.banned}, owned=[], legal=legal).banned_source["extra"]
                     if s not in spec.banned]
            spec.banned += extra
            spec.banned_source["extra"] = list(spec.banned_source.get("extra") or []) + extra
            spec.owned = [s for s in spec.owned if s not in set(spec.banned)]
        # --spec と併用した --rules / --moves / --sets-file は spec に足す (spec の値は残す)
        if args.rules:
            spec.rules = sorted(set(spec.rules) | {r for r in args.rules.split(",") if r.strip()})
            spec.provenance["rules"] = "resolved"
        if args.moves:
            for sid, mvs in parse_required_moves(args.moves).items():
                cur = spec.required_moves.setdefault(sid, [])
                cur.extend(m for m in mvs if m not in cur)
            spec.provenance["required_moves"] = "resolved"
        if sets_text:
            spec.custom_sets.update(parse_custom_sets(sets_text))
            spec.provenance["custom_sets"] = "resolved"
        if args.ace:
            from tools.team_build.spec import resolve_species_token
            spec.ace = resolve_species_token(args.ace)
            spec.favorites = sorted(set(spec.favorites) | {spec.ace})
            spec.locked = list(spec.favorites)
            spec.provenance["ace"] = "resolved"
    else:
        spec = parse_form({"favorites": args.favorites, "ace": args.ace, "banned": args.banned, "style": args.style,
                           "objective": args.objective, "profile": args.profile, "rules": args.rules,
                           "moves": args.moves, "sets": sets_text}, legal=legal)
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
    if args.targets:
        # 想定する相手 (日本語可): 脅威リストに加え、重みを最大にする (型生成の技・配分と並びの被覆に効く)
        from tools.team_build.spec import resolve_species_token
        for tok in args.targets.split(","):
            sid = resolve_species_token(tok)
            if sid:
                session_w[sid] = max(session_w.get(sid, 0.0), 1.0)
    doc, split, tv, feats = stage_s1_s3(run_dir, spec, prof, args.seed, args.top_n, extra_threats=list(session_w),
                                        pool_source=args.pool_source)
    threats = list(tv.keys())
    threat_weights = {t["id"]: threat_weight(t) for t in doc["top"] if t["id"] in tv}
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
    # learnset からの型生成 (想定する相手 = 脅威の重み) と、規則の判定材料 (構築の軸も同じ材料を使う)
    gen = make_generator(tv, threat_weights, doc["snapshot"]["id"])
    use_arch = args.archetypes == "on"
    capture: Optional[dict] = {} if use_arch else None
    rule_ctx = rule_context(run_dir, spec, feats, doc["snapshot"]["id"], tv, gen=gen,
                            extra_moves=(sorted(ARCH.moves_needed()) if use_arch else ()), capture=capture)
    arch_ctx = (archetype_context(run_dir, spec, feats, threats, threat_weights, tv, capture, doc["snapshot"]["id"])
                if use_arch else None)
    if args.reuse_concepts and (run_dir / "s04_concepts.json").exists():
        fams = json.loads((run_dir / "s04_concepts.json").read_text(encoding="utf-8"))["families"]
        log(run_dir, f"S4 concepts: 既存を再利用 families={len(fams)}")
    else:
        fams = stage_s4(run_dir, spec, feats, threats, legal, args.llm, threat_weights, rule_ctx=rule_ctx, arch_ctx=arch_ctx,
                        pool_source=args.pool_source, seed=args.seed)
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
    if args.search_mode == "joint":
        # S5 統合段 (2026-10-02 再設計): 並びと型を同時に探索し、s05_candidates / s06_sets を同じ形で保存する。
        # 不変条件 (除外・エースの石) は従来どおりここで検査する
        from tools.team_build.joint_stage import stage_s5_joint
        lineups, results = stage_s5_joint(run_dir, spec, fams, feats, tv, threat_weights, split, prof, doc["snapshot"]["id"],
                                          session_weights=session_w, n_neighbors=args.incumbent_neighbors_s5,
                                          only_incumbent=args.only_incumbent, rule_ctx=rule_ctx,
                                          registered=registered_team(), log=lambda m: log(run_dir, m))
        _assert_no_banned(run_dir, "S5", [(r["candidate_id"], r["members"]) for r in results], set(spec.banned))
        _assert_ace_mega(run_dir, results)
    else:
        lineups = stage_s5(run_dir, spec, fams, feats, threats, prof, threat_weights,
                           only_incumbent=args.only_incumbent, n_neighbors=args.incumbent_neighbors_s5,
                           rule_ctx=rule_ctx, arch_ctx=arch_ctx)
        concept_mega = {f["family_id"]: f.get("mega_id") for f in fams}
        results = stage_s6(run_dir, spec, lineups, doc["snapshot"]["id"], tv, concept_mega, rule_ctx=rule_ctx, gen=gen,
                           arch_ctx=arch_ctx, fams=fams)
    manifest["search_mode"] = args.search_mode
    from champions_agent.env.ranked_teams import pinned_meta_snapshot_id
    manifest.update({"meta_snapshot": doc["snapshot"]["id"], "meta_pin": pinned_meta_snapshot_id(),
                     "pool_source": split.get("pool_source"), "pool_snapshot": split.get("pool_snapshot"),
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
    # 修理モードの変種 (tag repair、測定の途中で s06_sets に足される) は strata の順位に入れない (--resume で測定対象が変わらないように)
    ok_rows = [r for r in rows if r.get("ok") and (r.get("tag") or "") != "repair"]
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


def parse_extra_lineups(spec: Optional[str]) -> list:
    """--extra-lineups "run_id:candidate_id,run_id:candidate_id" → [(run_id, candidate_id)] (純粋)"""
    out = []
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise SystemExit(f"--extra-lineups の書式は run_id:candidate_id ({part!r})")
        run_id, cid = part.split(":", 1)
        out.append((run_id.strip(), cid.strip()))
    return out


def imported_candidate_id(run_id: str, cid: str) -> str:
    """持ち込んだ並びの candidate_id。run_id を末尾に置き、記事の系統検索 (candidate_id の末尾一致) に掛からないようにする"""
    return f"{cid}_from_{run_id}"


def import_lineups(run_dir: Path, rows: list, extra: list, regulation: str, validate=None,
                   runs_dir: Path = RUNS_DIR, log=print, banned=None) -> list:
    """別の run の並び (s06_sets/<cid>.txt と s06_sets.json の行) をこの run の s06_sets に写し、この run のレギュレーションで
    validate-team する (前の run の勝者を新しい手順・新しい相手列で候補と同じ土俵に乗せる)。写してあれば再利用。
    rows は書き換える (呼び出し側が s06_sets.json に書き戻す)。戻り値: 持ち込んだ candidate_id の列。
    banned (使わないポケモン) を含む並びは持ち込めない (止める)"""
    from tools.team_build.spec import banned_in_members
    validate = validate or (lambda text: S.validate_team_text(text, regulation))
    have = {r.get("candidate_id") for r in rows}
    ids = []
    for src_run, cid in extra:
        new_id = imported_candidate_id(src_run, cid)
        ids.append(new_id)
        if new_id in have:
            continue
        src_dir = Path(runs_dir) / src_run
        try:
            src_rows = json.loads((src_dir / "s06_sets.json").read_text(encoding="utf-8"))
        except Exception:
            raise SystemExit(f"--extra-lineups: {src_run} の s06_sets.json が読めない")
        src = next((r for r in src_rows if r.get("candidate_id") == cid), None)
        src_txt = src_dir / "s06_sets" / f"{cid}.txt"
        if src is None or not src_txt.exists():
            raise SystemExit(f"--extra-lineups: {src_run} に {cid} が無い")
        found = banned_in_members(src.get("members") or [], banned)
        if found:
            raise SystemExit(f"--extra-lineups: {src_run}:{cid} に使わないポケモン {found} が含まれる (config/banned_species.txt)")
        text = src_txt.read_text(encoding="utf-8")
        ok, errs = validate(text)
        (run_dir / "s06_sets").mkdir(parents=True, exist_ok=True)
        (run_dir / "s06_sets" / f"{new_id}.txt").write_text(text, encoding="utf-8")
        row = {k: src.get(k) for k in ("members", "sets", "score", "rule_setter", "rule_notes", "rule_pair",
                                       "team_field", "registered_sets")}
        row.update({"index": len(rows), "candidate_id": new_id, "ok": bool(ok), "errors": list(errs)[:5],
                    "tag": "imported", "imported_from": {"run_id": src_run, "candidate_id": cid}})
        rows.append(row)
        have.add(new_id)
        log(f"extra lineup: {src_run}:{cid} → {new_id} ({'合法' if ok else '不合法: ' + '; '.join(list(errs)[:2])})")
    return ids


def _measure(run_dir: Path, args) -> None:
    from champions_agent.config import (BUILD_ADAPT_MIN_BATTLES, BUILD_RACE_DEFAULT_MAX, BUILD_RACE_STEPS, BUILD_REPAIR_ROUNDS,
                                        TRAINING_BATTLE_FORMAT)
    from tools.team_build import ablation as AB, adapt as AD, racing as R, stress as ST
    from tools.team_build.pipeline import run_measurement
    from tools.team_build.registry import Registry
    steps = tuple(int(x) for x in args.race_steps.split(",")) if args.race_steps else BUILD_RACE_STEPS
    reg = Registry(Path(args.registry)) if args.registry else Registry()
    rows = json.loads((run_dir / "s06_sets.json").read_text(encoding="utf-8"))
    imported = []
    extra = parse_extra_lineups(getattr(args, "extra_lineups", None))
    if extra:
        try:
            req = json.loads((run_dir / "request.json").read_text(encoding="utf-8"))
        except Exception:
            req = {}
        imported = import_lineups(run_dir, rows, extra, req.get("regulation") or TRAINING_BATTLE_FORMAT,
                                  log=lambda m: log(run_dir, m), banned=set(req.get("banned") or []))
        (run_dir / "s06_sets.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    subset = resolve_candidate_subset(rows, args.candidates, args.strata,
                                      args.include_incumbent, args.incumbent_neighbors)
    if subset is not None:
        subset += [i for i in imported if i not in subset]
    if subset is not None:
        log(run_dir, f"measure subset: {len(subset)} チーム {subset}")
    from champions_agent.config import (BUILD_ADAPT_VALIDATE_MAX_CKPTS, BUILD_ADAPT_VALIDATE_N, BUILD_FINALISTS,
                                        BUILD_PLAN_PRIOR, BUILD_REFERENCE_FULL_ADAPT, BUILD_SCREEN_ADAPT_BATTLES,
                                        BUILD_SCREEN_MARGIN, BUILD_SCREEN_MAX, BUILD_SCREEN_STEPS)
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
                    repairs=(BUILD_REPAIR_ROUNDS if args.repairs is None else args.repairs),
                    n_threats=PROFILE_DEFAULTS.get(getattr(args, "profile", "full"), PROFILE_DEFAULTS["full"])["threats"],
                    max_candidates=args.max_candidates, registry=reg, llm_provider=provider,
                    # 行動 adapter (構築と連動した学習): auto は medium 以上で有効 (2026-09-17 常時学習の停止で CPU が空いた)
                    adapt_action=(args.adapt_action == "on"
                                  or (args.adapt_action == "auto" and args.profile in ("medium", "full"))),
                    finalists_k=args.finalists or BUILD_FINALISTS,
                    action_steps=args.action_steps or AD.ACTION_CHUNK_STEPS,
                    action_eval=args.action_eval or AD.ACTION_EVAL_BATTLES,
                    screen_adapt=args.screen_adapt or BUILD_SCREEN_ADAPT_BATTLES,
                    screen_margin=BUILD_SCREEN_MARGIN if args.screen_margin is None else args.screen_margin,
                    screen_steps=screen_steps, screen_max=args.screen_max or BUILD_SCREEN_MAX,
                    candidate_ids=subset, stop_after=args.stop_after, s08b_seed_offset=args.s08b_seed_offset,
                    s11=(args.s11 == "on"), validate_n=args.validate_n or BUILD_ADAPT_VALIDATE_N,
                    validate_max=args.validate_max or BUILD_ADAPT_VALIDATE_MAX_CKPTS, resume=args.resume,
                    reference_full_adapt=(BUILD_REFERENCE_FULL_ADAPT if args.reference_adapt is None
                                          else args.reference_adapt == "on"),
                    plan_prior=(args.plan_prior or BUILD_PLAN_PRIOR))


if __name__ == "__main__":
    main()
