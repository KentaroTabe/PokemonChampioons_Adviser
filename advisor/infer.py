"""相手ポケモンの種族推測。

選出画面では相手はタイプアイコンしか分からないため、
「そのタイプ構成を持つ種族」を最新の使用率データ (championsbattledata +
pokedb上位構築由来のスナップショット) から確率付きで推測する。

例: ほのお/ゴースト -> ラウドボーン 62% / ソウブレイズ 38% など
(確率は使用率に比例。メガ形態の使用率はベース種へ合算する)
"""
from __future__ import annotations

import json
import re
import sqlite3
from functools import lru_cache
from typing import Optional

from advisor.dex import get_dex
from advisor.sets import DB_PATH

_TYPE_JA2EN = None
_ID2JA = None


def _ja2en() -> dict:
    global _TYPE_JA2EN
    if _TYPE_JA2EN is None:
        from vision.normalize import JP_NAMES_PATH
        raw = json.loads(JP_NAMES_PATH.read_text(encoding="utf-8"))
        _TYPE_JA2EN = dict(raw.get("types", {}))
    return _TYPE_JA2EN


def species_ja_name(species_id: str) -> str:
    """showdown ID -> 日本語種族名 (無ければIDのまま)"""
    global _ID2JA
    if _ID2JA is None:
        from vision.normalize import JP_NAMES_PATH
        raw = json.loads(JP_NAMES_PATH.read_text(encoding="utf-8"))
        _ID2JA = {}
        for ja, v in raw.get("species", {}).items():
            _ID2JA.setdefault(v["id"], ja)
    return _ID2JA.get(species_id, species_id)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _base_species_id(species_id: str) -> str:
    """メガ形態は選出画面ではベース種として表示されるため合算用に丸める"""
    for suf in ("megax", "megay", "mega"):
        if species_id.endswith(suf) and len(species_id) > len(suf) + 2:
            return species_id[: -len(suf)]
    return species_id


class TypeInference:
    """タイプ構成 -> 種族候補 (確率付き) の推測器"""

    def __init__(self):
        self._index: dict[frozenset, list] = {}
        self._build()

    def _build(self) -> None:
        if not DB_PATH.exists():
            return
        dex = get_dex()
        try:
            conn = sqlite3.connect(str(DB_PATH))
            conn.row_factory = sqlite3.Row
            snap = conn.execute(
                "SELECT id FROM usage_snapshot ORDER BY id DESC LIMIT 1").fetchone()
            if snap is None:
                return
            rows = conn.execute(
                "SELECT pokemon_name, usage_percent FROM pokemon_usage "
                "WHERE snapshot_id = ?", (snap["id"],)).fetchall()
            conn.close()
        except Exception:
            return

        # ベース種へ使用率を合算 (メガ形態ページ等)
        usage: dict[str, float] = {}
        for r in rows:
            base = _base_species_id(_slug(r["pokemon_name"]))
            usage[base] = usage.get(base, 0.0) + max(float(r["usage_percent"]), 0.05)

        for sid, weight in usage.items():
            sp = dex.species(sid)
            if sp is None:
                continue
            key = frozenset(sp["types"])
            self._index.setdefault(key, []).append((sid, weight))

        for key, entries in self._index.items():
            entries.sort(key=lambda e: -e[1])

    def candidates(self, types_ja: list, top_k: int = 5) -> list:
        """タイプ構成 (日本語) から候補を返す。

        戻り値: [(species_id, 確率, 日本語名)] 確率は正規化済み・降順。
        """
        ja2en = _ja2en()
        types_en = frozenset(ja2en.get(t, t) for t in (types_ja or []) if t)
        if not types_en:
            return []
        entries = self._index.get(types_en, [])[:top_k]
        total = sum(w for _, w in entries)
        if total <= 0:
            return []
        return [(sid, w / total, species_ja_name(sid)) for sid, w in entries]


_inference: Optional[TypeInference] = None


def get_inference() -> TypeInference:
    global _inference
    if _inference is None:
        _inference = TypeInference()
    return _inference
