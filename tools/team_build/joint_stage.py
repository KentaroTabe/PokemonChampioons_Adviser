"""S5 統合段 (並びと型の同時探索) の配線: run の成果物 (相手プールの分割、脅威、S3 の特徴、S4 の構想、仕様) から
lineup_search を動かし、従来の s05_candidates.json / s06_sets.json / s06_sets/<並び>.txt と同じ形で保存する。

- 相手プール = opponent_families.json の探索 tier (系統 = チーム、重み = チーム数 × セッションの指定)
- 型の候補 = 役割の雛形 (role_sets.generate_role_sets) + 使用率の代表型 (合成の印つき 1 候補) + 指定の型 (その種はそれだけ)
- 被覆の行 = interaction_row (型の場込み) → coverage_value
- 種の事前の絞り込み = S3 の特徴 (代表型の被覆) で穴の相手に強い順 + 未充足の役割を満たせる種
- 現行チーム枝 = 登録の型を一体の候補として固定 (役割は型から逆引き) し近傍 (1 枠入替) と同じ評価で測る
純粋な判断 (役割の逆引き、役割を満たせるか、要求の導出) はこのモジュールの関数に置き、DB・図鑑・learnset は閉じた callable にする。
"""
from __future__ import annotations

import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Callable, Optional

from champions_agent.config import (BUILD_ARCHETYPE_SPEED_PLAN, BUILD_COMPLEMENT_ROLE_SPECIES_K, BUILD_INCUMBENT_NEIGHBORS,
                                    BUILD_JOINT_REFINE_TARGETS, BUILD_LOCK_IMMUNE_DISCOUNT, BUILD_ROLE_FIELD_MOVES,
                                    BUILD_ROLE_OFFENSE_MIN, BUILD_ROLE_SUPPORT_BULK_MIN, BUILD_ROLE_UTILITY_MOVES,
                                    BUILD_ROLE_WALL_OFFENSE_MAX, BUILD_SELFKO_COST, BUILD_SESSION_THREAT_BOOST,
                                    BUILD_SPECIES_SHARE_MAX)
from tools.team_build import candidates as C
from tools.team_build import sets as S
from tools.team_build.lineup_search import (TEAM_SIZE, LineupResult, LineupSearch, OppPool, OppSet, SearchConfig, SetEntry,
                                            concept_requirements, role_matches, team_field_from)  # noqa: F401 (repair が使う)
from tools.team_build.role_sets import (TERRAINS, WEATHER_FIELD_NAME, WEATHERS, bulk_product, pick_utility, template_of,
                                        wall_speed_ok)

FIELD_WEATHER_ROLE = {v: k for k, v in WEATHER_FIELD_NAME.items()}      # 場の名前 → 役割 id の接頭辞 (sandstorm → sand)
TR_NATURES = ("brave", "quiet", "relaxed", "sassy")


# ------------------------------------------------------------------ 純粋な判断
def infer_role(cand, category_of: Optional[Callable] = None, setup_of: Optional[Callable] = None,
               field_of: Optional[Callable] = None) -> str:
    """型 → 役割 id (登録の型・指定の型・代表型の逆引き)。技と持ち物と性格から機械的に:
    トリックルーム → tr_setter、壁技 → screens_*、設置 → hazard_lead、除去 → hazard_removal、天候・フィールドの始動 (特性/技) →
    <場>_setter、積み技 → sweeper_setup、交代技 → pivot、回復 + 攻撃技 2 本以下 → wall、状態異常 + 攻撃技 2 本以下 →
    status_spreader、スカーフ / 先制技 → cleaner、−Spe の性格 → tr_ace、それ以外 → breaker"""
    moves = list(cand.moves or [])
    ms = set(moves)
    util = BUILD_ROLE_UTILITY_MOVES
    cat = category_of or (lambda m: "")
    n_attacks = sum(1 for m in moves if cat(m) not in ("status", ""))
    if "trickroom" in ms:
        return "tr_setter"
    if "auroraveil" in ms:
        return "screens_veil"
    if len(ms & set(util["screens"])) >= 2 or (ms & set(util["screens"]) and cand.item == "lightclay"):
        return "screens_dual"
    if field_of is not None:
        f = field_of(cand.ability, moves) or {}
        if f.get("terrain"):
            return f"{f['terrain']}_setter"
        if f.get("weather"):
            return f"{FIELD_WEATHER_ROLE.get(f['weather'], f['weather'])}_setter"
    if ms & set(util["hazard"]):
        return "hazard_lead"
    if ms & set(util["removal"]):
        return "hazard_removal"
    if setup_of is not None and any(setup_of(m) for m in moves):
        return "sweeper_setup"
    if ms & set(util["pivot"]):
        return "pivot"
    if ms & set(util["heal"]) and n_attacks <= 2:
        return "wall"
    if ms & set(util["status"]) and n_attacks <= 2:
        return "status_spreader"
    if cand.item == "choicescarf" or (ms & set(util["priority"]) and n_attacks >= 3):
        return "cleaner"
    if (cand.nature or "") in TR_NATURES:
        # −Spe の性格だけでは決めない (2026-10-04: 現行のアシレーヌが のんき だけで TR 要員になっていた)。
        # 回復・状態技があれば受け、攻撃技 3 本以上で素早さ 0 なら tr_ace、それ以外は breaker
        if ms & set(util["heal"]) or ms & set(util["status"]):
            return "wall"
        if n_attacks >= 3 and _speed_points(cand.evs) == 0:
            return "tr_ace"
    return "breaker"


def _speed_points(evs) -> int:
    try:
        return int(str(evs or "").split("/")[5])
    except (IndexError, ValueError):
        return 0


def demote_tr_ace(entries: list) -> list:
    """並びの文脈で役割を直す: トリックルームの始動役 (tr_setter) が居ない並びに tr_ace は居ない → breaker に (純粋)。
    登録の型・行の型の逆引き (infer_role は 1 体ずつ見る) の後に呼ぶ"""
    if any(role_matches(e.role, "tr_setter") for e in entries):
        return entries
    out = []
    for e in entries:
        if e.role == "tr_ace":
            e = replace(e, role="breaker")
        out.append(e)
    return out


