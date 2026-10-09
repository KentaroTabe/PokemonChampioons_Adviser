"""静的データ (種族値/技/タイプ相性) へのアクセス。

データソース: advisor/data/dex.json (python -m advisor.data.fetch_dex で生成)
ポケモンチャンピオンズはLv50固定・個体値31固定なので、ステータス計算もここで提供する。
"""
from __future__ import annotations

import json
import re
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

    def species_ids(self) -> list:
        """収録している種族 id の一覧 (構築システムの合法種集合などに使う)"""
        return list(self._species.keys())

    def species(self, species_id: Optional[str]) -> Optional[dict]:
        """種族 id → 図鑑の項目。シミュレータの id の別名 (indeedeef 等、canonical_species_id) も同じ項目を引く"""
        if not species_id:
            return None
        got = self._species.get(species_id)
        if got is None:
            alias = species_aliases().get(species_id)
            if alias:
                got = self._species.get(alias)
        return got

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


SPECIES_ALIASES_PATH = Path(__file__).resolve().parent / "data" / "species_aliases.json"


@lru_cache(maxsize=1)
def species_aliases() -> dict:
    """シミュレータ (Showdown / poke-env) の種族 id → 助言の図鑑の id の別名表 (advisor/data/species_aliases.json)。読めなければ空"""
    try:
        return dict(json.loads(SPECIES_ALIASES_PATH.read_text(encoding="utf-8")).get("aliases") or {})
    except Exception:
        return {}


def canonical_species_id(species_id: Optional[str]) -> str:
    """種族 id の正規化 (2026-10-09 M2e): 英数字の小文字にし、シミュレータの id と図鑑の id が別名のもの (indeedeef →
    indeedeefemale、vivillonfancy → vivillon 等。表は species_aliases) を図鑑の id にする。別名でなければそのまま。
    図鑑の項目の引き当て (Dex.species)・助言の個体の名前 (advisor.infer.species_ja_name)・送った本文と参照した型の照合
    (champions_agent.env.advisor_player)・構築プールの検査 (tools.scoring_pool) がこの 1 つの変換を使う。
    状態の species_id そのものは書き換えない (使用率 DB の名前はシミュレータの id のため)"""
    sid = re.sub(r"[^a-z0-9]", "", str(species_id or "").lower())
    return species_aliases().get(sid, sid)


def get_dex() -> Dex:
    global _dex
    if _dex is None:
        _dex = Dex()
    return _dex


FIELD_EFFECTS_PATH = Path(__file__).resolve().parent / "data" / "field_effects.json"


@lru_cache(maxsize=1)
def field_effects() -> dict:
    """フィールド/天候の補正表 (advisor/data/field_effects.json: 場を張る特性/技、タイプ別倍率、技固有の効果、優先度)。
    ダメージ計算と型生成の唯一の源。読めなければ空"""
    try:
        return json.loads(FIELD_EFFECTS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


BOOST_MOVES_PATH = Path(__file__).resolve().parent / "data" / "boost_moves.json"


@lru_cache(maxsize=1)
def _boost_moves() -> dict:
    try:
        return json.loads(BOOST_MOVES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def move_boost_effects(move_id: Optional[str]) -> Optional[dict]:
    """技の確定的な能力ランク変化 (100%発動のみ)。

    戻り値: {"self": {stat: delta}, "target": {stat: delta}} (該当なしはNone)。
    データは advisor/data/boost_moves.json (確率発動の追加効果は含まない)。
    """
    if not move_id:
        return None
    data = _boost_moves()
    self_eff = (data.get("self") or {}).get(move_id)
    target_eff = (data.get("target") or {}).get(move_id)
    if not self_eff and not target_eff:
        return None
    return {"self": self_eff or {}, "target": target_eff or {}}


def switch_in_ability_effects(ability_id: Optional[str]) -> Optional[dict]:
    """着地時に確定発動する特性の相手能力変化 (いかく等)。"""
    if not ability_id:
        return None
    return (_boost_moves().get("ability_on_switch") or {}).get(ability_id)


ITEM_EFFECTS_PATH = Path(__file__).resolve().parent / "data" / "item_effects.json"


@lru_cache(maxsize=1)
def _item_effects() -> dict:
    try:
        return json.loads(ITEM_EFFECTS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def item_activation_effects(item_id: Optional[str]) -> Optional[dict]:
    """発動型の持ち物の効果 (発動観測時に適用する内容)。

    戻り値: {"consume", "heal_fraction", "boosts", ...} (該当なしはNone)。
    データは advisor/data/item_effects.json。
    """
    if not item_id:
        return None
    return (_item_effects().get("activation") or {}).get(item_id)


def item_damaging_move_effects(item_id: Optional[str]) -> Optional[dict]:
    """ダメージ技の使用時に確定発動する持ち物の効果 (いのちのたま反動等)。"""
    if not item_id:
        return None
    return (_item_effects().get("on_damaging_move") or {}).get(item_id)


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
