"""learnset からの型生成 (使用率が無い種の代表型の補完と、使用率がある種の候補追加)。

方針 (2026-09-11 ユーザー決定): 覚える技を全探索せず、
  段 1  技プールの刈り込み: 攻撃技はタイプ × 分類ごとに「威力 × 命中 × STAB × 分類の適合」の上位、補助技は役割辞書に載るものだけ
  段 2  テンプレート (config BUILD_GEN_TEMPLATES) で組み立て: 攻撃技は想定する相手 (脅威の重み) への被覆の増分で貪欲に選ぶ、
        補助技は役割ごとに 1 本。持ち物/性格/配分は役割の定型 (config) から、性格は +Spe で上を取れる相手が増えるかで決める
  段 3  既存の採点 (被覆 + 使用率の事前分布 + 常識フィルタ + validate-team) に合流 (sets.enumerate_sets)
フィールド/天候 (2026-09-11): 自分で張れるもの (特性は常時、フィールド技はその技が型に入るテンプレートだけ) の威力補正を
  段 1 の採点と段 2 の与ダメージ表の両方に入れる (config BUILD_GEN_FIELD_*)。相手側やチームの設置役のフィールドは見ない。
純粋関数は move_info / damage_fn などを引数で受け取り、図鑑・learnset の読み出しは generate_for_species が行う。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Optional

from champions_agent.config import (BUILD_GEN_ABILITY_PRIORITY, BUILD_GEN_ARCHETYPES, BUILD_GEN_ATTACKS_PER_TYPE,
                                    BUILD_GEN_AVOID_MOVES, BUILD_GEN_CATEGORY_TOLERANCE, BUILD_GEN_EV_POINT_CAP,
                                    BUILD_GEN_EV_STEP, BUILD_GEN_EV_THREATS, BUILD_GEN_EV_TUNE, BUILD_GEN_EV_WEIGHTS,
                                    BUILD_GEN_FAST_SPEED_SHARE, BUILD_GEN_FIELD_MOVE_BOOSTS, BUILD_GEN_FIELD_MOVE_TYPES,
                                    BUILD_GEN_FIELD_SOURCES, BUILD_GEN_FIELD_TYPE_BOOSTS, BUILD_GEN_MAX_ATTACKS,
                                    BUILD_GEN_MAX_SETS, BUILD_GEN_MEGA_SETS, BUILD_GEN_POINT_BUDGET,
                                    BUILD_GEN_SETUP_ITEMS, BUILD_GEN_SPEED_GAIN_MIN, BUILD_GEN_TEMPLATES,
                                    BUILD_GEN_UTILITY_MOVES, BUILD_GEN_WALL_OFFENSE_MAX, BUILD_TRICK_ROOM_MOVES)
from tools.team_build.sets import SetCandidate

STAB_MULT = 1.5
NO_FIELD = {"terrain": None, "weather": None}
STAT_KEYS = ("hp", "atk", "def", "spa", "spd", "spe")


# ------------------------------------------------------------------ 能力ポイントの微調整 (想定する相手ごと)。純粋
def spread_string(pts: dict) -> str:
    return "/".join(str(int(pts.get(k, 0) or 0)) for k in STAT_KEYS)


def parse_spread(text: str) -> dict:
    """"2/0/0/32/0/32" → {"hp": 2, ...}。形式が違えば {}"""
    try:
        parts = [int(p) for p in str(text or "").split("/")]
    except ValueError:
        return {}
    return dict(zip(STAT_KEYS, parts)) if len(parts) == 6 else {}


def speed_point_options(speed_of: Callable, threat_speeds: dict, cap: int = BUILD_GEN_EV_POINT_CAP) -> list:
    """speed_of(points) → 実効素早さ (単調非減少)。脅威ごとの「上を取る最小ポイント」を候補にする (0 と cap を含む、昇順)"""
    opts = {0, int(cap)}
    top = speed_of(int(cap))
    for s in set(float(v) for v in threat_speeds.values()):
        if top <= s:
            continue
        lo, hi = 0, int(cap)
        while lo < hi:
            mid = (lo + hi) // 2
            if speed_of(mid) > s:
                hi = mid
            else:
                lo = mid + 1
        opts.add(lo)
    return sorted(opts)


def enumerate_spreads(budget: int, cap: int, speed_options, attack_key: str, step: int = BUILD_GEN_EV_STEP) -> list:
    """全ポイントを使い切る配分の候補。素早さは候補値、攻撃 (attack_key) は step 刻み、残りを HP/防御/特防に配る
    (2 つを step 刻み、残る 1 つは端数まで。0..cap)。重複は除く。戻り値 [dict]"""
    out: list = []
    seen: set = set()
    grid = list(range(0, int(cap) + 1, int(step)))
    bulk = ("hp", "def", "spd")
    for spe in speed_options:
        for atk in grid:
            rest = int(budget) - int(spe) - atk
            if rest < 0:
                continue
            for rem_i, rem_key in enumerate(bulk):
                others = [k for k in bulk if k != rem_key]
                for a in grid:
                    for b in grid:
                        r = rest - a - b
                        if r < 0 or r > cap:
                            continue
                        pts = {k: 0 for k in STAT_KEYS}
                        pts["spe"], pts[attack_key] = int(spe), atk
                        pts[others[0]], pts[others[1]], pts[rem_key] = a, b, r
                        key = tuple(pts[k] for k in STAT_KEYS)
                        if key not in seen:
                            seen.add(key)
                            out.append(pts)
    return out


def score_spread(stats: dict, threats, weights: dict, threat_speeds: dict, incoming: dict, outgoing: dict,
                 params: dict = BUILD_GEN_EV_WEIGHTS, trick_room: bool = False) -> float:
    """配分 (実数値 stats) の評価: Σ_t w_t [outspeed·1[上を取る]
        + survive·1[最大打点を 1 発耐える] + survive_2hit·1[2 発耐える] + survive_margin·残り HP
        + ko·1[最大打点で 1 発] + ko_2hko·1[2 発] + ko_margin·min(1, 与ダメ)]。
    基準線 (耐える / 倒す) を越える配分が優先され、連続項は同点の中での選好。
    incoming[t] = {"def": (被ダメ割合の基準値, 基準の防御, 基準の HP), "spd": (...)}: 被ダメは 基準 × 基準防御/防御 × 基準HP/HP で伸縮。
    outgoing[t] = [(攻撃の能力キー, 与ダメ割合の基準値, 基準の攻撃)]: 与ダメは 基準 × 攻撃/基準攻撃"""
    total = 0.0
    for t in threats:
        w = float(weights.get(t, 1.0))
        ts = float(threat_speeds.get(t, 0.0))
        out_ok = (stats["spe"] < ts) if trick_room else (stats["spe"] > ts)
        taken = 0.0
        for def_key, (frac0, def0, hp0) in (incoming.get(t) or {}).items():
            if frac0 <= 0:
                continue
            taken = max(taken, frac0 * (def0 / max(1.0, stats[def_key])) * (hp0 / max(1.0, stats["hp"])))
        ko = 0.0
        for atk_key, frac0, atk0 in (outgoing.get(t) or ()):
            ko = max(ko, frac0 * (stats[atk_key] / max(1.0, atk0)))
        total += w * (params["outspeed"] * (1.0 if out_ok else 0.0)
                      + params["survive"] * (1.0 if taken < 1.0 else 0.0)
                      + params.get("survive_2hit", 0.0) * (1.0 if taken < 0.5 else 0.0)
                      + params["survive_margin"] * max(0.0, 1.0 - taken)
                      + params["ko"] * (1.0 if ko >= 1.0 else 0.0)
                      + params.get("ko_2hko", 0.0) * (1.0 if ko >= 0.5 else 0.0)
                      + params.get("ko_margin", 0.0) * min(1.0, ko))
    return total


def tune_spread(default: dict, candidates: list, stats_of: Callable, threats, weights: dict, threat_speeds: dict,
                incoming: dict, outgoing: dict, params: dict = BUILD_GEN_EV_WEIGHTS, trick_room: bool = False) -> dict:
    """候補のうち score_spread が最大の配分。同点は定型 → 素早さの少ない順 → 文字列順。候補が無ければ default"""
    best, best_key = None, None
    for pts in [default] + [c for c in candidates if c != default]:
        sc = score_spread(stats_of(pts), threats, weights, threat_speeds, incoming, outgoing, params, trick_room)
        key = (-round(sc, 9), 0 if pts == default else 1, int(pts.get("spe", 0)), spread_string(pts))
        if best_key is None or key < best_key:
            best, best_key = pts, key
    return dict(best if best is not None else default)


# ------------------------------------------------------------------ フィールド/天候 (自分で張れるもの) の補正。純粋
def own_field(ability: Optional[str], moves, sources=BUILD_GEN_FIELD_SOURCES) -> dict:
    """自分で張れるフィールド/天候: 特性は常時、技はその技が型にあるとき (先に載っている技が優先)。
    {"terrain": id or None, "weather": id or None}"""
    out = dict(NO_FIELD)
    kind_val = sources["abilities"].get(ability or "")
    if kind_val:
        out[kind_val[0]] = kind_val[1]
    for m in moves or []:
        kind_val = sources["moves"].get(m)
        if kind_val and out[kind_val[0]] is None:
            out[kind_val[0]] = kind_val[1]
    return out


def field_key(field: Optional[dict]) -> tuple:
    return ((field or {}).get("terrain"), (field or {}).get("weather"))


def field_move_boost(move: str, field: Optional[dict], user_grounded: bool = True, target_grounded: bool = True,
                     boosts=BUILD_GEN_FIELD_MOVE_BOOSTS) -> Optional[float]:
    """技固有のフィールド/天候補正が発動する条件なら倍率 (ワイドフォース 1.5、ライジングボルト 2.0 …)、しなければ None。
    倍率 1.0 でも None でなければ「条件下で使える技」(ソーラービームの除外解除に使う)"""
    spec = boosts.get(move)
    if not spec:
        return None
    kind, cond, mult, who = spec
    cur = (field or {}).get(kind)
    if not cur or not (cond == "any" or cond == cur):
        return None
    if (who == "user" and not user_grounded) or (who == "target" and not target_grounded):
        return None
    return float(mult)


def field_move_type(move: str, move_type: str, field: Optional[dict], types=BUILD_GEN_FIELD_MOVE_TYPES) -> str:
    """条件下でタイプが変わる技 (ウェザーボール/ダイチノハドウ) の実際のタイプ。変わらなければ move_type"""
    table = types.get(move)
    if not table:
        return move_type
    for kind in ("terrain", "weather"):
        cur = (field or {}).get(kind)
        if cur and cur in table:
            return table[cur]
    return move_type


def standard_field_mult(move_type: str, field: Optional[dict], grounded: bool = True,
                        table=BUILD_GEN_FIELD_TYPE_BOOSTS) -> float:
    """標準のフィールド/天候補正 (フィールドは接地した使用者だけ)。刈り込みの採点用"""
    mult = 1.0
    t = (field or {}).get("terrain")
    w = (field or {}).get("weather")
    if grounded and t:
        mult *= float(table["terrain"].get(t, {}).get(move_type, 1.0))
    if w:
        mult *= float(table["weather"].get(w, {}).get(move_type, 1.0))
    return mult


def preferred_field_moves(field_moves, types, sources=BUILD_GEN_FIELD_SOURCES,
                          table=BUILD_GEN_FIELD_TYPE_BOOSTS) -> list:
    """フィールド役割の技の順: 自分のタイプの技を強化するフィールド/天候を張る技を先に (元の順は保つ)"""
    def boosts_own(m: str) -> bool:
        kind_val = sources["moves"].get(m)
        if not kind_val:
            return False
        kind, val = kind_val
        return any(float(table[kind].get(val, {}).get(t, 1.0)) > 1.0 for t in types)
    return sorted(field_moves, key=lambda m: 0 if boosts_own(m) else 1)


@dataclass(frozen=True)
class Attack:
    move: str
    category: str      # physical / special
    type: str
    score: float       # 威力 × 命中 × STAB × 分類の適合


def prune_moves(learnset, move_info: Callable, base_stats: dict, types, roles: dict,
                avoid=BUILD_GEN_AVOID_MOVES, per_type: int = BUILD_GEN_ATTACKS_PER_TYPE,
                tolerance: float = BUILD_GEN_CATEGORY_TOLERANCE, max_attacks: int = BUILD_GEN_MAX_ATTACKS,
                field: Optional[dict] = None, grounded: bool = True) -> dict:
    """learnset → {"attacks": [Attack (score 降順)], "utility": {role: [move]}}。
    move_info(m) → {"type", "category", "power", "accuracy", "priority"} か None。roles: {role: iterable of move ids}。
    field (自分で張れるフィールド/天候) があれば、その補正込みの採点でもタイプ × 分類ごとの上位を残す
    (ワイドフォースはサイコフィールド下でサイコキネシスを上回る、等)。フィールド無しの上位と両方残す"""
    atk, spa = int(base_stats.get("atk") or 0), int(base_stats.get("spa") or 0)
    hi, lo = max(atk, spa), min(atk, spa)
    both = hi > 0 and lo / hi >= tolerance
    main_cat = "physical" if atk >= spa else "special"
    attacks: list = []
    boosted: list = []
    has_field = bool(field and (field.get("terrain") or field.get("weather")))
    for m in sorted(learnset):
        boost = field_move_boost(m, field, grounded) if has_field else None
        if m in avoid and boost is None:
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
        mtype = str(mi.get("type") or "")
        fit = 1.0 if cat == main_cat else (lo / hi if hi else 0.0)
        if m not in avoid:
            stab = STAB_MULT if mtype in tuple(types or ()) else 1.0
            attacks.append(Attack(m, cat, mtype, power * acc_f * stab * fit))
        if has_field:
            # 条件下の実際のタイプ (ウェザーボール等) で STAB と標準補正、技固有の補正を掛けた採点
            etype = field_move_type(m, mtype, field)
            stab = STAB_MULT if etype in tuple(types or ()) else 1.0
            fm = standard_field_mult(etype, field, grounded) * (boost if boost is not None else 1.0)
            boosted.append(Attack(m, cat, etype, power * acc_f * stab * fit * fm))

    def top_per_type(cands: list) -> list:
        cands = sorted(cands, key=lambda a: -a.score)
        out: list = []
        seen: dict = {}
        for a in cands:
            key = (a.type, a.category)
            if seen.get(key, 0) >= per_type:
                continue
            seen[key] = seen.get(key, 0) + 1
            out.append(a)
        return out

    kept = top_per_type(attacks)
    names = {a.move for a in kept}
    for a in top_per_type(boosted):
        if a.move not in names:
            kept.append(a)      # フィールド込みの採点で残った技は、その採点のまま (フィールド無しの候補と併存)
            names.add(a.move)
    kept.sort(key=lambda a: -a.score)
    kept = kept[:max_attacks]
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


def choose_wall_nature(natures, main_physical: bool) -> str:
    """壁型の性格 = (物理攻撃向け −SpA, 特殊攻撃向け −Atk) の順。攻撃に使わない側を下げる"""
    if len(natures) < 2:
        return natures[0]
    return natures[0] if main_physical else natures[1]


def wall_nature_for_moves(natures, categories, main_physical: bool) -> str:
    """壁型の性格をその型の攻撃技の分類で決める: 特殊技だけなら −Atk、物理技だけなら −SpA、
    混合なら先頭の攻撃技 (貪欲選択で最も被覆に効いた技) の分類を残す。攻撃技が無ければ種族値の高い側"""
    cats = [str(c).lower() for c in categories]
    if not cats:
        return choose_wall_nature(natures, main_physical)
    return choose_wall_nature(natures, cats[0] == "physical")


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
                  setup_items=BUILD_GEN_SETUP_ITEMS, fixed_item: Optional[str] = None, extra_notes=(),
                  max_sets: int = BUILD_GEN_MAX_SETS) -> list:
    """テンプレートごとに 1 型。pick_attacks(n, exclude) → [move]。役割が埋まらないテンプレートは捨てる。
    fixed_item (メガ石) があれば全型その持ち物。戻り値 [SetCandidate (source learnset)]"""
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
        out.append(SetCandidate(species_id, ability, fixed_item or items[0], nature, arch["evs"], moves, "learnset",
                                notes=[f"gen:{tpl['name']}:{archetype}"] + list(extra_notes)))
        if len(out) >= max_sets:
            break
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


def _generate_form(species_id: str, learnset, base: dict, types: list, eval_abilities: list,
                   set_ability: Optional[str], fixed_item: Optional[str], extra_notes, threat_views: dict,
                   weights: dict, dex, max_sets: int = BUILD_GEN_MAX_SETS) -> dict:
    """1 フォルム (通常 / メガ後) の型生成。評価 (フィールド・接地・与ダメ・配分) は base/types/eval_abilities で、
    型に書く特性は set_ability (メガ型はメガ前の特性)。戻り値 {"sets", "items", "archetype"}"""
    from advisor.damage import FieldView, MonView, _is_grounded, calc_damage, effective_speed
    from advisor.ev_infer import _nature_mult
    from tools.team_build.interaction import _points_to_ev

    def move_info(m: str):
        return dex.move(m)

    # 自分で張れるフィールド/天候: 特性 (常時) と、覚えるフィールド技 (自分のタイプを強化するものを先に)。
    # 刈り込みは「特性 + 先頭のフィールド技」の条件込みでも上位を残す (ワイドフォース等を落とさない)
    ability = choose_ability(eval_abilities)
    ability_set = set_ability or ability
    roles = role_moves()
    learnable_field = preferred_field_moves([m for m in roles.get("field", ()) if m in learnset], types)
    roles["field"] = tuple(learnable_field)
    grounded = _is_grounded(MonView(species_id=species_id, types=types, base=base, ability=ability))
    potential = own_field(ability, learnable_field[:1])
    pool = prune_moves(learnset, move_info, base, types, roles, field=potential, grounded=grounded)
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
                phys_p += w * d
            else:
                spec_p += w * d
    archetype = choose_archetype(base, share_plus, phys_p, spec_p)
    archetypes, setup_items = legal_item_tables()
    arch = archetypes[archetype]
    if archetype.startswith("wall"):
        nature = choose_wall_nature(arch["natures"], main_phys)
    else:
        nature = choose_nature(arch["natures"], effective_speed(view_plus), effective_speed(view_neutral), threat_speeds,
                               weights) if len(arch["natures"]) > 1 else arch["natures"][0]
    attacker = MonView(species_id=species_id, types=types, base=base, ev=_points_to_ev(arch["evs"]),
                       nature=_nature_mult(nature), ability=ability)
    tables: dict = {}
    mtypes = {a.move: str((dex.move(a.move) or {}).get("type") or "") for a in pool["attacks"]}
    cat_of = {a.move: a.category for a in pool["attacks"]}

    def table_for(field: dict) -> dict:
        """その型のフィールド/天候 (特性 + 型に入るフィールド技) 込みの与ダメージ表。フィールドごとにキャッシュ"""
        key = field_key(field)
        if key not in tables:
            fv = FieldView(terrain=field.get("terrain"), weather=field.get("weather"))

            def damage_fn(move: str, tid: str) -> float:
                tv, _m = threat_views[tid]
                boost = field_move_boost(move, field, grounded, _is_grounded(tv))
                if move in BUILD_GEN_AVOID_MOVES and boost is None:
                    return 0.0      # 条件 (晴れのソーラービーム等) が無ければ使わない
                etype = field_move_type(move, mtypes.get(move, ""), field)
                dmg = calc_damage(attacker, tv, move, fieldv=fv,
                                  override_move_type=etype if etype != mtypes.get(move) else None)
                return dmg.get("avg", 0.0) / 100.0 * (boost if boost is not None else 1.0)

            tables[key] = attack_table(pool["attacks"], list(threat_views), damage_fn)
        return tables[key]

    def pick_attacks(n: int, exclude) -> list:
        tbl = table_for(own_field(ability, exclude))
        sub = {m: row for m, row in tbl.items() if m not in exclude}
        return greedy_attacks(sub, n, weights)

    sets = assemble_sets(species_id, pool, pick_attacks, archetype, nature, ability_set, archetypes=archetypes,
                         setup_items=setup_items, fixed_item=fixed_item, extra_notes=extra_notes, max_sets=max_sets)
    if archetype.startswith("wall"):
        # 壁型の性格は型ごとの攻撃技の分類で決め直す (特殊技だけの型に −SpA を付けない)
        sets = [replace(s, nature=wall_nature_for_moves(arch["natures"], [cat_of[m] for m in s.moves if m in cat_of],
                                                          main_phys)) for s in sets]
    if BUILD_GEN_EV_TUNE and threat_views:
        sets = [tune_set_spread(s, base, types, ability, threat_views, weights, threat_speeds, attacker, cat_of,
                                lambda field: table_for(field), dex) for s in sets]
    return {"sets": sets, "items": item_options(archetype, archetypes, setup_items), "archetype": archetype}


def tune_set_spread(s: SetCandidate, base: dict, types: list, ability: Optional[str], threat_views: dict,
                    weights: dict, threat_speeds: dict, attacker, cat_of: dict, table_for: Callable, dex,
                    n_threats: int = BUILD_GEN_EV_THREATS, budget: int = BUILD_GEN_POINT_BUDGET,
                    cap: int = BUILD_GEN_EV_POINT_CAP, step: int = BUILD_GEN_EV_STEP) -> SetCandidate:
    """1 型の能力ポイントを想定する相手 (重みの大きい n_threats 種) に合わせて微調整する。
    被ダメは基準配分からの伸縮 (基準 × 基準防御/防御 × 基準HP/HP)、与ダメは型の与ダメージ表からの伸縮 (× 攻撃/基準攻撃)。
    定型より良い配分が無ければそのまま。トリックルーム型は素早さに振らず「下を取る」"""
    from advisor.damage import MonView, calc_damage
    from advisor.ev_infer import _nature_mult
    from tools.team_build.interaction import _points_to_ev

    default = parse_spread(s.evs)
    if not default:
        return s
    ranked = sorted(threat_views, key=lambda t: (-float(weights.get(t, 1.0)), t))[:n_threats]
    if not ranked:
        return s
    nature_mult = _nature_mult(s.nature) if s.nature else {}
    trick_room = any(m in BUILD_TRICK_ROOM_MOVES for m in s.moves)

    def view_of(pts: dict):
        return MonView(species_id=s.species_id, types=types, base=base, ev=_points_to_ev(pts), nature=nature_mult,
                       ability=ability)

    cache: dict = {}

    def stats_of(pts: dict) -> dict:
        key = spread_string(pts)
        if key not in cache:
            v = view_of(pts)
            st = {k: float(v.stat(k)) for k in STAT_KEYS if k != "hp"}
            st["hp"] = float(v.max_hp())
            cache[key] = st
        return cache[key]

    def speed_of(p: int) -> float:
        return float(view_of({"spe": p}).stat("spe"))

    # 被ダメの基準 (0 ポイント、その型の性格): 脅威ごとに物理/特殊の最大打点
    probe = view_of({})
    hp0, def0, spd0 = float(probe.max_hp()), float(probe.stat("def")), float(probe.stat("spd"))
    incoming: dict = {}
    for t in ranked:
        tv, tmoves = threat_views[t]
        best = {"def": 0.0, "spd": 0.0}
        for m in tmoves:
            mi = dex.move(m) or {}
            if (mi.get("power") or 0) <= 0:
                continue
            key = "def" if str(mi.get("category") or "").lower() == "physical" else "spd"
            best[key] = max(best[key], calc_damage(tv, probe, m).get("avg", 0.0) / 100.0)
        incoming[t] = {"def": (best["def"], def0, hp0), "spd": (best["spd"], spd0, hp0)}
    # 与ダメの基準: その型のフィールド込みの表 (attacker = 定型の配分) からの伸縮
    tbl = table_for(own_field(ability, s.moves))
    attacks = [m for m in s.moves if m in tbl]
    outgoing: dict = {}
    for t in ranked:
        rows = []
        for m in attacks:
            atk_key = "atk" if cat_of.get(m) == "physical" else "spa"
            rows.append((atk_key, float(tbl[m].get(t, 0.0)), float(attacker.stat(atk_key))))
        outgoing[t] = rows
    attack_key = ("atk" if cat_of.get(attacks[0]) == "physical" else "spa") if attacks else \
        ("atk" if base.get("atk", 0) >= base.get("spa", 0) else "spa")
    speeds = {t: threat_speeds[t] for t in ranked}
    speed_opts = [0] if trick_room else speed_point_options(speed_of, speeds, cap)
    cands = enumerate_spreads(budget, cap, speed_opts, attack_key, step)
    tuned = tune_spread(default, cands, stats_of, ranked, weights, speeds, incoming, outgoing, trick_room=trick_room)
    if tuned == default:
        return s
    st = stats_of(tuned)
    n_out = sum(1 for t in ranked if ((st["spe"] < speeds[t]) if trick_room else (st["spe"] > speeds[t])))
    n_surv = 0
    for t in ranked:
        taken = max(f0 * (d0 / max(1.0, st[k])) * (h0 / max(1.0, st["hp"])) for k, (f0, d0, h0) in incoming[t].items())
        n_surv += 1 if taken < 1.0 else 0
    note = f"ev:tuned outspeed={n_out}/{len(ranked)} survive={n_surv}/{len(ranked)}"
    return replace(s, evs=spread_string(tuned), notes=list(s.notes) + [note])


def generate_for_species(species_id: str, threat_views: dict, threat_weights: Optional[dict] = None,
                         learnset_table: Optional[dict] = None, cdex_species: Optional[dict] = None) -> dict:
    """{"sets": [SetCandidate], "items": [item ids], "archetype": str, "megas": [メガ後の種族 id]}。learnset が無ければ空。
    threat_views: {threat_id: (MonView, moves)} (想定する相手)。threat_weights で特定の相手を重くできる。
    メガ石を持てる種は、メガ後の種族値・タイプ・特性 (メガリザードン Y のひでり等) でメガ型を別に生成する
    (フォルムごと BUILD_GEN_MEGA_SETS 型、持ち物はその石、型に書く特性はメガ前のもの)"""
    import json
    from pathlib import Path

    from advisor.dex import get_dex
    from tools.team_build.interaction import _mega_stone_ids
    from tools.team_build.learnsets import learnset_of, learnsets

    dex = get_dex()
    table = learnset_table if learnset_table is not None else learnsets()
    learnset = learnset_of(species_id, table)
    sp = dex.species(species_id)
    if not learnset or not sp:
        return {"sets": [], "items": [], "archetype": None, "megas": []}
    if cdex_species is None:
        p = Path(__file__).resolve().parent.parent.parent / "champions_agent" / "data" / "champions_dex.json"
        try:
            cdex_species = json.loads(p.read_text(encoding="utf-8")).get("species") or {}
        except Exception:
            cdex_species = {}
    entry = cdex_species.get(species_id) or {}
    abilities = [_toid(v) for k, v in sorted((entry.get("abilities") or {}).items())]
    stones = _mega_stone_ids()
    megas = []
    for sid2, e2 in cdex_species.items():
        if e2.get("baseSpecies") == entry.get("name") and e2.get("isMega") and e2.get("requiredItem"):
            cand = _toid(e2["requiredItem"])
            if cand in stones and dex.species(sid2):
                megas.append((sid2, cand, e2))
    weights = dict(threat_weights or {})
    res = _generate_form(species_id, learnset, dict(sp["baseStats"]), list(sp["types"]), abilities, None, None, (),
                         threat_views, weights, dex)
    sets = list(res["sets"])
    set_ability = choose_ability(abilities)
    for sid2, stone, e2 in megas:
        msp = dex.species(sid2)
        mab = [_toid(v) for k, v in sorted((e2.get("abilities") or {}).items())] or abilities
        mres = _generate_form(species_id, learnset, dict(msp["baseStats"]), list(msp["types"]), mab, set_ability, stone,
                              (f"gen:mega:{sid2}",), threat_views, weights, dex, max_sets=BUILD_GEN_MEGA_SETS)
        sets += mres["sets"]
    return {"sets": sets, "items": res["items"], "archetype": res["archetype"], "megas": [m[0] for m in megas],
            "mega": megas[0][0] if megas else None}
