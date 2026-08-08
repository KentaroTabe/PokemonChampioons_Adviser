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

# 引数ごとにキャッシュする (top_n違いで前回の結果が返る不具合を避ける)
_cache: dict = {}


# プールとして採用する最小チーム数。シーズン切替直後のopendataはほぼ空で、
# 「最新ファイル」を無条件に読むとプールが3チームに激減した (2026-08-05:
# M-5開始2時間後の取得でベンチ・学習の相手が3チームになりかけた)
MIN_POOL_TEAMS = 100
# ベンチ基盤のピン止め。ここに書いたファイルがある限りそれを使い、
# シーズンデータの蓄積でプールが黙って切り替わるのを防ぐ。
# 基盤を切り替えるときは POOL_PIN を書き換え、training_changes.json に記録する
PIN_PATH = ARCHIVE_DIR / "POOL_PIN"


def _load_ladder_teams() -> list:
    """アーカイブ済みのpokedbオープンデータから上位構築を読み込む (レート順)"""
    files = sorted(ARCHIVE_DIR.glob("pokedb_s*_single_*.json.gz"))
    if not files:
        return []
    # 1. ピン止めがあれば最優先 (評価基準の固定)
    try:
        pinned = ARCHIVE_DIR / PIN_PATH.read_text(encoding="utf-8").strip()
        if pinned.exists():
            files = [pinned]
    except OSError:
        pass
    # 2. 新しい順に、十分なチーム数を持つファイルを採用する
    for f in reversed(files):
        with gzip.open(f, "rt", encoding="utf-8") as fh:
            payload = json.load(fh)
        teams = payload.get("teams", [])
        if len(teams) >= MIN_POOL_TEAMS or len(files) == 1:
            teams.sort(key=lambda t: -(t.get("rating_value") or 0))
            return teams
    # どれも閾値未満なら最大のものを使う (安全側)
    best = max(files, key=lambda f: f.stat().st_size)
    with gzip.open(best, "rt", encoding="utf-8") as fh:
        payload = json.load(fh)
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


EXTERNAL_PATH = (Path(__file__).resolve().parents[1] / "data" / "teams" /
                 "external_teams.json")


def _load_external_teams() -> list:
    """tools/import_teams.py で取り込んだ外部構築 (種族+持ち物)。

    ラダー構築と同じ形 ({"team": [{"pokemon":..., "item":...}]}) に揃えて返し、
    以降の技構成補完を共通の経路に通す。
    """
    if not EXTERNAL_PATH.exists():
        return []
    try:
        store = json.loads(EXTERNAL_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    out = []
    for t in store.get("teams", []):
        items = t.get("items") or [None] * 6
        out.append({"team": [{"pokemon": s, "item": items[i] or ""}
                             for i, s in enumerate(t.get("species", []))]})
    return out


def _to_team_text(t: dict, meta: dict, team_size: int) -> str | None:
    """{"team": [{"pokemon","item"}]} -> Showdownチームテキスト (組めなければNone)"""
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
    if len(sets) < team_size:
        return None
    sets = sets[:team_size]
    _enforce_item_clause(sets)
    return "\n\n".join(s.to_showdown_text() for s in sets)


def build_ranked_teams(top_n: int | None = None, team_size: int = 6,
                       include_external: bool = True) -> list:
    """構築プールのチームテキスト一覧を作る。

    - 種族と持ち物: ラダー構築 / 取り込んだ外部構築そのまま
    - 技/特性/性格/能力ポイント: meta_sets (その種族の最多構成)
    - meta_setsに無い種族が含まれるチームはその枠を除外し、6体未満になったら捨てる

    top_n はラダー構築側の上限 (None で全件)。外部構築は常に全件使う。
    選出モデルの汎化はチームの「種類」で頭打ちになるため、既定を全件にしている。
    """
    key = (top_n, team_size, include_external)
    if key in _cache:
        return _cache[key]

    meta = _load_meta_sets()
    ladder = _load_ladder_teams()
    if top_n is not None:
        ladder = ladder[:top_n * 2]

    result = []
    for t in ladder:
        text = _to_team_text(t, meta, team_size)
        if text:
            result.append(text)
        if top_n is not None and len(result) >= top_n:
            break
    if include_external:
        for t in _load_external_teams():
            text = _to_team_text(t, meta, team_size)
            if text:
                result.append(text)

    _cache[key] = result
    return result


try:
    from poke_env.teambuilder import Teambuilder as _PokeEnvTeambuilder
except Exception:
    _PokeEnvTeambuilder = object


class RankedTeambuilder(_PokeEnvTeambuilder):
    """バトルごとに上位構築からランダムに1チームを選ぶTeambuilder"""

    def __init__(self, top_n: int | None = None,
                 rng: random.Random | None = None,
                 include_external: bool = True):
        self.teams = build_ranked_teams(top_n=top_n,
                                        include_external=include_external)
        self.rng = rng or random.Random()
        if not self.teams:
            raise RuntimeError(
                "上位構築が読み込めません。champions_agent/data/archive/ に "
                "pokedb_s*_single_*.json.gz があるか確認してください "
                "(bash champions_agent/scripts/update_usage_db.sh で取得)")

    def yield_team(self) -> str:
        text = self.rng.choice(self.teams)
        return self.join_team(self.parse_showdown_team(text))