SUPPORT_BULK_TEMPLATES = ("wall", "hazard_removal", "phazer", "pivot", "status_spreader", "support_screens", "support_veil")
LEAD_TEMPLATES = ("hazard_lead", "suicide_lead", "speed_control")


def role_aptitude(role: str, learnset: set, abilities: list, base: dict, field_of: Callable) -> float:
    """種がその役割にどれだけ向くか (0 = 作れない / 向かない、1 に近いほど適任)。純粋。
    2026-10-04: 被覆の高い種が名目だけ役割を満たしていた (リザードンの吹き飛ばし役、エンブオーの受け、ガブリアスの雨始動、
    カイリューの雪始動) ので、受け・設置除去・吹き飛ばし・技だけの始動役には耐久 (BUILD_ROLE_SUPPORT_BULK_MIN) と
    攻撃種族値の上限 (BUILD_ROLE_WALL_OFFENSE_MAX) を要求し、攻撃役には火力を要求する"""
    try:
        tname, template, rfield = template_of(role)
    except KeyError:
        return 0.0
    atk, spa = int(base.get("atk") or 0), int(base.get("spa") or 0)
    off = max(atk, spa)
    bulk = bulk_product(base)
    if template.get("offensive", True):
        if off < BUILD_ROLE_OFFENSE_MIN:
            return 0.0
        apt = min(1.0, off / 150.0)
    else:
        apt = min(1.0, bulk / float(2 * BUILD_ROLE_SUPPORT_BULK_MIN))
    if tname == "wall":
        if not wall_speed_ok(base) or off > BUILD_ROLE_WALL_OFFENSE_MAX or bulk < BUILD_ROLE_SUPPORT_BULK_MIN:
            return 0.0
    elif tname in SUPPORT_BULK_TEMPLATES:
        if bulk < BUILD_ROLE_SUPPORT_BULK_MIN:
            return 0.0
    if tname in ("weather_setter", "terrain_setter", "support_veil"):
        want = WEATHER_FIELD_NAME.get(rfield, rfield) if rfield in WEATHERS else rfield
        kind = "weather" if rfield in WEATHERS else "terrain"
        by_ability = any((field_of(a, ()) or {}).get(kind) == want for a in abilities)
        by_move = bool(set(BUILD_ROLE_FIELD_MOVES.get(rfield or "", ())) & learnset)
        if tname != "support_veil" and not (by_ability or by_move):
            return 0.0
        if tname != "support_veil" and not by_ability:
            # 技だけの始動役: 耐久があり攻撃役でない種に限る (ガブリアスの あまごい / カイリューの ゆきげしき を始動役にしない)
            if bulk < BUILD_ROLE_SUPPORT_BULK_MIN or off > BUILD_ROLE_WALL_OFFENSE_MAX:
                return 0.0
        elif by_ability:
            apt = min(1.0, apt + 0.3)
    prefs = {"status_order": list(BUILD_ROLE_UTILITY_MOVES["status"]),
             "speed_control_order": list(BUILD_ROLE_UTILITY_MOVES["speed_control"])}
    for kind in template.get("utility", ()):
        if "|" in kind or kind in ("setup", "field"):
            continue
        if pick_utility(kind, learnset, [], prefs, rfield) is None:
            return 0.0
    return round(max(apt, 0.05), 3)


def role_capable(role: str, learnset: set, abilities: list, base: dict, field_of: Callable) -> bool:
    """種がその役割の型を作れそうか (雛形の必須の補助枠・始動源・火力・速さ・耐久)。事前の絞り込み用の軽い判定"""
    return role_aptitude(role, learnset, abilities, base, field_of) > 0.0


def _role_capable_legacy(role: str, learnset: set, abilities: list, base: dict, field_of: Callable) -> bool:
    try:
        tname, template, rfield = template_of(role)
    except KeyError:
        return False
    atk, spa = int(base.get("atk") or 0), int(base.get("spa") or 0)
    if template.get("offensive", True) and max(atk, spa) < BUILD_ROLE_OFFENSE_MIN:
        return False
    if tname == "wall" and not wall_speed_ok(base):
        return False
    if tname in ("weather_setter", "terrain_setter", "support_veil"):
        want = WEATHER_FIELD_NAME.get(rfield, rfield) if rfield in WEATHERS else rfield
        kind = "weather" if rfield in WEATHERS else "terrain"
        by_ability = any((field_of(a, ()) or {}).get(kind) == want for a in abilities)
        by_move = bool(set(BUILD_ROLE_FIELD_MOVES.get(rfield or "", ())) & learnset)
        if tname != "support_veil" and not (by_ability or by_move):
            return False
    prefs = {"status_order": list(BUILD_ROLE_UTILITY_MOVES["status"]),
             "speed_control_order": list(BUILD_ROLE_UTILITY_MOVES["speed_control"])}
    for kind in template.get("utility", ()):
        if "|" in kind or kind in ("setup", "field"):
            continue
        if pick_utility(kind, learnset, [], prefs, rfield) is None:
            return False
    return True


def default_roles(base: dict, learnset: set, max_roles: int = 3) -> list:
    """役割の指定が無い種に試す役割 (雛形名、最大 max_roles)。火力があれば 積みエース (積み技を覚えれば) と breaker、
    交代技があれば pivot、設置技があれば hazard_lead、火力が控えめで回復技があり速くなければ wall"""
    from advisor.effects import move_entry
    atk, spa, spe = int(base.get("atk") or 0), int(base.get("spa") or 0), int(base.get("spe") or 0)
    util = BUILD_ROLE_UTILITY_MOVES
    off = max(atk, spa)
    roles: list = []
    if off >= BUILD_ROLE_OFFENSE_MIN:
        if any(move_entry(m).get("setup_boosts") for m in learnset):
            roles.append("sweeper_setup")
        roles.append("breaker")
    if off <= BUILD_ROLE_WALL_OFFENSE_MAX and wall_speed_ok(base) and bulk_product(base) >= BUILD_ROLE_SUPPORT_BULK_MIN \
            and (set(util["heal"]) & learnset):
        roles.append("wall")
    if set(util["pivot"]) & learnset:
        roles.append("pivot")
    if set(util["hazard"]) & learnset:
        roles.append("hazard_lead")
    if not roles:
        roles.append("breaker" if off >= spe else "status_spreader")
    return roles[:max_roles]


