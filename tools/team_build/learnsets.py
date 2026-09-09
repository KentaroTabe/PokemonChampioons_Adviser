"""champions mod の learnsets.ts (pokemon-showdown/data/mods/champions) を読み、種族が覚える技の集合を返す。

- parse_learnsets(text) は純粋 (テスト用)。learnsets() はファイルを 1 回だけ読む
- can_learn(species_id, move_id): フォルム id (rotomwash / raichumegay 等) は本体の learnset へ倒す
  (メガ形態の技は基本種の learnset に載っている。無ければ最長の前方一致)
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
LEARNSETS_TS = REPO / "pokemon-showdown" / "data" / "mods" / "champions" / "learnsets.ts"

_SPECIES_LINE = re.compile(r"^\t([a-z0-9]+): \{\s*$")
_MOVE_LINE = re.compile(r"^\t\t\t([a-z0-9]+): \[")


def parse_learnsets(text: str) -> dict:
    """learnsets.ts の本文 → {species_id: set(move_id)}。1 タブ = 種族、3 タブ = 技 (eventData 等の深い行は無視)"""
    out: dict = {}
    cur = None
    for line in text.splitlines():
        m = _SPECIES_LINE.match(line)
        if m:
            cur = m.group(1)
            out.setdefault(cur, set())
            continue
        m = _MOVE_LINE.match(line)
        if m and cur is not None:
            out[cur].add(m.group(1))
    return out


@lru_cache(maxsize=1)
def learnsets(path: str = str(LEARNSETS_TS)) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    return parse_learnsets(p.read_text(encoding="utf-8"))


def resolve_species(species_id: str, table: dict) -> str | None:
    """table に無いフォルム id は最長の前方一致 (4 文字以上) に倒す"""
    if species_id in table:
        return species_id
    cands = [k for k in table if len(k) >= 4 and species_id.startswith(k)]
    return max(cands, key=len) if cands else None


def learnset_of(species_id: str, table: dict | None = None) -> set:
    table = table if table is not None else learnsets()
    key = resolve_species(species_id, table)
    return set(table.get(key, ())) if key else set()


def can_learn(species_id: str, move_id: str, table: dict | None = None) -> bool:
    return move_id in learnset_of(species_id, table)
