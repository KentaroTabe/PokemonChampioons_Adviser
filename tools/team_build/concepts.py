"""コンセプト系統の生成 (S4): ルール生成の baseline + LLM の複数独立生成 → 重複除去・クラスタリング → coverage 停止。

LLM は探索ヒューリスティックの一つ。出力は authoritative (id / enum) だけを検証して使い、display は表示専用
(docs/TEAM_BUILDING_IMPLEMENTATION.md §10)。回数上限ではなく「新しい系統がほぼ出なくなったら」停止する。
"""
from __future__ import annotations

import itertools
import json
from typing import Callable, Optional

from champions_agent.config import BUILD_ARCHETYPE_FULL_PASS
from tools.team_build.families import jaccard

WIN_CONDITIONS = ("setup_sweep", "offense_trade", "cycle_pressure", "hazard_chip", "speed_control",
                  "bulky_attrition", "priority_cleanup", "anti_meta")
SUPPORT_ROLES = ("speed_control", "hazard_control", "priority", "pivot", "status", "bulk", "setup")
STYLE_FRAMINGS = ("offense", "balance", "bulky_offense", "cycle", "setup", "speed_control", "anti_meta",
                  "specific_core")
CONCEPT_MIN_CORE, CONCEPT_MAX_CORE = 2, 3
# 役割の設計図 (docs/TEAM_BUILD_REDESIGN_1002.md §4.1、2026-10-02): core[].role / complement_requirements[].role は役割の語彙
# (tools/team_build/role_sets.template_of で解決できる id)、plan は場と速度の計画。どれも任意 (無ければ従来どおり core_ids だけ)
COMPLEMENT_REQUIREMENTS_MAX = 3
SPEED_PLANS = ("outspeed", "trick_room", "neutral")
PLAN_WEATHERS = ("sun", "rain", "sand", "snow")
PLAN_TERRAINS = ("electric", "grassy", "psychic", "misty")
ROLE_VOCABULARY_JA = {
    "sweeper_setup": "積みエース (積んで抜く)", "breaker": "崩し役 (高火力の攻撃 4 本)", "cleaner": "詰め役 (速い / 先制技)",
    "tr_ace": "トリックルームのエース (低速高火力)", "weather_ace": "天候の恩恵を受けるエース (sun/rain/sand/snow_abuser)",
    "terrain_ace": "フィールドの恩恵を受けるエース (psychic/grassy/electric/misty_abuser)",
    "hazard_lead": "設置役 (ステルスロック / まきびし / ねばねばネット)", "hazard_removal": "除去役 (きりばらい / こうそくスピン)",
    "speed_control": "速度操作 (トリックルーム / おいかぜ)", "weather_setter": "天候の始動役 (sun/rain/sand/snow_setter)",
    "terrain_setter": "フィールドの始動役 (psychic/grassy/electric/misty_setter)", "pivot": "交代役 (とんぼがえり / ボルトチェンジ)",
    "wall": "受け (回復技 + 耐久)", "status_spreader": "状態異常のばらまき", "support_screens": "2 枚壁", "support_veil": "オーロラベール",
    "phazer": "吹き飛ばし (積みの阻止)", "trapper": "交代封じ", "suicide_lead": "捨て駒の先発 (自爆 / みちづれ / 起点作り)",
}
STOP_NEW_YIELD = 0.15      # 直近の生成で新系統の割合がこれ未満なら停止
STOP_DUP_RATE = 0.7        # 重複率がこれ以上でも停止
MAX_ROUNDS = 6


def concept_key(core_ids: list) -> tuple:
    return tuple(sorted(set(core_ids)))


def role_id_ok(role) -> bool:
    """役割 id が語彙 (雛形名・軸の別名・<場>_setter / <場>_abuser) にあるか"""
    if not isinstance(role, str) or not role:
        return False
    from tools.team_build.role_sets import template_of
    try:
        template_of(role)
        return True
    except KeyError:
        return False