def rule_roles(rule_field: Optional[dict]) -> list:
    """規則の場 → 要求する役割 [(role, n)] (始動役 1 + 恩恵を受ける役 1)"""
    out: list = []
    t = (rule_field or {}).get("terrain")
    w = (rule_field or {}).get("weather")
    if t in TERRAINS:
        out += [(f"{t}_setter", 1), (f"{t}_abuser", 1)]
    if w:
        p = FIELD_WEATHER_ROLE.get(w, w)
        if p in WEATHERS:
            out += [(f"{p}_setter", 1), (f"{p}_abuser", 1)]
    return out


def branch_roles_of(concept: dict, archetypes: Optional[dict] = None) -> list:
    """軸つきの構想 → 分岐の役割の最小数 [(role, n)] (特殊な分岐の併用も足す)"""
    if archetypes is None:
        from tools.team_build.archetypes import ARCHETYPES
        archetypes = ARCHETYPES
    out: list = []
    axis, branch = concept.get("archetype"), concept.get("branch")
    br = ((archetypes.get(axis or "") or {}).get("branches") or {}).get(branch or "")
    if br:
        out += [(r, int(n)) for r, n in br.get("roles", [])]
    sb = concept.get("special_branch")
    sbr = ((archetypes.get("special") or {}).get("branches") or {}).get(sb or "")
    if sbr and axis != "special":
        out += [(r, int(n)) for r, n in sbr.get("roles", [])]
    return out


def role_for_required(role: str, required_moves: list, is_setup: Callable, fallback: str = "sweeper_setup") -> str:
    """技の指定に積み技がある種の役割 (純粋): 求められた役割の雛形が攻撃役でなければ積みエース (fallback) にする。
    攻撃役の役割 (breaker / setup_ace / 天候・フィールドのエース等) や未知の役割、積み技の指定が無い種はそのまま"""
    if not any(is_setup(m) for m in (required_moves or [])):
        return role
    try:
        _tname, template, _f = template_of(role)
    except KeyError:
        return role
    return role if template.get("offensive", True) else fallback


def to_lineup(r: LineupResult) -> C.Lineup:
    return C.Lineup(tuple(r.members), r.concept, float(r.score), dict(r.parts), tag=r.tag, origin=dict(r.origin))


def family_weight(n_teams: int, species: set, session_weights: Optional[dict], boost: float = BUILD_SESSION_THREAT_BOOST) -> float:
    """系統の重み = チーム数 × (1 + boost × セッションの指定の最大)"""
    w = float(n_teams)
    if session_weights:
        m = max((float(session_weights.get(s, 0.0)) for s in species), default=0.0)
        w *= 1.0 + boost * m
    return w


# ------------------------------------------------------------------ 配線 (DB・図鑑・learnset を閉じる)
def pool_from_split(doc: dict, tier: str = "search", session_weights: Optional[dict] = None) -> OppPool:
    """opponent_families.json → OppPool (その tier の系統とチーム本文から)"""
    from tools.team_build.interaction import view_from_set
    fam_rows = [f for f in doc.get("families", []) if f.get("tier") == tier]
    teams: dict = {}
    for f in fam_rows:
        for tid in f.get("teams", []):
            text = (doc.get("texts") or {}).get(tid)
            if not text or tid in teams:
                continue
            lst = []
            for sid, c in S.parse_set_text(text).items():
                try:
                    view, moves = view_from_set(sid, c.as_row())
                except Exception:
                    continue
                lst.append(OppSet(c.key(), sid, view, moves))
            teams[tid] = lst
    fams = []
    for f in fam_rows:
        species = {s for t in f.get("teams", []) for s in ((doc.get("teams") or {}).get(t) or {}).get("species", [])}
        fams.append((f["family_id"], family_weight(len(f.get("teams", [])), species, session_weights), list(f.get("teams", []))))
    return OppPool.build(teams, fams)


def selfko_adjust(with_move, without_move, cost: float = BUILD_SELFKO_COST):
    """自爆・捨て技の 1 回だけの費用 (§12、純粋): 行 = 技なしの行に、利得 (技あり − 技なし) が最大の相手 1 体へだけ利得を足す。
    利得は「使った個体を失う費用」としてその個体の他の相手への被覆の平均 × cost だけ割り引く (負にはしない)"""
    import numpy as np
    w = np.asarray(with_move, dtype=np.float32)
    wo = np.asarray(without_move, dtype=np.float32)
    gain = np.clip(w - wo, 0.0, None)
    out = wo.copy()
    if gain.size and float(gain.max()) > 0.0:
        j = int(np.argmax(gain))
        out[j] = wo[j] + float(gain[j]) * max(0.0, 1.0 - float(cost) * float(wo.mean()))
    return out


def selfko_moves(moves: list) -> list:
    from advisor.effects import move_entry
    return [m for m in moves if move_entry(m).get("selfdestruct") or move_entry(m).get("condition") == "selfko"]


def locked_move_types(moves: list) -> list:
    """数ターン固定の攻撃技 (げきりん等) のタイプ (効果表 locked)"""
    from advisor.dex import get_dex
    from advisor.effects import move_entry
    dex = get_dex()
    out = []
    for m in moves:
        if move_entry(m).get("locked"):
            t = (dex.move(m) or {}).get("type")
            if t:
                out.append(t)
    return out


def lock_immune_adjust(vec, pool: OppPool, immune_rows: set, discount: float = BUILD_LOCK_IMMUNE_DISCOUNT):
    """こだわり + 固定技の型の行 (純粋): 固定技を無効にする相手 (immune_rows = 相手の型の index) が居る系統の相手への被覆を
    (1 − discount) 倍にする (技に固定されている間にその相手へ交代されると何もできない)。他の系統はそのまま"""
    import numpy as np
    out = np.asarray(vec, dtype=np.float32).copy()
    if not immune_rows or discount <= 0:
        return out
    hit: set = set()
    for _fid, _w, rows in pool.families:
        if immune_rows & set(rows):
            hit |= set(rows)
    for r in hit:
        out[r] *= max(0.0, 1.0 - float(discount))
    return out


