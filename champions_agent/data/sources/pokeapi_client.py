"""
PokeAPI (https://pokeapi.co) から静的データ(種族値・タイプ・特性・技・持ち物)を取得するクライアント。

PokeAPIは英語名ベースのため、日本語表示名は `names` エンドポイントの
language=ja-Hrkt / ja を利用して補完する。
"""
from __future__ import annotations

import time
from typing import Any, Iterable

import requests

from champions_agent.config import POKEAPI_BASE_URL

_SESSION = requests.Session()
_REQUEST_INTERVAL_SEC = 0.1  # PokeAPIへの配慮(レートリミット対策)


def _get(path: str) -> dict[str, Any]:
    url = path if path.startswith("http") else f"{POKEAPI_BASE_URL}{path}"
    resp = _SESSION.get(url, timeout=15)
    resp.raise_for_status()
    time.sleep(_REQUEST_INTERVAL_SEC)
    return resp.json()


def _japanese_name(names: list[dict[str, Any]]) -> str | None:
    for n in names:
        lang = n.get("language", {}).get("name")
        if lang == "ja-Hrkt":
            return n.get("name")
    for n in names:
        lang = n.get("language", {}).get("name")
        if lang == "ja":
            return n.get("name")
    return None


def iter_pokemon_list(limit: int = 2000) -> Iterable[dict[str, Any]]:
    """/pokemon 一覧(name, url)を返す。国民図鑑準拠の範囲に絞りたい場合は呼び出し側でフィルタする。"""
    data = _get(f"/pokemon?limit={limit}")
    return data.get("results", [])


def fetch_pokemon_detail(name_or_id: str | int) -> dict[str, Any]:
    """種族値・タイプ・特性・技一覧を含む詳細を取得し、DB投入向けに整形して返す。"""
    poke = _get(f"/pokemon/{name_or_id}")
    species = _get(poke["species"]["url"].replace(POKEAPI_BASE_URL, ""))

    stats = {s["stat"]["name"]: s["base_stat"] for s in poke["stats"]}
    types = [t["type"]["name"] for t in sorted(poke["types"], key=lambda x: x["slot"])]

    abilities = []
    for a in poke["abilities"]:
        abilities.append({
            "name": a["ability"]["name"],
            "url": a["ability"]["url"],
            "is_hidden": a["is_hidden"],
            "slot": a["slot"],
        })

    moves = []
    for m in poke["moves"]:
        move_name = m["move"]["name"]
        for vgd in m.get("version_group_details", []):
            moves.append({
                "name": move_name,
                "url": m["move"]["url"],
                "learn_method": vgd["move_learn_method"]["name"],
            })
            break  # 最新バージョングループの情報だけで十分なので1件のみ採用

    display_name = _japanese_name(species.get("names", []))

    return {
        "id": poke["id"],
        "name": poke["name"],
        "display_name": display_name,
        "hp": stats.get("hp"),
        "attack": stats.get("attack"),
        "defense": stats.get("defense"),
        "sp_attack": stats.get("special-attack"),
        "sp_defense": stats.get("special-defense"),
        "speed": stats.get("speed"),
        "type1": types[0] if len(types) > 0 else None,
        "type2": types[1] if len(types) > 1 else None,
        "abilities": abilities,
        "moves": moves,
    }


def fetch_ability_detail(name_or_id: str | int) -> dict[str, Any]:
    data = _get(f"/ability/{name_or_id}")
    display_name = _japanese_name(data.get("names", []))
    effect_text = None
    for e in data.get("effect_entries", []):
        if e.get("language", {}).get("name") == "en":
            effect_text = e.get("short_effect") or e.get("effect")
            break
    return {
        "id": data["id"],
        "name": data["name"],
        "display_name": display_name,
        "effect_text": effect_text,
    }


def fetch_move_detail(name_or_id: str | int) -> dict[str, Any]:
    data = _get(f"/move/{name_or_id}")
    display_name = _japanese_name(data.get("names", []))
    effect_text = None
    for e in data.get("effect_entries", []):
        if e.get("language", {}).get("name") == "en":
            effect_text = e.get("short_effect") or e.get("effect")
            break
    return {
        "id": data["id"],
        "name": data["name"],
        "display_name": display_name,
        "type": data["type"]["name"] if data.get("type") else None,
        "category": data["damage_class"]["name"] if data.get("damage_class") else None,
        "power": data.get("power"),
        "accuracy": data.get("accuracy"),
        "pp": data.get("pp"),
        "priority": data.get("priority"),
        "effect_text": effect_text,
    }


def fetch_item_detail(name_or_id: str | int) -> dict[str, Any]:
    data = _get(f"/item/{name_or_id}")
    display_name = _japanese_name(data.get("names", []))
    effect_text = None
    for e in data.get("effect_entries", []):
        if e.get("language", {}).get("name") == "en":
            effect_text = e.get("short_effect") or e.get("effect")
            break
    return {
        "id": data["id"],
        "name": data["name"],
        "display_name": display_name,
        "effect_text": effect_text,
    }
