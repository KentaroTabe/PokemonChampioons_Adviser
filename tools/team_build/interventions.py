"""仮説 → 介入 → 検証 (S9)。敗因の「診断」ではなく、変更候補を作って実対戦で競わせる。

- 入力: dev の敗因統計 (loss_stats)、親候補 (並び・型)、所持種の特徴
- 仮説: LLM (Opus) が最大 N 個 (authoritative: {"hypotheses": [{"id","kind":"member|set|pick","target_id","replacement_id"|null,"reason_enum"}]})
        + ルール側の mutation (最も負けに寄与した相手を最も扱える所持種へ 1 体入替、その相手への打点技を 1 本入替)
- 介入: Variant A (1 体変更) / B (型だけ変更) / C (選出方策だけ変更) を作り、系譜 (lineage) を付ける
- ≤2 枠は同系統の改修、3 枠以上は新しいコンセプト系統として扱う
検証 (paired racing) は run.py 側で行う。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from champions_agent.config import BUILD_MAX_CHANGES

HYPOTHESIS_KINDS = ("member", "set", "pick")
REASON_ENUM = ("no_switch_in", "outsped", "setup_not_stopped", "hazard_pressure", "status_pressure",
               "selection_mismatch", "resource_depleted", "mega_timing", "unknown")


def validate_hypotheses(auth: dict, owned: set, members: set) -> list:
    problems = []
    hs = auth.get("hypotheses")
    if not isinstance(hs, list) or not hs:
        return ["hypotheses が空"]
    for i, h in enumerate(hs[:3]):
        if h.get("kind") not in HYPOTHESIS_KINDS:
            problems.append(f"hypotheses[{i}]: kind は {HYPOTHESIS_KINDS}")
        if h.get("kind") in ("member", "set") and h.get("target_id") not in members:
            problems.append(f"hypotheses[{i}]: target_id は現在の並びの id")
        if h.get("kind") == "member":
            rep = h.get("replacement_id")
            if rep not in owned or rep in members:
                problems.append(f"hypotheses[{i}]: replacement_id は所持で並びに無い id")
        if h.get("reason") not in REASON_ENUM:
            problems.append(f"hypotheses[{i}]: reason は {REASON_ENUM}")
    return problems


def rule_mutations(stats: dict, members: list, feats: dict, threats: list, max_changes: int = BUILD_MAX_CHANGES) -> list:
    """ルール側の変更候補: 負けに寄与した相手上位に対して、被覆が最も弱い味方を最も扱える所持種に入替"""
    out = []
    top = [r["key"] for r in stats.get("loss_by_opponent_species", [])[:3] if r["key"] in threats]
    for t in top:
        worst = min(members, key=lambda m: feats[m].coverage.get(t, 0.0) if m in feats else 1.0)
        best = max((s for s in feats if s not in members), key=lambda s: feats[s].coverage.get(t, 0.0), default=None)
        if best is None:
            continue
        out.append({"id": f"rule:member:{t}", "kind": "member", "target_id": worst, "replacement_id": best,
                    "reason": "no_switch_in", "source": "rule", "against": t})
        if len(out) >= max_changes:
            break
    return out


def make_variants(parent_id: str, members: list, hypotheses: list) -> list:
    """仮説 → Variant (系譜つき)。member は 1 体入替、set は型だけ、pick は選出方策だけ"""
    variants = []
    for h in hypotheses:
        kind = h.get("kind")
        if kind == "member":
            new_members = [h["replacement_id"] if m == h["target_id"] else m for m in members]
            variants.append({"variant_id": f"{parent_id}-A-{h['target_id']}-{h['replacement_id']}", "kind": "A_member",
                             "members": sorted(new_members),
                             "changes": [{"out": h["target_id"], "in": h["replacement_id"], "hypothesis": h.get("id"),
                                          "reason": h.get("reason")}], "parent_team_id": parent_id})
        elif kind == "set":
            variants.append({"variant_id": f"{parent_id}-B-{h['target_id']}", "kind": "B_set", "members": sorted(members),
                             "set_target": h["target_id"], "set_against": h.get("against"),
                             "changes": [{"out": None, "in": None, "hypothesis": h.get("id"), "reason": h.get("reason")}],
                             "parent_team_id": parent_id})
        elif kind == "pick":
            variants.append({"variant_id": f"{parent_id}-C-pick", "kind": "C_pick", "members": sorted(members),
                             "pick_policy": "teampreview",
                             "changes": [{"out": None, "in": None, "hypothesis": h.get("id"), "reason": h.get("reason")}],
                             "parent_team_id": parent_id})
    return variants


def classify_change(parent_members: list, child_members: list, max_changes: int = BUILD_MAX_CHANGES) -> str:
    """変更枠数で repair (同系統) か new_branch (新系統) かを決める"""
    diff = len(set(parent_members) ^ set(child_members)) // 2
    return "repair" if diff <= max_changes else "new_branch"


def record_lineage(run_dir: Path, parent_id: str, variants: list) -> Path:
    p = Path(run_dir) / "lineage.json"
    data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"nodes": []}
    for v in variants:
        data["nodes"].append({"id": v["variant_id"], "parent": parent_id, "kind": v["kind"], "members": v["members"],
                              "changes": v["changes"], "at": time.strftime("%Y-%m-%d %H:%M:%S")})
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return p


HYPOTHESIS_SYSTEM = (
    "あなたはポケモンチャンピオンズの構築の改修仮説を出す。入力は機械が集計した敗因統計 (どの相手に負けたか、"
    "誰に何で倒されたか、選出されなかった味方) と現在の並び・所持種。診断を断定せず、検証可能な修正仮説を最大 3 つ、"
    "authoritative に {\"hypotheses\": [{\"id\", \"kind\": member|set|pick, \"target_id\", \"replacement_id\" (member のみ), "
    "\"reason\": enum}]} で返す。id は所持リストのもののみ。説明は display に。出力は JSON オブジェクト 1 つ。"
)


def llm_hypotheses(provider, stats: dict, members: list, owned: list, feats_summary: dict, log_dir_stage: str = "s09_hypotheses") -> list:
    payload = {"task": "修正仮説 ≤3", "members": sorted(members), "owned": sorted(owned),
               "loss_stats": {k: stats.get(k) for k in ("loss_by_opponent_species", "loss_by_opponent_lead",
                                                        "loss_by_our_lead", "ko_source", "unused_members")},
               "coverage_summary": feats_summary, "enums": {"kind": HYPOTHESIS_KINDS, "reason": REASON_ENUM}}
    res = provider.call(log_dir_stage, "opus", HYPOTHESIS_SYSTEM, payload,
                        validator=lambda a: validate_hypotheses(a, set(owned), set(members)))
    if not res["ok"]:
        return []
    return [dict(h, source="llm") for h in res["authoritative"]["hypotheses"][:3]]