def make_row_post(row_fn: Callable) -> Callable:
    """行の後処理: 自爆技を持つ型 (selfko_adjust、技なしの行を別に計算する) と、こだわり + 固定技の型 (lock_immune_adjust)"""
    from dataclasses import replace as _replace
    from advisor.dex import get_dex
    dex = get_dex()

    def post(entry: SetEntry, vec, pool: OppPool):
        ko = selfko_moves(entry.moves)
        rest = [m for m in entry.moves if m not in ko]
        if ko and rest:
            alt = _replace(entry, moves=rest)
            without = [float(row_fn(alt, opp)) for opp in pool.sets]
            vec = selfko_adjust(vec, without)
        if entry.item and str(entry.item).startswith("choice"):
            types = locked_move_types(entry.moves)
            if types:
                immune = {i for i, opp in enumerate(pool.sets)
                          if any(dex.effectiveness(t, list(getattr(opp.view, "types", None) or [])) == 0.0 for t in types)}
                vec = lock_immune_adjust(vec, pool, immune)
        return vec
    return post


def make_row_fn() -> Callable:
    from advisor.damage import FieldView
    from tools.team_build.gen_sets import has_field
    from tools.team_build.interaction import _mega_stone_ids, coverage_value, interaction_row
    stones = _mega_stone_ids()

    def row_fn(entry: SetEntry, opp: OppSet) -> float:
        fv = FieldView(terrain=entry.field.get("terrain"), weather=entry.field.get("weather")) if has_field(entry.field) else None
        try:
            row = interaction_row(entry.species_id, entry.view, entry.moves, opp.species_id, opp.view, opp.moves, stones, fieldv=fv)
        except Exception:
            return 0.0
        return coverage_value(row)
    return row_fn


def entry_from_candidate(cand, role: str, team_field: Optional[dict], locked: bool = False) -> SetEntry:
    """SetCandidate → SetEntry (メガ石なら メガ後のビュー、評価の場 = 自分の場を優先して並びの始動源で埋める)"""
    from tools.team_build import gen_sets as G
    from tools.team_build.interaction import view_from_set
    view, moves = view_from_set(cand.species_id, cand.as_row())
    own = G.own_field(view.ability, moves)
    fld = G.merge_fields(own, team_field)
    return SetEntry((cand.key(), G.field_key(fld)), cand.species_id, role, cand, view, moves, cand.item,
                    S.has_mega_stone(cand.item), fld, own, locked)


def _species_tables():
    """図鑑・learnset・特性を引く callable の束 (種ごとにキャッシュ)"""
    from advisor.dex import get_dex
    from tools.team_build import gen_sets as G
    from tools.team_build.learnsets import learnset_of
    from tools.team_build.role_sets import _species_abilities
    dex = get_dex()
    cache: dict = {}

    def info(sid: str) -> dict:
        """base = 役割判定用の種族値: メガ石を持てる種はメガ後の方が高い能力をそのまま (エースの役割はメガ後の火力で決まる)"""
        if sid not in cache:
            from advisor.gimmick import mega_forms
            sp = dex.species(sid) or {}
            base = dict(sp.get("baseStats") or {})
            for msid in mega_forms(sid):
                mb = (dex.species(msid) or {}).get("baseStats") or {}
                for k, v in mb.items():
                    if k != "spe":
                        base[k] = max(int(base.get(k) or 0), int(v or 0))
            cache[sid] = {"base": base, "learnset": set(learnset_of(sid)), "abilities": list(_species_abilities(sid))}
        return cache[sid]
    return info, (lambda ability, moves: G.own_field(ability, moves))


def make_roles_of(info: Callable) -> Callable:
    cache: dict = {}

    def roles_of(sid: str) -> list:
        if sid not in cache:
            i = info(sid)
            cache[sid] = default_roles(i["base"], i["learnset"]) if i["base"] else []
        return cache[sid]
    return roles_of


def make_capable(info: Callable, field_of: Callable) -> Callable:
    """capable(sid, role) → bool。capable.aptitude(sid, role) → 0..1 (役割の適性)"""
    cache: dict = {}

    def aptitude(sid: str, role: str) -> float:
        key = (sid, role)
        if key not in cache:
            i = info(sid)
            cache[key] = role_aptitude(role, i["learnset"], i["abilities"], i["base"], field_of) if i["base"] else 0.0
        return cache[key]

    def capable(sid: str, role: str) -> bool:
        return aptitude(sid, role) > 0.0
    capable.aptitude = aptitude          # type: ignore[attr-defined]
    return capable


def make_prefilter(feats: dict, threat_weights: dict, capable: Callable, role_k: int = BUILD_COMPLEMENT_ROLE_SPECIES_K) -> Callable:
    """S3 の特徴 (代表型の脅威への被覆) で、穴の相手 (脅威リストに居るもの) に強い順。未充足の役割 1 つにつき、
    役割を満たせる種を適性 (capable.aptitude があればそれ、次に被覆) の順に role_k 体まで先頭に足す"""
    wmax = max(threat_weights.values(), default=1.0) or 1.0
    aptitude = getattr(capable, "aptitude", None)

    def score(sid: str, targets: list) -> float:
        f = feats.get(sid)
        if f is None:
            return 0.0
        ts = targets or list(f.coverage)
        tot = sum(threat_weights.get(t, 0.0) / wmax for t in ts) or 1.0
        return sum(threat_weights.get(t, 0.0) / wmax * float(f.coverage.get(t, 0.0)) for t in ts) / tot

    def prefilter(species_ids: list, hole_species: list, unmet: list, k: int) -> list:
        targets = [t for t in hole_species if t in threat_weights]
        ranked = sorted(species_ids, key=lambda s: -score(s, targets))
        out: list = []
        for role in dict.fromkeys(unmet):
            able = [s for s in ranked if capable(s, role)]
            if aptitude is not None:
                able.sort(key=lambda s: (-float(aptitude(s, role)), -score(s, targets)))
            n = 0
            for s in able:
                if s in out:
                    continue
                out.append(s)
                n += 1
                if n >= role_k:
                    break
        for s in ranked:
            if len(out) >= k:
                break
            if s not in out:
                out.append(s)
        return out
    return prefilter


