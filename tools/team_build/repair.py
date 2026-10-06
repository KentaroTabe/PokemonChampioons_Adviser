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

from champions_agent.config import (BUILD_CONSUMABLE_ITEMS, BUILD_MAX_CHANGES, BUILD_REPAIR_ANSWER_MIN, BUILD_REPAIR_ARMS,
                                    BUILD_REPAIR_MIN_CHANGES,
                                    BUILD_REPAIR_DISTINCT_IN, BUILD_REPAIR_FAMILY_BOOST, BUILD_REPAIR_ITEM_UNUSED_RATE,
                                    BUILD_REPAIR_KO_MIN_N, BUILD_REPAIR_LOSS_RATE_MIN, BUILD_REPAIR_MIN_GAIN, BUILD_REPAIR_MIN_N,
                                    BUILD_REPAIR_MIN_N_SHARE, BUILD_REPAIR_UNUSED_RATE)
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
             item_unused_rate: float = BUILD_REPAIR_ITEM_UNUSED_RATE, consumable=BUILD_CONSUMABLE_ITEMS,
             min_n_share: float = BUILD_REPAIR_MIN_N_SHARE) -> dict:
    """敗因統計 → 制約 (§14.2)。items_of = 親の型の持ち物 {種: 持ち物}、consumed = resource_counts。
    must_cover は下限 (min(min_n, n × min_n_share)) に届かなくても残す (must_cover_enough=False、evidence = 届いた比率)"""
    n = int(stats.get("n") or 0)
    members = list(members)
    # 下限は対戦数に相対 (min_n_share): 300 戦の screening では系統あたり数戦なので絶対値 20 に届かないことがある (1003: 18 戦)。
    # 届かなくても束ねた系統は「部分の証拠」として使い、重みの引き上げを証拠の比率で弱める (must_cover_enough / evidence)
    min_n_eff = min(int(min_n), max(5, int(round(min_n_share * n)))) if n > 0 else int(min_n)
    must_cover, acc = [], 0
    for row in stats.get("loss_by_opponent_family") or []:
        if row.get("losses", 0) < 2 or row.get("loss_rate", 0.0) < loss_rate_min or row.get("key") in (None, "?"):
            continue
        must_cover.append(row["key"])
        acc += int(row.get("n") or 0)
        if acc >= min_n_eff:
            break
    enough = acc >= min_n_eff
    evidence = round(min(1.0, acc / max(1, min_n_eff)), 3)
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
        notes.append(f"負けに効いた系統 {must_cover} (束ねて {acc} 戦 / 下限 {min_n_eff}{'' if enough else '、下限未満: 部分の証拠'})")
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
    return {"n": n, "must_cover": must_cover, "must_cover_n": acc, "must_cover_enough": enough, "min_n": min_n_eff,
            "evidence": evidence, "threat_species": threat_species,
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
                    boost: float = BUILD_REPAIR_FAMILY_BOOST, min_gain: float = BUILD_REPAIR_MIN_GAIN,
                    min_changes: int = BUILD_REPAIR_MIN_CHANGES) -> list:
    """親の並び → 変種 [LineupResult] (型だけの変種 B、形態の変更 F、個体の入替 A。親より点が上がるものだけ、点の降順で
    max_arms まで)。fixed = 入替えない個体 (エース・核・固定枠)。評価は負けに効いた系統の重みを上げたもの (boosted_weights)。
    min_changes: 入替 (A) は親との違いがこの枠数以上のものだけ作る (2026-10-05 判断 #6: 1 枠の変種 ±0.04 は測っても分からない。
    型だけの変種 B は役割・型の変更として可)。
    F = 同じ種のメガ型 ↔ 非メガ型 (判断 §9.6: 独立した候補として生成し、役割と構築全体を評価し直す。「型・役割の変更」として記録し、
    2 体の入替の件数には含めない。個体は残るので fixed でも可。指定エースの形態は変えない)。
    変種の origin = {"kind": "repair", "parent", "round", "variant": "B"|"F"|"A", "changes": [...], "diagnosis": 要約}"""
    combo = [search.lib.by_key[e.key] for e in parent.entries]
    species = list(diag.get("threat_species") or []) + [k["by"] for k in (diag.get("ko") or []) if k.get("by")]
    eff_boost = float(boost) * float(diag.get("evidence", 1.0) if diag.get("evidence") is not None else 1.0)
    w, boosted_idx = boosted_weights(search.fam_w, search.pool.families, search.pool.sets, diag.get("must_cover"), species, eff_boost)
    targets = search.pool.species_of_families(boosted_idx) or None
    # 「誰に何で倒されたか」の集中への受け: 入替先はその相手 (ko の by) の型への被覆が BUILD_REPAIR_ANSWER_MIN 以上の種に限る
    # (2026-10-04: 診断で出た「アシレーヌのムーンフォースで倒される」への受けが入替先に入らなかった)
    answer_rows = [i for i, o in enumerate(search.pool.sets) if o.species_id in {k["by"] for k in (diag.get("ko") or []) if k.get("by")}]

    def answers(idx: int) -> bool:
        if not answer_rows:
            return True
        row = search.rows_for([idx])[0]
        return float(max(row[r] for r in answer_rows)) >= BUILD_REPAIR_ANSWER_MIN
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
        # F: 形態の変更 (同じ種のメガ型 ↔ 非メガ型)。石の上限・エースの規則は constraints_ok で (親に石持ちが居れば他の個体は
        # メガ型になれない)。候補はメガ石を持てるものとして引く (mega_allowed=True)
        for k in order:
            e = parent.entries[k]
            if e.locked or (cfg.ace and e.species_id == cfg.ace):
                continue
            others = [search.lib.entries[i] for j, i in enumerate(combo) if j != k]
            used = frozenset(o.item for o in others if o.item)
            tfield = team_field_from(others)
            best = None
            for c in search.candidates_fn(e.species_id, e.role, used, True, tfield, cfg.speed_plan, targets=targets):
                if c.stone == e.stone or (c.item and c.item in used):
                    continue
                i = search.lib.add(c)
                trial = combo[:k] + [i] + combo[k + 1:]
                if not constraints_ok([search.lib.entries[x] for x in trial], ace, max_stones, cfg.base_of)[0]:
                    continue
                sc, _ = search.score_of(trial, required, cfg)
                if best is None or sc > best[0]:
                    best = (sc, trial, i)
            if best:
                c = search.lib.entries[best[2]].cand
                item = (best[0], best[1], "F", [{"species": e.species_id, "from": _set_summary(e.cand), "to": _set_summary(c),
                                                 "form": "mega->normal" if e.stone else "normal->mega"}])
                tried.append(item)
                if best[0] >= base_sc + min_gain:
                    found.append(item)
        # A: 個体の入替 (差し替え対象 → 無ければ固定でない個体のうち担当の少ない順)
        repl = [s for s in (diag.get("replace_candidates") or []) if s in parent.members and s not in fixed]
        if not repl:
            cand_out = [e.species_id for e in parent.entries if e.species_id not in fixed and not e.locked]
            repl = sorted(cand_out, key=lambda s: len(parent.assignments.get(s, [])))[:max_changes]
        repl = repl[:max(1, max_changes)]
        if min_changes >= 2 and len(repl) < 2 and max_changes >= 2:
            # 2 枠以上の入替しか作らないのに差し替え対象が 1 体 → 固定 (エース・固定枠・核) でない個体のうち担当の少ない順で 2 体目を足す。
            # 登録の型の個体 (locked) も候補にする: 「登録した型を変更しない」と「その個体を構築から外さない」は別の制約
            # (2026-10-05 判断 §9.6。現行チームは 6 体とも登録の型なので、除くと入替の変種が 1 本も出なかった)
            extra = [e.species_id for e in parent.entries if e.species_id not in fixed and e.species_id not in repl]
            extra.sort(key=lambda sp: len(parent.assignments.get(sp, [])))
            repl = (repl + extra)[:2]
        stone_holder = next((e.species_id for e in parent.entries if e.stone), None)
        for out_sid in (repl if min_changes <= 1 else []):        # 1 枠の入替は min_changes ≥ 2 なら作らない
            k = next(i for i, e in enumerate(parent.entries) if e.species_id == out_sid)
            rest = combo[:k] + combo[k + 1:]
            sc0, _ = search.score_of(rest, required, cfg)
            mega_id = ace or (stone_holder if stone_holder != out_sid else None)
            # 外した種は候補に戻さない (2026-10-03: 外したヒスイヌメルゴンが同じ型で戻り、親と同じ並びの変種ができた)
            cfg_x = replace(cfg_r, banned=frozenset(set(cfg_r.banned) | {out_sid}))
            ext = search._extend([(sc0, rest, {})], cfg_x, species_pool, roles_of, required, cfg.speed_plan, mega_id,
                                 max(2, max_arms), ace)
            with_answer = [x for x in ext if answers(x[1][-1])]
            if answer_rows and with_answer:
                ext = with_answer
            for sc, new, _fills in ext:
                in_e = search.lib.entries[new[-1]]
                item = (sc, new, "A", [{"out": out_sid, "in": in_e.species_id, "to": _set_summary(in_e.cand),
                                        "answers_ko": bool(answer_rows) and answers(new[-1])}])
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
    eligible = lambda it: it[2] != "A" or len(it[3]) >= max(1, int(min_changes))      # noqa: E731
    found = [it for it in found if eligible(it)]
    tried = [it for it in tried if eligible(it)]
    forced = False
    if not found and tried:
        found = [max(tried, key=lambda x: x[0])]
        forced = True
    found.sort(key=lambda x: -x[0])
    if BUILD_REPAIR_DISTINCT_IN:
        # 入替 (A) の入れる種を散らす: 入れる種が初出の変種を先に、同じ種の 2 つ目以降は後ろに (点の順は各群の中で保つ)
        first, later, seen_in = [], [], set()
        for item in found:
            ins = tuple(sorted(c.get("in") for c in item[3] if c.get("in")))
            if item[2] == "A" and ins and ins in seen_in:
                later.append(item)
            else:
                seen_in.add(ins)
                first.append(item)
        found = first + later
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


