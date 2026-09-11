"""コンセプト規則 (BuildSpec.rules): 候補の hard constraint、規則から作る軸 (S4)、型への反映 (S6)、対の相補性 (S5/記事)。

psychic_terrain_priority_ace = 「サイコフィールド + 先制技に弱いエース」
- 設置役: 技 psychicterrain を覚える (champions mod の learnset) か、特性 psychicsurge の型
- エース: 接地している (ひこうタイプ無し・ふゆう無し・ふうせん無し = フィールドの恩恵を受ける) 個体で、
  「先手を取る手段」ごとに次のいずれか (2026-09-10 改訂、閾値は config):
    速攻型      上位脅威への先手率 (加速前) ≥ FAST_SPEED_SHARE かつ 防御種族値 (メガ後) ≤ FAST_MAX_DEF
    自己加速型  かそく / かるわざ (消費アイテム) / 加速技 を持ち、加速後の先手率 ≥ BOOST_SPEED_SHARE かつ 耐久 ≥ BOOST_MIN_BULK
    トリックルーム型  先手率 ≤ TR_SPEED_SHARE で、同じ 6 体に TR 使いがいる (TR は先制技を無効にしない)
  「先制技に弱い」は型ごとに意味が違う (速攻型 = 止め手がほぼ先制技、加速型 = 積んだ後の止め手、TR 型 = TR が効かない)
- 設置役とエースは別個体。S5 では規則を満たさない並びを候補にしない (固定枠と同じ hard constraint)
- S6 では設置役の型に技を保証し、エースに優先の持ち物 (きあいのタスキ) を付ける (使用率データに無い技は代表型に差し込む)
- 対の相補性: (設置役, エース) の共通の苦手を他の 4 体が見ているかを採点し、記事に書く
純粋関数は RuleInfo (種ごとの判定材料) を受け取り、図鑑・DB・learnset の読み出しは run.py 側で行う。
"""
from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace as dc_replace
from typing import Callable, Optional

from champions_agent.config import (BUILD_LINEUP_HOLE_THRESHOLD, BUILD_RULE_ACE_BOOST_MIN_BULK,
                                    BUILD_RULE_ACE_BOOST_SPEED_SHARE, BUILD_RULE_ACE_FAST_MAX_DEF,
                                    BUILD_RULE_ACE_FAST_SPEED_SHARE, BUILD_RULE_ACE_ITEM_MIN_PCT, BUILD_RULE_ACE_ITEMS,
                                    BUILD_RULE_ACE_ITEMS_BY_ABILITY, BUILD_RULE_ACE_MIN_ATTACK_MOVES,
                                    BUILD_RULE_ACE_MIN_ATTACK_TYPES, BUILD_RULE_ACE_MIN_COVERAGE,
                                    BUILD_RULE_ACE_MIN_OFFENSE, BUILD_RULE_ACE_TR_SPEED_SHARE, BUILD_RULE_MAX_CORES,
                                    BUILD_RULE_PAIR_COVER, BUILD_UNBURDEN_TRIGGERS)

RULES = {
    "psychic_terrain_priority_ace": {
        "label": "サイコフィールド + 先制技に弱いエース",
        "description": ("6 体にサイコフィールドの設置役 (技 psychicterrain か特性 psychicsurge) と、先制技に弱いエース "
                        "(接地していて、速攻型 / 自己加速型 / トリックルーム型 のいずれか。フィールド下では先制技を受けない) "
                        "を必ず 1 体ずつ含める。設置役とエースは別個体。トリックルーム型のエースは同じ 6 体に TR 使いが要る"),
        "setter_move": "psychicterrain",
        "setter_ability": "psychicsurge",
        "airborne_types": ("Flying",),
        "airborne_abilities": ("levitate",),
        "airborne_items": ("airballoon",),
        # エースの持ち物: その種での使用率が min_pct 以上なら先頭から優先 (きあいのタスキ)。クローズでもエースが残す。
        # 特性ごとの優先品 (かるわざ = 発動させる消耗品: しろいハーブ / ノーマルジュエル / タスキ) は ace_items より先
        "ace_items": BUILD_RULE_ACE_ITEMS,
        "ace_items_by_ability": BUILD_RULE_ACE_ITEMS_BY_ABILITY,
        "unburden_triggers": BUILD_UNBURDEN_TRIGGERS,
        "ace_item_min_pct": BUILD_RULE_ACE_ITEM_MIN_PCT,
    },
}
CHOICE_ITEMS = ("choicescarf", "choiceband", "choicespecs")
ACE_TYPE_ORDER = ("fast", "boost", "tr")


