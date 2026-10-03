"""測定からの戻り (S8a / S8b → S5 修理モード)。docs/TEAM_BUILD_REDESIGN_1002.md §14 (D-20、LLM の仮説は使わない: D-28)。

診断 (diagnose、純粋): 探索 fold の対戦記録の敗因統計 (loss_stats) を制約に変換する
    must_cover            負けに最も効いた相手の系統 (負けの多い順に束ねて BUILD_REPAIR_MIN_N 戦に達するまで、敗率 ≥ 下限)
    threat_species        負けに効いた相手の種 (敗率 ≥ 下限)
    ko                    誰に何で倒されたか の集中 (n ≥ BUILD_REPAIR_KO_MIN_N) → 受けを要件に (その相手の系統を担当する枠)
    replace_candidates    選出率が BUILD_REPAIR_UNUSED_RATE 以下の個体 (差し替え対象)
    unused_items          消費アイテムが発動しなかった個体 (型の変更対象)
    mega_review           メガを使った試合の敗率が使わなかった試合より高い (見直しの注記)
修理モード (repair_variants): 親の並びのエースと核は固定、変更は BUILD_MAX_CHANGES 枠まで。負けに効いた系統の重みを上げた評価で、
型だけの変種 (B) を先に、個体の入替 (A) を次に作る。親より点が上がる変種を 1 周あたり BUILD_REPAIR_ARMS まで (上がるものが
無ければ最良の 1 つだけ forced で測る: 診断は実対戦、点は代理なので採否は測定に委ねる)。
配線 (run_repair_round): run の成果物から探索器を組み直し、変種を s06_sets に足して (validate-team 済み)、系譜 (lineage.json) と
診断 (evaluation/s09_repair<周>.json) を書く。racing への追加は pipeline 側。
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from champions_agent.config import (BUILD_CONSUMABLE_ITEMS, BUILD_MAX_CHANGES, BUILD_REPAIR_ARMS, BUILD_REPAIR_FAMILY_BOOST,
                                    BUILD_REPAIR_ITEM_UNUSED_RATE, BUILD_REPAIR_KO_MIN_N, BUILD_REPAIR_LOSS_RATE_MIN,
                                    BUILD_REPAIR_MIN_GAIN, BUILD_REPAIR_MIN_N, BUILD_REPAIR_UNUSED_RATE)
from tools.team_build.lineup_search import LineupResult, LineupSearch, SearchConfig, constraints_ok, team_field_from
from tools.team_build.loss_stats import _norm_name


# ------------------------------------------------------------------ 診断 (純粋)
def resource_counts(records: list, role: str = "p1") -> dict:
    """対戦記録 → 自分の個体ごとの消費アイテムの発動回数 {species_id: n}"""
    out: Counter = Counter()
    for r in records:
        seen: set = set()
        for ev in r.get("resource_usage") or []:
            tgt = str(ev.get("target") or "")
            if tgt.startswith(role) and ev.get("kind") == "-enditem":
                sid = _norm_name(tgt)
                if sid and sid not in seen:
                    out[sid] += 1
                    seen.add(sid)
    return dict(out)


def diagnose(stats: dict, members: list, items_of: Optional[dict] = None, consumed: Optional[dict] = None,
             min_n: int = BUILD_REPAIR_MIN_N, loss_rate_min: float = BUILD_REPAIR_LOSS_RATE_MIN,
             unused_rate: float = BUILD_REPAIR_UNUSED_RATE, ko_min_n: int = BUILD_REPAIR_KO_MIN_N,
             item_unused_rate: float = BUILD_REPAIR_ITEM_UNUSED_RATE, consumable=BUILD_CONSUMABLE_ITEMS) -> dict:
    """敗因統計 → 制約 (§14.2)。items_of = 親の型の持ち物 {種: 持ち物}、consumed = resource_counts"""
    n = int(stats.get("n") or 0)
    members = list(members)
    must_cover, acc = [], 0
    for row in stats.get("loss_by_opponent_family") or []:
        if row.get("losses", 0) < 2 or row.get("loss_rate", 0.0) < loss_rate_min or row.get("key") in (None, "?"):
            continue
        must_cover.append(row["key"])
        acc += int(row.get("n") or 0)
        if acc >= min_n:
            break
    enough = acc >= min_n
    threat_species = [row["key"] for row in (stats.get("loss_by_opponent_species") or [])
                      if row.get("losses", 0) >= 2 and row.get("loss_rate", 0.0) >= loss_rate_min][:3]
    ko = [{"ours": k.get("ours"), "by": k.get("by"), "move": k.get("move"), "n": int(k.get("n") or 0)}
          for k in (stats.get("ko_source") or []) if int(k.get("n") or 0) >= ko_min_n][:3]
    vuln: Counter = Counter()
    for k in ko:
        if k["ours"] in members:
            vuln[k["ours"]] += k["n"]
    selected = {u.get("species"): int(u.get("selected") or 0) for u in (stats.get("unused_members") or [])}
    replace = [u["species"] for u in (stats.get("unused_members") or [])
               if u.get("species") in members and float(u.get("rate") or 0.0) <= unused_rate]
    unused_items: list = []
    for sid in members:
        item = (items_of or {}).get(sid)
        if not item or item not in consumable:
            continue
        sel = selected.get(sid, 0)
        if sel >= min_n and (consumed or {}).get(sid, 0) / max(1, sel) < item_unused_rate:
            unused_items.append({"species": sid, "item": item, "selected": sel, "consumed": (consumed or {}).get(sid, 0)})
    mega = stats.get("mega_usage_vs_outcome") or {}
    used, unused = mega.get("used") or {}, mega.get("unused") or {}
    n_used = int(used.get("wins", 0)) + int(used.get("losses", 0))
    n_unused = int(unused.get("wins", 0)) + int(unused.get("losses", 0))
    mega_review = bool(n_used >= min_n and n_unused >= min_n
                       and used.get("losses", 0) / max(1, n_used) > unused.get("losses", 0) / max(1, n_unused) + 0.1)
    notes = []
    if must_cover:
        notes.append(f"負けに効いた系統 {must_cover} (束ねて {acc} 戦{'' if enough else '、下限未満'})")
    if threat_species:
        notes.append(f"負けに効いた相手 {threat_species}")
    for k in ko:
        notes.append(f"{k['ours']} が {k['by']} の {k['move']} に {k['n']} 回倒された")
    if replace:
        notes.append(f"選出されなかった個体 {replace}")
    for u in unused_items:
        notes.append(f"{u['species']} の {u['item']} は {u['selected']} 選出で {u['consumed']} 回しか発動しなかった")
    if mega_review:
        notes.append("メガを使った試合の敗率が使わなかった試合より高い (メガ枠の見直し)")
    return {"n": n, "must_cover": must_cover if enough else [], "must_cover_n": acc, "threat_species": threat_species,
            "ko": ko, "vulnerable": [s for s, _c in vuln.most_common()], "replace_candidates": replace,
            "unused_items": unused_items, "mega_review": mega_review, "notes": notes}


def boosted_weights(fam_w: np.ndarray, families: list, sets: list, must_cover: list, species: list,
                    boost: float = BUILD_REPAIR_FAMILY_BOOST) -> tuple:
    """系統の重みに、負けに効いた系統 (must_cover) と負けに効いた相手の種が居る系統の倍率 (1 + boost) を掛ける。
    families = [(family_id, 重み, [行])]、sets = 相手の型 (species_id を持つ)。戻り値 (重み, 上げた系統の index)"""
    w = np.array(fam_w, dtype=np.float32).copy()
    must = set(must_cover or [])
    sp = set(species or [])
    idx = []
    for i, (fid, _w, rows) in enumerate(families):
        if fid in must or (sp and any(sets[r].species_id in sp for r in rows)):
            idx.append(i)
    if idx:
        w[idx] *= (1.0 + float(boost))
    return w, idx


# ------------------------------------------------------------------ 修理モード (探索器を使う)
def repair_variants(search: LineupSearch, parent: LineupResult, diag: dict, cfg: SearchConfig, species_pool: list,
                    roles_of: Callable, required: list, fixed: set, parent_id: str, round_no: int,
                    max_changes: int = BUILD_MAX_CHANGES, max_arms: int = BUILD_REPAIR_ARMS,
                    boost: float = BUILD_REPAIR_FAMILY_BOOST, min_gain: float = BUILD_REPAIR_MIN_GAIN) -> list:
    """親の並び → 変種 [LineupResult] (型だけの変種 B を先に、個体の入替 A を次に。親より点が上がるものだけ、点の降順で
    max_arms まで)。fixed = 入替えない個体 (エース・核・固定枠)。評価は負けに効いた系統の重みを上げたもの (boosted_weights)。
    変種の origin = {"kind": "repair", "parent", "round", "variant": "B"|"A", "changes": [...], "diagnosis": 要約}"""
    combo = [search.lib.by_key[e.key] for e in parent.entries]
    species = list(diag.get("threat_species") or []) + [k["by"] for k in (diag.get("ko") or []) if k.get("by")]
    w, boosted_idx = boosted_weights(search.fam_w, search.pool.families, search.pool.sets, diag.get("must_cover"), species, boost)
    targets = search.pool.species_of_families(boosted_idx) or None
    # 親が既にエース・石の制約に合わない並び (現行チーム: 登録の石が 2 個など) なら、その親の修理ではエースの規則を当てず、
    # 石の数は親のまま (S5 の現行チーム枝と同じ扱い。2026-10-03: 石 2 個の現行チームから変種が 1 つも出なかった)
    ace, max_stones = cfg.ace, cfg.max_stones
    if not constraints_ok(parent.entries, cfg.ace, cfg.max_stones, cfg.base_of)[0]:
        ace, max_stones = None, max(cfg.max_stones, sum(1 for e in parent.entries if e.stone))
    cfg_r = replace(cfg, max_stones=max_stones)
    old_w = search.fam_w
    search.fam_w = w
    found: list = []       # (点, combo, 種別, 変更) 親より点が上がるもの
    tried: list = []       # 全候補 (上がらなくても最良の 1 つは測る: 診断は実対戦、点は代理なので測定に委ねる)
    try:
        base_sc, _ = search.score_of(combo, required, cfg)
        # B: 型だけ (倒された個体・持ち物が発動しなかった個体を先に)
        prio = {s: 0 for s in (diag.get("vulnerable") or [])}
        for u in diag.get("unused_items") or []:
            prio.setdefault(u["species"], 1)
        order = sorted(range(len(parent.entries)), key=lambda k: prio.get(parent.entries[k].species_id, 2))
        for k in order:
            e = parent.entries[k]
            if e.locked:
                continue
            others = [search.lib.entries[i] for j, i in enumerate(combo) if j != k]
            used = frozenset(o.item for o in others if o.item)
            tfield = team_field_from(others)
            best = None
            for c in search.candidates_fn(e.species_id, e.role, used, e.stone, tfield, cfg.speed_plan, targets=targets):
                if c.stone != e.stone or (c.item and c.item in used):
                    continue
                i = search.lib.add(c)
                if i == combo[k]:
                    continue
                trial = combo[:k] + [i] + combo[k + 1:]
                if not constraints_ok([search.lib.entries[x] for x in trial], ace, max_stones, cfg.base_of)[0]:
                    continue
                sc, _ = search.score_of(trial, required, cfg)
                if best is None or sc > best[0]:
                    best = (sc, trial, i)
            if best:
                c = search.lib.entries[best[2]].cand
                item = (best[0], best[1], "B", [{"species": e.species_id, "from": _set_summary(e.cand), "to": _set_summary(c)}])
                tried.append(item)
                if best[0] >= base_sc + min_gain:
                    found.append(item)
        # A: 個体の入替 (差し替え対象 → 無ければ固定でない個体のうち担当の少ない順)
        repl = [s for s in (diag.get("replace_candidates") or []) if s in parent.members and s not in fixed]
        if not repl:
            cand_out = [e.species_id for e in parent.entries if e.species_id not in fixed and not e.locked]
            repl = sorted(cand_out, key=lambda s: len(parent.assignments.get(s, [])))[:max_changes]
        repl = repl[:max(1, max_changes)]
        stone_holder = next((e.species_id for e in parent.entries if e.stone), None)
        for out_sid in repl:
            k = next(i for i, e in enumerate(parent.entries) if e.species_id == out_sid)
            rest = combo[:k] + combo[k + 1:]
            sc0, _ = search.score_of(rest, required, cfg)
            mega_id = ace or (stone_holder if stone_holder != out_sid else None)
            # 外した種は候補に戻さない (2026-10-03: 外したヒスイヌメルゴンが同じ型で戻り、親と同じ並びの変種ができた)
            cfg_x = replace(cfg_r, banned=frozenset(set(cfg_r.banned) | {out_sid}))
            ext = search._extend([(sc0, rest, {})], cfg_x, species_pool, roles_of, required, cfg.speed_plan, mega_id, 2, ace)
            for sc, new, _fills in ext:
                in_e = search.lib.entries[new[-1]]
                item = (sc, new, "A", [{"out": out_sid, "in": in_e.species_id, "to": _set_summary(in_e.cand)}])
                tried.append(item)
                if sc >= base_sc + min_gain:
                    found.append(item)
        if max_changes >= 2 and len(repl) >= 2:
            ks = [next(i for i, e in enumerate(parent.entries) if e.species_id == s) for s in repl[:2]]
            rest = [i for j, i in enumerate(combo) if j not in ks]
            sc0, _ = search.score_of(rest, required, cfg)
            mega_id = ace or (stone_holder if stone_holder not in repl[:2] else None)
            cfg_x = replace(cfg_r, banned=frozenset(set(cfg_r.banned) | set(repl[:2])))
            beam = [(sc0, rest, {})]
            for _slot in range(2):
                beam = search._extend(beam, cfg_x, species_pool, roles_of, required, cfg.speed_plan, mega_id, 1, ace)
                if not beam:
                    break
            for sc, new, _fills in beam:
                ins = [search.lib.entries[i] for i in new[-2:]]
                item = (sc, new, "A", [{"out": o, "in": e.species_id, "to": _set_summary(e.cand)} for o, e in zip(repl[:2], ins)])
                tried.append(item)
                if sc >= base_sc + min_gain:
                    found.append(item)
    finally:
        search.fam_w = old_w
    forced = False
    if not found and tried:
        found = [max(tried, key=lambda x: x[0])]
        forced = True
    found.sort(key=lambda x: -x[0])
    out: list = []
    seen: set = {tuple(sorted(e.key for e in parent.entries))}        # 親と同じ型の組は変種にしない
    summary = {"must_cover": diag.get("must_cover"), "threat_species": diag.get("threat_species"), "ko": diag.get("ko"),
               "replace_candidates": diag.get("replace_candidates"), "notes": diag.get("notes")}
    for sc_boost, new, kind, changes in found:
        key = tuple(sorted(search.lib.entries[i].key for i in new))
        if key in seen:
            continue
        seen.add(key)
        sc, _ = search.score_of(new, required, cfg)
        r = search._finalize(sc, new, parent.fills, parent.concept, required, cfg, tag="repair",
                             origin={"kind": "repair", "parent": parent_id, "round": round_no, "variant": kind,
                                     "changes": changes, "repair_score": round(float(sc_boost), 4),
                                     "parent_repair_score": round(float(base_sc), 4), "forced": forced, "diagnosis": summary})
        out.append(r)
        if len(out) >= max_arms:
            break
    return out


def _set_summary(cand) -> dict:
    return {"ability": cand.ability, "item": cand.item, "nature": cand.nature, "evs": cand.evs, "moves": list(cand.moves)}


def variant_id(parent_id: str, round_no: int, kind: str, n: int) -> str:
    return f"{parent_id}-R{round_no}{kind}{n}"


# ------------------------------------------------------------------ 配線
def run_repair_round(run_dir: Path, parents: list, round_no: int, battles_prefix: str, log: Optional[Callable] = None,
                     n_threats: int = 30, max_arms: int = BUILD_REPAIR_ARMS, max_changes: int = BUILD_MAX_CHANGES) -> dict:
    """1 周の修理: parents = [(candidate_id, 診断に使う腕の arm_id)]。探索 fold の対戦記録 (evaluation/battles/
    <battles_prefix>_<arm_id>.jsonl) を診断し、変種を s06_sets.json / s06_sets/<id>.txt に足す (validate-team 済み)。
    戻り値 {"ids": 合法な変種の candidate_id, "report": 診断と変種の一覧}。従来の S5 (roles の無い行) の親は飛ばす"""
    from tools.team_build import interventions as IV
    from tools.team_build import joint_stage as J
    from tools.team_build import sets as S
    from tools.team_build.loss_stats import loss_stats
    from tools.team_build.spec import load_spec
    log = log or print
    rows = json.loads((run_dir / "s06_sets.json").read_text(encoding="utf-8"))
    by_id = {r.get("candidate_id"): r for r in rows}
    spec = load_spec(run_dir / "request.json")
    ctx = None
    report = {"round": round_no, "parents": [], "variants": []}
    new_ids: list = []
    per_parent = max(1, max_arms // max(1, len(parents)))
    fams = (J.load_json(run_dir / "s04_concepts.json") or {}).get("families") or []
    fam_by = {f.get("family_id"): f for f in fams}
    for cid, arm_id in parents:
        row = by_id.get(cid)
        if not row or not row.get("roles"):
            log(f"S9 repair {round_no}: {cid} は役割つきの並びでない (従来の S5) → 飛ばす")
            continue
        bl = run_dir / "evaluation" / "battles" / f"{battles_prefix}_{arm_id}.jsonl"
        recs = [json.loads(l) for l in bl.read_text(encoding="utf-8").splitlines() if l.strip()] if bl.exists() else []
        stats = loss_stats(recs, our_species=list(row["members"]))
        items_of = {s.get("species"): s.get("item") for s in (row.get("sets") or [])}
        diag = diagnose(stats, row["members"], items_of, resource_counts(recs))
        if ctx is None:
            ctx = J.load_search(run_dir, spec, n_threats, log=log)
        parent = J.result_from_row(ctx.search, row, ctx.cfg, ctx.custom)
        fam = fam_by.get(parent.concept) or {}
        fixed = set(spec.favorites) | ({spec.ace} if spec.ace else set()) | {c for c in (fam.get("core_ids") or []) if c in parent.members}
        required = list(ctx.cfg.required_roles) + J.concept_requirements(fam, J.branch_roles_of(fam))
        try:
            variants = repair_variants(ctx.search, parent, diag, ctx.cfg, ctx.species_pool, ctx.roles_of, required, fixed, cid,
                                       round_no, max_changes=max_changes, max_arms=per_parent)
        except Exception as e:
            log(f"S9 repair {round_no}: {cid} error {e!r}")
            variants = []
        made: list = []
        counts: dict = {}
        lineage: list = []
        for v in variants:
            kind = v.origin.get("variant", "X")
            counts[kind] = counts.get(kind, 0) + 1
            vid = variant_id(cid, round_no, kind, counts[kind])
            team = [e.cand for e in v.entries]
            text = S.to_showdown_text(team)
            ok, errs = S.validate_team_text(text, spec.regulation)
            (run_dir / "s06_sets" / f"{vid}.txt").write_text(text, encoding="utf-8")
            r = J.make_row(len(rows), vid, v, "repair", ok, errs, spec.ace or None, is_inc=False)
            rows.append(r)
            made.append({"candidate_id": vid, "ok": ok, "score": v.score, "repair_score": v.origin.get("repair_score"),
                         "changes": v.origin.get("changes")})
            lineage.append({"variant_id": vid, "kind": f"{kind}_{'set' if kind == 'B' else 'member'}", "members": list(v.members),
                            "changes": v.origin.get("changes")})
            if ok:
                new_ids.append(vid)
        if lineage:
            IV.record_lineage(run_dir, cid, lineage)
        report["parents"].append({"candidate_id": cid, "arm_id": arm_id, "n_records": len(recs), "diagnosis": diag,
                                  "fixed": sorted(fixed), "variants": made})
        report["variants"].extend(made)
        log(f"S9 repair {round_no}: {cid} ({len(recs)} 戦) 診断 {diag['notes'] or ['特記なし']} → 変種 {len(made)} "
            f"(合法 {sum(1 for m in made if m['ok'])})")
    (run_dir / "s06_sets.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    report["ids"] = list(new_ids)
    (run_dir / "evaluation").mkdir(parents=True, exist_ok=True)
    (run_dir / "evaluation" / f"s09_repair{round_no}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                                                                       encoding="utf-8")
    return {"ids": new_ids, "report": report}