def select_variants(per_parent: list, max_arms: int) -> list:
    """親ごとの変種の候補 [(parent_id, [LineupResult ...])] から測る変種を選ぶ (純粋)。
    親を順に回り、まず各親の最良の入替 (A、2 枠以上) を 1 本ずつ、次に各親の最良の型だけの変種 (B)、次に形態の変更 (F) を
    1 本ずつ、残りは修理の点 (origin.repair_score) の降順で max_arms まで。A を先にするのは次の run で 2 枠の入替を検証する機会を確保するため
    (2026-10-05 判断 §9.6。2 枠の方が強いと結論したわけではない)。親ごとの上限 (max_arms // 親の数) だと 3 本 2 親で 1 本ずつになり、
    2 枠の入替が測られなかった"""
    chosen: list = []
    taken: set = set()

    def best_of(vs: list, kind: str):
        cands = [v for v in vs if v.origin.get("variant") == kind and id(v) not in taken]
        return max(cands, key=lambda v: float(v.origin.get("repair_score") or 0.0), default=None)
    for kind in ("A", "B", "F"):
        for pid, vs in per_parent:
            if len(chosen) >= max_arms:
                return chosen
            v = best_of(vs, kind)
            if v is not None:
                chosen.append((pid, v))
                taken.add(id(v))
    rest = [(pid, v) for pid, vs in per_parent for v in vs if id(v) not in taken]
    rest.sort(key=lambda pv: -float(pv[1].origin.get("repair_score") or 0.0))
    for pid, v in rest:
        if len(chosen) >= max_arms:
            break
        chosen.append((pid, v))
        taken.add(id(v))
    return chosen