@dataclass(frozen=True)
class RuleInfo:
    species_id: str
    spe: int                 # 素早さ種族値 (メガ後)
    dfn: int                 # 防御種族値 (メガ後)
    types: tuple
    ability: str
    item: str
    can_learn: dict          # move_id -> bool (規則が参照する技だけ)
    speed_share: float = 0.0     # 上位脅威への先手率 (S3 roles.speed、加速前)
    boost_share: float = 0.0     # 自己加速後の先手率 (S3 roles.speed_boost)
    boost_mult: float = 1.0      # 自己加速の倍率 (features.boost_multiplier、1.0 = 加速手段なし)
    bulk: float = 0.0            # 耐久 (S3 roles.bulk)
    has_tr: bool = False         # 代表型にトリックルーム
    offense: int = 0             # 使う側の攻撃種族値 (代表型の攻撃技が物理なら攻撃、特殊なら特攻、両方なら大きい方。メガ後)
    attack_moves: int = 0        # 代表型の攻撃技 (威力 > 0) の本数
    attack_types: int = 0        # 攻撃技のタイプ数 (技範囲)
    coverage_mean: float = 0.0   # 脅威への平均被覆 (S3 coverage)


def grounded(info: RuleInfo, rule: dict) -> bool:
    return (not any(t in rule["airborne_types"] for t in info.types)
            and info.ability not in rule["airborne_abilities"]
            and info.item not in rule["airborne_items"])


def offense_metrics(moves, base_stats: dict, move_info: Optional[Callable]) -> tuple:
    """(使う側の攻撃種族値, 攻撃技の本数, 攻撃技のタイプ数)。move_info(id) → (category, type, power) か None。純粋"""
    atk = []
    for m in moves or []:
        mi = move_info(m) if move_info else None
        if not mi:
            continue
        cat, typ, power = mi
        if str(cat or "").lower() in ("physical", "special") and (power or 0) > 0:
            atk.append((str(cat).lower(), typ))
    has_p = any(c == "physical" for c, _ in atk)
    has_s = any(c == "special" for c, _ in atk)
    offense = max(int(base_stats.get("atk") or 0) if has_p else 0, int(base_stats.get("spa") or 0) if has_s else 0)
    return offense, len(atk), len({t for _, t in atk})


def setup_stages(moves, setup_moves: dict, ability_spe_mult: float = 1.0) -> dict:
    """1 回積んだ後の能力ランク {stat: 段}: 型の積み技 (setup_moves: move → {stat: 段}) の各能力の最大 (下降はそのまま) と、
    特性の加速 (倍率 1.5 → +1、2.0 → +2)。積み手段が無ければ空 (被覆は積む前の値のまま)。純粋"""
    stages: dict = {}
    for m in moves or []:
        for k, d in (setup_moves.get(m) or {}).items():
            cur = stages.get(k, 0)
            stages[k] = max(cur, d) if d > 0 else min(cur, d) if cur <= 0 else cur
    if ability_spe_mult > 1.0:
        stages["spe"] = max(stages.get("spe", 0), int(round((ability_spe_mult - 1.0) * 2)))
    return {k: v for k, v in stages.items() if v}


def pick_ace_set(cands: list, base_stats: dict, move_info: Optional[Callable], coverage_of: Callable,
                 min_offense: int = BUILD_RULE_ACE_MIN_OFFENSE, min_attack_moves: int = BUILD_RULE_ACE_MIN_ATTACK_MOVES,
                 min_attack_types: int = BUILD_RULE_ACE_MIN_ATTACK_TYPES, min_coverage: float = BUILD_RULE_ACE_MIN_COVERAGE):
    """型ライブラリの候補 (代表型 + 単独入替の代替、adj 降順) のうち火力・技範囲・被覆の門を通る最初の型を返す
    (無ければ None)。coverage_of(c) → その型の平均被覆。代表型が通ればそれが返る (並びの先頭にあるため)。
    2026-09-10: ポットデス (代表型 = からをやぶる + バトンタッチ、攻撃技 1 本) のように、殴れる型が代替にある個体を
    エースにするため"""
    for c in cands:
        off, n, t = offense_metrics(c.moves, base_stats, move_info)
        if off >= min_offense and n >= min_attack_moves and t >= min_attack_types and coverage_of(c) >= min_coverage:
            return c
    return None