def make_candidates_fn(tv: dict, threat_weights: dict, snapshot_id: Optional[int], custom_sets: dict,
                       log: Optional[Callable] = None, required_moves: Optional[dict] = None) -> Callable:
    """candidates_fn(sid, role, used_items, mega_allowed, team_field, speed_plan) → [SetEntry]。
    指定の型の種はその型だけ (役割は逆引き)。それ以外は役割の雛形の型 (≤ BUILD_SET_CANDIDATES_PER_ROLE) + 代表型 (合成の印、
    役割が一致するときだけ)。(種, 役割, メガ可否, 場, 速度計画) でキャッシュし、持ち物が全部使用済みのときだけ使用済みを除いて作り直す。
    required_moves = 技の指定 {種: [技]} (--moves)。その種の雛形の型には必ず入れ、代表型は指定の技を全部持つときだけ候補にする
    (2026-10-04: 同時探索は技の指定を見ていなかった)"""
    from champions_agent.data import database as db
    from tools.team_build import gen_sets as G
    from tools.team_build.role_sets import RoleContext, generate_role_sets
    log = log or (lambda m: None)
    req_of = {sid: list(mv) for sid, mv in (required_moves or {}).items() if mv}
    cache: dict = {}
    usage: dict = {}
    locked: dict = {}
    cat_of = S.default_category_of()
    setup_moves = S.default_setup_moves()

    def usage_of(sid: str) -> tuple:
        if sid not in usage:
            item_pct, move_pct, ability_pct, rep = {}, {}, {}, None
            if snapshot_id is not None:
                try:
                    with db.get_connection() as conn:
                        item_pct = (S.item_usage_pct_map(conn, snapshot_id, [sid]) or {}).get(sid) or {}
                        move_pct = S.move_usage_pct(conn, snapshot_id, sid)
                        ability_pct = {a: float(p or 0.0) for a, p in S._rows(conn, "ability_usage", "ability_name", snapshot_id, sid, 0.0)}
                        rep = S.representative_set(conn, snapshot_id, sid)
                except Exception as e:
                    log(f"S5 usage {sid}: {e!r}")
            usage[sid] = (item_pct, move_pct, ability_pct, rep)
        return usage[sid]

    def rep_entry(sid: str, role: str, team_field: dict, mega_allowed: bool) -> Optional[SetEntry]:
        rep = usage_of(sid)[3]
        if rep is None or S.set_sanity(rep) or not S.legal_item(rep.item):
            return None
        if S.has_mega_stone(rep.item) and not mega_allowed:
            return None
        if req_of.get(sid) and not set(req_of[sid]) <= set(rep.moves or []):
            return None                                  # 技の指定を満たさない代表型は候補にしない
        rrole = infer_role(rep, cat_of, lambda m: m in setup_moves, G.own_field)
        if not role_matches(rrole, role):
            return None
        c = S.SetCandidate(rep.species_id, rep.ability, rep.item, rep.nature, rep.evs, list(rep.moves), "representative", 0.0,
                           ["representative:synthesized", f"role:{rrole}"])
        try:
            return entry_from_candidate(c, rrole, team_field)
        except Exception:
            return None

    def build(sid: str, role: str, used_items: frozenset, mega_allowed: bool, team_field: dict, speed_plan: str,
              targets: Optional[list] = None) -> list:
        item_pct, move_pct, ability_pct, _rep = usage_of(sid)
        tgt = [t for t in (targets or []) if t in tv] or None
        ctx = RoleContext(threat_views=tv, weights=dict(threat_weights), targets=tgt, team_field=dict(team_field),
                          speed_plan=speed_plan, used_items=set(used_items), mega_allowed=mega_allowed, item_pct=item_pct,
                          move_pct=move_pct, ability_pct=ability_pct, required_moves=list(req_of.get(sid) or []))
        # 技の指定に積み技がある種は積み役として作る: 補助・受けの役割 (交代役 / 受け 等) を求められても積みエースの雛形にする
        # (2026-10-04: つるぎのまい + バトンタッチ指定のバシャーモに、交代役の雛形で ねむる + カゴのみ の型ができた)
        role = role_for_required(role, req_of.get(sid) or [], lambda m: m in setup_moves)
        try:
            sets = generate_role_sets(sid, role, ctx)
        except Exception as e:
            log(f"S5 role_sets {sid}/{role}: {e!r}")
            sets = []
        out: list = []
        for c in sets:
            try:
                out.append(entry_from_candidate(c, role, team_field))
            except Exception:
                continue
        r = rep_entry(sid, role, team_field, mega_allowed)
        if r is not None:
            out.append(r)
        return out

    def candidates_fn(sid: str, role: str, used_items: frozenset, mega_allowed: bool, team_field: dict, speed_plan: str,
                      targets: Optional[list] = None) -> list:
        fkey = G.field_key(team_field)
        if targets:
            # 仕上げ (担当に合わせた型): 担当の相手の組ごとに作る (キャッシュは担当のキー込み。使用済みの持ち物は除いて作る)
            tkey = tuple(sorted(t for t in targets if t in tv))
            if not tkey:
                return candidates_fn(sid, role, used_items, mega_allowed, team_field, speed_plan)
            key = (sid, role, mega_allowed, fkey, speed_plan, tkey, tuple(sorted(used_items)))
            if key not in cache:
                cache[key] = build(sid, role, used_items, mega_allowed, team_field, speed_plan, list(tkey))
            return list(cache[key])
        if sid in custom_sets:
            k = (sid, fkey)
            if k not in locked:
                c = custom_sets[sid]
                rrole = infer_role(c, cat_of, lambda m: m in setup_moves, G.own_field)
                try:
                    locked[k] = [entry_from_candidate(c, rrole, team_field, locked=True)]
                except Exception as e:
                    log(f"S5 custom set {sid}: {e!r}")
                    locked[k] = []
            return list(locked[k])
        key = (sid, role, mega_allowed, fkey, speed_plan)
        if key not in cache:
            cache[key] = build(sid, role, frozenset(), mega_allowed, team_field, speed_plan)
        out = [e for e in cache[key] if not (e.item and e.item in used_items)]
        if not out and cache[key] and used_items:
            key2 = key + (tuple(sorted(used_items)),)
            if key2 not in cache:
                cache[key2] = build(sid, role, used_items, mega_allowed, team_field, speed_plan)
            out = list(cache[key2])
        return out

    candidates_fn.cache = cache          # type: ignore[attr-defined]
    return candidates_fn


