"""PokeAPIのCSVデータから日本語名 -> 英語ID の対応辞書 (jp_names.json) を構築する。

OCRで読み取った日本語の種族名/技名/特性名/持ち物名を、
pokemon-showdown / 使用率DB の英語IDへ変換するために使用する。

実行 (リポジトリルートから):
    python -m vision.data.fetch_jp_names

ネットワーク必須。生成物: vision/data/jp_names.json
"""
from __future__ import annotations

import csv
import io
import json
import re
import urllib.request
from pathlib import Path

BASE = "https://raw.githubusercontent.com/PokeAPI/pokeapi/master/data/v2/csv"
OUT_PATH = Path(__file__).resolve().parent / "jp_names.json"

# ja-Hrkt(カナ表記)=1 を優先し、無ければ ja=11 を使う
JA_LANGS = (1, 11)


def _fetch_csv(name: str) -> list[dict]:
    url = f"{BASE}/{name}.csv"
    print(f"fetching {url} ...")
    with urllib.request.urlopen(url, timeout=60) as res:
        text = res.read().decode("utf-8")
    return list(csv.DictReader(io.StringIO(text)))


def _to_showdown_id(identifier: str) -> str:
    """PokeAPIのidentifier(例: mr-mime, charizard-mega-y)をshowdown形式IDへ"""
    return re.sub(r"[^a-z0-9]", "", identifier.lower())


def _pick_ja(rows: list[dict], id_key: str) -> dict[int, str]:
    """names系CSVから {resource_id: 日本語名} を作る"""
    result: dict[int, str] = {}
    for lang in reversed(JA_LANGS):  # 後勝ちにするため ja -> ja-Hrkt の順で上書き
        for row in rows:
            if int(row["local_language_id"]) == lang and row.get("name"):
                result[int(row[id_key])] = row["name"]
    return result


def build() -> dict:
    species = _fetch_csv("pokemon_species")
    species_names = _fetch_csv("pokemon_species_names")
    pokemon = _fetch_csv("pokemon")
    moves = _fetch_csv("moves")
    move_names = _fetch_csv("move_names")
    abilities = _fetch_csv("abilities")
    ability_names = _fetch_csv("ability_names")
    items = _fetch_csv("items")
    item_names = _fetch_csv("item_names")
    types_rows = _fetch_csv("types")
    type_names = _fetch_csv("type_names")

    ja_species = _pick_ja(species_names, "pokemon_species_id")
    ja_moves = _pick_ja(move_names, "move_id")
    ja_abilities = _pick_ja(ability_names, "ability_id")
    ja_items = _pick_ja(item_names, "item_id")
    ja_types = _pick_ja(type_names, "type_id")

    out: dict = {"species": {}, "moves": {}, "abilities": {}, "items": {}, "types": {}}

    # --- 種族 (基本フォーム) ---
    for row in species:
        sid = int(row["id"])
        ja = ja_species.get(sid)
        if not ja:
            continue
        out["species"][ja] = {
            "id": _to_showdown_id(row["identifier"]),
            "num": sid,
        }

    # --- メガシンカ等の別フォーム: "メガ<種族名>(X|Y)" 形式の日本語名を合成 ---
    species_ident = {int(r["id"]): r["identifier"] for r in species}
    for row in pokemon:
        ident = row["identifier"]
        if "-mega" not in ident:
            continue
        sid = int(row["species_id"])
        ja = ja_species.get(sid)
        if not ja:
            continue
        suffix = ""
        if ident.endswith("-x"):
            suffix = "X"
        elif ident.endswith("-y"):
            suffix = "Y"
        out["species"][f"メガ{ja}{suffix}"] = {
            "id": _to_showdown_id(ident),
            "num": sid,
        }

    # --- 技 ---
    for row in moves:
        mid = int(row["id"])
        ja = ja_moves.get(mid)
        if ja and mid < 10000:  # シャドウ技等の特殊枠を除外
            out["moves"][ja] = _to_showdown_id(row["identifier"])

    # --- 特性 ---
    for row in abilities:
        aid = int(row["id"])
        ja = ja_abilities.get(aid)
        if ja and int(row.get("is_main_series") or 1):
            out["abilities"][ja] = _to_showdown_id(row["identifier"])

    # --- 持ち物 ---
    for row in items:
        iid = int(row["id"])
        ja = ja_items.get(iid)
        if ja:
            out["items"][ja] = _to_showdown_id(row["identifier"])

    # --- チャンピオンズ新規メガストーンの補完 ---
    # 標準データソースには存在しないため、ローカルShowdownのメガ種一覧から
    # 「{日本語種族名}ナイト(X/Y)」を生成して追加する (既存の正規ID登録は優先)
    try:
        import subprocess
        # championsモッドの実在ストーンID (megaStone付きitems) を正とする。
        # IDを推測生成するとシムに存在しないIDが混ざり、チーム生成の
        # バリデーション却下で学習が止まる (2026-07-23 に発生)
        node_out = subprocess.run(
            ["node", "-e",
             "const dex=require('./pokemon-showdown/dist/sim/dex.js')"
             ".Dex.mod('champions');const m=[];"
             "for(const it of dex.items.all()){if(it.megaStone)"
             "m.push({id:it.id,base:Object.keys(it.megaStone)[0]});}"
             "console.log(JSON.stringify(m));"],
            capture_output=True, text=True, timeout=60)
        stones = json.loads(node_out.stdout.strip())
        ja_by_id = {v["id"]: k for k, v in out["species"].items()
                    if isinstance(v, dict)}
        for st in stones:
            base_id = re.sub(r"[^a-z0-9]", "", st["base"].lower())
            base_ja = ja_by_id.get(base_id)
            if not base_ja:
                continue
            suffix = "X" if st["id"].endswith("x") else \
                ("Y" if st["id"].endswith("y") else "")
            stone_ja = base_ja + "ナイト" + suffix
            out["items"][stone_ja] = st["id"]
    except Exception as e:
        print(f"[fetch_jp_names] メガストーン補完をスキップ: {e}")

    # --- タイプ ---
    for row in types_rows:
        tid = int(row["id"])
        ja = ja_types.get(tid)
        if ja and tid < 10000:
            out["types"][ja] = row["identifier"].capitalize()

    return out


def main() -> None:
    data = build()
    OUT_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {OUT_PATH}")
    for k, v in data.items():
        print(f"  {k}: {len(v)} entries")


if __name__ == "__main__":
    main()
