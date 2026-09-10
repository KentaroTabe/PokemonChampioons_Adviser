"""learnset からの型生成 (使用率が無い種の代表型の補完と、使用率がある種の候補追加)。

方針 (2026-09-11 ユーザー決定): 覚える技を全探索せず、
  段 1  技プールの刈り込み: 攻撃技はタイプ × 分類ごとに「威力 × 命中 × STAB × 分類の適合」の上位、補助技は役割辞書に載るものだけ
  段 2  テンプレート (config BUILD_GEN_TEMPLATES) で組み立て: 攻撃技は想定する相手 (脅威の重み) への被覆の増分で貪欲に選ぶ、
        補助技は役割ごとに 1 本。持ち物/性格/配分は役割の定型 (config) から、性格は +Spe で上を取れる相手が増えるかで決める
  段 3  既存の採点 (被覆 + 使用率の事前分布 + 常識フィルタ + validate-team) に合流 (sets.enumerate_sets)
純粋関数は move_info / damage_fn などを引数で受け取り、図鑑・learnset の読み出しは generate_for_species が行う。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from champions_agent.config import (BUILD_GEN_ABILITY_PRIORITY, BUILD_GEN_ARCHETYPES, BUILD_GEN_ATTACKS_PER_TYPE,
                                    BUILD_GEN_AVOID_MOVES, BUILD_GEN_CATEGORY_TOLERANCE, BUILD_GEN_FAST_SPEED_SHARE,
                                    BUILD_GEN_MAX_ATTACKS, BUILD_GEN_MAX_SETS, BUILD_GEN_SETUP_ITEMS,
                                    BUILD_GEN_SPEED_GAIN_MIN, BUILD_GEN_TEMPLATES, BUILD_GEN_UTILITY_MOVES,
                                    BUILD_GEN_WALL_OFFENSE_MAX)
from tools.team_build.sets import SetCandidate

STAB_MULT = 1.5


@dataclass(frozen=True)
class Attack:
    move: str
    category: str      # physical / special
    type: str
    score: float       # 威力 × 命中 × STAB × 分類の適合


def prune_moves(learnset, move_info: Callable, base_stats: dict, types, roles: dict,
                avoid=BUILD_GEN_AVOID_MOVES, per_type: int = BUILD_GEN_ATTACKS_PER_TYPE,
                tolerance: float = BUILD_GEN_CATEGORY_TOLERANCE, max_attacks: int = BUILD_GEN_MAX_ATTACKS) -> dict:
    """learnset → {"attacks": [Attack (score 降順)], "utility": {role: [move]}}。
    move_info(m) → {"type", "category", "power", "accuracy", "priority"} か None。roles: {role: iterable of move ids}"""
    atk, spa = int(base_stats.get("atk") or 0), int(base_stats.get("spa") or 0)
    hi, lo = max(atk, spa), min(atk, spa)
    both = hi > 0 and lo / hi >= tolerance
    main_cat = "physical" if atk >= spa else "special"
    attacks: list = []
    for m in sorted(learnset):
        if m in avoid:
            continue
        mi = move_info(m)
        if not mi:
            continue
        cat = str(mi.get("category") or "").lower()
        power = float(mi.get("power") or 0)
        if cat not in ("physical", "special") or power <= 0:
            continue
        if cat != main_cat and not both:
            continue
        acc = float(mi.get("accuracy") or 0)
        acc_f = (acc / 100.0) if acc else 1.0
        stab = STAB_MULT if mi.get("type") in tuple(types or ()) else 1.0
        fit = 1.0 if cat == main_cat else (lo / hi if hi else 0.0)
        attacks.append(Attack(m, cat, str(mi.get("type") or ""), power * acc_f * stab * fit))
    attacks.sort(key=lambda a: -a.score)
    kept: list = []
    seen: dict = {}
    for a in attacks:
        key = (a.type, a.category)
        if seen.get(key, 0) >= per_type:
            continue
        seen[key] = seen.get(key, 0) + 1
        kept.append(a)
        if len(kept) >= max_attacks:
            break
    utility = {}
    for role, moves in roles.items():
        have = [m for m in moves if m in learnset]
        if have:
            utility[role] = have
    return {"attacks": kept, "utility": utility}


def attack_table(attacks, threats, damage_fn: Callable) -> dict:
    """{move: {threat: 与ダメージ割合 (0..∞、相手の最大 HP 比)}}。damage_fn(move, threat_id) → float"""
    return {a.move: {t: float(damage_fn(a.move, t)) for t in threats} for a in attacks}


def greedy_attacks(table: dict, n: int, weights: Optional[dict] = None, preselected=()) -> list:
    """脅威ごとの最大ダメージ (min(1, dmg)) の重みつき和を最も増やす技から順に n 本選ぶ (同点は表の順)"""
    chosen = [m for m in preselected if m in table]
    threats = sorted({t for row in table.values() for t in row})
    w = {t: float((weights or {}).get(t, 1.0)) for t in threats}
    while len(chosen) < n:
        best, best_gain = None, -1.0
        for m in table:
            if m in chosen:
                continue
            gain = 0.0
            for t in threats:
                cur = max((min(1.0, table[c].get(t, 0.0)) for c in chosen), default=0.0)
                gain += w[t] * max(0.0, min(1.0, table[m].get(t, 0.0)) - cur)
            if gain > best_gain + 1e-12:
                best, best_gain = m, gain
        if best is None:
            break
        chosen.append(best)
    return chosen


def outsped_share(speed: float, threat_speeds: dict, weights: Optional[dict] = None) -> float:
    """自分の素早さで上を取れる脅威の重みの割合"""
    if not threat_speeds:
        return 0.0
    w = {t: float((weights or {}).get(t, 1.0)) for t in threat_speeds}
    tot = sum(w.values()) or 1.0
    return sum(w[t] for t, s in threat_speeds.items() if speed > s) / tot


def choose_nature(natures, speed_plus: float, speed_neutral: float, threat_speeds: dict,
                  weights: Optional[dict] = None, gain_min: float = BUILD_GEN_SPEED_GAIN_MIN) -> str:
    """natures = (+Spe 性格, +攻撃 性格) の順。+Spe で上を取れる脅威の重みの和が gain_min 以上増えれば +Spe"""
    if len(natures) < 2:
        return natures[0]
    w = {t: float((weights or {}).get(t, 1.0)) for t in threat_speeds}
    gain = sum(w[t] for t, s in threat_speeds.items() if speed_plus > s >= speed_neutral)
    return natures[0] if gain >= gain_min else natures[1]


def choose_archetype(base_stats: dict, fast_share: float, phys_pressure: float, spec_pressure: float,
                     fast_min: float = BUILD_GEN_FAST_SPEED_SHARE, wall_max: int = BUILD_GEN_WALL_OFFENSE_MAX) -> str:
    """fast_share = +Spe 振り切りでの先手率、phys/spec_pressure = 脅威からの物理/特殊の被ダメ (重みつき)。
    速ければ速攻型、遅くて火力があれば耐久型、火力が無ければ壁型 (被ダメの大きい側に振る)"""
    atk, spa = int(base_stats.get("atk") or 0), int(base_stats.get("spa") or 0)
    physical = atk >= spa
    if max(atk, spa) <= wall_max:
        return "wall_physical" if phys_pressure >= spec_pressure else "wall_special"
    if fast_share >= fast_min:
        return "fast_physical" if physical else "fast_special"
    return "bulky_physical" if physical else "bulky_special"


def choose_ability(abilities, priority=BUILD_GEN_ABILITY_PRIORITY) -> Optional[str]:
    """abilities: [ability_id ...] (0 → 1 → 隠れ の順)。優先表にあるものを先に、無ければ先頭"""
    ids = [a for a in abilities if a]
    for p in priority:
        if p in ids:
            return p
    return ids[0] if ids else None


def assemble_sets(species_id: str, pool: dict, pick_attacks: Callable, archetype: str, nature: str,
                  ability: Optional[str], templates=BUILD_GEN_TEMPLATES, archetypes=BUILD_GEN_ARCHETYPES,
                  setup_items=BUILD_GEN_SETUP_ITEMS, mega_stone: Optional[str] = None,
                  max_sets: int = BUILD_GEN_MAX_SETS) -> list:
    """テンプレートごとに 1 型 (+ メガ石があれば先頭テンプレートのメガ変種)。pick_attacks(n, exclude) → [move]。
    役割が埋まらないテンプレートは捨てる。戻り値 [SetCandidate (source learnset)]"""
    arch = archetypes[archetype]
    out: list = []
    seen: set = set()
    wall = archetype.startswith("wall")
    order = sorted(templates, key=lambda t: (0 if (t["attacks"] <= 2) == wall else 1))
    for tpl in order:
        util: list = []
        ok = True
        for role in tpl["utility"]:
            cand = next((m for m in pool["utility"].get(role, []) if m not in util), None)
            if cand is None:
                ok = False
                break
            util.append(cand)
        if not ok:
            continue
        attacks = pick_attacks(int(tpl["attacks"]), util)
        if len(attacks) < int(tpl["attacks"]):
            continue
        moves = list(attacks) + util
        key = tuple(sorted(moves))
        if key in seen:
            continue
        seen.add(key)
        items = setup_items if "setup" in tpl["utility"] else arch["items"]
        out.append(SetCandidate(species_id, ability, items[0], nature, arch["evs"], moves, "learnset",
                                notes=[f"gen:{tpl['name']}:{archetype}"]))
        if len(out) >= max_sets:
            break
    if mega_stone and out:
        first = out[0]
        out.insert(1, SetCandidate(species_id, ability, mega_stone, first.nature, first.evs, list(first.moves),
                                   "learnset", notes=list(first.notes) + ["gen:mega"]))
        out = out[:max_sets]
    return out


def generated_usage_gap(moves, rep_moves, move_pct: dict) -> float:
    """使用率がある種の生成型の罰則材料: 代表型に無い技それぞれの (代表型で最も使用率の低い技 − その技の使用率)+ の平均 (0..1)"""
    novel = [m for m in moves if m not in rep_moves]
    if not novel:
        return 0.0
    ref = min((float(move_pct.get(m, 0.0)) for m in rep_moves), default=0.0)
    return sum(max(0.0, ref - float(move_pct.get(m, 0.0))) for m in novel) / len(novel) / 100.0


def item_options(archetype: str, archetypes=BUILD_GEN_ARCHETYPES, setup_items=BUILD_GEN_SETUP_ITEMS) -> list:
    """アイテムクローズの差し替え先 (使用率が無い種向け): 型の定型の持ち物 + 積み用"""
    out = list(archetypes[archetype]["items"])
    out += [i for i in setup_items if i not in out]
    return out


# ------------------------------------------------------------------ 副作用あり (図鑑・learnset・対面表)
def role_moves() -> dict:
    """役割 → 技 id の集合 (既存の辞書から)"""
    from advisor.search import HEAL_MOVES, PROTECT_MOVES, SETUP_MOVES
    from champions_agent.config import BUILD_SPEED_SETUP_MOVES
    from tools.team_build.features import PIVOT_MOVES, PRIORITY_MOVES
    from tools.team_build.interaction import HAZARD_REMOVE, HAZARD_SET, STATUS_MOVES
    return {"setup": tuple(SETUP_MOVES) + tuple(m for m in BUILD_SPEED_SETUP_MOVES if m not in SETUP_MOVES),
            "protect": tuple(PROTECT_MOVES), "priority": tuple(PRIORITY_MOVES), "pivot": tuple(PIVOT_MOVES),
            "hazard": tuple(HAZARD_SET), "removal": tuple(HAZARD_REMOVE), "status": tuple(STATUS_MOVES),
            "heal": tuple(HEAL_MOVES), "field": tuple(BUILD_GEN_UTILITY_MOVES["field"]),
            "screens": tuple(BUILD_GEN_UTILITY_MOVES["screens"])}


def _toid(name: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


_ILLEGAL_ITEMS: Optional[set] = None


def illegal_items() -> set:
    """champions mod の items.ts で isNonstandard が "Past"/"Future"/"Unobtainable" の持ち物 id (M-C に無いもの)。
    載っていない持ち物は本体データを継承して合法"""
    global _ILLEGAL_ITEMS
    if _ILLEGAL_ITEMS is None:
        import re
        from pathlib import Path
        p = Path(__file__).resolve().parent.parent.parent / "pokemon-showdown" / "data" / "mods" / "champions" / "items.ts"
        out: set = set()
        try:
            for m in re.finditer(r"^\t(\w+): \{([^}]*)\}", p.read_text(encoding="utf-8"), flags=re.M):
                if re.search(r'isNonstandard:\s*"(Past|Future|Unobtainable)"', m.group(2)):
                    out.add(m.group(1))
        except OSError:
            pass
        _ILLEGAL_ITEMS = out
    return _ILLEGAL_ITEMS


def legal_item_tables(archetypes=BUILD_GEN_ARCHETYPES, setup_items=BUILD_GEN_SETUP_ITEMS, illegal=None) -> tuple:
    """型の定型と積み用の持ち物から、レギュレーションに無い持ち物を落とした表を返す。純粋 (illegal を渡せば)"""
    bad = set(illegal if illegal is not None else illegal_items())
    arch = {k: dict(v, items=tuple(i for i in v["items"] if i not in bad) or tuple(v["items"][:1]))
            for k, v in archetypes.items()}
    setup = tuple(i for i in setup_items if i not in bad) or tuple(setup_items[:1])
    return arch, setup


def generate_for_species(species_id: str, threat_views: dict, threat_weights: Optional[dict] = None,
                         learnset_table: Optional[dict] = None, cdex_species: Optional[dict] = None) -> dict:
    """{"sets": [SetCandidate], "items": [item ids], "archetype": str}。learnset が無ければ空。
    threat_views: {threat_id: (MonView, moves)} (想定する相手)。threat_weights で特定の相手を重くできる"""
    import json
    from pathlib import Path

    from advisor.damage import MonView, calc_damage, effective_speed
    from advisor.dex import get_dex
    from advisor.ev_infer import _nature_mult
    from tools.team_build.interaction import _mega_stone_ids, _points_to_ev
    from tools.team_build.learnsets import learnset_of, learnsets

    dex = get_dex()
    table = learnset_table if learnset_table is not None else learnsets()
    learnset = learnset_of(species_id, table)
    sp = dex.species(species_id)
    if not learnset or not sp:
        return {"sets": [], "items": [], "archetype": None}
    if cdex_species is None:
        p = Path(__file__).resolve().parent.parent.parent / "champions_agent" / "data" / "champions_dex.json"
        try:
            cdex_species = json.loads(p.read_text(encoding="utf-8")).get("species") or {}
        except Exception:
            cdex_species = {}
    entry = cdex_species.get(species_id) or {}
    abilities = [_toid(v) for k, v in sorted((entry.get("abilities") or {}).items())]
    stones = _mega_stone_ids()
    mega_stone, mega_sid = None, None
    for sid2, e2 in cdex_species.items():
        if e2.get("baseSpecies") == entry.get("name") and e2.get("isMega") and e2.get("requiredItem"):
            cand = _toid(e2["requiredItem"])
            if cand in stones:
                mega_stone, mega_sid = cand, sid2
                break
    base = dict(sp["baseStats"])
    types = list(sp["types"])
    weights = dict(threat_weights or {})

    def move_info(m: str):
        return dex.move(m)

    pool = prune_moves(learnset, move_info, base, types, role_moves())
    if not pool["attacks"]:
        return {"sets": [], "items": [], "archetype": None}
    # 想定する相手への素早さ関係と被ダメの偏り (物理 / 特殊) で型の定型を決める
    threat_speeds = {t: float(effective_speed(v)) for t, (v, _m) in threat_views.items()}
    main_phys = base.get("atk", 0) >= base.get("spa", 0)
    plus_nature = "jolly" if main_phys else "timid"
    fast_ev = _points_to_ev("2/32/0/0/0/32" if main_phys else "2/0/0/32/0/32")
    view_plus = MonView(species_id=species_id, types=types, base=base, ev=fast_ev, nature=_nature_mult(plus_nature))
    view_neutral = MonView(species_id=species_id, types=types, base=base, ev=fast_ev, nature={})
    share_plus = outsped_share(effective_speed(view_plus), threat_speeds, weights)
    probe = MonView(species_id=species_id, types=types, base=base, ev=_points_to_ev("32/0/32/0/2/0"), nature={})
    phys_p, spec_p = 0.0, 0.0
    for t, (tv, tmoves) in threat_views.items():
        w = float(weights.get(t, 1.0))
        for m in tmoves:
            mi = dex.move(m) or {}
            if (mi.get("power") or 0) <= 0:
                continue
            d = calc_damage(tv, probe, m).get("avg", 0.0) / 100.0
            if str(mi.get("category") or "").lower() == "physical":
                phys_p = max(phys_p, 0.0) + w * d
            else:
                spec_p += w * d
    archetype = choose_archetype(base, share_plus, phys_p, spec_p)
    archetypes, setup_items = legal_item_tables()
    arch = archetypes[archetype]
    nature = choose_nature(arch["natures"], effective_speed(view_plus), effective_speed(view_neutral), threat_speeds,
                           weights) if len(arch["natures"]) > 1 else arch["natures"][0]
    ability = choose_ability(abilities)
    attacker = MonView(species_id=species_id, types=types, base=base, ev=_points_to_ev(arch["evs"]),
                       nature=_nature_mult(nature), ability=ability)

    def damage_fn(move: str, tid: str) -> float:
        tv, _m = threat_views[tid]
        return calc_damage(attacker, tv, move).get("avg", 0.0) / 100.0

    tbl = attack_table(pool["attacks"], list(threat_views), damage_fn)

    def pick_attacks(n: int, exclude) -> list:
        sub = {m: row for m, row in tbl.items() if m not in exclude}
        return greedy_attacks(sub, n, weights)

    sets = assemble_sets(species_id, pool, pick_attacks, archetype, nature, ability, archetypes=archetypes,
                         setup_items=setup_items, mega_stone=mega_stone)
    return {"sets": sets, "items": item_options(archetype, archetypes, setup_items), "archetype": archetype,
            "mega": mega_sid}