def normalize_blueprint(c: dict) -> dict:
    """役割の設計図の正規化 (その場で書き換える): core ([{species_id, role}]) があれば core_ids と roles ({role: [sid]}) を
    埋める (無ければ従来の core_ids のまま)。純粋"""
    core = c.get("core")
    if isinstance(core, list) and core:
        ids, roles = [], {}
        for row in core:
            if not isinstance(row, dict):
                continue
            sid = row.get("species_id")
            if sid and sid not in ids:
                ids.append(sid)
            if sid and row.get("role"):
                roles.setdefault(row["role"], []).append(sid)
        if not c.get("core_ids"):
            c["core_ids"] = ids
        if roles and not c.get("roles"):
            c["roles"] = roles
    return c


def blueprint_problems(c: dict, i: int) -> list:
    """役割の設計図の検証 (任意の項目だけ): core[].role と complement_requirements[].role は語彙、要件は最大
    COMPLEMENT_REQUIREMENTS_MAX、plan の場と速度は enum。純粋"""
    problems: list = []
    core = c.get("core")
    if core is not None:
        if not isinstance(core, list):
            problems.append(f"concepts[{i}]: core は [{{species_id, role}}] の配列")
        else:
            for row in core:
                if not isinstance(row, dict) or not row.get("species_id"):
                    problems.append(f"concepts[{i}]: core の各要素は {{species_id, role}}")
                elif row.get("role") is not None and not role_id_ok(row.get("role")):
                    problems.append(f"concepts[{i}]: core の役割 {row.get('role')} は役割の語彙 (role_vocabulary) のいずれか")
            if c.get("core_ids") and set(c["core_ids"]) != {r.get("species_id") for r in core if isinstance(r, dict)}:
                problems.append(f"concepts[{i}]: core と core_ids の種が食い違う")
    reqs = c.get("complement_requirements")
    if reqs is not None:
        if not isinstance(reqs, list):
            problems.append(f"concepts[{i}]: complement_requirements は [{{role, targets, note}}] の配列")
        else:
            if len(reqs) > COMPLEMENT_REQUIREMENTS_MAX:
                problems.append(f"concepts[{i}]: complement_requirements は最大 {COMPLEMENT_REQUIREMENTS_MAX} 件")
            for r in reqs:
                role = r.get("role") if isinstance(r, dict) else r
                if not role_id_ok(role):
                    problems.append(f"concepts[{i}]: complement_requirements の役割 {role} は役割の語彙のいずれか")
    plan = c.get("plan")
    if plan is not None:
        if not isinstance(plan, dict):
            problems.append(f"concepts[{i}]: plan は {{field, speed_plan}}")
        else:
            sp = plan.get("speed_plan")
            if sp is not None and sp not in SPEED_PLANS:
                problems.append(f"concepts[{i}]: plan.speed_plan は {SPEED_PLANS} のいずれか")
            fld = plan.get("field") or {}
            if not isinstance(fld, dict):
                problems.append(f"concepts[{i}]: plan.field は {{weather, terrain}}")
            else:
                if fld.get("weather") is not None and fld.get("weather") not in PLAN_WEATHERS:
                    problems.append(f"concepts[{i}]: plan.field.weather は {PLAN_WEATHERS} か null")
                if fld.get("terrain") is not None and fld.get("terrain") not in PLAN_TERRAINS:
                    problems.append(f"concepts[{i}]: plan.field.terrain は {PLAN_TERRAINS} か null")
    return problems


