"""役割の雛形から型 (特性・持ち物・技・性格・配分) を一体で作る (docs/TEAM_BUILD_REDESIGN_1002.md §6〜§12)。

2026-10-02 の再設計: 使用率を第一の決め手にせず (D-10)、役割 (config BUILD_ROLE_TEMPLATES) と並びの文脈 (担当する相手、
並びの場、速度計画、使用済みの持ち物) から型を作る。構成:
  1. 特性: 候補の特性 (champions_dex、メガ後はメガの特性) を効果表のタグと担当する相手への適合で選ぶ (§9.2)
  2. 配分の方針: 雛形の spread (fast / bulky / wall / tr) を担当への先手率・被ダメの偏りで具体化する
  3. 攻撃技: learnset の攻撃技を効果表の 2 階層 (安定 / それ以外) と耐久・速攻の採用規則 (§7.3) で絞り、担当する相手への
     期待ダメージ (advisor.damage の expected = 命中・連続・特性込み) の被覆で貪欲に選ぶ。1 発化の加点は担当だけ (§7.4)。
     条件つき技・天候依存技は並びの始動源があるときだけ (§7.6)
  4. 積み技: 3 軸 (何が下がるか / 型の計画 / 特性と代替) で選び、上げた能力を下げる攻撃技は積み型では使わない (§8)
  5. 補助技: 役割の種類 (設置 / 除去 / 状態異常 / 回復 / 交代 / 先制 / 壁 / 場 / 速度操作 …) ごとの候補から
  6. 持ち物: 雛形のクラスの順で、並びで使用済みのものを除いて選ぶ (§10)。かるわざ等は消費アイテムを先に
  7. 性格・配分: 定型 + 担当する相手に合わせた微調整 (gen_sets.tune_set_spread)
純粋関数 (template_of / classify_bulk / attack_rules / choose_setup / choose_ability_fit / pick_utility / pick_item) と、
図鑑・learnset・ダメージ計算を使う generate_role_sets を分ける。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from champions_agent.config import (BUILD_BULK_POINTS_MIN, BUILD_DEMERIT_NEED_MIN, BUILD_FAST_POINTS_MIN, BUILD_ITEM_CLASSES,
                                    BUILD_OHKO_BONUS, BUILD_OHKO_THREAT_MIN_W, BUILD_ROLE_ALIASES, BUILD_ROLE_FAST_SHARE,
                                    BUILD_ROLE_FIELD_MOVES, BUILD_ROLE_SPREADS, BUILD_ROLE_TEMPLATES, BUILD_ROLE_UTILITY_MOVES,
                                    BUILD_SET_CANDIDATES_PER_ROLE, BUILD_TYPE_ITEMS, BUILD_WALL_SPEED_MAX, BUILD_WEATHER_ROCKS)
from tools.team_build.sets import SetCandidate

WEATHERS = ("sun", "rain", "sand", "snow")
TERRAINS = ("psychic", "grassy", "electric", "misty")
WEATHER_FIELD_NAME = {"sun": "sun", "rain": "rain", "sand": "sandstorm", "snow": "snow"}
STAT_KEYS = ("hp", "atk", "def", "spa", "spd", "spe")
SELFKO_CONDITIONS = ("selfko",)


@dataclass
class RoleContext:
    """型を作るときの並びの文脈。threat_views = {id: (MonView, moves)}、weights = {id: 重み}、targets = 担当する相手の id
    (None なら全部)、team_field = 並びの始動源 {"terrain", "weather"} (自分の特性・技は含めない)、speed_plan = outspeed /
    trick_room / neutral、used_items = 並びで使用済みの持ち物、mega_allowed = この枠がメガ石を持てるか、
    item_pct / move_pct / ability_pct = 使用率 (同点のときの参考)、abilities = 候補の特性 (None なら図鑑)、
    learnset = 覚える技 (None なら champions mod の learnset)"""
    threat_views: dict
    weights: dict = field(default_factory=dict)
    targets: Optional[list] = None
    team_field: dict = field(default_factory=dict)
    speed_plan: str = "neutral"
    used_items: set = field(default_factory=set)
    mega_allowed: bool = True
    item_pct: dict = field(default_factory=dict)
    move_pct: dict = field(default_factory=dict)
    ability_pct: dict = field(default_factory=dict)
    abilities: Optional[list] = None
    learnset: Optional[set] = None
    max_sets: int = BUILD_SET_CANDIDATES_PER_ROLE


# ------------------------------------------------------------------ 役割の解決 (純粋)
def template_of(role: str, templates: dict = BUILD_ROLE_TEMPLATES, aliases: dict = BUILD_ROLE_ALIASES) -> tuple:
    """役割 id → (雛形名, 雛形, 場の種別 or None)。天候・フィールドの始動役 / エースは接尾辞で解決する"""
    if role in templates:
        return role, templates[role], None
    if role in aliases:
        return aliases[role], templates[aliases[role]], None
    for w in WEATHERS:
        if role == f"{w}_setter":
            return "weather_setter", templates["weather_setter"], w
        if role == f"{w}_abuser":
            return "weather_ace", templates["weather_ace"], w
    for t in TERRAINS:
        if role == f"{t}_setter":
            return "terrain_setter", templates["terrain_setter"], t
        if role == f"{t}_abuser":
            return "terrain_ace", templates["terrain_ace"], t
    raise KeyError(f"未知の役割: {role}")


def classify_bulk(evs: str, item: Optional[str] = None, template_spread: str = "", bulk_min: int = BUILD_BULK_POINTS_MIN,
                  fast_min: int = BUILD_FAST_POINTS_MIN) -> str:
    """型を「耐久 (bulky)」か「速攻 (fast)」に分ける (D-03、§7.2)。壁・耐久の定型、HP か防御側への投資、回復系の持ち物なら耐久。
    素早さ投資があり耐久投資が無い、またはタスキ・たま・こだわりなら速攻。どちらでもなければ耐久"""
    pts = [int(p) for p in (evs or "0/0/0/0/0/0").split("/")] if evs and evs.count("/") == 5 else [0] * 6
    hp, de, sd, sp = pts[0], pts[2], pts[4], pts[5]
    if template_spread.startswith(("wall", "tr_bulky")) or template_spread == "bulky":
        return "bulky"
    if item in ("leftovers", "rockyhelmet", "sitrusberry"):
        return "bulky"
    if hp >= bulk_min or de >= bulk_min or sd >= bulk_min:
        return "bulky"
    if sp >= fast_min or item in ("focussash", "lifeorb", "choicescarf", "choiceband", "choicespecs"):
        return "fast"
    return "bulky"


def effective_accuracy(entry: dict, has_field: Optional[dict] = None):
    """並びの天候を踏まえた命中 (None = 必中)。ぼうふう / かみなり は雨で必中、ふぶき は雪で必中 (効果表 accuracy_weather)"""
    acc = entry.get("accuracy")
    w = (has_field or {}).get("weather")
    aw = entry.get("accuracy_weather") or {}
    key = {"sandstorm": "sand", "sunnyday": "sun", "raindance": "rain", "snowscape": "snow", "hail": "snow"}.get(w or "", w)
    if w and (w in aw or key in aw):
        v = aw.get(w, aw.get(key))
        return None if v is True else v
    return acc


def demerit_kinds(entry: dict, has_field: Optional[dict] = None) -> list:
    """技のデメリットの種類 (効果表から。優先表の印と同じ定義。命中は並びの天候込み)"""
    out = []
    sec = entry.get("secondary") or {}
    if any(v < 0 for v in (entry.get("self_boosts") or {}).values()) or \
            (sec.get("chance") == 100 and any(v < 0 for v in (sec.get("self_boosts") or {}).values())):
        out.append("selfdrop")
    if entry.get("self_volatile"):
        out.append("selfvolatile")
    if entry.get("recoil") or entry.get("mindblown"):
        out.append("recoil")
    if entry.get("crash"):
        out.append("crash")
    if entry.get("locked"):
        out.append("locked")
    if entry.get("charge"):
        out.append("charge")
    if entry.get("recharge"):
        out.append("recharge")
    if entry.get("selfdestruct") or entry.get("condition") == "selfko":
        out.append("selfdestruct")
    acc = effective_accuracy(entry, has_field)
    if acc is not None and acc < 90:
        out.append("lowacc")
    cond = entry.get("condition")
    if cond and cond not in ("selfko", "hp_cost"):
        out.append("condition")
    if cond == "hp_cost":
        out.append("hp_cost")
    return out


def attack_allowed(entry: dict, bulk: str, ability: Optional[str], item: Optional[str], has_field: dict,
                   role_name: str, allow_selfko: bool = False) -> tuple:
    """採用規則 (§7.3) → (使って良いか, 理由)。耐久の個体は安定技だけ (穴の救済は呼び出し側)、速攻の個体はデメリット技も可
    (ただし種類ごとの個別則)。条件つき技は並びの始動源 (has_field) で成立するものだけ"""
    kinds = demerit_kinds(entry, has_field)
    cond = entry.get("condition")
    # 条件つき: 場が要るものは始動源があるときだけ、きのみ / 眠り / 他の技を使い切る等は生成では使わない
    if cond == "terrain_required" and not has_field.get("terrain"):
        return False, "フィールドの始動源なし"
    if cond in ("berry_eaten", "target_asleep", "user_asleep", "other_moves_used", "user_berry", "user_item", "stockpile",
                "hit_by_physical", "hit_by_special", "hit_this_turn", "wait", "not_hit", "shared_type", "species", "delayed"):
        return False, f"条件つき ({cond})"
    if cond == "selfko" and not allow_selfko:
        return False, "自爆"
    if "charge" in kinds:
        # ため技はソーラービーム系が晴れのときだけ (field_effects の表で判定するため、ここでは天候の有無だけ見る)
        if not (entry.get("field_power", {}).get("weather") and has_field.get("weather") in ("sun", "sunnyday")):
            return False, "ため技"
        kinds = [k for k in kinds if k != "charge"]
    if "recharge" in kinds:
        return False, "反動で動けない"
    if "recoil" in kinds and ability not in ("rockhead", "reckless", "magicguard") and bulk == "bulky":
        return False, "反動 (耐久型)"
    if "crash" in kinds and bulk == "bulky":
        return False, "外すと自傷 (耐久型)"
    if "locked" in kinds and bulk == "bulky" and item not in ("choiceband", "choicespecs", "choicescarf"):
        return False, "数ターン固定 (耐久型)"
    if "selfvolatile" in kinds and bulk == "bulky":
        return False, "自分に不利な状態 (耐久型)"
    return True, ""


def setup_axes(setup_entry: dict, setup_kind: str, main_stat: str, speed_plan: str, ability: Optional[str]) -> tuple:
    """積み技の 3 軸 (§8) → (使えるか, 点)。offense: 主攻撃が上がるものだけ、defense: 守る側が上がるもの、
    トリックルーム計画では素早さを上げる技を使わない。点 = 上昇の段数の重みつき和 (主 1.0、素早さ 1.0 / 0.5、他 0.25、下降 −0.25)"""
    boosts = setup_entry.get("setup_boosts") or {}
    if not boosts or ability == "contrary":
        return False, 0.0
    up = {k: int(v) for k, v in boosts.items() if int(v) > 0}
    down = {k: int(v) for k, v in boosts.items() if int(v) < 0}
    if speed_plan == "trick_room" and up.get("spe"):
        return False, 0.0
    if setup_kind == "offense" and not up.get(main_stat):
        return False, 0.0
    if setup_kind == "defense" and not (up.get("def") or up.get("spd")):
        return False, 0.0
    if setup_kind == "none":
        return False, 0.0
    main = main_stat if setup_kind == "offense" else ("def" if up.get("def") else "spd")
    score = 1.0 * up.get(main, 0)
    for k, v in up.items():
        if k == main:
            continue
        score += (1.0 if speed_plan == "outspeed" else 0.5) * v if k == "spe" else 0.25 * v
    score -= 0.25 * sum(-v for v in down.values())
    return True, score


def drops_boosted_stat(entry: dict, boosted: dict) -> bool:
    """攻撃技が積みで上げた能力を自分で下げるか (あまのじゃくは呼び出し側で反転)"""
    drops = {k for k, v in (entry.get("self_boosts") or {}).items() if int(v) < 0}
    sec = entry.get("secondary") or {}
    if sec.get("chance") == 100:
        drops |= {k for k, v in (sec.get("self_boosts") or {}).items() if int(v) < 0}
    return bool(drops & {k for k, v in boosted.items() if int(v) > 0})


def choose_ability_fit(abilities: list, template: dict, ctx_tags: dict, ability_tags_of: Callable,
                       ability_pct: Optional[dict] = None) -> tuple:
    """特性を並びへの適合で選ぶ (§9.2)。点 = 雛形のタグとの一致 (順位の重み) + 文脈のタグ (ctx_tags: tag → 重み、
    例: intimidate 1.0 (担当が物理寄り)、weather_user 1.0 (並びの天候と一致)、immunity:<Type> (担当の技に多いタイプ))。
    価値 0 の特性は 0。同点は使用率 (ability_pct) → 先頭。戻り値 (特性, 点, 理由)"""
    best = (None, -1.0, "")
    want = list(template.get("ability_tags") or ())
    for ab in [a for a in abilities if a]:
        info = ability_tags_of(ab) or {}
        tags = set(info.get("tags") or ())
        score = 0.0
        why = []
        if info.get("value_zero"):
            score = 0.0
        else:
            for i, t in enumerate(want):
                if t in tags:
                    score += 1.0 - 0.1 * i
                    why.append(t)
            for t, w in ctx_tags.items():
                if t in tags or any(t == f"{tag}" for tag in tags):
                    score += float(w)
                    why.append(t)
            if "no_item" in tags:
                score -= 1.0
                why.append("no_item")
        score += 0.001 * float((ability_pct or {}).get(ab, 0.0))
        if score > best[1]:
            best = (ab, score, "/".join(why))
    return best


def pick_utility(kind: str, learnset: set, taken: list, ctx_prefs: dict, role_field: Optional[str] = None,
                 pools: dict = BUILD_ROLE_UTILITY_MOVES) -> Optional[str]:
    """補助枠 1 つを埋める。kind は "a|b" で代替可。候補は役割の表の順 (状態異常は ctx_prefs["status_order"]、
    場は役割の場の技)。既に入っている技は除く"""
    for k in kind.split("|"):
        if k == "field":
            cands = list(BUILD_ROLE_FIELD_MOVES.get(role_field or "", ()))
        elif k == "setup":
            cands = list(ctx_prefs.get("setup_order") or ())
        elif k == "status":
            cands = list(ctx_prefs.get("status_order") or pools.get("status", ()))
        elif k == "speed_control":
            cands = list(ctx_prefs.get("speed_control_order") or pools.get("speed_control", ()))
        else:
            cands = list(pools.get(k, ()))
        for m in cands:
            if m in learnset and m not in taken:
                return m
    return None


def pick_item(classes, category: str, stab_types: list, used: set, item_pct: dict, legal: Callable, role_field: Optional[str],
              ability: Optional[str], stone: Optional[str], mega_allowed: bool, setup_in_set: bool,
              selfdrop_in_set: bool) -> list:
    """持ち物の候補を雛形のクラスの順に (§10)。並びで使用済みは除く。使用率 (item_pct) がある持ち物はクラス内で先に。
    かるわざ / しゅうかく 等は消費アイテムを先に、自己能力低下の攻撃技があればしろいハーブも候補、こだわりは分類で決める"""
    out: list = []
    for cls in classes:
        if cls == "stone":
            cands = [stone] if (stone and mega_allowed) else []
        elif cls == "type_item":
            cands = [BUILD_TYPE_ITEMS[t] for t in stab_types if t in BUILD_TYPE_ITEMS]
        elif cls == "weather_rock":
            cands = [BUILD_WEATHER_ROCKS[role_field]] if role_field in BUILD_WEATHER_ROCKS else []
        elif cls == "choice":
            cands = [("choiceband" if category == "physical" else "choicespecs"), "choicescarf"]
        elif cls == "choice_power":
            cands = [("choiceband" if category == "physical" else "choicespecs")]
        else:
            cands = list(BUILD_ITEM_CLASSES.get(cls, ()))
        if cls in ("setup_berry", "sash") and ability in ("unburden", "harvest", "cudchew", "ripen", "gluttony"):
            cands = [c for c in cands if c.endswith("berry")] + [c for c in cands if not c.endswith("berry")]
        cands = [c for c in cands if c and c not in used and legal(c) and c not in out]
        cands.sort(key=lambda c: -float(item_pct.get(c, 0.0)))
        out.extend(cands)
    if selfdrop_in_set and ability == "unburden" and legal("whiteherb") and "whiteherb" not in used and "whiteherb" not in out:
        out.insert(0, "whiteherb")
    if setup_in_set:
        out = [c for c in out if not c.startswith("choice")]        # こだわり + 積み技は作らない
    return out


# ------------------------------------------------------------------ 生成 (副作用あり: 図鑑・learnset・ダメージ計算)
def _toid(name: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _species_abilities(species_id: str) -> list:
    from tools.team_build.interaction import _cdex_species
    e = _cdex_species().get(species_id) or {}
    return [_toid(v) for _k, v in sorted((e.get("abilities") or {}).items())]


def _stone_of(species_id: str) -> Optional[str]:
    from advisor.gimmick import stones_by_base
    rows = stones_by_base().get(_toid(species_id)) or []
    return rows[0][0] if rows else None


def _ability_info(ability_id: str) -> dict:
    from advisor.effects import ability_entry
    return ability_entry(ability_id)


def _threat_profile(ctx: RoleContext) -> dict:
    """担当する相手の特徴: 物理/特殊の重み、技タイプの重み、速さの分布"""
    from advisor.damage import effective_speed
    from advisor.dex import get_dex
    dex = get_dex()
    ids = ctx.targets if ctx.targets is not None else list(ctx.threat_views)
    phys = spec = 0.0
    types: dict = {}
    speeds: dict = {}
    for tid in ids:
        if tid not in ctx.threat_views:
            continue
        tv, moves = ctx.threat_views[tid]
        w = float(ctx.weights.get(tid, 1.0))
        speeds[tid] = float(effective_speed(tv))
        for m in moves:
            mi = dex.move(m) or {}
            if (mi.get("power") or 0) <= 0:
                continue
            if str(mi.get("category") or "").lower() == "physical":
                phys += w
            else:
                spec += w
            types[mi.get("type")] = types.get(mi.get("type"), 0.0) + w
    tot = sum(types.values()) or 1.0
    return {"ids": [t for t in ids if t in ctx.threat_views], "phys": phys, "spec": spec,
            "types": {k: v / tot for k, v in types.items()}, "speeds": speeds}


def _ctx_tags(profile: dict, ctx: RoleContext, role_field: Optional[str]) -> dict:
    tags: dict = {}
    if profile["phys"] >= profile["spec"] * 1.2:
        tags["intimidate"] = 1.0
    w = ctx.team_field.get("weather")
    t = ctx.team_field.get("terrain")
    if w or role_field in WEATHERS:
        tags["weather_user"] = 1.0
    if t or role_field in TERRAINS:
        tags["terrain_user"] = 1.0
    if ctx.speed_plan == "trick_room":
        tags["slow_offense"] = 0.5
    return tags


def _spread_for(template: dict, bulk_hint: str, category: str, profile: dict, base: dict, view_speed_fn: Callable) -> tuple:
    """(配分の定型名, evs, natures)。auto は担当への先手率で fast / bulky、wall_auto は被ダメの偏りで物理 / 特殊"""
    kind = template.get("spread", "auto")
    cat = "physical" if category == "physical" else "special"
    if kind == "auto":
        share = view_speed_fn()
        kind = "fast" if share >= BUILD_ROLE_FAST_SHARE else "bulky"
    if kind == "wall_auto":
        kind = "wall_physical" if profile["phys"] >= profile["spec"] else "wall_special"
    if kind in ("fast", "bulky", "tr", "tr_bulky"):
        name = f"{kind}_{cat}"
    else:
        name = kind
    evs, natures = BUILD_ROLE_SPREADS[name]
    return name, evs, natures


def generate_role_sets(species_id: str, role: str, ctx: RoleContext) -> list:
    """種 × 役割 → 一体の型の候補 [SetCandidate] (最大 ctx.max_sets、被覆の高い順)。候補が作れなければ空"""
    from advisor.damage import FieldView, calc_damage, effective_speed
    from advisor.dex import get_dex
    from advisor.effects import move_entry
    from advisor.ev_infer import _nature_mult
    from tools.team_build import gen_sets as G
    from tools.team_build.interaction import _points_to_ev, view_from_set
    from tools.team_build.learnsets import learnset_of
    from tools.team_build.sets import legal_item, set_sanity

    dex = get_dex()
    sp = dex.species(species_id)
    if not sp:
        return []
    tname, template, role_field = template_of(role)
    learnset = set(ctx.learnset if ctx.learnset is not None else learnset_of(species_id))
    if not learnset:
        return []
    base = dict(sp["baseStats"])
    types = list(sp["types"])
    if tname == "wall" and int(base.get("spe", 0)) >= BUILD_WALL_SPEED_MAX:
        return []                                   # 速い種は壁型にしない (D-17)
    profile = _threat_profile(ctx)
    targets = profile["ids"]
    if not targets:
        return []
    weights = {t: float(ctx.weights.get(t, 1.0)) for t in targets}
    wmax = max(weights.values()) or 1.0
    norm_w = {t: w / wmax for t, w in weights.items()}
    field_have = {"terrain": ctx.team_field.get("terrain"), "weather": ctx.team_field.get("weather")}
    if role_field in WEATHERS:
        field_have["weather"] = field_have["weather"] or WEATHER_FIELD_NAME[role_field]
    if role_field in TERRAINS:
        field_have["terrain"] = field_have["terrain"] or role_field

    # ---- 特性 (自分の特性で張る場も始動源に数える)
    abilities = list(ctx.abilities) if ctx.abilities is not None else _species_abilities(species_id)
    stone = _stone_of(species_id) if ctx.mega_allowed else None
    ability, _ab_score, ab_why = choose_ability_fit(abilities, template, _ctx_tags(profile, ctx, role_field), _ability_info,
                                                   ctx.ability_pct)
    own = G.own_field(ability, ())
    field_have = G.merge_fields(field_have, own)
    fv = FieldView(terrain=field_have.get("terrain"), weather=field_have.get("weather"))

    # ---- 攻撃の分類 (物理 / 特殊) は種族値で。両方近ければ両方の技を候補にする
    atk, spa = int(base.get("atk") or 0), int(base.get("spa") or 0)
    category = "physical" if atk >= spa else "special"
    both = min(atk, spa) >= 0.85 * max(atk, spa)
    main_stat = "atk" if category == "physical" else "spa"

    # ---- 配分の方針 (担当への先手率)
    def fast_share() -> float:
        ev_fast = _points_to_ev(BUILD_ROLE_SPREADS[f"fast_{category}"][0])
        nat = _nature_mult("jolly" if category == "physical" else "timid")
        from advisor.damage import MonView
        v = MonView(species_id=species_id, types=types, base=base, ev=ev_fast, nature=nat, ability=ability)
        spe = float(effective_speed(v, fv))
        return sum(norm_w[t] for t in targets if spe > profile["speeds"][t]) / (sum(norm_w.values()) or 1.0)
    spread_name, evs, natures = _spread_for(template, "", category, profile, base, fast_share)
    bulk = classify_bulk(evs, None, template.get("spread", ""))
    if ctx.speed_plan == "trick_room" and not spread_name.startswith(("wall", "tr")):
        # トリックルーム計画: 攻撃役は素早さ 0 で下を取る (−Spe の性格)、補助役は耐久の定型のまま
        kind = f"tr_{category}" if template.get("offensive", True) else f"tr_bulky_{category}"
        spread_name, (evs, natures) = kind, BUILD_ROLE_SPREADS[kind]
        bulk = "bulky"

    # ---- 攻撃技の候補 (効果表の 2 階層 + 採用規則)
    allow_selfko = any(k.startswith("selfko") for k in template.get("utility", ()))
    attack_pool: dict = {}
    for m in sorted(learnset):
        e = move_entry(m)
        mi = dex.move(m) or {}
        if not mi or str(mi.get("category") or "").lower() == "status" or e.get("nonstandard"):
            continue
        if (mi.get("power") or 0) <= 0 and not e.get("variable_power") and not e.get("fixed_damage"):
            continue
        cat = str(mi.get("category") or "").lower()
        if cat != category and not both and template.get("offensive", True):
            continue
        if e.get("fixed_damage") and template.get("offensive", True):
            continue                                  # 固定ダメージは壁 / 補助役だけ
        ok, why = attack_allowed(e, bulk, ability, None, field_have, tname, allow_selfko)
        ok_choice, _w2 = attack_allowed(e, bulk, ability, "choiceband", field_have, tname, allow_selfko)
        kinds = demerit_kinds(e, field_have)
        attack_pool[m] = {"entry": e, "cat": cat, "type": mi.get("type"), "tier": "stable" if not kinds else "other",
                          "allowed": ok, "allowed_choice": ok_choice, "why": why, "kinds": kinds}

    # ---- 攻撃側のビュー (定型の配分) と担当への期待ダメージ表
    def attacker_view(item: Optional[str], nature: str, evs_s: str):
        row = {"item": item, "ability": ability, "nature": nature, "evs": evs_s, "moves": []}
        view, _ = view_from_set(species_id, row)
        return view

    def damage_table(view, moves) -> dict:
        tbl: dict = {}
        for m in moves:
            tbl[m] = {}
            for t in targets:
                tv, _tm = ctx.threat_views[t]
                try:
                    tbl[m][t] = calc_damage(view, tv, m, fieldv=fv).get("expected", 0.0) / 100.0
                except Exception:
                    tbl[m][t] = 0.0
        return tbl

    nature0 = natures[0]
    view0 = attacker_view(None, nature0, evs)
    cand_moves = [m for m, a in attack_pool.items() if a["allowed"]]
    # こだわり持ちなら数ターン固定の技 (げきりん等) も候補 (§7.3)。表はその分も引いておく
    choice_extra = [m for m, a in attack_pool.items() if a["allowed_choice"] and not a["allowed"]]
    table = damage_table(view0, cand_moves + choice_extra)
    # 耐久型: 安定技だけで担当に空く穴がデメリット技で埋まるときだけ、その技を許す (BUILD_DEMERIT_NEED_MIN)
    if bulk == "bulky":
        stable_best = {t: max((table[m].get(t, 0.0) for m in cand_moves if attack_pool[m]["tier"] == "stable"), default=0.0)
                       for t in targets}
        for m in list(cand_moves):
            if attack_pool[m]["tier"] == "other":
                gain = max((min(1.0, table[m].get(t, 0.0)) - min(1.0, stable_best[t])) * norm_w[t] for t in targets)
                if gain < BUILD_DEMERIT_NEED_MIN:
                    cand_moves.remove(m)
                    attack_pool[m]["allowed"] = False
                    attack_pool[m]["why"] = "安定技で足りる (耐久型)"
    # 速攻型: デメリット技は「担当 (重み ≥ BUILD_OHKO_THREAT_MIN_W) を安定技なら 2 発のところ 1 発にできる」ときだけ許し、
    # その相手に加点 (§7.4)。数ターン固定 (げきりん) は速攻型なら加点無しでも可 (§7.3)
    bonus: dict = {m: {} for m in cand_moves}
    if bulk == "fast":
        stable_best = {t: max((table[m].get(t, 0.0) for m in cand_moves if attack_pool[m]["tier"] == "stable"), default=0.0)
                       for t in targets}
        drop: list = []
        for m in cand_moves:
            if attack_pool[m]["tier"] != "other":
                continue
            for t in targets:
                if norm_w[t] >= BUILD_OHKO_THREAT_MIN_W and table[m].get(t, 0.0) >= 1.0 and stable_best[t] < 1.0:
                    bonus[m][t] = BUILD_OHKO_BONUS
            if not bonus[m] and attack_pool[m]["kinds"] != ["locked"]:
                drop.append(m)
        for m in drop:
            cand_moves.remove(m)
            attack_pool[m]["allowed"] = False
            attack_pool[m]["why"] = "1 発化に効かない (速攻型)"
    scored = {m: {t: min(1.0, table[m].get(t, 0.0)) + bonus.get(m, {}).get(t, 0.0) for t in targets}
              for m in cand_moves + choice_extra}

    # ---- 積み技 (3 軸)
    setup_order: list = []
    if template.get("setup_kind", "none") != "none":
        rows = []
        for m in sorted(learnset):
            e = move_entry(m)
            if not e.get("setup_boosts"):
                continue
            ok, s = setup_axes(e, template["setup_kind"], main_stat, ctx.speed_plan, ability)
            if ok:
                rows.append((s, m))
        rows.sort(key=lambda r: (-r[0], r[1]))
        setup_order = [m for _s, m in rows]
    status_order = list(BUILD_ROLE_UTILITY_MOVES["status"])
    if profile["phys"] >= profile["spec"]:
        status_order.sort(key=lambda m: 0 if m in ("spore", "willowisp") else 1)
    else:
        status_order.sort(key=lambda m: 0 if m in ("spore", "thunderwave") else 1)
    speed_order = ["trickroom", "tailwind", "stickyweb", "thunderwave", "icywind", "electroweb"]
    if ctx.speed_plan == "trick_room":
        speed_order = ["trickroom"] + [m for m in speed_order if m != "trickroom"]
    elif ctx.speed_plan == "outspeed":
        speed_order = ["tailwind", "stickyweb", "thunderwave", "icywind", "electroweb"]
    prefs = {"setup_order": setup_order, "status_order": status_order, "speed_control_order": speed_order}

    # ---- 候補の組み立て: 補助枠 → 攻撃技 (積み技があれば上げた能力を下げる技を除く) → 持ち物 → 配分の微調整
    out: list = []
    seen: set = set()
    stab_types = [t for t in types]

    def build(item: Optional[str], setup_choice: Optional[str]) -> Optional[SetCandidate]:
        taken: list = []
        notes = [f"role:{role}", f"template:{tname}", f"targets:{len(targets)}", f"spread:{spread_name}:{bulk}",
                 f"ability:{ability}:{ab_why or 'default'}"]
        for kind in template.get("utility", ()):
            if kind.split("|")[0] == "setup" and setup_choice:
                taken.append(setup_choice)
                notes.append(f"setup:{setup_choice}")
                continue
            m = pick_utility(kind, learnset, taken, prefs, role_field)
            if m is None:
                if "|" in kind or kind in ("setup",):
                    continue
                return None
            taken.append(m)
            notes.append(f"utility:{kind}:{m}")
        boosted = (move_entry(setup_choice).get("setup_boosts") or {}) if setup_choice else {}
        pool = [m for m in cand_moves if m not in taken]
        if item and item.startswith("choice"):
            pool += [m for m in choice_extra if m not in taken]
        if boosted and ability != "contrary":
            pool = [m for m in pool if not drops_boosted_stat(attack_pool[m]["entry"], boosted)]
        if ability == "contrary":
            pool.sort(key=lambda m: 0 if "selfdrop" in attack_pool[m]["kinds"] else 1)
        n_attacks = int(template.get("attacks", 3))
        n_attacks = min(n_attacks, 4 - len(taken))
        # メガ石を持つ候補はメガ後の種族値・タイプ・特性で担当への期待ダメージを引き直す (view_from_set が石でフォルムを切り替える)
        use_scored = scored
        if item and stone and item == stone:
            tbl_m = damage_table(attacker_view(item, nature0, evs), pool)
            use_scored = {m: {t: min(1.0, tbl_m[m].get(t, 0.0)) + bonus.get(m, {}).get(t, 0.0) for t in targets} for m in pool}
        chosen = G.greedy_attacks({m: use_scored[m] for m in pool}, n_attacks, norm_w)
        if len(chosen) < min(n_attacks, 1):
            return None
        moves = chosen + taken
        if len(moves) > 4:
            moves = moves[:4]
        nature = natures[0]
        if len(natures) > 1 and bulk == "fast":
            nature = natures[0] if fast_share() >= BUILD_ROLE_FAST_SHARE else natures[1]
        cats = [attack_pool[m]["cat"] for m in chosen]
        if spread_name.startswith("wall"):
            nature = G.wall_nature_for_moves(natures, cats, category == "physical")
        c = SetCandidate(species_id, ability, item, nature, evs, moves, f"role:{role}", 0.0, notes)
        for m in chosen:
            notes.append(f"attack:{m}:{attack_pool[m]['tier']}" + (":" + "+".join(attack_pool[m]["kinds"]) if attack_pool[m]["kinds"] else ""))
        # 被覆 (担当の重みつき)
        cov = 0.0
        for t in targets:
            best = max((use_scored[m].get(t, 0.0) for m in chosen), default=0.0)
            cov += norm_w[t] * min(1.0, best)
        c.score = round(cov / (sum(norm_w.values()) or 1.0), 4)
        return c

    setup_choices = [setup_order[0]] if setup_order else [None]
    if "setup" in "|".join(template.get("utility", ())) and not setup_order:
        setup_choices = [None]
    selfdrop_any = any("selfdrop" in attack_pool[m]["kinds"] for m in cand_moves)
    items = pick_item(template.get("items", ()), category, stab_types, set(ctx.used_items), ctx.item_pct, legal_item, role_field,
                      ability, stone, ctx.mega_allowed, bool(setup_choices[0]), selfdrop_any)
    if not items:
        items = [None]
    for item in items:
        for sc in setup_choices:
            c = build(item, sc)
            if c is None or c.key() in seen:
                continue
            if "rest" in c.moves and "sleeptalk" not in c.moves and c.item != "chestoberry":
                # ねむる単体は作らない: カゴのみに差し替える (§13。カゴが使用済み / 不合法なら候補にしない)
                if "chestoberry" in ctx.used_items or not legal_item("chestoberry"):
                    continue
                c = SetCandidate(c.species_id, c.ability, "chestoberry", c.nature, c.evs, list(c.moves), c.source, c.score,
                                 list(c.notes) + ["item:chestoberry:rest"])
                if c.key() in seen:
                    continue
            if set_sanity(c):
                c.notes.append("sanity:" + ";".join(set_sanity(c)))
                continue
            seen.add(c.key())
            out.append(c)
            if len(out) >= ctx.max_sets:
                break
        if len(out) >= ctx.max_sets:
            break
    out.sort(key=lambda c: -c.score)
    return out[:ctx.max_sets]