def registered_entries(reg_text: str, log: Optional[Callable] = None) -> list:
    """登録チーム本文 → [SetEntry] (型は固定、役割は逆引き)。6 体に満たなければ空"""
    from tools.team_build import gen_sets as G
    log = log or (lambda m: None)
    cat_of = S.default_category_of()
    setup_moves = S.default_setup_moves()
    out: list = []
    for sid, c in S.parse_set_text(reg_text).items():
        role = infer_role(c, cat_of, lambda m: m in setup_moves, G.own_field)
        try:
            out.append(entry_from_candidate(c, role, None, locked=True))
        except Exception as e:
            log(f"S5 registered {sid}: {e!r}")
            return []
    return demote_tr_ace(out) if len(out) == TEAM_SIZE else []


def make_base_of() -> Callable:
    """種 id → Species Clause の同一視キー (advisor の図鑑の図鑑番号。無ければ種 id)。ヌメルゴン と ヒスイヌメルゴン は同じ 706
    (2026-10-03: 両方入った並びが validate-team で落ちた)"""
    from advisor.dex import get_dex
    dex = get_dex()
    cache: dict = {}

    def base_of(sid: str) -> str:
        if sid not in cache:
            num = (dex.species(sid) or {}).get("num")
            cache[sid] = f"num:{num}" if num else sid
        return cache[sid]
    return base_of


def load_json(p: Path):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return None


def build_search(spec, feats: dict, tv: dict, threat_weights: dict, split: dict, snapshot_id: Optional[int],
                 session_weights: Optional[dict] = None, rule_names: Optional[list] = None, log: Optional[Callable] = None):
    """探索器の束 (SimpleNamespace): pool / search / roles_of / capable / species_pool / cfg / custom / tv / threat_weights"""
    from types import SimpleNamespace
    from tools.team_build import rules as RU
    log = log or (lambda m: None)
    pool = pool_from_split(split, "search", session_weights)
    custom = {sid: S.candidate_from_row(sid, row) for sid, row in (spec.custom_sets or {}).items()}
    info, field_of = _species_tables()
    roles_of = make_roles_of(info)
    capable = make_capable(info, field_of)
    row_fn = make_row_fn()
    search = LineupSearch(pool, make_candidates_fn(tv, threat_weights, snapshot_id, custom, log,
                                                   required_moves=dict(getattr(spec, "required_moves", None) or {})), row_fn,
                          make_prefilter(feats, threat_weights, capable), log, capable=capable, row_post=make_row_post(row_fn))
    banned = set(spec.banned)
    species_pool = [s for s in feats if s not in banned]
    rule_field = RU.rule_field(rule_names) if rule_names else {}
    cfg = SearchConfig(ace=(spec.ace or None), favorites=tuple(spec.favorites), banned=frozenset(banned),
                       required_roles=rule_roles(rule_field), rule_field=dict(rule_field), base_of=make_base_of())
    return SimpleNamespace(pool=pool, search=search, roles_of=roles_of, capable=capable, species_pool=species_pool, cfg=cfg,
                           custom=custom, tv=tv, threat_weights=threat_weights, fams=[])


def load_search(run_dir: Path, spec, n_threats: int, session_weights: Optional[dict] = None, log: Optional[Callable] = None):
    """run の成果物 (meta_snapshot / opponent_families / species_features / s04_concepts) から探索器を組み直す (測定段の修理モード用)"""
    from tools.team_build.features import features_from_json
    from tools.team_build.meta_snapshot import load_snapshot, threat_sets, threat_weight
    run_dir = Path(run_dir)
    doc = load_snapshot(run_dir / "meta_snapshot.json")
    split = load_json(run_dir / "opponent_families.json") or {}
    feats = features_from_json(load_json(run_dir / "species_features.json") or {})
    tv = threat_sets(doc, n_threats)
    weights = {t["id"]: threat_weight(t) for t in doc["top"] if t["id"] in tv}
    ctx = build_search(spec, feats, tv, weights, split, doc["snapshot"]["id"], session_weights, rule_names=list(spec.rules or []), log=log)
    ctx.fams = (load_json(run_dir / "s04_concepts.json") or {}).get("families") or []
    return ctx


def result_from_row(search: LineupSearch, row: dict, cfg: SearchConfig, custom_sets: Optional[dict] = None) -> LineupResult:
    """s06_sets.json の行 (roles つき) → LineupResult (型は行のまま、評価の場は並びの始動源)。指定の型と登録の型は locked"""
    sets = row.get("sets") or []
    roles = row.get("roles") or {}
    cands = [S.SetCandidate(s["species"], s.get("ability"), s.get("item"), s.get("nature"), s.get("evs"), list(s.get("moves") or []),
                            s.get("source") or "row", float(s.get("coverage") or 0.0), list(s.get("notes") or [])) for s in sets]
    first = demote_tr_ace([entry_from_candidate(c, roles.get(c.species_id) or "breaker", None) for c in cands])
    tfield = team_field_from(first)
    entries = [entry_from_candidate(c, e.role, tfield, locked=(c.source == "custom" or c.species_id in (custom_sets or {})))
               for c, e in zip(cands, first)]
    combo = [search.lib.add(e) for e in entries]
    cid = str(row.get("candidate_id") or "")
    concept = cid.split("_", 1)[1] if "_" in cid else str(row.get("concept") or "")
    sc, _info = search.score_of(combo, [], cfg)
    return search._finalize(sc, combo, dict(row.get("fills") or {}), concept, [], cfg, tag=str(row.get("tag") or ""),
                            origin=dict(row.get("origin") or {}))