def validate_concepts(auth: dict, owned: set, legal: set, mega_capable: set,
                      archetypes: Optional[dict] = None, banned: Optional[set] = None,
                      ace: Optional[str] = None, ace_mega: bool = False) -> list:
    """authoritative の検証: 問題の一覧 (空なら OK)。archetypes ({axis_id: [branch_id]}) を渡すと、concept の
    archetype / branch (任意) が既知の id であることも検査する。banned (使わないポケモン) が core にあれば差し戻す。
    ace (指定エース) があれば各 concept の core_ids に含め、ace_mega (エースがメガ石を持てる) なら mega_id はエース"""
    problems = []
    items = auth.get("concepts")
    if not isinstance(items, list) or not items:
        return ["concepts が空 (配列で返す)"]
    for i, c in enumerate(items):
        if isinstance(c, dict):
            normalize_blueprint(c)
            problems += blueprint_problems(c, i)
    for i, c in enumerate(items):
        if archetypes is not None and (c.get("archetype") or c.get("branch")):
            ax = c.get("archetype")
            if ax not in archetypes:
                problems.append(f"concepts[{i}]: archetype {ax} は {sorted(archetypes)} のいずれか")
            elif c.get("branch") and c.get("branch") not in archetypes[ax]:
                problems.append(f"concepts[{i}]: branch {c.get('branch')} は {archetypes[ax]} のいずれか")
        if archetypes is not None and c.get("special_branch"):
            if c.get("special_branch") not in (archetypes.get("special") or []):
                problems.append(f"concepts[{i}]: special_branch {c.get('special_branch')} は {archetypes.get('special')} のいずれか")
        core = c.get("core_ids") or []
        if not (CONCEPT_MIN_CORE <= len(set(core)) <= CONCEPT_MAX_CORE):
            problems.append(f"concepts[{i}]: core_ids は {CONCEPT_MIN_CORE}〜{CONCEPT_MAX_CORE} 体")
        for sid in core:
            if banned and sid in banned:
                problems.append(f"concepts[{i}]: {sid} は使わないポケモン (banned) なので使えない")
            elif sid not in owned:
                problems.append(f"concepts[{i}]: {sid} は所持にない (所持リストの id だけを使う)")
            elif legal and sid not in legal:
                problems.append(f"concepts[{i}]: {sid} は使用不可")
        mega = c.get("mega_id")
        if mega and (mega not in core or mega not in mega_capable):
            problems.append(f"concepts[{i}]: mega_id {mega} は core に含まれメガ石を持てる種でなければならない")
        if ace:
            if ace not in core:
                problems.append(f"concepts[{i}]: エース {ace} を core_ids に必ず含める")
            elif ace_mega and mega != ace:
                problems.append(f"concepts[{i}]: mega_id はエース {ace} (エースだけがメガ石を持つ)")
        if c.get("win_condition") not in WIN_CONDITIONS:
            problems.append(f"concepts[{i}]: win_condition は {WIN_CONDITIONS} のいずれか")
        for r in c.get("support_roles") or []:
            if r not in SUPPORT_ROLES:
                problems.append(f"concepts[{i}]: support_roles の値 {r} は {SUPPORT_ROLES} のいずれか")
        for sid in c.get("weak_to") or []:
            if legal and sid not in legal:
                problems.append(f"concepts[{i}]: weak_to の {sid} は未知")
    return problems


