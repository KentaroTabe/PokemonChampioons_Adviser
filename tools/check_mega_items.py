"""メガ進化する種族と、対応するメガストーンの登録状況を突き合わせる。

    python -m tools.check_mega_items

メガストーンのIDは命名が不規則 (アローラ→alakazite, ヤミラミ→sablenite 等) なので、
種族名から組み立てず champions_dex の requiredItem を正とする。

見るのは2点:
  - requiredItem がシミュレータ (pokemon-showdown の items.ts) にあるか
  - そのアイテムの日本語名が jp_names.json にあるか (無いとIDのまま表示される)

⚠ シムに存在しないアイテムIDをこちらのデータに足してはいけない。
  バリデーション却下で env 起動が全滅する (2026-07-23 に発生)。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEX = REPO / "champions_agent" / "data" / "champions_dex.json"
JP = REPO / "vision" / "data" / "jp_names.json"
ITEM_TS = [
    REPO / "pokemon-showdown" / "data" / "mods" / "champions" / "items.ts",
    REPO / "pokemon-showdown" / "data" / "items.ts",
]


def item_id(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def sim_items() -> set:
    ids: set = set()
    for ts in ITEM_TS:
        try:
            ids |= set(re.findall(r"^\t(\w+): \{", ts.read_text(), re.M))
        except Exception as e:
            print(f"  ⚠ 読めません {ts}: {e}")
    return ids


def mega_stones() -> list:
    """[(種族ID, 表示名, requiredItemの名前, そのID)] を返す"""
    dex = json.loads(DEX.read_text(encoding="utf-8")).get("species", {})
    out = []
    for sid, e in dex.items():
        if not e.get("isMega") and "Mega" not in (e.get("forme") or ""):
            continue
        req = e.get("requiredItem")
        if not req:
            out.append((sid, e.get("name", sid), None, None))
            continue
        out.append((sid, e.get("name", sid), req, item_id(req)))
    return out


def main() -> None:
    jp = json.loads(JP.read_text(encoding="utf-8"))
    jp_items = jp.get("items", {})
    id2ja = {v: k for k, v in jp_items.items()}
    items = sim_items()
    stones = mega_stones()
    print(f"■ シムのアイテムID {len(items)}件 / 日本語名 {len(jp_items)}件")
    print(f"■ メガ形態 {len(stones)}件")

    no_req = [s for s in stones if s[3] is None]
    missing_sim = [s for s in stones if s[3] and s[3] not in items]
    missing_ja = [s for s in stones if s[3] and s[3] in items
                  and s[3] not in id2ja]

    print(f"\n▼ requiredItem が dex に無い: {len(no_req)}件")
    for sid, name, _, _ in no_req:
        print(f"  {name} ({sid})")
    print(f"\n▼ シムの items.ts に無い: {len(missing_sim)}件")
    for sid, name, req, iid in missing_sim:
        print(f"  {name}: {req} ({iid})")
    print(f"\n▼ シムにはあるが日本語名が無い: {len(missing_ja)}件")
    for sid, name, req, iid in missing_ja:
        print(f"  {name}: {req} ({iid})")
    if not (no_req or missing_sim or missing_ja):
        print("\nすべて登録済み")


if __name__ == "__main__":
    main()