def make_row(index: int, cid: str, r: LineupResult, tag: str, ok: bool, errs: list, ace_sid: Optional[str], is_inc: bool,
             registered: Optional[list] = None) -> dict:
    """s06_sets.json の行 (従来の項目 + roles / assignments / fills / selection_plan)"""
    from tools.team_build.archetypes import label_ja
    team = [e.cand for e in r.entries]
    tf = team_field_from(r.entries)
    role_groups: dict = {}
    for sid, role in r.roles.items():
        role_groups.setdefault(role, []).append(sid)
    return {"index": index, "candidate_id": cid, "members": list(r.members), "ok": ok, "errors": list(errs)[:5],
            "tag": tag, "score": round(float(r.score), 4), "origin": dict(r.origin or {}),
            "registered_sets": list(registered or []), "rule_setter": None, "rule_notes": [], "rule_pair": None,
            "team_field": (tf if (tf.get("terrain") or tf.get("weather")) else None),
            "archetype": {"roles": role_groups, "label": label_ja(None)} if role_groups else None,
            "ace": (ace_sid if (ace_sid and ace_sid in r.members and not is_inc) else None), "ace_notes": [],
            "locked_restored": [],
            "roles": dict(r.roles), "assignments": dict(r.assignments), "fills": dict(r.fills),
            "selection_plan": dict(r.selection_plan), "family_values": dict(getattr(r, "family_values", {}) or {}),
            "parts": {k: (round(float(v), 4) if isinstance(v, (int, float)) else v) for k, v in (r.parts or {}).items()},
            "sets": [{"species": c.species_id, "ability": c.ability, "item": c.item, "nature": c.nature, "evs": c.evs,
                      "moves": list(c.moves), "source": c.source, "coverage": round(float(c.score), 3),
                      "role": r.roles.get(c.species_id), "notes": list(c.notes)} for c in team]}


