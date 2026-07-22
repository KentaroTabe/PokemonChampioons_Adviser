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


# リージョン/フォルムのID接尾辞 -> 日本語表示 (base日本語名を{}に埋める)
_FORM_SUFFIXES = [
    ("hisui", "ヒスイ{}"),
    ("galar", "ガラル{}"),
    ("alola", "アローラ{}"),
    ("paldeacombatbreed", "パルデア{}(コンバット)"),
    ("paldeablazebreed", "パルデア{}(ブレイズ)"),
    ("paldeaaquabreed", "パルデア{}(ウォーター)"),
    ("paldea", "パルデア{}"),
    ("therian", "{}(れいじゅう)"),
    ("incarnate", "{}(けしん)"),
    ("wellspringmask", "{}(いどのめん)"),
    ("hearthflamemask", "{}(かまどのめん)"),
    ("cornerstonemask", "{}(いしずえのめん)"),
    ("singlestrike", "{}(いちげき)"),
    ("rapidstrike", "{}(れんげき)"),
    ("male", "{}(オス)"),
    ("female", "{}(メス)"),
]

# 個別フォルムの明示マッピング
_FORM_EXPLICIT = {
    "rotomwash": "ウォッシュロトム",
    "rotomheat": "ヒートロトム",
    "rotomfrost": "フロストロトム",
    "rotomfan": "スピンロトム",
    "rotommow": "カットロトム",
    "urshifusinglestrikegmax": "ウーラオス(いちげき)",
    "urshifurapidstrikegmax": "ウーラオス(れんげき)",
}


def species_ja_name(species_id: str) -> str:
    """showdown ID -> 日本語種族名。

    ヒスイ/ガラル/ロトム等のフォルムIDは jp_names に無いことが多いため、
    接尾辞を解析してベース種の日本語名から組み立てる (無ければIDのまま)。
    """
    global _ID2JA
    if _ID2JA is None:
        from vision.normalize import JP_NAMES_PATH
        raw = json.loads(JP_NAMES_PATH.read_text(encoding="utf-8"))
        _ID2JA = {}
        for ja, v in raw.get("species", {}).items():
            _ID2JA.setdefault(v["id"], ja)
    if species_id in _ID2JA:
        return _ID2JA[species_id]
    if species_id in _FORM_EXPLICIT:
        return _FORM_EXPLICIT[species_id]
    for suf, fmt in _FORM_SUFFIXES:
        if species_id.endswith(suf):
            base = species_id[: -len(suf)]
            if base in _ID2JA:
                return fmt.format(_ID2JA[base])
    # メガフォルム: swampertmega -> メガラグラージ
    base = _base_species_id(species_id)
    if base != species_id and base in _ID2JA:
        suffix = species_id[len(base):]
        xy = {"megax": "X", "megay": "Y"}.get(suffix, "")
        return f"メガ{_ID2JA[base]}{xy}"
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
            # 全スナップショットを読む: 最新だけだと約半数の種族が候補から
            # 消える (実測: 最新235種/全期間491種。むし/ひこう構成の
            # ストライク等が推測不能だった)
            rows = conn.execute(
                "SELECT pokemon_name, usage_percent, snapshot_id "
                "FROM pokemon_usage").fetchall()
            conn.close()
        except Exception:
            return

        # ベース種へ使用率を合算 (メガ形態ページ等)。
        # 最新スナップショットの使用率を優先し、最新に載っていない種族は
        # 過去の最大使用率を減衰 (x0.25) して採用する (現メタ優先は保ちつつ
        # 低使用率種もゼロにしない)
        latest_id = snap["id"]
        latest: dict[str, float] = {}
        past: dict[str, float] = {}
        for r in rows:
            base = _base_species_id(_slug(r["pokemon_name"]))
            v = max(float(r["usage_percent"]), 0.05)
            if r["snapshot_id"] == latest_id:
                latest[base] = latest.get(base, 0.0) + v
            else:
                past[base] = max(past.get(base, 0.0), v)
        usage: dict[str, float] = dict(latest)
        for base, v in past.items():
            if base not in usage:
                usage[base] = v * 0.25

        # チャンピオンズフィルタ: 全期間へ広げた際にSV由来スナップショットの
        # 種族 (チャンピオンズに存在しない) が混入しないようにする
        try:
            from advisor.team_advice import champions_usable
        except Exception:
            def champions_usable(_sid):
                return True

        for sid, weight in usage.items():
            if not champions_usable(sid):
                continue
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