def rule_baseline_concepts(feats: dict, threats: list, mega_capable: set, top_k: int = 8,
                           favorites: Optional[list] = None, threat_weights: Optional[dict] = None) -> list:
    """ルール生成: framing ごとに軸を選び、相方は「軸に対する被覆の増分」が最大の種 (相方の重複を避けて多様に)。
    framing: favorites (固定枠) / mega (メガ候補) / offense (対面が強い) / bulky (後投げが強い) /
             speed (上を取れる) / anti_meta (使用率上位 5 種への被覆)"""
    fav = list(favorites or [])
    tw = threat_weights or {t: 1.0 for t in threats}
    top5 = sorted(threats, key=lambda t: -tw.get(t, 0.0))[:5]

    def wcov(sid: str, ts: list) -> float:
        f = feats[sid]
        return sum(tw.get(t, 1.0) * f.coverage.get(t, 0.0) for t in ts) / max(1e-9, sum(tw.get(t, 1.0) for t in ts))

    anchors: list = []
    for a in fav:
        if a in feats:
            anchors.append((a, "favorites"))
    for a in sorted((s for s in feats if feats[s].mega), key=lambda s: -wcov(s, threats)):
        anchors.append((a, "mega"))
    ranked_all = sorted(feats, key=lambda s: -wcov(s, threats))
    anchors.append((ranked_all[0], "offense"))
    anchors.append((max(feats, key=lambda s: feats[s].roles.get("bulk", 0.0)), "bulky"))
    anchors.append((max(feats, key=lambda s: feats[s].roles.get("speed", 0.0)), "speed"))
    anchors.append((max(feats, key=lambda s: wcov(s, top5)), "anti_meta"))
    used_partners: set = set()
    out, seen = [], set()
    for a, framing in anchors[: top_k + len(fav)]:
        fa = feats[a]
        best = None
        for b in feats:
            if b == a:
                continue
            gain = sum(tw.get(t, 1.0) * max(fa.coverage.get(t, 0.0), feats[b].coverage.get(t, 0.0)) for t in threats)
            if b in used_partners:
                gain *= 0.9       # 相方の使い回しを避けて多様にする
            if best is None or gain > best[0]:
                best = (gain, b)
        core = [a] + ([best[1]] if best else [])
        key = tuple(sorted(core))
        if key in seen:
            continue
        seen.add(key)
        if best:
            used_partners.add(best[1])
        wc = "setup_sweep" if fa.roles.get("setup", 0) >= 1 else (
            "priority_cleanup" if fa.roles.get("priority", 0) >= 1 else (
                "bulky_attrition" if framing == "bulky" else (
                    "speed_control" if framing == "speed" else (
                        "anti_meta" if framing == "anti_meta" else "offense_trade"))))
        weak = sorted(threats, key=lambda t: max(feats[m].coverage.get(t, 0.0) for m in core))[:3]
        out.append({"name": f"rule:{framing}:{a}", "core_ids": core, "mega_id": a if fa.mega else None,
                    "win_condition": wc, "support_roles": ["speed_control", "hazard_control"],
                    "weak_to": weak, "source": f"rule:{framing}"})
    return out


def apply_ace(families: list, ace: Optional[str], ace_mega: bool) -> list:
    """指定エース: 系統の mega_id をエースにそろえる (ace_mega のとき。ルール生成・historical・軸の core は別のメガを
    mega_id に持ちうるが、S6 ではエースだけが石を持つ)。core_ids は S5 が固定枠として足す。純粋"""
    if not ace or not ace_mega:
        return families
    return [dict(f, mega_id=ace) for f in families]


def cluster_concepts(concepts: list, min_jaccard: float = 0.5) -> list:
    """core の Jaccard で系統にまとめ、代表 (先に出た方) だけ残す。family_id と members 数を付ける"""
    fams: list = []
    for c in concepts:
        key = set(c.get("core_ids") or [])
        placed = False
        for fam in fams:
            if jaccard(key, set(fam["core_ids"])) >= min_jaccard:
                fam["members"] += 1
                fam.setdefault("aliases", []).append(c.get("name"))
                placed = True
                break
        if not placed:
            d = dict(c)
            d["members"] = 1
            d["family_id"] = f"C{len(fams) + 1:03d}"
            fams.append(d)
    return fams


