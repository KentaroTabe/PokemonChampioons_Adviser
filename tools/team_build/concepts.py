"""コンセプト系統の生成 (S4): ルール生成の baseline + LLM の複数独立生成 → 重複除去・クラスタリング → coverage 停止。

LLM は探索ヒューリスティックの一つ。出力は authoritative (id / enum) だけを検証して使い、display は表示専用
(docs/TEAM_BUILDING_IMPLEMENTATION.md §10)。回数上限ではなく「新しい系統がほぼ出なくなったら」停止する。
"""
from __future__ import annotations

import itertools
import json
from typing import Callable, Optional

from tools.team_build.families import jaccard

WIN_CONDITIONS = ("setup_sweep", "offense_trade", "cycle_pressure", "hazard_chip", "speed_control",
                  "bulky_attrition", "priority_cleanup", "anti_meta")
SUPPORT_ROLES = ("speed_control", "hazard_control", "priority", "pivot", "status", "bulk", "setup")
STYLE_FRAMINGS = ("offense", "balance", "bulky_offense", "cycle", "setup", "speed_control", "anti_meta",
                  "specific_core")
CONCEPT_MIN_CORE, CONCEPT_MAX_CORE = 2, 3
STOP_NEW_YIELD = 0.15      # 直近の生成で新系統の割合がこれ未満なら停止
STOP_DUP_RATE = 0.7        # 重複率がこれ以上でも停止
MAX_ROUNDS = 6


def concept_key(core_ids: list) -> tuple:
    return tuple(sorted(set(core_ids)))


def validate_concepts(auth: dict, owned: set, legal: set, mega_capable: set,
                      archetypes: Optional[dict] = None) -> list:
    """authoritative の検証: 問題の一覧 (空なら OK)。archetypes ({axis_id: [branch_id]}) を渡すと、concept の
    archetype / branch (任意) が既知の id であることも検査する"""
    problems = []
    items = auth.get("concepts")
    if not isinstance(items, list) or not items:
        return ["concepts が空 (配列で返す)"]
    for i, c in enumerate(items):
        if archetypes is not None and (c.get("archetype") or c.get("branch")):
            ax = c.get("archetype")
            if ax not in archetypes:
                problems.append(f"concepts[{i}]: archetype {ax} は {sorted(archetypes)} のいずれか")
            elif c.get("branch") and c.get("branch") not in archetypes[ax]:
                problems.append(f"concepts[{i}]: branch {c.get('branch')} は {archetypes[ax]} のいずれか")
        core = c.get("core_ids") or []
        if not (CONCEPT_MIN_CORE <= len(set(core)) <= CONCEPT_MAX_CORE):
            problems.append(f"concepts[{i}]: core_ids は {CONCEPT_MIN_CORE}〜{CONCEPT_MAX_CORE} 体")
        for sid in core:
            if sid not in owned:
                problems.append(f"concepts[{i}]: {sid} は所持にない (所持リストの id だけを使う)")
            elif legal and sid not in legal:
                problems.append(f"concepts[{i}]: {sid} は使用不可")
        mega = c.get("mega_id")
        if mega and (mega not in core or mega not in mega_capable):
            problems.append(f"concepts[{i}]: mega_id {mega} は core に含まれメガ石を持てる種でなければならない")
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
    owned = set(spec.owned)
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
            "constraints": ("各 concept の core_ids には、rules ごとに setters から 1 体と aces から 1 体 (別個体) を必ず含める"
                            if rules else ""),
            "output_schema": {"authoritative": {"concepts": [{"name": "str", "core_ids": ["id"], "mega_id": "id|null",
                                                             "win_condition": "enum", "support_roles": ["enum"],
                                                             "weak_to": ["id"],
                                                             **({"archetype": "id|null", "branch": "id|null"} if archetypes else {})}]},
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
                    + (" この軸は特殊な勝ち筋の集まりなので、分岐ごとに 1 個までにする。" if ax.get("special") else ""))
            else:
                payload["instruction"] = (f"framing={framing} の観点で、既出 (already_found) と core が 4/6 以上重ならない"
                                          f"新しいコンセプトを {per_round} 個。core_ids は owned の id のみ。")
            res = provider.call("s04_concepts", "opus", system_prompt or DEFAULT_SYSTEM, payload,
                                validator=lambda a: validate_concepts(a, owned, legal, mega_capable,
                                                                      archetypes=arch_ids if archetypes else None))
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
