"""コンセプト規則 (BuildSpec.rules): 候補の hard constraint、規則から作る軸 (S4)、型への反映 (S6)。

psychic_terrain_priority_ace = 「サイコフィールド + 先制技に弱いエース」
- 設置役: 技 psychicterrain を覚える (champions mod の learnset) か、特性 psychicsurge の型
- エース: 接地している (ひこうタイプ無し・ふゆう無し・ふうせん無し = フィールドの恩恵を受ける) 個体で、
  素早さ種族値 ≥ BUILD_RULE_ACE_MIN_SPE かつ 防御種族値 ≤ BUILD_RULE_ACE_MAX_DEF (先制技はほぼ物理)。
  メガ石を持つ型はメガ後の種族値・タイプで判定する
- 設置役とエースは別個体。S5 では規則を満たさない並びを候補にしない (固定枠と同じ hard constraint)
- S6 では設置役の型に技を保証する (使用率データに無い技なので代表型に差し込む)
純粋関数は RuleInfo (種ごとの判定材料) を受け取り、図鑑・DB・learnset の読み出しは run.py 側で行う。
"""
from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace as dc_replace
from typing import Callable, Optional

from champions_agent.config import BUILD_RULE_ACE_MAX_DEF, BUILD_RULE_ACE_MIN_SPE, BUILD_RULE_MAX_CORES

RULES = {
    "psychic_terrain_priority_ace": {
        "label": "サイコフィールド + 先制技に弱いエース",
        "description": ("6 体にサイコフィールドの設置役 (技 psychicterrain か特性 psychicsurge) と、先制技に弱いエース "
                        "(接地していて速く、物理耐久が低い個体。フィールド下では先制技を受けない) を必ず 1 体ずつ含める。"
                        "設置役とエースは別個体"),
        "setter_move": "psychicterrain",
        "setter_ability": "psychicsurge",
        "airborne_types": ("Flying",),
        "airborne_abilities": ("levitate",),
        "airborne_items": ("airballoon",),
    },
}
CHOICE_ITEMS = ("choicescarf", "choiceband", "choicespecs")


@dataclass(frozen=True)
class RuleInfo:
    species_id: str
    spe: int
    dfn: int
    types: tuple
    ability: str
    item: str
    can_learn: dict          # move_id -> bool (規則が参照する技だけ)


def grounded(info: RuleInfo, rule: dict) -> bool:
    return (not any(t in rule["airborne_types"] for t in info.types)
            and info.ability not in rule["airborne_abilities"]
            and info.item not in rule["airborne_items"])


def classify(infos: dict, rule: dict, min_spe: int = BUILD_RULE_ACE_MIN_SPE,
             max_def: int = BUILD_RULE_ACE_MAX_DEF) -> tuple:
    """{species_id: RuleInfo} → (設置役の集合, エースの集合)"""
    setters = {s for s, i in infos.items()
               if i.can_learn.get(rule["setter_move"]) or i.ability == rule["setter_ability"]}
    aces = {s for s, i in infos.items() if grounded(i, rule) and i.spe >= min_spe and i.dfn <= max_def}
    return setters, aces


def lineup_satisfies(members, setters: set, aces: set) -> bool:
    """設置役とエースを別個体で含む (同じ 1 体が両方を兼ねるだけでは不可)"""
    ms = set(members)
    s, a = ms & setters, ms & aces
    return bool(s and a and len(s | a) >= 2)


def build_context(names, infos: dict) -> dict:
    per = []
    for n in names:
        rule = RULES[n]
        setters, aces = classify(infos, rule)
        per.append({"name": n, "rule": rule, "setters": setters, "aces": aces})
    return {"names": list(names), "per_rule": per,
            "llm": [{"name": p["name"], "label": p["rule"]["label"], "description": p["rule"]["description"],
                     "setters": sorted(p["setters"]), "aces": sorted(p["aces"])} for p in per]}


def satisfies(members, ctx: dict) -> bool:
    return all(lineup_satisfies(members, p["setters"], p["aces"]) for p in ctx["per_rule"])