def offensive(info: RuleInfo, min_offense: int = BUILD_RULE_ACE_MIN_OFFENSE,
              min_attack_moves: int = BUILD_RULE_ACE_MIN_ATTACK_MOVES,
              min_attack_types: int = BUILD_RULE_ACE_MIN_ATTACK_TYPES,
              min_coverage: float = BUILD_RULE_ACE_MIN_COVERAGE) -> bool:
    """エース共通の火力・技範囲: 攻撃種族値、攻撃技の本数、タイプ数、平均被覆がそれぞれ閾値以上
    (加速するだけの補助型や攻撃技 1 本の型をエースにしない)"""
    return (info.offense >= min_offense and info.attack_moves >= min_attack_moves
            and info.attack_types >= min_attack_types and info.coverage_mean >= min_coverage)


def ace_types(info: RuleInfo, rule: dict, fast_share: float = BUILD_RULE_ACE_FAST_SPEED_SHARE,
              fast_max_def: int = BUILD_RULE_ACE_FAST_MAX_DEF, boost_share: float = BUILD_RULE_ACE_BOOST_SPEED_SHARE,
              boost_min_bulk: float = BUILD_RULE_ACE_BOOST_MIN_BULK, tr_share: float = BUILD_RULE_ACE_TR_SPEED_SHARE,
              min_offense: int = BUILD_RULE_ACE_MIN_OFFENSE, min_attack_moves: int = BUILD_RULE_ACE_MIN_ATTACK_MOVES,
              min_attack_types: int = BUILD_RULE_ACE_MIN_ATTACK_TYPES,
              min_coverage: float = BUILD_RULE_ACE_MIN_COVERAGE) -> list:
    """個体が満たすエースの型 (fast / boost / tr の部分集合、ACE_TYPE_ORDER 順)。
    接地していない、または火力・技範囲の条件 (offensive) を満たさなければ空"""
    if not grounded(info, rule) or not offensive(info, min_offense, min_attack_moves, min_attack_types, min_coverage):
        return []
    out = []
    if info.speed_share >= fast_share and info.dfn <= fast_max_def:
        out.append("fast")
    if info.boost_mult > 1.0 and info.boost_share >= boost_share and info.bulk >= boost_min_bulk:
        out.append("boost")
    if info.speed_share <= tr_share:
        out.append("tr")
    return out


def classify(infos: dict, rule: dict, **thresholds) -> tuple:
    """{species_id: RuleInfo} → (設置役の集合, エース {species_id: [型]}, TR 使いの集合)"""
    setters = {s for s, i in infos.items()
               if i.can_learn.get(rule["setter_move"]) or i.ability == rule["setter_ability"]}
    aces = {}
    for s, i in infos.items():
        types = ace_types(i, rule, **thresholds)
        if types:
            aces[s] = types
    tr_setters = {s for s, i in infos.items() if i.has_tr}
    return setters, aces, tr_setters


def _types_of(aces, sid) -> list:
    return list(aces[sid]) if isinstance(aces, dict) else ["fast"]


def valid_pairs(members, setters, aces, tr_setters=()) -> list:
    """並びの中で成立する (設置役, エース) の対。TR 型のエースは別個体の TR 使いが同じ並びに要る"""
    ms = set(members)
    trs = ms & set(tr_setters or ())
    out = []
    for s in sorted(ms & set(setters)):
        for a in sorted(ms & set(aces)):
            if a == s:
                continue
            types = _types_of(aces, a)
            ok = any(t in ("fast", "boost") for t in types) or ("tr" in types and any(t != a for t in trs))
            if ok:
                out.append((s, a))
    return out


def lineup_satisfies(members, setters, aces, tr_setters=()) -> bool:
    """設置役とエースを別個体で含む (同じ 1 体が両方を兼ねるだけでは不可)"""
    return bool(valid_pairs(members, setters, aces, tr_setters))


