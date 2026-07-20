"""上位ランカーの実構築からチームを生成する (ベンチマーク相手用)。

champs.pokedb.tokyo 公式オープンデータ (上位構築: 6体+持ち物) に、
使用率DB (meta_sets) の技構成・特性・性格・能力ポイントを合成して
「実際のラダー上位の構築」を再現する。

SimpleHeuristicsPlayer と組み合わせて、Randomより明確に強い
固定ベンチマーク相手 (学習相手ミックス/評価基準) として使う。
"""
from __future__ import annotations

import gzip
import json
import random
from pathlib import Path

from champions_agent.data import database as db
from champions_agent.data.sources.pokedb_opendata import _species_id, _item_id
from champions_agent.data.sources.name_mapping import to_showdown_name
from champions_agent.env.team_builder import (
    PokemonSet, _sanitize_species, _sanitize_item, _enforce_item_clause,
    _base_species_key,
)

ARCHIVE_DIR = Path(__file__).resolve().parents[1] / "data" / "archive"

_cache = None


def _load_ladder_teams() -> list:
    """アーカイブ済みのpokedbオープンデータから上位構築を読み込む (レート順)"""
    files = sorted(ARCHIVE_DIR.glob("pokedb_s*_single_*.json.gz"))
    if not files:
        return []
    with gzip.open(files[-1], "rt", encoding="utf-8") as f:
        payload = json.load(f)
    teams = payload.get("teams", [])
    teams.sort(key=lambda t: -(t.get("rating_value") or 0))
    return teams


def _load_meta_sets() -> dict:
    """species_id -> meta_sets行 (技/特性/性格/能力ポイント)"""
    with db.get_connection() as conn:
        snap = db.latest_snapshot_id(conn)
        if snap is None:
            return {}
        rows = conn.execute(
            """SELECT pokemon_name, ability_name, item_name, nature, evs,
                       move1, move2, move3, move4
               FROM meta_sets WHERE snapshot_id = ? AND move1 IS NOT NULL""",
            (snap,),
        ).fetchall()
    return {r["pokemon_name"]: dict(r) for r in rows}


def build_ranked_teams(top_n: int = 60, team_size: int = 6) -> list:
    """上位構築のチームテキスト一覧を作る。

    - 種族と持ち物: ラダー構築そのまま
    - 技/特性/性格/能力ポイント: meta_sets (その種族の最多構成)
    - meta_setsに無い種族が含まれるチームはその枠を除外し、6体未満になったら捨てる
    """
    global _cache
    if _cache is not None:
        return _cache

    teams = _load_ladder_teams()
    meta = _load_meta_sets()
    result = []
    for t in teams[:top_n * 2]:
        sets = []
        seen = set()
        for m in t.get("team", []):
            sid = _species_id(m.get("pokemon", ""), m.get("form", ""))
            meta_row = meta.get(sid)
            if meta_row is None:
                # メガ形態ページの構成を参照できる場合がある
                for cand in (sid + "mega", sid + "megax", sid + "megay"):
                    if cand in meta:
                        meta_row = meta[cand]
                        break
            if meta_row is None:
                continue
            base_key = _base_species_key(sid)
            if base_key in seen:
                continue
            seen.add(base_key)
            item = _item_id((m.get("item") or "").strip()) or meta_row["item_name"]
            sets.append(PokemonSet(
                species=to_showdown_name(_sanitize_species(sid)),
                ability=meta_row["ability_name"],
                item=_sanitize_item(item),
                tera_type=None,
                nature=meta_row["nature"],
                evs=meta_row["evs"],
                moves=[meta_row["move1"], meta_row["move2"],
                       meta_row["move3"], meta_row["move4"]],
            ))
        if len(sets) >= team_size:
            sets = sets[:team_size]
            _enforce_item_clause(sets)
            result.append("\n\n".join(s.to_showdown_text() for s in sets))
        if len(result) >= top_n:
            break
    _cache = result
    return result


try:
    from poke_env.teambuilder import Teambuilder as _PokeEnvTeambuilder
except Exception:
    _PokeEnvTeambuilder = object


class RankedTeambuilder(_PokeEnvTeambuilder):
    """バトルごとに上位構築からランダムに1チームを選ぶTeambuilder"""

    def __init__(self, top_n: int = 60, rng: random.Random | None = None):
        self.teams = build_ranked_teams(top_n=top_n)
        self.rng = rng or random.Random()
        if not self.teams:
            raise RuntimeError(
                "上位構築が読み込めません。champions_agent/data/archive/ に "
                "pokedb_s*_single_*.json.gz があるか確認してください "
                "(bash champions_agent/scripts/update_usage_db.sh で取得)")

    def yield_team(self) -> str:
        text = self.rng.choice(self.teams)
        return self.join_team(self.parse_showdown_team(text))
