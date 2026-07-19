"""種族ごとの合法特性 (champions_dex.json 由来)。

各ポケモンの特性は最大3択程度なので、画面から読んだ特性はその種族の
合法特性セットに対して検証する (メガラグラージに「ばけのかわ」が付くような
誤帰属を弾く)。メガ・リージョン等の同系フォルムは baseSpecies でまとめ、
どのフォルムでも同じ集合を返す (メガシンカ前後の追跡ずれに耐えるため)。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

DEX_PATH = (Path(__file__).resolve().parent.parent
            / "champions_agent" / "data" / "champions_dex.json")

_LEGAL: Optional[dict] = None


def _to_id(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _load() -> dict:
    global _LEGAL
    if _LEGAL is not None:
        return _LEGAL
    _LEGAL = {}
    try:
        species = json.loads(DEX_PATH.read_text(encoding="utf-8"))["species"]
    except Exception:
        return _LEGAL
    groups: dict = {}
    for key, ent in species.items():
        base = _to_id(ent.get("baseSpecies") or key)
        abset = {_to_id(a) for a in (ent.get("abilities") or {}).values()}
        groups.setdefault(base, set()).update(abset)
    for key, ent in species.items():
        base = _to_id(ent.get("baseSpecies") or key)
        _LEGAL[key] = groups.get(base) or None
    return _LEGAL


def legal_abilities(species_id: Optional[str]):
    """種族の合法特性IDセット (同系フォルム込み)。不明な種族は None"""
    if not species_id:
        return None
    return _load().get(_to_id(species_id))