def rule_cores(name: str, setters: set, aces: set, feats: dict, threats: list,
               max_cores: int = BUILD_RULE_MAX_CORES) -> list:
    """(設置役, エース) の対を軸にした concept。使用率の和が大きい対から max_cores 個。
    mega_id はエース → 設置役の順にメガ候補を採る (メガ枠 1 体は S6 の enforce_single_mega が守る)"""
    pairs = [(s, a) for s in sorted(setters) for a in sorted(aces) if s != a and s in feats and a in feats]
    pairs.sort(key=lambda p: -(feats[p[0]].usage + feats[p[1]].usage))
    out = []
    for s, a in pairs[:max_cores]:
        core = [s, a]
        fa, fs = feats[a], feats[s]
        mega = a if fa.mega else (s if fs.mega else None)
        wc = "setup_sweep" if fa.roles.get("setup", 0.0) >= 1 else "speed_control"
        weak = sorted(threats, key=lambda t: max(feats[m].coverage.get(t, 0.0) for m in core))[:3]
        out.append({"name": f"rule:{name}:{s}+{a}", "core_ids": core, "mega_id": mega, "win_condition": wc,
                    "support_roles": ["speed_control", "hazard_control"], "weak_to": weak, "source": f"rule:{name}"})
    return out


def context_cores(ctx: dict, feats: dict, threats: list) -> list:
    out = []
    for p in ctx["per_rule"]:
        out.extend(rule_cores(p["name"], p["setters"], p["aces"], feats, threats))
    return out


def ensure_setter(team: list, setters: set, aces: set, rule: dict, alternatives: Optional[dict] = None,
                  category_of: Optional[Callable] = None, setup_moves=(), stones=frozenset(),
                  used_items=frozenset()) -> tuple:
    """team: [SetCandidate] (1 並び)。設置役を 1 体決めて技 (か特性) を保証する。
    設置役はエースでない個体を優先し、既に技/特性を持つ個体をさらに優先する。技を差し込む枠は
    (1) 積み技でない変化技の末尾 → (2) 変化技の末尾 → (3) 末尾。こだわり系の持ち物では技を選べないので、
    代替の持ち物 (alt:item のうち こだわり/メガ石/チーム内重複でないもの) に替える。
    元の SetCandidate は変更しない。戻り値 (team, setter_id or None, notes)"""
    move, abil = rule["setter_move"], rule["setter_ability"]
    cands = [c for c in team if c.species_id in setters]
    if not cands:
        return team, None, ["no_setter"]
    cands.sort(key=lambda c: (c.species_id in aces, not (move in c.moves or c.ability == abil)))
    c = cands[0]
    if move in c.moves or c.ability == abil:
        return team, c.species_id, []
    moves = list(c.moves)
    cats = [(category_of(m) if category_of else "") for m in moves]
    idx = next((k for k in range(len(moves) - 1, -1, -1) if cats[k] == "status" and moves[k] not in setup_moves), None)
    if idx is None:
        idx = next((k for k in range(len(moves) - 1, -1, -1) if cats[k] == "status"), len(moves) - 1)
    replaced = moves[idx]
    moves[idx] = move
    notes = [f"rule:{move}<-{replaced}"]
    item = c.item
    if (item or "") in CHOICE_ITEMS:
        taken = set(used_items) | {t.item for t in team if t is not c and t.item}
        alt = next((a for a in (alternatives or {}).get(c.species_id, [])
                    if a.source == "alt:item" and a.item and a.item not in CHOICE_ITEMS
                    and a.item not in stones and a.item not in taken), None)
        if alt is not None:
            item = alt.item
            notes.append(f"rule:item<-{c.item}")
        else:
            notes.append("rule:choice_item_kept")
    new = dc_replace(c, moves=moves, item=item, source=c.source + "+rule", notes=list(c.notes) + notes)
    return [new if t is c else t for t in team], c.species_id, notes


def apply_to_team(team: list, ctx: dict, **kw) -> tuple:
    """規則ごとに ensure_setter。戻り値 (team, {rule_name: setter_id}, notes)"""
    setters_used, notes = {}, []
    for p in ctx["per_rule"]:
        team, sid, n = ensure_setter(team, p["setters"], p["aces"], p["rule"], **kw)
        setters_used[p["name"]] = sid
        notes.extend(n)
    return team, setters_used, notes
