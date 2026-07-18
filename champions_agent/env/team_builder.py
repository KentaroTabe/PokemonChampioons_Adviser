"""
DBの meta_sets(環境データ)から、自己対戦やシミュレーション用の
「現環境らしい」ポケモンパーティを確率的に生成する。

- 生成されたパーティは Showdown のチームフォーマット(パックド形式 / showdown形式)へ変換し、
  poke-env の Player にセットして使うことを想定している。
- 使用率(weight)に比例した重み付きサンプリングでポケモンを選出し、
  そのポケモンの代表的な型(技/持ち物/特性/テラス/努力値)を meta_sets から引く。
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from champions_agent.config import (
    USAGE_TARGET_FORMAT, DEFAULT_REGULATION, PLAY_STYLES, DEFAULT_PLAY_STYLE,
)
from champions_agent.data import database as db
from champions_agent.data.sources.name_mapping import to_showdown_name




@dataclass
class PokemonSet:
    species: str
    ability: str | None
    item: str | None
    tera_type: str | None
    nature: str | None
    evs: str | None
    moves: list[str]

    def to_showdown_text(self) -> str:
        """poke-env / Showdown のteambuilder importable format(簡易版)へ変換する。"""
        lines = [f"{self.species} @ {self.item or ''}".strip()]
        # チャンピオンズのランクバトルはLv50固定
        lines.append("Level: 50")
        if self.ability:
            lines.append(f"Ability: {self.ability}")
        if self.evs:
            # evs文字列 "HP/Atk/Def/SpA/SpD/Spe" を Showdown形式へ変換。
            # champions mod はEV欄を「能力ポイント (各0-32・合計66)」として
            # ネイティブ解釈するため、CBD由来の生の値をそのまま渡す。
            # 安全のため 各32/合計66 を超えた分だけ削る
            labels = ["HP", "Atk", "Def", "SpA", "SpD", "Spe"]
            values = []
            for v in self.evs.split("/"):
                try:
                    values.append(max(0, min(32, int(v))))
                except ValueError:
                    values.append(0)
            while sum(values) > 66:
                i = values.index(max(values))
                values[i] -= sum(values) - 66 if values[i] >= sum(values) - 66 else 1
            ev_parts = [f"{v} {l}" for l, v in zip(labels, values) if v]
            if ev_parts:
                lines.append("EVs: " + " / ".join(ev_parts))
        if self.nature:
            lines.append(f"{self.nature.capitalize()} Nature")
        if self.tera_type:
            lines.append(f"Tera Type: {self.tera_type.capitalize()}")
        for m in self.moves:
            if m:
                lines.append(f"- {m}")
        return "\n".join(lines)


def _fetch_meta_pool(conn, snapshot_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT pokemon_name, ability_name, item_name, tera_type,
               nature, evs, move1, move2, move3, move4, weight
        FROM meta_sets
        WHERE snapshot_id = ? AND move1 IS NOT NULL
        """,
        (snapshot_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _fetch_fallback_items(conn, snapshot_id: int) -> list[str]:
    """アイテム重複解消用の代替候補を、実使用率DB (=champions実在確定) から取る"""
    rows = conn.execute(
        """
        SELECT item_name, SUM(usage_percent) AS total
        FROM item_usage WHERE snapshot_id = ?
        GROUP BY item_name ORDER BY total DESC LIMIT 30
        """,
        (snapshot_id,),
    ).fetchall()
    return [r["item_name"] for r in rows]


def _fetch_role_scores(conn, snapshot_id: int) -> dict[str, dict[str, float]]:
    """pokemon_name -> {role: score} のマップを返す。"""
    rows = conn.execute(
        "SELECT pokemon_name, role, score FROM pokemon_role_tags WHERE snapshot_id = ?",
        (snapshot_id,),
    ).fetchall()
    result: dict[str, dict[str, float]] = {}
    for r in rows:
        result.setdefault(r["pokemon_name"], {})[r["role"]] = r["score"]
    return result


def _apply_play_style_bias(pool: list[dict], role_scores: dict[str, dict[str, float]],
                            play_style: str) -> list[float]:
    """性格(PlayStyle)の役割重み倍率を反映した、サンプリング用の重みリストを返す。

    基本重み(使用率weight)に対し、各役割スコア×倍率の合計を乗算係数として掛け合わせる。
    どの役割にも該当しない(スコア0)ポケモンは倍率1.0のまま(基本重みのみ)。
    """
    style = PLAY_STYLES.get(play_style, PLAY_STYLES[DEFAULT_PLAY_STYLE])
    multipliers = style.role_weight_multipliers

    weights = []
    for p in pool:
        base = max(p["weight"], 0.01)
        roles = role_scores.get(p["pokemon_name"], {})
        if not roles:
            weights.append(base)
            continue
        # 役割スコアで重み付けした倍率の加重平均(1.0を中心に増減)
        bias = 1.0
        total_score = sum(roles.values())
        if total_score > 0:
            bias = sum(score * multipliers.get(role, 1.0) for role, score in roles.items()) / total_score
        weights.append(base * bias)
    return weights


def build_random_party(size: int = 6, fmt: str = USAGE_TARGET_FORMAT,
                        source: str | None = None, rng: random.Random | None = None,
                        play_style: str = DEFAULT_PLAY_STYLE,
                        ) -> list[PokemonSet]:

    """使用率(weight)と性格(play_style)の役割バイアスに応じた重み付きサンプリングで、
    size体のパーティを生成する。

    play_style: config.PLAY_STYLES のキー('offense'/'cycle'/'stall'/'balance')。
                役割タグ(data/role_tagger.py)が未生成の場合は使用率のみで選出される。
    poke-env で自己対戦相手のチームとして利用する想定。
    """
    rng = rng or random.Random()

    with db.get_connection() as conn:
        snapshot_id = db.latest_snapshot_id(conn, source=source, fmt=fmt)
        if snapshot_id is None:
            raise RuntimeError(
                f"usage_snapshot が見つかりません(source={source}, format={fmt})。"
                "先に data.ingest / data.build_meta を実行してください。"
            )
        pool = _fetch_meta_pool(conn, snapshot_id)
        role_scores = _fetch_role_scores(conn, snapshot_id)
        fallback_items = _fetch_fallback_items(conn, snapshot_id)

    if len(pool) < size:
        raise RuntimeError(
            f"meta_sets の候補数({len(pool)})がパーティサイズ({size})未満です。"
            "ingest対象のポケモン数を増やしてください。"
        )

    banned = set(DEFAULT_REGULATION.banned_species)
    pool = [p for p in pool if p["pokemon_name"] not in banned]

    weights = _apply_play_style_bias(pool, role_scores, play_style)
    chosen = rng.choices(pool, weights=weights, k=size)

    # 重複を避けるための簡易リトライ(完全ユニークにはならない場合もあるがプロトタイプとして許容)
    seen = set()
    result = []
    for c in chosen:
        if c["pokemon_name"] in seen:
            continue
        seen.add(c["pokemon_name"])
        result.append(c)
    while len(result) < size:
        candidate = rng.choices(pool, weights=weights, k=1)[0]
        if candidate["pokemon_name"] not in seen:
            seen.add(candidate["pokemon_name"])
            result.append(candidate)

    sets = [
        PokemonSet(
            species=to_showdown_name(_sanitize_species(r["pokemon_name"])),
            ability=r["ability_name"],
            item=_sanitize_item(r["item_name"]),
            tera_type=r["tera_type"],
            nature=r["nature"],
            evs=r["evs"],
            moves=[r["move1"], r["move2"], r["move3"], r["move4"]],
        )
        for r in result
    ]
    _enforce_item_clause(sets, fallback_items)
    return sets



# --- champions形式向けサニタイズ ---------------------------------------------
# - メガ形態の種族エントリ (CBDはメガを独立ページで集計) はベース種+メガストーンで表現
# - アイテムはchampions modの実在IDのみ許可 (新メガストーン dragoninite 等は実在)
# - Flat Rules の Item Clause 用にチーム内アイテム重複を解消する
_LEGAL_ITEM_IDS = None

# アイテム重複時の代替候補 (使用率DBから動的に取得。これは最終フォールバック)
_FALLBACK_ITEMS = ["leftovers", "sitrusberry", "focussash", "lumberry"]


def _legal_item_ids() -> set:
    """championsのmod items.ts + 本体items.ts + jp_names からアイテムIDを収集する"""
    global _LEGAL_ITEM_IDS
    if _LEGAL_ITEM_IDS is None:
        import json
        import re as _re
        from pathlib import Path
        ids: set = set()
        repo = Path(__file__).resolve().parents[2]
        for ts in (repo / "pokemon-showdown" / "data" / "mods" / "champions" / "items.ts",
                   repo / "pokemon-showdown" / "data" / "items.ts"):
            try:
                ids |= set(_re.findall(r"^\t(\w+): \{", ts.read_text(), _re.M))
            except Exception:
                pass
        try:
            jp = repo / "vision" / "data" / "jp_names.json"
            ids |= set(json.loads(jp.read_text()).get("items", {}).values())
        except Exception:
            pass
        _LEGAL_ITEM_IDS = ids
    return _LEGAL_ITEM_IDS


def _sanitize_species(name: str) -> str:
    for suf in ("megax", "megay", "mega"):
        if name.endswith(suf) and len(name) > len(suf) + 2:
            return name[: -len(suf)]
    return name


def _sanitize_item(item: str | None) -> str | None:
    if not item:
        return item
    legal = _legal_item_ids()
    if legal and item not in legal:
        return "leftovers"
    return item


def _enforce_item_clause(sets: list[PokemonSet], fallback_items: list[str] | None = None) -> None:
    """Flat Rules (Item Clause = 1) のためチーム内のアイテム重複を解消する"""
    candidates = (fallback_items or []) + _FALLBACK_ITEMS
    used: set = set()
    for s in sets:
        if s.item and s.item in used:
            s.item = next((f for f in candidates if f not in used), None)
        if s.item:
            used.add(s.item)


def build_random_team_text(size: int = 6, **kwargs) -> str:
    """poke-env の Teambuilder(ShowdownTeam)にそのまま渡せるテキスト形式で返す。"""
    party = build_random_party(size=size, **kwargs)
    return "\n\n".join(p.to_showdown_text() for p in party)


try:
    from poke_env.teambuilder import Teambuilder as _PokeEnvTeambuilder
except Exception:  # poke-env未導入環境 (データ収集のみ) でもimport可能にする
    _PokeEnvTeambuilder = object


class ChampionsTeambuilder(_PokeEnvTeambuilder):
    """毎バトル新しいメタチームを生成する poke-env Teambuilder。

    ConstantTeambuilder と違い、バトルごとに使用率メタから確率生成するため、
    学習が特定チームに過学習しない。style_pool を渡すとバトルごとに
    性格 (プレイスタイル) もランダムに切り替わる。
    """

    def __init__(self, size: int = 6, play_style: str | None = None,
                 style_pool: list[str] | None = None,
                 rng: random.Random | None = None):
        self.size = size
        self.play_style = play_style
        self.style_pool = style_pool
        self.rng = rng or random.Random()

    def yield_team(self) -> str:
        style = self.play_style
        if style is None:
            pool = self.style_pool or list(PLAY_STYLES.keys())
            style = self.rng.choice(pool)
        text = build_random_team_text(size=self.size, play_style=style)
        mons = self.parse_showdown_team(text)
        return self.join_team(mons)


if __name__ == "__main__":
    print(build_random_team_text(size=6))
