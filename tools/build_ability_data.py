"""Showdown の特性データ (pokemon-showdown/data/abilities.ts + mods/champions/abilities.ts) と参戦種 (champions_dex) から、
構築と助言が使う特性の効果表 advisor/data/ability_effects.json を起こす (docs/TEAM_BUILD_REDESIGN_1002.md §9)。

    python -m tools.build_ability_data            # 生成
    python -m tools.build_ability_data --dry-run  # 要約だけ

1 特性 1 行: name / holders (参戦種、メガ後は "(mega)") / hooks (Showdown のフック名) / categories (フック名からの推定) /
numbers (フックごとに見つかった chainModify 等の倍率、自動) / nonstandard / formula (計算と選択に使う式。
人手の表 advisor/data/ability_effects_overrides.json の値を合成。無ければ null で _meta.todo に列挙) / tags (選択の役割タグ) /
value_zero (対戦に効かない)。式の種類 (kind) は FORMULA_KINDS に限る (計算側がその enum で分岐する)。
純粋な解析関数はテストから使う。
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ABILITIES_TS = REPO / "pokemon-showdown" / "data" / "abilities.ts"
MOD_TS = REPO / "pokemon-showdown" / "data" / "mods" / "champions" / "abilities.ts"
CDEX_PATH = REPO / "champions_agent" / "data" / "champions_dex.json"
OUT_PATH = REPO / "advisor" / "data" / "ability_effects.json"
OVERRIDES_PATH = REPO / "advisor" / "data" / "ability_effects_overrides.json"

# 式の種類 (計算・選択側が分岐する enum)。overrides の formula[].kind はこの中から
FORMULA_KINDS = (
    "offense_mult",        # 与ダメ倍率 (when で条件)
    "defense_mult",        # 被ダメ倍率 (when で条件)
    "type_change",         # 技のタイプを変える (from → to、mult)
    "stab_any",            # 使う技が全部タイプ一致 (へんげんじざい / リベロ)
    "immune",              # そのタイプを無効 (effect: 吸収・能力上昇)
    "accuracy_mult",       # 命中倍率
    "evasion_mult",        # 回避 (相手の命中倍率)
    "no_miss",             # 必中 (ノーガード)
    "crit_stage",          # 急所段階 +n
    "crit_mult",           # 急所倍率の変更 (スナイパー)
    "no_crit_taken",       # 急所を受けない
    "multihit_max",        # 連続技が最大回数 (スキルリンク)
    "extra_hit",           # 2 発目 (おやこあい)
    "recoil_none",         # 反動無し
    "secondary_none",      # 追加効果を消して威力増 (ちからずく、when 付き)
    "secondary_chance_mult",  # 追加効果の確率倍率 (てんのめぐみ)
    "speed_mult",          # 素早さ倍率 (when で天候・条件)
    "priority_delta",      # 優先度 +n (when で技の種類)
    "weather_set",         # 着地で天候
    "terrain_set",         # 着地でフィールド
    "weather_negate",      # 天候無効
    "switch_in_boost",     # 着地で能力変化 (target: self|foe)
    "on_ko_boost",         # 撃破で能力変化
    "contact_effect",      # 接触で相手に効果 (状態・ダメージ)
    "status_immune",       # 状態異常を受けない
    "stat_drop_immune",    # 能力低下を受けない
    "heal_on_switch",      # 交代で回復 (さいせいりょく)
    "heal_turn",           # ターン終了時に回復 (when 天候 等)
    "damage_turn",         # ターン終了時に減る (サンパワー等)
    "item_effect",         # 持ち物に関わる (かるわざ / しゅうかく 等)
    "misc",                # 計算に入れない説明だけ
    "none",                # 対戦に効かない (価値 0)
)
HOOK_CATEGORY = [
    ("天候・フィールド始動", lambda h, b: ("setWeather(" in b or "setTerrain(" in b) and ({"onStart", "onSwitchIn"} & h)),
    ("タイプ付与・変更", lambda h, b: "onModifyType" in h or ("onPrepareHit" in h and "setType(" in b)),
    ("火力・追加効果", lambda h, b: bool(h & {"onModifyAtk", "onModifySpA", "onBasePower", "onModifyDamage", "onModifyCritRatio",
                                            "onAllyBasePower"}) or ("onModifyMove" in h and any(
        k in b for k in ("basePower", "secondaries", "multihit", "ignoreAbility", "critRatio", "willCrit")))),
    ("無効・吸収・妨害", lambda h, b: ("onTryHit" in h and any(k in b for k in ("immune", "heal(", "boost(", "addVolatile", "return null")))
     or bool(h & {"onImmunity", "onEffectiveness", "onFoeTryMove", "onTryBoost"})),
    ("被ダメ・反動の補正", lambda h, b: bool(h & {"onSourceModifyAtk", "onSourceModifySpA", "onSourceModifyDamage", "onSourceBasePower",
                                               "onAnyModifyDamage", "onDamage", "onSourceModifySecondaries", "onCriticalHit",
                                               "onAnyBasePower"})),
    ("命中・回避", lambda h, b: bool(h & {"onModifyAccuracy", "onSourceModifyAccuracy", "onAnyAccuracy", "onAnyInvulnerability"})),
    ("素早さ・優先度", lambda h, b: bool(h & {"onModifySpe", "onModifyPriority", "onFractionalPriority"})),
    ("着地時の能力変化", lambda h, b: bool({"onStart", "onSwitchIn"} & h) and "boost(" in b and "setWeather(" not in b),
    ("撃破・技後の能力変化", lambda h, b: bool(h & {"onSourceAfterFaint", "onAfterMoveSecondarySelf", "onAnyFaint"})),
    ("被弾時の効果", lambda h, b: "onDamagingHit" in h),
    ("ターン経過・場の効果", lambda h, b: bool(h & {"onResidual", "onWeather", "onTerrain"})),
    ("状態異常・交代・その他", lambda h, b: bool(h & {"onUpdate", "onSetStatus", "onTryAddVolatile", "onAfterSetStatus", "onSwitchOut",
                                                  "onFaint", "onAfterEachBoost"})),
]
NUMBER_HOOKS = ("onModifyAtk", "onModifySpA", "onModifyDef", "onModifySpD", "onBasePower", "onModifyDamage", "onSourceModifyAtk",
                "onSourceModifySpA", "onSourceModifyDamage", "onSourceBasePower", "onAnyModifyDamage", "onModifySpe",
                "onModifyAccuracy", "onSourceModifyAccuracy", "onAllyBasePower", "onAnyBasePower", "onModifyCritRatio",
                "onFoeBasePower", "onAllyModifyAtk", "onAllyModifySpD")


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


# ------------------------------------------------------------------ 解析 (純粋)
def parse_blocks(text: str) -> dict:
    """abilities.ts の本文 → {id: {"body", "hooks", "nonstandard", "inherit", "name", "flags"}}"""
    out = {}
    for m in re.finditer(r"^\t([a-z0-9]+): \{\n(.*?)^\t\},", text, flags=re.M | re.S):
        aid, body = m.group(1), m.group(2)
        hooks = set(re.findall(r"^\t\t(on\w+)\s*\(", body, flags=re.M)) | set(re.findall(r"^\t\t(on\w+):", body, flags=re.M))
        ns = re.search(r'^\t\tisNonstandard: (null|"\w+"),', body, flags=re.M)
        name = re.search(r'^\t\tname: "([^"]+)",', body, flags=re.M)
        fl = re.search(r"^\t\tflags: \{([^}]*)\}", body, flags=re.M)
        out[aid] = {"body": body, "hooks": hooks, "inherit": "inherit: true" in body,
                    "nonstandard": (None if (ns and ns.group(1) == "null") else (ns.group(1).strip('"') if ns else None)),
                    "name": name.group(1) if name else None,
                    "flags": sorted(re.findall(r"(\w+): 1", fl.group(1))) if fl else []}
    return out


def merge_mod(base: dict, mod: dict) -> dict:
    out = {k: dict(v) for k, v in base.items()}
    for aid, d in mod.items():
        if aid in out and d["inherit"]:
            out[aid]["hooks"] = set(out[aid]["hooks"]) | set(d["hooks"])
            out[aid]["body"] = out[aid]["body"] + "\n" + d["body"]
            if re.search(r"^\t\tisNonstandard:", d["body"], flags=re.M):
                out[aid]["nonstandard"] = d["nonstandard"]
            out[aid]["mod"] = True
        else:
            out[aid] = dict(d, mod=True)
    return out


def hook_body(body: str, name: str) -> str:
    m = re.search(rf"^\t\t{name}\([^)]*\) \{{\n(.*?)^\t\t\}},", body, flags=re.M | re.S)
    return m.group(1) if m else ""


def extract_numbers(body: str, hooks: set) -> dict:
    """フックごとの倍率 (chainModify(1.5) / chainModify([5325, 4096]) / modify(...))。自動抽出、人手の式の材料"""
    out = {}
    for h in NUMBER_HOOKS:
        if h not in hooks:
            continue
        hb = hook_body(body, h)
        nums = []
        for a, b in re.findall(r"chainModify\(\[(\d+), (\d+)\]\)", hb):
            nums.append(round(int(a) / int(b), 4))
        for v in re.findall(r"chainModify\((\d+(?:\.\d+)?)\)", hb):
            nums.append(float(v))
        for v in re.findall(r"return (\d+(?:\.\d+)?);", hb):
            if h in ("onModifyCritRatio",):
                nums.append(float(v))
        if nums:
            out[h] = nums
    return out


def categorize(hooks: set, body: str) -> list:
    cats = [name for name, pred in HOOK_CATEGORY if pred(hooks, body)]
    return cats or ["その他"]


def holders_of(cdex: dict, legal: set) -> dict:
    """{ability_id: [species_id or species_id(mega)]} (参戦種とそのメガ後)"""
    out = defaultdict(set)
    for sid, e in cdex.items():
        base_id = norm(e.get("baseSpecies") or e.get("name"))
        is_mega = bool(e.get("isMega"))
        if sid in legal or (is_mega and base_id in legal):
            for v in (e.get("abilities") or {}).values():
                out[norm(v)].add(sid + ("(mega)" if is_mega else ""))
    return {k: sorted(v) for k, v in out.items()}


def validate_overrides(overrides: dict) -> list:
    """overrides の式の種類が FORMULA_KINDS に収まっているか (問題の一覧)"""
    problems = []
    for aid, entry in overrides.items():
        if aid.startswith("_"):
            continue
        for i, f in enumerate(entry.get("formula") or []):
            if f.get("kind") not in FORMULA_KINDS:
                problems.append(f"{aid}: formula[{i}].kind={f.get('kind')!r} は FORMULA_KINDS に無い")
    return problems


def build_table(base_text: str, mod_text: str, cdex: dict, legal: set, overrides: dict | None = None) -> dict:
    """{ability_id: 行} (参戦種が持ちうる特性だけ)。純粋"""
    data = merge_mod(parse_blocks(base_text), parse_blocks(mod_text) if mod_text else {})
    holders = holders_of(cdex, legal)
    overrides = overrides or {}
    out = {}
    for aid, sp in sorted(holders.items()):
        d = data.get(aid)
        row = {"name": (d or {}).get("name") or aid, "holders": sp, "n_holders": len(sp),
               "hooks": sorted((d or {}).get("hooks") or []), "flags": (d or {}).get("flags") or [],
               "categories": categorize(set((d or {}).get("hooks") or []), (d or {}).get("body") or "") if d else ["データ無し"],
               "numbers": extract_numbers((d or {}).get("body") or "", set((d or {}).get("hooks") or [])) if d else {},
               "nonstandard": (d or {}).get("nonstandard"), "formula": None, "tags": [], "value_zero": False, "source": "auto"}
        ov = overrides.get(aid)
        if ov:
            row.update({k: v for k, v in ov.items() if k in ("formula", "tags", "value_zero", "note")})
            row["source"] = "curated"
        out[aid] = row
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Showdown の特性データ + 参戦種 → advisor/data/ability_effects.json")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    from tools.team_build.spec import legal_species_ids
    base_text = ABILITIES_TS.read_text(encoding="utf-8")
    mod_text = MOD_TS.read_text(encoding="utf-8") if MOD_TS.exists() else ""
    cdex = json.loads(CDEX_PATH.read_text(encoding="utf-8")).get("species") or {}
    overrides = json.loads(OVERRIDES_PATH.read_text(encoding="utf-8")) if OVERRIDES_PATH.exists() else {}
    problems = validate_overrides(overrides)
    if problems:
        raise SystemExit("overrides の式に問題: " + "; ".join(problems))
    table = build_table(base_text, mod_text, cdex, legal_species_ids(), overrides)
    todo = sorted(a for a, r in table.items() if r["formula"] is None and not r["value_zero"])
    by_cat = defaultdict(int)
    for r in table.values():
        by_cat[r["categories"][0]] += 1
    print(f"特性 {len(table)} 種類 (参戦種 + メガ後)、式あり {sum(1 for r in table.values() if r['formula'])}、"
          f"価値 0 {sum(1 for r in table.values() if r['value_zero'])}、未記入 {len(todo)}")
    print("分類:", dict(by_cat))
    print("未記入:", todo)
    if args.dry_run:
        return
    doc = {"_meta": {"source": [str(ABILITIES_TS.relative_to(REPO)), str(MOD_TS.relative_to(REPO)), str(CDEX_PATH.relative_to(REPO))],
                     "overrides": str(OVERRIDES_PATH.relative_to(REPO)), "formula_kinds": list(FORMULA_KINDS), "todo": todo,
                     "note": "python -m tools.build_ability_data で生成。式 (formula) と選択のタグは overrides に書く"},
           "abilities": table}
    OUT_PATH.write_text(json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"書き出し: {OUT_PATH.relative_to(REPO)}")


if __name__ == "__main__":
    main()