def generate_concepts(spec, feats: dict, threats: list, legal: set, mega_capable: set,
                      provider=None, rounds: int = MAX_ROUNDS, per_round: int = 8,
                      system_prompt: Optional[str] = None, log: Optional[Callable] = None,
                      threat_weights: Optional[dict] = None, rules: Optional[list] = None,
                      archetypes: Optional[list] = None) -> dict:
    """ルール baseline + LLM 複数ラウンド (framing を変える) → クラスタリング。coverage で停止。
    archetypes (archetypes.llm_axes の一覧) を渡すと、LLM の framing は軸ごと (archetype:<id>) になり、軸の説明・分岐の環境適合・
    役割の候補を渡して concept に archetype / branch を書かせる (2026-09-18)。特殊な勝ち筋も 1 回にまとめる。
    戻り値: {"families": [...], "raw": [...], "rounds": n, "stop_reason": str, "llm_calls": [...]}"""
    banned = set(spec.banned)
    owned = set(spec.owned) - banned          # LLM に渡す所持 (使える候補) は使わないポケモンを含まない
    ace = getattr(spec, "ace", "") or None     # 指定エース: core_ids に必須、メガ石を持てるなら mega_id もエース
    ace_mega = bool(ace) and ace in mega_capable
    raw = rule_baseline_concepts(feats, threats, mega_capable, favorites=spec.favorites,
                                 threat_weights=threat_weights)
    fams = cluster_concepts(raw)
    calls = []
    stop_reason = "no_provider" if provider is None else "max_rounds"
    arch_by_id = {a["id"]: a for a in (archetypes or [])}
    arch_ids = {a["id"]: [b["id"] for b in a.get("branches") or []] for a in (archetypes or [])}
    framings = [f"archetype:{a['id']}" for a in (archetypes or [])] or list(STYLE_FRAMINGS)
    if provider is not None:
        payload_base = {
            "task": "パーティのコンセプト系統を提案する",
            "owned": sorted(owned), "favorites": spec.favorites, "banned": spec.banned, "style": spec.style,
            "threats": threats,
            "coverage": {s: {t: round(v, 2) for t, v in f.coverage.items()} for s, f in feats.items()},
            "roles": {s: {k: round(v, 2) for k, v in f.roles.items() if v} for s, f in feats.items()},
            "mega_capable": sorted(mega_capable & owned),
            "enums": {"win_condition": WIN_CONDITIONS, "support_roles": SUPPORT_ROLES},
            # コンセプト規則 (hard constraint): 設置役/エースの候補を渡し、core_ids に両方を含めさせる (S5 で機械的に検査)
            "rules": rules or [],
            # 指定エース (hard constraint): 全 concept の core_ids に含め、メガ石を持てるなら mega_id もエース (S4 検証 / S6 で機械的に保証)
            "ace": ace,
            "constraints": " ".join(x for x in (
                ("各 concept の core_ids には、rules ごとに setters から 1 体と aces から 1 体 (別個体) を必ず含める" if rules else ""),
                ((f"エース {ace} をこの構築の勝ち筋の中心とし、各 concept の core_ids に必ず含める"
                  + ("。mega_id は必ずエース (この構築ではエースだけがメガ石を持ち、他のメンバーは持たない)" if ace_mega else ""))
                 if ace else ""),
            ) if x),
            # 役割の設計図 (§4.1): 核は種 + 役割、補完枠は種を書かず要件 (役割 + 見る相手) で書く。S5 統合段が役割の雛形から型を作る
            "role_vocabulary": ROLE_VOCABULARY_JA,
            "blueprint": "core には核 2〜3 体を {species_id, role} で書く (role は role_vocabulary の id。天候/フィールドは "
                         "sun_setter / rain_abuser / psychic_setter のように場つきの id)。残りの枠は complement_requirements に "
                         "役割と見る相手 (threats の id) で書き、種は書かない。plan には前提の場と速度の計画を書く",
            "output_schema": {"authoritative": {"concepts": [{"name": "str", "core_ids": ["id"],
                                                             "core": [{"species_id": "id", "role": "role_vocabulary の id"}],
                                                             "mega_id": "id|null",
                                                             "win_condition": "enum", "support_roles": ["enum"],
                                                             "weak_to": ["id"],
                                                             "complement_requirements": [{"role": "role_vocabulary の id",
                                                                                          "targets": ["threat id"], "note": "str"}],
                                                             "plan": {"field": {"weather": "sun|rain|sand|snow|null",
                                                                                "terrain": "electric|grassy|psychic|misty|null"},
                                                                      "speed_plan": "outspeed|trick_room|neutral"},
                                                             **({"archetype": "id|null", "branch": "id|null",
                                                                 "special_branch": "id|null"} if archetypes else {})}]},
                              "display": {"explanations": {"<name>": "str"}}},
        }
        if archetypes:
            payload_base["archetypes"] = [{"id": a["id"], "label": a["label"], "special": a.get("special", False)}
                                          for a in archetypes]
        for r in range(rounds):
            framing = framings[r % len(framings)]
            payload = dict(payload_base)
            payload["framing"] = framing
            payload["n"] = per_round
            payload["already_found"] = [f["core_ids"] for f in fams]
            if framing.startswith("archetype:"):
                ax = arch_by_id[framing.split(":", 1)[1]]
                payload["archetype"] = ax
                payload["instruction"] = (
                    f"構築の軸 {ax['id']} ({ax['label']}: {ax['description']}。交代方針: {ax.get('switching_ja', '')}) の観点で、"
                    f"既出 (already_found) と core が 4/6 以上重ならない新しいコンセプトを {per_round} 個。"
                    f"分岐 (branches) の環境適合 (fit、根拠 notes) が高いものを優先し、各分岐の roles (必要な役割と候補 candidates) を "
                    f"core_ids に含める。concept には archetype と branch (この軸の分岐 id) を書く。core_ids は owned の id のみ。"
                    + (" この軸は特殊な勝ち筋の集まりなので、分岐ごとに 1 個までにする。" if ax.get("special") else
                       " special_options (特殊な勝ち筋の分岐と役の候補) があれば、その役を 1 体足した併用案も出してよい"
                       " (concept に special_branch を書き、その役を core_ids に含める)。"))
            else:
                payload["instruction"] = (f"framing={framing} の観点で、既出 (already_found) と core が 4/6 以上重ならない"
                                          f"新しいコンセプトを {per_round} 個。core_ids は owned の id のみ。")
            res = provider.call("s04_concepts", "opus", system_prompt or DEFAULT_SYSTEM, payload,
                                validator=lambda a: validate_concepts(a, owned, legal, mega_capable,
                                                                      archetypes=arch_ids if archetypes else None,
                                                                      banned=banned, ace=ace, ace_mega=ace_mega))
            calls.append({"round": r, "framing": framing, "ok": res["ok"], "attempts": res["attempts"],
                          "problems": res.get("problems"), "record": res.get("record")})
            if not res["ok"]:
                continue
            new_items = [dict(c, source=f"llm:{framing}") for c in res["authoritative"]["concepts"]]
            if framing.startswith("archetype:"):
                ax_id = framing.split(":", 1)[1]
                for c in new_items:
                    c.setdefault("archetype", ax_id)
                    if c.get("archetype") != ax_id:
                        c["branch"] = c.get("branch") if c.get("branch") in arch_ids.get(c.get("archetype"), []) else None
            before = len(fams)
            fams = cluster_concepts([dict(f) for f in fams] + new_items)
            gained = len(fams) - before
            dup_rate = 1.0 - gained / max(1, len(new_items))
            if log:
                log(f"[concepts] round {r} framing={framing}: +{gained} 系統 (dup {dup_rate:.0%})")
            if gained / max(1, len(new_items)) < STOP_NEW_YIELD or dup_rate >= STOP_DUP_RATE:
                # 軸ごとの framing では全部の軸を 1 回ずつ回し終えるまで止めない (config BUILD_ARCHETYPE_FULL_PASS)。
                # 2026-09-24: 1 軸の重複率で残りの軸 (special を含む) が打ち切られていた
                if framing.startswith("archetype:") and BUILD_ARCHETYPE_FULL_PASS and r + 1 < len(framings):
                    if log:
                        log(f"[concepts] round {r}: coverage 条件だが軸の一巡 ({r + 1}/{len(framings)}) まで続ける")
                    continue
                stop_reason = "coverage"
                break
    return {"families": fams, "rounds": len(calls), "stop_reason": stop_reason, "llm_calls": calls}


DEFAULT_SYSTEM = (
    "あなたはポケモンチャンピオンズ (Lv50、6体から3体選出、メガシンカは1試合1回) の構築コンセプトを提案する。"
    "入力の owned (所持 id) と threats (脅威 id)、coverage (所持種が各脅威をどれだけ扱えるか 0..1)、roles を根拠に、"
    "軸 2〜3 体 (core_ids) と勝ち筋 (win_condition)、支援役割 (support_roles)、苦手 (weak_to) を JSON で返す。"
    "archetype (構築の軸: 積み展開は積んだ後に交代しない、壁や起点作りで積む隙を作る 等) が渡されたら、その軸の分岐と役割の構造に"
    "沿った core を出し、archetype / branch を書く。"
    "機械が使う値は authoritative に id / enum だけで書き、説明文は display に書く。"
    "owned に無い id、存在しない技・持ち物、数値の指定は書かない。出力は JSON オブジェクト 1 つのみ。"
)
