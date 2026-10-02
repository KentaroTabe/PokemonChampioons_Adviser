"""特性の効果表の生成 (tools/build_ability_data.py) のテスト。解析は TS 断片で、実ファイルは煙試験と人手の式の整合。

    python -m tests.test_ability_data
"""
from __future__ import annotations

import json

from tools import build_ability_data as A

SNIPPET = """export const Abilities: import('../sim/dex-abilities').AbilityDataTable = {
\tsharpness: {
\t\tonBasePowerPriority: 19,
\t\tonBasePower(basePower, attacker, defender, move) {
\t\t\tif (move.flags['slicing']) {
\t\t\t\tthis.debug('Sharpness boost');
\t\t\t\treturn this.chainModify(1.5);
\t\t\t}
\t\t},
\t\tflags: {},
\t\tname: "Sharpness",
\t\trating: 3.5,
\t\tnum: 292,
\t},
\tdragonize: {
\t\tisNonstandard: "Future",
\t\tonModifyTypePriority: -1,
\t\tonModifyType(move, pokemon) {
\t\t\tif (move.type === 'Normal') {
\t\t\t\tmove.type = 'Dragon';
\t\t\t}
\t\t},
\t\tonBasePowerPriority: 23,
\t\tonBasePower(basePower, pokemon, target, move) {
\t\t\tif (move.typeChangerBoosted === this.effect) return this.chainModify([4915, 4096]);
\t\t},
\t\tflags: {},
\t\tname: "Dragonize",
\t\trating: 4,
\t\tnum: 312,
\t},
\tdrizzle: {
\t\tonStart(source) {
\t\t\tthis.field.setWeather('raindance', source);
\t\t},
\t\tflags: {},
\t\tname: "Drizzle",
\t\trating: 4,
\t\tnum: 2,
\t},
\tfrisk: {
\t\tonStart(pokemon) {
\t\t\tthis.add('-ability', pokemon, 'Frisk');
\t\t},
\t\tflags: {},
\t\tname: "Frisk",
\t\trating: 1.5,
\t\tnum: 119,
\t},
};
"""
MOD_SNIPPET = """export const Abilities: import('../../../sim/dex-abilities').ModdedAbilityDataTable = {
\tdragonize: {
\t\tinherit: true,
\t\tisNonstandard: null,
\t},
};
"""
CDEX = {"kleavor": {"name": "Kleavor", "baseSpecies": "Kleavor", "abilities": {"0": "Swarm", "1": "Sheer Force", "H": "Sharpness"}},
        "feraligatr": {"name": "Feraligatr", "baseSpecies": "Feraligatr", "abilities": {"0": "Torrent", "H": "Sheer Force"}},
        "feraligatrmega": {"name": "Feraligatr-Mega", "baseSpecies": "Feraligatr", "isMega": True, "abilities": {"0": "Dragonize"}},
        "pelipper": {"name": "Pelipper", "baseSpecies": "Pelipper", "abilities": {"0": "Keen Eye", "1": "Drizzle"}},
        "mew": {"name": "Mew", "baseSpecies": "Mew", "abilities": {"0": "Synchronize"}}}


def test_parse_snippet():
    legal = {"kleavor", "feraligatr", "pelipper"}            # mew は参戦外 → synchronize は表に出ない
    overrides = {"sharpness": {"formula": [{"kind": "offense_mult", "mult": 1.5, "when": {"move_flag": "slicing"}}], "tags": ["offense"]},
                 "frisk": {"formula": [{"kind": "none"}], "value_zero": True}}
    assert A.validate_overrides(overrides) == []
    assert A.validate_overrides({"x": {"formula": [{"kind": "bogus"}]}}) == ["x: formula[0].kind='bogus' は FORMULA_KINDS に無い"]
    table = A.build_table(SNIPPET, MOD_SNIPPET, CDEX, legal, overrides)
    assert set(table) == {"sharpness", "dragonize", "drizzle", "keeneye", "sheerforce", "swarm", "torrent"}
    sh = table["sharpness"]
    assert sh["categories"] == ["火力・追加効果"] and sh["numbers"] == {"onBasePower": [1.5]} and sh["holders"] == ["kleavor"]
    assert sh["formula"][0]["mult"] == 1.5 and sh["source"] == "curated"
    dz = table["dragonize"]
    assert dz["holders"] == ["feraligatrmega(mega)"] and dz["nonstandard"] is None            # mod で解禁
    assert dz["categories"][0] == "タイプ付与・変更" and dz["numbers"] == {"onBasePower": [1.2]}   # [4915, 4096] → 1.2
    assert dz["formula"] is None and dz["source"] == "auto"
    assert table["drizzle"]["categories"] == ["天候・フィールド始動"]
    assert table["keeneye"]["categories"] == ["データ無し"] and table["keeneye"]["formula"] is None   # 断片に定義が無い
    # 人手の表に無い特性は未記入として要約に出る (frisk は参戦種が持たないので表に出ない)
    assert "frisk" not in table
    print("test_parse_snippet OK")