def choose_pair(members, setters, aces, tr_setters=()) -> Optional[tuple]:
    """相補性の採点と記事に使う代表の対: 設置役はエースでない個体を優先、エースは型の順 (速攻 > 加速 > TR) → id 順"""
    pairs = valid_pairs(members, setters, aces, tr_setters)
    if not pairs:
        return None

    def key(p) -> tuple:
        s, a = p
        types = _types_of(aces, a)
        rank = min(ACE_TYPE_ORDER.index(t) for t in types if t in ACE_TYPE_ORDER)
        return (s in aces, rank, s, a)

    return min(pairs, key=key)


def build_context(names, infos: dict, **thresholds) -> dict:
    per = []
    for n in names:
        rule = RULES[n]
        setters, aces, tr_setters = classify(infos, rule, **thresholds)
        per.append({"name": n, "rule": rule, "setters": setters, "aces": aces, "tr_setters": tr_setters})
    return {"names": list(names), "per_rule": per,
            "llm": [{"name": p["name"], "label": p["rule"]["label"], "description": p["rule"]["description"],
                     "setters": sorted(p["setters"]), "aces": sorted(p["aces"]),
                     "ace_types": {s: list(t) for s, t in sorted(p["aces"].items())},
                     "tr_setters": sorted(p["tr_setters"])} for p in per]}


def rule_field(names) -> dict:
    """規則が前提とする場 (設置役の技が張るフィールド/天候): {"terrain", "weather"}。無ければ両方 None"""
    from tools.team_build.gen_sets import merge_fields, own_field
    out = {"terrain": None, "weather": None}
    for n in names or ():
        mv = (RULES.get(n) or {}).get("setter_move")
        out = merge_fields(out, own_field(None, [mv] if mv else []))
    return out


def satisfies(members, ctx: dict) -> bool:
    return all(lineup_satisfies(members, p["setters"], p["aces"], p.get("tr_setters", ())) for p in ctx["per_rule"])


def rule_cores(name: str, setters: set, aces, feats: dict, threats: list, tr_setters=(),
               max_cores: int = BUILD_RULE_MAX_CORES) -> list:
    """(設置役, エース) の対を軸にした concept。使用率の和が大きい対から max_cores 個。TR 型だけのエースは使用率最大の
    TR 使いを軸に足す (居なければ作らない)。mega_id はエース → 設置役 → TR 使いの順にメガ候補を採る"""
    trs = [t for t in sorted(tr_setters or ()) if t in feats]
    pairs = []
    for s in sorted(setters):
        for a in sorted(aces):
            if s == a or s not in feats or a not in feats:
                continue
            types = _types_of(aces, a)
            core = [s, a]
            if not any(t in ("fast", "boost") for t in types):
                cand = [t for t in trs if t not in (s, a)]
                if not cand:
                    continue
                core.append(max(cand, key=lambda t: feats[t].usage))
            pairs.append(core)
    pairs.sort(key=lambda c: -sum(feats[m].usage for m in c))
    out = []
    for core in pairs[:max_cores]:
        s, a = core[0], core[1]
        mega = next((m for m in [a, s] + core[2:] if feats[m].mega), None)
        wc = "setup_sweep" if feats[a].roles.get("setup", 0.0) >= 1 else "speed_control"
        weak = sorted(threats, key=lambda t: max(feats[m].coverage.get(t, 0.0) for m in core))[:3]
        out.append({"name": f"rule:{name}:{'+'.join(core)}", "core_ids": list(core), "mega_id": mega,
                    "win_condition": wc, "support_roles": ["speed_control", "hazard_control"], "weak_to": weak,
                    "source": f"rule:{name}"})
    return out


def context_cores(ctx: dict, feats: dict, threats: list) -> list:
    out = []
    for p in ctx["per_rule"]:
        out.extend(rule_cores(p["name"], p["setters"], p["aces"], feats, threats, p.get("tr_setters", ())))
    return out