# ------------------------------------------------------------------ 段の本体
def stage_s5_joint(run_dir: Path, spec, fams: list, feats: dict, tv: dict, threat_weights: dict, split: dict, prof: dict,
                   snapshot_id: Optional[int], session_weights: Optional[dict] = None, n_neighbors: Optional[int] = None,
                   only_incumbent: bool = False, rule_ctx: Optional[dict] = None, registered: Optional[tuple] = None,
                   log: Optional[Callable] = None, refine: bool = BUILD_JOINT_REFINE_TARGETS) -> tuple:
    """S5 統合段。戻り値 (選んだ並び [candidates.Lineup], s06_sets.json の行)。
    s05_candidates.json (従来 + mode=joint) と s06_sets.json / s06_sets/<並び>.txt (従来 + roles / assignments / fills /
    selection_plan) を保存する。validate-team は run.py 側の不変条件 (除外・エースの石) の前に通す"""
    log = log or print
    t0 = time.time()
    ctx = build_search(spec, feats, tv, threat_weights, split, snapshot_id, session_weights,
                       rule_names=(rule_ctx or {}).get("names") if rule_ctx else None, log=log)
    pool, search, roles_of, species_pool, cfg = ctx.pool, ctx.search, ctx.roles_of, ctx.species_pool, ctx.cfg
    banned = set(spec.banned)
    log(f"S5 joint: 相手プール 系統 {len(pool.families)} 型 {len(pool.sets)}、所持 {len(species_pool)} 種、構想 {len(fams)}")
    results: list = []
    if not only_incumbent:
        for n, fam in enumerate(fams, 1):
            core_ok = [c for c in fam.get("core_ids", []) if c in feats and c not in banned]
            if not core_ok:
                continue
            cfg_f = replace(cfg, speed_plan=BUILD_ARCHETYPE_SPEED_PLAN.get(fam.get("archetype") or "", "neutral"))
            try:
                res = search.search(fam, cfg_f, species_pool, roles_of, branch_roles=branch_roles_of(fam))
            except Exception as e:
                log(f"S5 joint {fam.get('family_id')}: error {e!r}")
                res = []
            results.extend(res)
            if n % 10 == 0 or n == len(fams):
                log(f"S5 joint: 構想 {n}/{len(fams)} 並び {len(results)} 型 {len(search.lib.entries)} "
                    f"行 {search.n_rows_computed} 評価 {search.n_evals} {time.time() - t0:.0f} 秒")
    by_members: dict = {}
    for r in results:
        if r.members not in by_members or r.score > by_members[r.members].score:
            by_members[r.members] = r
    lineups = [to_lineup(r) for r in by_members.values()]
    fav = set(spec.favorites)
    if fav:
        lineups = [l for l in lineups if fav <= set(l.members)]
    chosen = C.select_with_quotas(lineups, prof["quotas"]) if lineups else []
    rest = sorted((l for l in lineups if l not in chosen), key=lambda l: -l.score)
    for l in rest:
        if len(chosen) >= prof["n_lineups"]:
            break
        if all(C.distance(l.members, c.members) >= C.MIN_DISTANCE for c in chosen):
            l.tag = "fill"
            chosen.append(l)
    if chosen:
        before = {m: sum(1 for l in chosen if m in l.members) for l in chosen for m in l.members}
        chosen = cap_species_share(chosen, [l for l in rest if l not in chosen], BUILD_SPECIES_SHARE_MAX,
                                   protected=set(spec.favorites) | ({spec.ace} if spec.ace else set()), min_distance=C.MIN_DISTANCE)
        after = {m: sum(1 for l in chosen if m in l.members) for l in chosen for m in l.members}
        top_b = sorted(before.items(), key=lambda kv: -kv[1])[:3]
        top_a = sorted(after.items(), key=lambda kv: -kv[1])[:3]
        log(f"S5 species cap (≤ {BUILD_SPECIES_SHARE_MAX:.0%}): 上位の種 {top_b} → {top_a} ({len(chosen)} 並び)")
    if refine and chosen:
        # 仕上げ: 担当 (選出計画でその個体を出す系統の相手) に合わせて型を作り直す (点が上がるときだけ)
        fam_species = {fid: pool.species_of_families([i]) for i, (fid, _w, _rows) in enumerate(pool.families)}

        def targets_of(res: LineupResult, sid: str) -> Optional[list]:
            out: list = []
            for fid in res.assignments.get(sid, []):
                out.extend(s for s in fam_species.get(fid, []) if s not in out)
            return out or None
        n_changed = 0
        for l in chosen:
            r = by_members[tuple(l.members)]
            req = list(cfg.required_roles) + concept_requirements(next((f for f in fams if f.get("family_id") == r.concept), {}),
                                                                  branch_roles_of(next((f for f in fams if f.get("family_id") == r.concept), {})))
            try:
                new, changed = search.refine(r, cfg, req, targets_of)
            except Exception as e:
                log(f"S5 refine {r.concept}: error {e!r}")
                continue
            if changed:
                by_members[tuple(l.members)] = new
                l.score = float(new.score)
                l.parts = dict(new.parts)
                n_changed += len(changed)
        log(f"S5 refine: 担当に合わせて型を作り直した個体 {n_changed} ({time.time() - t0:.0f} 秒)")
    reg_text, reg_ids, reg_mega = registered or ("", [], None)
    if rule_ctx:
        log("S5 incumbent branch: 規則つきのため入れない (参照との比較は S8a/S8b で行う)")
    elif reg_text:
        entries = registered_entries(reg_text, log)
        inc, neigh = (search.incumbent(entries, cfg, species_pool, roles_of, n_neighbors or BUILD_INCUMBENT_NEIGHBORS, reg_mega)
                      if entries else (None, []))
        existing = {l.members for l in chosen}
        branch = []
        for r in ([inc] if inc else []) + neigh:
            if r.members in existing:
                continue
            by_members[r.members] = r
            branch.append(to_lineup(r))
            existing.add(r.members)
        chosen = branch + chosen
        log(f"S5 incumbent branch: 現行={'あり' if inc else 'なし (除外/プール外/未登録)'} 近傍={len(neigh)} (登録 {len(reg_ids)} 体)")
    (run_dir / "s05_candidates.json").write_text(
        json.dumps({"n_generated": len(results), "lineups": [l.to_dict() for l in chosen], "mode": "joint",
                    "only_incumbent": bool(only_incumbent),
                    "stats": {"entries": len(search.lib.entries), "rows": search.n_rows_computed, "evals": search.n_evals,
                              "seconds": round(time.time() - t0, 1)}},
                   ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    log(f"S5 candidates: generated={len(results)} kept={len(chosen)} ({time.time() - t0:.0f} 秒)")
    rows = write_sets(run_dir, spec, chosen, by_members, reg_text, log)
    return chosen, rows


def write_plan_file(out_dir: Path, cid: str, r: LineupResult) -> Path:
    """選出計画 (系統 → 出す 3 体) を s06_sets/<cid>.plan.json に書く。測定 (check_advisor_player --selection-plan) と
    cheap adaptation の収集 (collect_selection_data --selection-plan) が選出モデルの初期値に使う (設計文書 §5.3)"""
    p = Path(out_dir) / f"{cid}.plan.json"
    p.write_text(json.dumps({"candidate_id": cid, "members": list(r.members), "selection_plan": dict(r.selection_plan),
                             "assignments": dict(r.assignments)}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return p


def cap_species_share(chosen: list, rest: list, max_share: float = BUILD_SPECIES_SHARE_MAX, protected=(),
                      min_distance: Optional[float] = None) -> list:
    """保持する並びで同じ種が入る割合に上限を掛ける (純粋)。chosen の順に見て、固定枠・エース (protected) 以外の種が
    上限 (ceil(max_share × 目標数)、最小 2) を超える並びは外し、rest (点の順) から上限と距離を満たすものを足して目標数に戻す。
    2026-10-04: カイリューが 79 並びすべてに入っていた"""
    import math
    target = len(chosen)
    if target == 0 or max_share >= 1.0:
        return list(chosen)
    cap = max(2, int(math.ceil(max_share * target)))
    prot = set(protected or ())
    counts: dict = {}
    out: list = []

    def fits(l) -> bool:
        return all(counts.get(m, 0) < cap for m in l.members if m not in prot)

    def far(l) -> bool:
        if min_distance is None:
            return True
        return all(C.distance(l.members, c.members) >= min_distance for c in out)

    def take(l) -> None:
        out.append(l)
        for m in l.members:
            counts[m] = counts.get(m, 0) + 1
    dropped: list = []
    for l in chosen:
        if fits(l):
            take(l)
        else:
            dropped.append(l)
    for l in rest:
        if len(out) >= target:
            break
        if l in out or not fits(l) or not far(l):
            continue
        l.tag = l.tag or "fill"
        take(l)
    for l in dropped:                       # 足りなければ外した並びを戻す (上限は目標数を割ってまで守らない)
        if len(out) >= target:
            break
        take(l)
    return out


def write_sets(run_dir: Path, spec, chosen: list, by_members: dict, reg_text: str, log: Callable) -> list:
    """s06_sets.json / s06_sets/<並び>.txt (従来の行の形 + roles / assignments / fills / selection_plan)"""
    out_dir = run_dir / "s06_sets"
    out_dir.mkdir(exist_ok=True)
    ace_sid = spec.ace or None
    rows: list = []
    for idx, l in enumerate(chosen):
        r = by_members[tuple(l.members)]
        team = [e.cand for e in r.entries]
        text = S.to_showdown_text(team)
        is_inc = l.tag in ("incumbent", "incumbent_mut") and bool(reg_text)
        registered: list = []
        if is_inc:
            text, registered = S.splice_registered_sets(text, reg_text)
        ok, errs = S.validate_team_text(text, spec.regulation)
        cid = f"L{idx:02d}_{l.concept}"
        (out_dir / f"{cid}.txt").write_text(text, encoding="utf-8")
        write_plan_file(out_dir, cid, r)
        r.score = float(l.score)
        r.origin = dict(l.origin or {})
        rows.append(make_row(idx, cid, r, l.tag, ok, errs, ace_sid, is_inc, registered))
    (run_dir / "s06_sets.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    n_ok = sum(1 for x in rows if x["ok"])
    log(f"S6 sets: {n_ok}/{len(rows)} 並びが合法 (validate-team)")
    return rows