def test_real_files():
    """実ファイル: 参戦種の特性 226 種類の全部に式があり、自動抽出の倍率と人手の式が食い違わない"""
    if not (A.ABILITIES_TS.exists() and A.CDEX_PATH.exists()):
        print("test_real_files SKIP")
        return
    from tools.team_build.spec import legal_species_ids
    cdex = json.loads(A.CDEX_PATH.read_text(encoding="utf-8")).get("species") or {}
    overrides = json.loads(A.OVERRIDES_PATH.read_text(encoding="utf-8"))
    assert A.validate_overrides(overrides) == []
    table = A.build_table(A.ABILITIES_TS.read_text(encoding="utf-8"), A.MOD_TS.read_text(encoding="utf-8"), cdex,
                          legal_species_ids(), overrides)
    assert len(table) >= 200
    missing = [a for a, r in table.items() if not r["formula"]]
    assert not missing, f"式が無い特性: {missing}"
    extra = [a for a in overrides if not a.startswith("_") and a not in table]
    assert not extra, f"参戦種が持たない特性が人手の表にある: {extra}"
    # 自動抽出の倍率 (onBasePower / onModifyAtk / onModifySpA) と人手の offense_mult が一致する
    bad = []
    for aid, r in table.items():
        nums = set()
        for h in ("onBasePower", "onModifyAtk", "onModifySpA"):
            nums |= set(r["numbers"].get(h, []))
        mults = {f.get("mult") for f in r["formula"] if f.get("kind") == "offense_mult" and f.get("mult")}
        if nums and mults and not any(abs(m - n) < 0.01 for m in mults for n in nums):
            bad.append((aid, sorted(nums), sorted(mults)))
    assert not bad, bad
    t = table
    assert t["lightningrod"]["formula"][0] == {"kind": "immune", "move_type": "Electric", "effect": "boost", "boosts": {"spa": 1}}
    assert t["sheerforce"]["formula"][0]["mult"] == 1.3 and t["supremeoverlord"]["formula"][0]["per_fainted_ally"] == 0.1
    assert t["protean"]["formula"][0]["kind"] == "stab_any" and t["pixilate"]["formula"][0]["to"] == "Fairy"
    assert t["frisk"]["value_zero"] and t["pressure"]["value_zero"] and t["plus"]["value_zero"]
    assert "kingambit" in t["supremeoverlord"]["holders"] and "charizardmegay(mega)" in t["drought"]["holders"]
    assert t["megasol"]["nonstandard"] is None and t["dragonize"]["nonstandard"] is None     # mod で解禁
    print("test_real_files OK")


def test_generated_file_matches_source():
    if not (A.OUT_PATH.exists() and A.ABILITIES_TS.exists()):
        print("test_generated_file_matches_source SKIP")
        return
    from tools.team_build.spec import legal_species_ids
    doc = json.loads(A.OUT_PATH.read_text(encoding="utf-8"))
    cdex = json.loads(A.CDEX_PATH.read_text(encoding="utf-8")).get("species") or {}
    overrides = json.loads(A.OVERRIDES_PATH.read_text(encoding="utf-8"))
    fresh = A.build_table(A.ABILITIES_TS.read_text(encoding="utf-8"), A.MOD_TS.read_text(encoding="utf-8"), cdex,
                          legal_species_ids(), overrides)
    diff = [a for a in sorted(set(doc["abilities"]) | set(fresh)) if doc["abilities"].get(a) != fresh.get(a)]
    assert not diff, f"ability_effects.json が古い (scripts/build_ability_data.sh で再生成): {diff[:10]}"
    assert doc["_meta"]["formula_kinds"] == list(A.FORMULA_KINDS)
    print("test_generated_file_matches_source OK")


if __name__ == "__main__":
    test_parse_snippet()
    test_real_files()
    test_generated_file_matches_source()