def pair_complementarity(members, setter: str, ace: str, feats: dict, threats: list,
                         threshold: float = BUILD_LINEUP_HOLE_THRESHOLD, cover: float = BUILD_RULE_PAIR_COVER) -> dict:
    """対の相補性 (純粋): 共通の苦手 = 設置役もエースも被覆 < threshold の脅威。他の 4 体のうち被覆 ≥ cover の個体が
    いれば「見ている」。score = 見ている割合 (共通の苦手が無ければ 1.0)。あわせて エースの止め手 (エースの被覆 < threshold)
    を設置役が見ている割合 (setter_covers_ace_checks) を出す"""
    others = [m for m in members if m not in (setter, ace) and m in feats]
    cs, ca = feats[setter].coverage, feats[ace].coverage
    shared = [t for t in threats if cs.get(t, 0.0) < threshold and ca.get(t, 0.0) < threshold]
    covered_by = {}
    for t in shared:
        best = max(others, key=lambda m: feats[m].coverage.get(t, 0.0), default=None)
        if best is not None and feats[best].coverage.get(t, 0.0) >= cover:
            covered_by[t] = best
    uncovered = [t for t in shared if t not in covered_by]
    ace_checks = [t for t in threats if ca.get(t, 0.0) < threshold]
    checks_by_setter = [t for t in ace_checks if cs.get(t, 0.0) >= cover]
    return {"setter": setter, "ace": ace, "shared_weak": shared, "covered_by": covered_by, "uncovered": uncovered,
            "score": (len(covered_by) / len(shared)) if shared else 1.0,
            "ace_checks": ace_checks, "setter_covers_ace_checks": (len(checks_by_setter) / len(ace_checks)) if ace_checks else 1.0}


