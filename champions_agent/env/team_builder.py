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
        if self.ability:
            lines.append(f"Ability: {self.ability}")
        if self.evs:
            # evs文字列 "HP/Atk/Def/SpA/SpD/Spe" を Showdown形式へ変換
            labels = ["HP", "Atk", "Def", "SpA", "SpD", "Spe"]
            values = self.evs.split("/")
            ev_parts = [f"{v} {l}" for l, v in zip(labels, values) if v and v != "0"]
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
        WHERE snapshot_id = ?
        """,
        (snapshot_id,),
    ).fetchall()
    return [dict(r) for r in rows]


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
                        source: str = "smogon", rng: random.Random | None = None,
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

    return [
        PokemonSet(
            species=to_showdown_name(r["pokemon_name"]),
            ability=r["ability_name"],

            item=r["item_name"],
            tera_type=r["tera_type"],
            nature=r["nature"],
            evs=r["evs"],
            moves=[r["move1"], r["move2"], r["move3"], r["move4"]],
        )
        for r in result
    ]


def build_random_team_text(size: int = 6, **kwargs) -> str:
    """poke-env の Teambuilder(ShowdownTeam)にそのまま渡せるテキスト形式で返す。"""
    party = build_random_party(size=size, **kwargs)
    return "\n\n".join(p.to_showdown_text() for p in party)


if __name__ == "__main__":
    print(build_random_team_text(size=6))
