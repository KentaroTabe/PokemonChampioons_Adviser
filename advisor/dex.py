"""静的データ (種族値/技/タイプ相性) へのアクセス。

データソース: advisor/data/dex.json (python -m advisor.data.fetch_dex で生成)
ポケモンチャンピオンズはLv50固定・個体値31固定なので、ステータス計算もここで提供する。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

DEX_PATH = Path(__file__).resolve().parent / "data" / "dex.json"

_dex = None


class Dex:
    def __init__(self, path: Path = DEX_PATH):
        raw = json.loads(path.read_text(encoding="utf-8"))
        self._species = raw["species"]
        self._moves = raw["moves"]
        self._chart = raw["typechart"]

    def species(self, species_id: Optional[str]) -> Optional[dict]:
        if not species_id:
            return None
        return self._species.get(species_id)

    def move(self, move_id: Optional[str]) -> Optional[dict]:
        if not move_id:
            return None
        return self._moves.get(move_id)

    def effectiveness(self, attack_type: str, defender_types: list) -> float:
        """攻撃タイプ -> 防御タイプ(1〜2個) の複合倍率"""
        mult = 1.0
        row = self._chart.get(attack_type, {})
        for t in defender_types:
            mult *= row.get(t, 1.0)
        return mult


def get_dex() -> Dex:
    global _dex
    if _dex is None:
        _dex = Dex()
    return _dex


# ==============================================================================
# ステータス計算 (Lv50 / 個体値31固定)
# ==============================================================================
def calc_hp(base: int, ev: int = 0, level: int = 50) -> int:
    return (2 * base + 31 + ev // 4) * level // 100 + level + 10


def calc_stat(base: int, ev: int = 0, nature: float = 1.0, level: int = 50) -> int:
    return int(((2 * base + 31 + ev // 4) * level // 100 + 5) * nature)


BOOST_MULT = {
    -6: 2 / 8, -5: 2 / 7, -4: 2 / 6, -3: 2 / 5, -2: 2 / 4, -1: 2 / 3,
    0: 1.0, 1: 3 / 2, 2: 4 / 2, 3: 5 / 2, 4: 6 / 2, 5: 7 / 2, 6: 8 / 2,
}

ACC_BOOST_MULT = {
    -6: 3 / 9, -5: 3 / 8, -4: 3 / 7, -3: 3 / 6, -2: 3 / 5, -1: 3 / 4,
    0: 1.0, 1: 4 / 3, 2: 5 / 3, 3: 6 / 3, 4: 7 / 3, 5: 8 / 3, 6: 9 / 3,
}