def ensure_setter(team: list, setters: set, aces, rule: dict, alternatives: Optional[dict] = None,
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
    from tools.team_build.sets import inject_move
    moves, replaced = inject_move(c.moves, move, category_of, setup_moves)
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


def choose_ace(team: list, aces, setter_id: Optional[str], rule: dict, usage_pct: Optional[dict] = None,
               tr_setters=()):
    """エース = aces のうち設置役でない個体 (TR 型だけの個体は TR 使いが並びにいるとき)。
    複数なら「既に優先品を持つ > 優先品の使用率が高い > 並び順」"""
    ids = [c.species_id for c in team]
    trs = set(tr_setters or ()) & set(ids)

    def usable(a: str) -> bool:
        types = _types_of(aces, a)
        return any(t in ("fast", "boost") for t in types) or ("tr" in types and any(t != a for t in trs))

    cands = [c for c in team if c.species_id in aces and c.species_id != setter_id and usable(c.species_id)]
    if not cands:
        return None
    pref = tuple(rule.get("ace_items") or ())

    def key(c) -> tuple:
        pct = (usage_pct or {}).get(c.species_id) or {}
        return ((c.item or "") in pref, max((pct.get(i, 0.0) for i in pref), default=0.0))

    best = cands[0]
    for c in cands[1:]:
        if key(c) > key(best):
            best = c
    return best


def _dex_move_info(m: str):
    from advisor.dex import get_dex
    mv = get_dex().move(m)
    return (mv.get("category"), mv.get("type"), mv.get("power")) if mv else None


def unburden_trigger_ok(item: Optional[str], moves, triggers: dict, move_info=None) -> bool:
    """かるわざを発動させる持ち物の条件: しろいハーブ = 自分の能力を下げる技 (インファイト等) がある、
    ノーマルジュエル = ノーマルタイプの攻撃技がある。条件の無い持ち物 (タスキ等) は True"""
    kind = (triggers or {}).get(item or "")
    if kind is None:
        return True
    if kind == "self_stat_drop":
        from advisor.dex import move_boost_effects
        return any(any(v < 0 for v in ((move_boost_effects(m) or {}).get("self") or {}).values()) for m in moves)
    if kind == "normal_attack":
        info = move_info or _dex_move_info
        for m in moves:
            mi = info(m)
            if mi and mi[1] == "Normal" and (mi[2] or 0) > 0:
                return True
        return False
    return True


def ace_item_preference(rule: dict, ability: Optional[str]) -> tuple:
    """エースの持ち物の優先順: 特性ごとの表 (ace_items_by_ability) があればそれ、無ければ ace_items"""
    by_ab = rule.get("ace_items_by_ability") or {}
    return tuple(by_ab.get(ability or "") or rule.get("ace_items") or ())


def ensure_ace_item(team: list, aces, setter_id: Optional[str], rule: dict, usage_pct: Optional[dict] = None,
                    stones=frozenset(), tr_setters=(), move_info=None) -> tuple:
    """エースの持ち物を規則の優先品 (特性ごとの表 → ace_items) にする。既に優先品 (かるわざなら発動条件つき) ならそのまま、
    メガ石は替えない、その種での使用率が ace_item_min_pct 未満なら据え置き (注記)。元の SetCandidate は変更しない。
    戻り値 (team, ace_id or None, notes)"""
    ace = choose_ace(team, aces, setter_id, rule, usage_pct, tr_setters)
    if ace is None:
        return team, None, ["no_ace"]
    pref = ace_item_preference(rule, ace.ability)
    triggers = rule.get("unburden_triggers") or {}
    unburden = (ace.ability or "") == "unburden"

    def usable(item: Optional[str]) -> bool:
        return (item or "") in pref and (not unburden or unburden_trigger_ok(item, ace.moves, triggers, move_info))

    if not pref or usable(ace.item) or (ace.item or "") in stones:
        return team, ace.species_id, []
    pct = (usage_pct or {}).get(ace.species_id) or {}
    item = next((i for i in pref if pct.get(i, 0.0) >= float(rule.get("ace_item_min_pct", 0.0)) and usable(i)), None)
    if item is None:
        return team, ace.species_id, ["rule:ace_item_kept"]
    note = f"rule:ace_item<-{ace.item}"
    new = dc_replace(ace, item=item, source=ace.source + "+rule", notes=list(ace.notes) + [note])
    return [new if t is ace else t for t in team], ace.species_id, [note]


def ensure_ace_set(team: list, ace_id: Optional[str], ace_sets: Optional[dict]) -> tuple:
    """エースの型を、判定に使った攻撃的な代替型 (ace_sets[species_id]: 技/性格/配分/特性) に合わせる。持ち物は今の型のまま
    (メガ枠・タスキ・クローズの解決に任せる)。既に同じ技構成なら何もしない。戻り値 (team, notes)"""
    if not ace_id or not ace_sets or ace_id not in ace_sets:
        return team, []
    a = ace_sets[ace_id]
    c = next((t for t in team if t.species_id == ace_id), None)
    if c is None or list(c.moves) == list(a.moves):
        return team, []
    note = f"rule:ace_set<-{'/'.join(c.moves)}"
    new = dc_replace(c, moves=list(a.moves), nature=a.nature, evs=a.evs, ability=a.ability,
                     source=c.source + "+rule", notes=list(c.notes) + [note], usage_gap=a.usage_gap)
    return [new if t is c else t for t in team], [note]


def apply_to_team(team: list, ctx: dict, usage_pct: Optional[dict] = None, **kw) -> tuple:
    """規則ごとに ensure_setter (技) → ensure_ace_set (エースの型) → ensure_ace_item (持ち物)。
    戻り値 (team, {rule_name: {"setter", "ace"}}, notes, prefer = クローズで持ち物を残す優先度 {species_id: 2 (エース) | 1 (設置役)})。
    設置役とエースの優先品が重なったら (きあいのタスキ) エースが残し、設置役は使用率次点の持ち物へ"""
    roles, notes, prefer = {}, [], {}
    stones = kw.get("stones", frozenset())
    ace_sets = ctx.get("ace_sets") or {}
    for p in ctx["per_rule"]:
        team, sid, n = ensure_setter(team, p["setters"], p["aces"], p["rule"], **kw)
        ace = choose_ace(team, p["aces"], sid, p["rule"], usage_pct, p.get("tr_setters", ()))
        n_set: list = []
        if ace is not None:
            team, n_set = ensure_ace_set(team, ace.species_id, ace_sets)
        team, aid, n2 = ensure_ace_item(team, p["aces"], sid, p["rule"], usage_pct, stones, p.get("tr_setters", ()))
        roles[p["name"]] = {"setter": sid, "ace": aid}
        notes.extend(n + n_set + n2)
        if sid:
            prefer[sid] = max(prefer.get(sid, 0), 1)
        if aid:
            prefer[aid] = max(prefer.get(aid, 0), 2)
    return team, roles, notes, prefer
