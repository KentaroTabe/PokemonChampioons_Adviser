"""championsbattledata.com からの採用率データ取得。

ポケモンチャンピオンズのゲーム内「バトルデータ」を日次収集して公開している
ファンプロジェクトの公式JSON API。利用規約 (https://championsbattledata.com/api-rules/)
によりプログラムからの利用が許可されている (要クレジット表記):

    Battle data provided by Pokémon Champions Battle Data
    (https://championsbattledata.com)

提供粒度: ポケモンごとの 技 / 持ち物 / 特性 / 性格(能力補正) / 能力ポイント配分 /
チームメイトの採用率。ポケモン自体の使用率%は存在しない (ゲーム内仕様どおり順位のみ)。
"""
from __future__ import annotations

import gzip
import json
import re
import time
from datetime import date
from pathlib import Path

import requests

BASE_URL = "https://championsbattledata.com"
ARCHIVE_DIR = Path(__file__).resolve().parent.parent / "archive"
USER_AGENT = "PokemonChampionsAdviser/1.0 (personal battle-advisor; data credit: championsbattledata.com)"

REQUEST_INTERVAL_SEC = 0.15   # 過負荷防止の礼儀的ウェイト


def _slugify(name: str) -> str:
    """'Earthquake' -> 'earthquake', 'Focus Sash' -> 'focussash' (showdown ID形式)"""
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _get_json(path: str, params: dict | None = None, timeout: int = 30) -> dict:
    url = f"{BASE_URL}{path}"
    resp = requests.get(url, params=params, timeout=timeout,
                        headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return resp.json()


def fetch_index() -> dict:
    """APIインデックス: 対象ポケモン一覧 (showdownId) とシーズン情報"""
    return _get_json("/api")


def fetch_pokemon_battle_data(showdown_id: str, fmt: str = "Singles",
                              season: str | None = None) -> dict:
    """個別ポケモンの採用率データ。fmt: 'Singles' | 'Doubles'"""
    params = {"season": season} if season else None
    return _get_json(f"/api/battle/{fmt}/{showdown_id}", params=params)


# --- stat_alignment (性格による能力補正) -> 性格名の逆引き ---
_STAT_KEY = {
    "attack": "atk", "defense": "def", "sp. atk": "spa", "sp.atk": "spa",
    "sp. def": "spd", "sp.def": "spd", "speed": "spe",
    "special attack": "spa", "special defense": "spd",
}
_NATURE_BY_UPDOWN = {
    ("atk", "spa"): "adamant", ("atk", "def"): "lonely", ("atk", "spd"): "naughty", ("atk", "spe"): "brave",
    ("def", "atk"): "bold", ("def", "spa"): "impish", ("def", "spd"): "lax", ("def", "spe"): "relaxed",
    ("spa", "atk"): "modest", ("spa", "def"): "mild", ("spa", "spd"): "rash", ("spa", "spe"): "quiet",
    ("spd", "atk"): "calm", ("spd", "def"): "gentle", ("spd", "spa"): "careful", ("spd", "spe"): "sassy",
    ("spe", "atk"): "timid", ("spe", "def"): "hasty", ("spe", "spa"): "jolly", ("spe", "spd"): "naive",
}


def _alignment_to_nature(stat_up: str, stat_down: str) -> str | None:
    up = _STAT_KEY.get((stat_up or "").strip().lower())
    down = _STAT_KEY.get((stat_down or "").strip().lower())
    if up and down:
        return _NATURE_BY_UPDOWN.get((up, down))
    if not stat_up and not stat_down:
        return "serious"  # 無補正
    return None


def parse_battle_rows(payload: dict) -> dict:
    """APIレスポンスの rows[] をカテゴリ別に整理する。

    戻り値: {"moves": {id: pct}, "items": {...}, "abilities": {...},
             "spreads": [{nature, evs, usage_percent}], "teammates": {id: pct}}
    """
    out = {"moves": {}, "items": {}, "abilities": {}, "spreads": [], "teammates": {}}
    alignments = []   # (nature, pct)
    for row in payload.get("rows", []):
        cat = row.get("category")
        pct = row.get("percentage_value")
        if pct is None:
            # "99.4%" 形式のフォールバック
            m = re.search(r"([\d.]+)", str(row.get("percentage") or ""))
            pct = float(m.group(1)) if m else None
        name = row.get("name") or ""
        rank = row.get("rank") or 99

        if cat == "move" and name:
            out["moves"][_slugify(name)] = pct if pct is not None else round(100.0 / rank, 2)
        elif cat == "held_item" and name:
            out["items"][_slugify(name)] = pct if pct is not None else round(100.0 / rank, 2)
        elif cat == "ability" and name:
            out["abilities"][_slugify(name)] = pct if pct is not None else round(100.0 / rank, 2)
        elif cat == "stat_alignment":
            nature = _alignment_to_nature(row.get("stat_up"), row.get("stat_down")) \
                or _slugify(name) or None
            if nature and pct is not None:
                alignments.append((nature, pct))
        elif cat == "stat_points":
            evs = "/".join(str(row.get(k) or 0) for k in (
                "hp_points", "attack_points", "defense_points",
                "sp_atk_points", "sp_def_points", "speed_points"))
            if pct is not None:
                out["spreads"].append({"nature": None, "evs": evs, "usage_percent": pct})
        elif cat == "teammate" and name:
            out["teammates"][_slugify(name)] = pct if pct is not None else round(100.0 / rank, 2)

    # 性格は evs=None の spread 行として格納 (schemaは nature/evs 共にNULL許容)
    for nature, pct in alignments:
        out["spreads"].append({"nature": nature, "evs": None, "usage_percent": pct})
    return out


def _archive(payload, name: str) -> Path:
    """取得した生データを gzip JSON でアーカイブする (サービス停止に備えた保全)"""
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    path = ARCHIVE_DIR / f"{name}_{date.today().isoformat()}.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    return path


def fetch_all(fmt: str = "Singles", season: str | None = None,
              limit: int | None = None, archive: bool = True,
              progress: bool = True) -> tuple[dict, dict]:
    """全ポケモンの採用率データを取得する。

    戻り値: (per_pokemon: {showdown_id: parse_battle_rows結果}, meta)
    """
    index = fetch_index()
    season = season or index.get("defaultSeason") or "Current"
    pokemon_list = index.get("pokemon", [])
    if limit:
        pokemon_list = pokemon_list[:limit]

    per_pokemon: dict = {}
    raw_all: dict = {"index_generatedAt": index.get("generatedAt"),
                     "season": season, "format": fmt, "data": {}}
    for i, p in enumerate(pokemon_list):
        sid = p.get("showdownId")
        if not sid:
            continue
        try:
            payload = fetch_pokemon_battle_data(sid, fmt=fmt, season=season)
        except Exception as e:
            print(f"  [warn] championsbattledata {sid} 取得失敗: {e}")
            continue
        raw_all["data"][sid] = payload
        per_pokemon[sid] = parse_battle_rows(payload)
        if progress and (i + 1) % 25 == 0:
            print(f"  [championsbattledata] {i + 1}/{len(pokemon_list)} 件取得...")
        time.sleep(REQUEST_INTERVAL_SEC)

    if archive and raw_all["data"]:
        path = _archive(raw_all, f"cbd_{season}_{fmt.lower()}")
        print(f"  [championsbattledata] 生データを保存: {path}")

    meta = {
        "season": index.get("seasons", [None, None])[-1] if season == "Current" else season,
        "generated_at": index.get("generatedAt"),
        "format": fmt,
        "count": len(per_pokemon),
    }
    return per_pokemon, meta
