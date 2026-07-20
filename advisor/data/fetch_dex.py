"""PokeAPIのCSVデータからダメージ計算用の静的データ (dex.json) を構築する。

- species: showdown形式ID -> 種族値/タイプ/体重
- moves:   showdown形式ID -> タイプ/分類/威力/命中/優先度/PP
- typechart: 攻撃タイプ -> 防御タイプ -> 倍率

実行 (リポジトリルートから):
    python -m advisor.data.fetch_dex

ネットワーク必須。生成物: advisor/data/dex.json
"""
from __future__ import annotations

import csv
import io
import json
import re
import urllib.request
from pathlib import Path

BASE = "https://raw.githubusercontent.com/PokeAPI/pokeapi/master/data/v2/csv"
OUT_PATH = Path(__file__).resolve().parent / "dex.json"

STAT_KEYS = {1: "hp", 2: "atk", 3: "def", 4: "spa", 5: "spd", 6: "spe"}
DAMAGE_CLASS = {1: "Status", 2: "Physical", 3: "Special"}


def _fetch_csv(name: str) -> list[dict]:
    url = f"{BASE}/{name}.csv"
    print(f"fetching {url} ...")
    with urllib.request.urlopen(url, timeout=60) as res:
        text = res.read().decode("utf-8")
    return list(csv.DictReader(io.StringIO(text)))


def _to_showdown_id(identifier: str) -> str:
    return re.sub(r"[^a-z0-9]", "", identifier.lower())


def build() -> dict:
    pokemon = _fetch_csv("pokemon")
    pokemon_species = _fetch_csv("pokemon_species")
    pokemon_stats = _fetch_csv("pokemon_stats")
    pokemon_types = _fetch_csv("pokemon_types")
    types_rows = _fetch_csv("types")
    type_efficacy = _fetch_csv("type_efficacy")
    moves = _fetch_csv("moves")

    species_ident = {int(r["id"]): r["identifier"] for r in pokemon_species}

    type_name = {int(r["id"]): r["identifier"].capitalize() for r in types_rows
                 if int(r["id"]) < 10000}

    stats_by_pokemon: dict[int, dict] = {}
    for r in pokemon_stats:
        pid = int(r["pokemon_id"])
        stats_by_pokemon.setdefault(pid, {})[STAT_KEYS[int(r["stat_id"])]] = int(r["base_stat"])

    types_by_pokemon: dict[int, list] = {}
    for r in pokemon_types:
        pid = int(r["pokemon_id"])
        types_by_pokemon.setdefault(pid, []).append(
            (int(r["slot"]), type_name[int(r["type_id"])]))

    species: dict[str, dict] = {}
    for r in pokemon:
        pid = int(r["id"])
        sid = _to_showdown_id(r["identifier"])
        base = stats_by_pokemon.get(pid)
        if not base:
            continue
        entry = {
            "num": int(r["species_id"]),
            "types": [t for _, t in sorted(types_by_pokemon.get(pid, []))],
            "baseStats": base,
            "weightkg": (int(r["weight"]) / 10.0) if r.get("weight") else None,
        }
        species[sid] = entry
        # デフォルトフォーム名が種族名と異なる場合 (mimikyu-disguised 等) は
        # 種族名でも引けるようにエイリアスを張る
        if r.get("is_default") == "1":
            alias = _to_showdown_id(species_ident.get(int(r["species_id"]), ""))
            if alias and alias not in species:
                species[alias] = entry

    move_data: dict[str, dict] = {}
    for r in moves:
        mid = int(r["id"])
        if mid >= 10000:
            continue
        sid = _to_showdown_id(r["identifier"])
        move_data[sid] = {
            "type": type_name.get(int(r["type_id"]), "Normal"),
            "category": DAMAGE_CLASS.get(int(r["damage_class_id"] or 1), "Status"),
            "power": int(r["power"]) if r.get("power") else 0,
            "accuracy": int(r["accuracy"]) if r.get("accuracy") else 0,  # 0 = 必中扱い
            "priority": int(r["priority"] or 0),
            "pp": int(r["pp"]) if r.get("pp") else 0,
        }

    chart: dict[str, dict] = {}
    for r in type_efficacy:
        atk = type_name[int(r["damage_type_id"])]
        dfn = type_name[int(r["target_type_id"])]
        chart.setdefault(atk, {})[dfn] = int(r["damage_factor"]) / 100.0

    return {"species": species, "moves": move_data, "typechart": chart}


def main() -> None:
    data = build()
    OUT_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {OUT_PATH}")
    print(f"  species: {len(data['species'])}, moves: {len(data['moves'])}")


if __name__ == "__main__":
    main()