# 変種の種類 → 系譜 (lineage) の記録名: A = 個体の入替、B = 型だけ、F = 形態の変更 (メガ型 ↔ 非メガ型。入替には数えない)
KIND_LABEL = {"A": "member", "B": "set", "F": "form"}


def _set_summary(cand) -> dict:
    return {"ability": cand.ability, "item": cand.item, "nature": cand.nature, "evs": cand.evs, "moves": list(cand.moves)}


def variant_id(parent_id: str, round_no: int, kind: str, n: int) -> str:
    return f"{parent_id}-R{round_no}{kind}{n}"


# ------------------------------------------------------------------ 配線
def run_repair_round(run_dir: Path, parents: list, round_no: int, battles_prefix: str, log: Optional[Callable] = None,
                     n_threats: int = 30, max_arms: int = BUILD_REPAIR_ARMS, max_changes: int = BUILD_MAX_CHANGES) -> dict:
    """1 周の修理: parents = [(candidate_id, 診断に使う腕の arm_id か [arm_id ...])]。探索 fold の対戦記録 (evaluation/battles/
    <battles_prefix>_<arm_id>.jsonl、複数なら束ねる) を診断し、変種を s06_sets.json / s06_sets/<id>.txt (+ .plan.json) に足す
    (validate-team 済み)。
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
    fams = (J.load_json(run_dir / "s04_concepts.json") or {}).get("families") or []
    fam_by = {f.get("family_id"): f for f in fams}
    generated: list = []          # (cid, arm_ids, recs, diag, fixed, variants)
    for cid, arm_id in parents:
        row = by_id.get(cid)
        if not row or not row.get("roles"):
            log(f"S9 repair {round_no}: {cid} は役割つきの並びでない (従来の S5) → 飛ばす")
            continue
        arm_ids = list(arm_id) if isinstance(arm_id, (list, tuple)) else [arm_id]
        recs: list = []
        for aid in arm_ids:          # 同じ並びの全 variant の記録を束ねる (診断の対戦数を増やす)
            bl = run_dir / "evaluation" / "battles" / f"{battles_prefix}_{aid}.jsonl"
            if bl.exists():
                recs.extend(json.loads(l) for l in bl.read_text(encoding="utf-8").splitlines() if l.strip())
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
            # 親ごとに max_arms まで作り、測る変種は親をまたいで選ぶ (select_variants: 各親の B と A を 1 本ずつ先に)
            variants = repair_variants(ctx.search, parent, diag, ctx.cfg, ctx.species_pool, ctx.roles_of, required, fixed, cid,
                                       round_no, max_changes=max_changes, max_arms=max_arms)
        except Exception as e:
            log(f"S9 repair {round_no}: {cid} error {e!r}")
            variants = []
        generated.append((cid, arm_ids, recs, diag, fixed, variants))
    chosen = select_variants([(cid, vs) for cid, _a, _r, _d, _f, vs in generated], max_arms)
    chosen_ids = {id(v) for _c, v in chosen}
    for cid, arm_ids, recs, diag, fixed, variants in generated:
        made: list = []
        counts: dict = {}
        lineage: list = []
        for v in variants:
            kind = v.origin.get("variant", "X")
            if id(v) not in chosen_ids:
                made.append({"candidate_id": None, "ok": None, "score": v.score, "repair_score": v.origin.get("repair_score"),
                             "changes": v.origin.get("changes"), "kind": kind, "measured": False})
                continue
            counts[kind] = counts.get(kind, 0) + 1
            vid = variant_id(cid, round_no, kind, counts[kind])
            team = [e.cand for e in v.entries]
            text = S.to_showdown_text(team)
            ok, errs = S.validate_team_text(text, spec.regulation)
            (run_dir / "s06_sets" / f"{vid}.txt").write_text(text, encoding="utf-8")
            J.write_plan_file(run_dir / "s06_sets", vid, v)
            r = J.make_row(len(rows), vid, v, "repair", ok, errs, spec.ace or None, is_inc=False)
            rows.append(r)
            made.append({"candidate_id": vid, "ok": ok, "score": v.score, "repair_score": v.origin.get("repair_score"),
                         "changes": v.origin.get("changes"), "kind": kind, "measured": True})
            lineage.append({"variant_id": vid, "kind": f"{kind}_{KIND_LABEL.get(kind, kind)}", "members": list(v.members),
                            "changes": v.origin.get("changes")})
            if ok:
                new_ids.append(vid)
        if lineage:
            IV.record_lineage(run_dir, cid, lineage)
        report["parents"].append({"candidate_id": cid, "arm_id": arm_ids, "n_records": len(recs), "diagnosis": diag,
                                  "fixed": sorted(fixed), "variants": made})
        report["variants"].extend(m for m in made if m["measured"])
        log(f"S9 repair {round_no}: {cid} ({len(recs)} 戦) 診断 {diag['notes'] or ['特記なし']} → 変種 {len(variants)} のうち測る "
            f"{sum(1 for m in made if m['measured'])} (合法 {sum(1 for m in made if m['ok'])}、"
            f"種類 {[m['kind'] + ':' + str(len(m['changes'] or [])) for m in made if m['measured']]})")
    (run_dir / "s06_sets.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    report["ids"] = list(new_ids)
    (run_dir / "evaluation").mkdir(parents=True, exist_ok=True)
    (run_dir / "evaluation" / f"s09_repair{round_no}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                                                                       encoding="utf-8")
    return {"ids": new_ids, "report": report}
