"""構築のセルフプレイ自動対戦評価 (Phase 6.4の結線)。

指定した6体を使用率DBのmeta_sets (型: 特性/持ち物/性格/能力ポイント/技) で
実チーム化し、ローカルShowdown (8100) でベンチマーク (ランクマ上位構築 x
ヒューリスティクス) と実対戦させて勝率を測る。
プレイヤー側も同じヒューリスティクスにすることで**構築の強さだけ**を分離評価する。

使い方:
    python -m tools.evaluate_team ガブリアス,サーフゴー,カイリュー,ドドゲザン,イダイナキバ,テツノブジン
    python -m tools.evaluate_team <6体> --battles 30

前提: bash champions_agent/scripts/setup_showdown.sh 済みで8100が稼働中。
"""
from __future__ import annotations

import asyncio
import re
import sys

from champions_agent.data import database as db
from champions_agent.env.team_builder import (
    PokemonSet, _fetch_meta_pool, _sanitize_item, _sanitize_species,
    to_showdown_name, USAGE_TARGET_FORMAT)


def _to_id(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def build_team_text(species_list: list) -> str:
    """種族名リスト (日本語/英語/showdown id) -> Showdownチームテキスト。

    型は meta_sets の最有力セットを使う。見つからない種族はエラー。
    """
    from vision.normalize import NameResolver
    resolver = NameResolver()
    wanted = []
    for name in species_list:
        name = name.strip()
        r = resolver.resolve_species(name, cutoff=0.8)
        wanted.append(_to_id(r[1] if r else name))

    with db.get_connection() as conn:
        snapshot_id = db.latest_snapshot_id(conn, fmt=USAGE_TARGET_FORMAT)
        pool = _fetch_meta_pool(conn, snapshot_id)
    by_id = {}
    for row in pool:
        by_id.setdefault(_to_id(row["pokemon_name"]), row)

    sets, used_items, missing = [], set(), []
    for sid in wanted:
        row = by_id.get(sid)
        if row is None:
            missing.append(sid)
            continue
        item = _sanitize_item(row["item_name"])
        if item in used_items:   # アイテムクローズ
            item = None
        if item:
            used_items.add(item)
        sets.append(PokemonSet(
            species=to_showdown_name(_sanitize_species(row["pokemon_name"])),
            ability=row["ability_name"], item=item,
            tera_type=row["tera_type"], nature=row["nature"],
            evs=row["evs"],
            moves=[row["move1"], row["move2"], row["move3"], row["move4"]],
        ))
    if missing:
        raise RuntimeError(f"meta_setsに型がない種族: {missing}")
    return "\n\n".join(s.to_showdown_text() for s in sets)


async def evaluate_team(species_list: list, n_battles: int = 20) -> dict:
    """チームをベンチマークと対戦させ勝率を返す"""
    from poke_env.player import SimpleHeuristicsPlayer
    from poke_env.teambuilder import ConstantTeambuilder
    from champions_agent.env.showdown_env import (
        TrainingServerConfiguration, make_benchmark_player,
        TRAINING_BATTLE_FORMAT)

    team_text = build_team_text(species_list)
    me = SimpleHeuristicsPlayer(
        battle_format=TRAINING_BATTLE_FORMAT,
        server_configuration=TrainingServerConfiguration,
        team=ConstantTeambuilder(team_text),
        max_concurrent_battles=1,
    )
    opp = make_benchmark_player(max_concurrent_battles=1)
    await asyncio.wait_for(me.battle_against(opp, n_battles=n_battles),
                           timeout=120 * n_battles)
    return {"team": species_list, "n_battles": n_battles,
            "wins": me.n_won_battles,
            "win_rate": me.n_won_battles / n_battles if n_battles else 0.0}


def build_myteam_text() -> str:
    """config/my_team.json の登録型 (性格/能力ポイント/持ち物/技/特性) で
    チームテキストを作る (meta_setsではなく実際の自分の型で評価する)"""
    from advisor.my_team import _load as load_my_team, _NATURES
    from vision.normalize import NameResolver
    resolver = NameResolver()
    team = load_my_team()
    if not team:
        raise RuntimeError("config/my_team.json が未登録です")
    ev_keys = {"h": "HP", "a": "Atk", "b": "Def", "c": "SpA", "d": "SpD",
               "s": "Spe", "hp": "HP", "atk": "Atk", "def": "Def",
               "spa": "SpA", "spd": "SpD", "spe": "Spe"}
    nature_en = {"いじっぱり": "Adamant", "ようき": "Jolly", "ひかえめ": "Modest",
                 "おくびょう": "Timid", "ずぶとい": "Bold", "わんぱく": "Impish",
                 "おだやか": "Calm", "しんちょう": "Careful", "のんき": "Relaxed",
                 "なまいき": "Sassy", "ゆうかん": "Brave", "れいせい": "Quiet"}
    blocks, used_items = [], set()
    for ja, entry in team.items():
        r = resolver.resolve_species(ja, cutoff=0.85)
        if not r:
            continue
        species = to_showdown_name(_sanitize_species(r[1]))
        item = None
        if entry.get("持ち物"):
            ri = resolver.resolve(entry["持ち物"], "items", cutoff=0.8)
            item = _sanitize_item(ri[1]) if ri else None
            if item in used_items:
                item = None
            if item:
                used_items.add(item)
        ability = None
        if entry.get("特性"):
            ra = resolver.resolve(entry["特性"], "abilities", cutoff=0.8)
            ability = ra[1] if ra else None
        moves = []
        for mj in (entry.get("技") or []):
            rm = resolver.resolve(mj, "moves", cutoff=0.8)
            if rm:
                moves.append(rm[1])
        if not moves:
            # 技未登録は使用率上位4つで補完 (tackle代替はバリデーション不通過)
            try:
                from advisor.sets import get_predictor
                moves = [m for m, _ in
                         get_predictor().predict(_to_id(r[1]))["moves"][:4]]
            except Exception:
                pass
        pts = entry.get("能力ポイント") or {}
        evs = " / ".join(f"{v} {ev_keys[str(k).lower()]}"
                         for k, v in pts.items()
                         if str(k).lower() in ev_keys)
        lines = [f"{species} @ {item}" if item else species, "Level: 50"]
        if ability:
            lines.append(f"Ability: {ability}")
        if evs:
            lines.append(f"EVs: {evs}")
        nat = nature_en.get(entry.get("性格") or "")
        if nat:
            lines.append(f"{nat} Nature")
        if not moves:
            print(f"  ! {ja}: 技を決められないためスキップ")
            continue
        lines += [f"- {m}" for m in moves]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


async def evaluate_team_text(team_text: str, n_battles: int = 20) -> dict:
    from poke_env.player import SimpleHeuristicsPlayer
    from poke_env.teambuilder import ConstantTeambuilder
    from champions_agent.env.showdown_env import (
        TrainingServerConfiguration, make_benchmark_player,
        TRAINING_BATTLE_FORMAT)
    me = SimpleHeuristicsPlayer(
        battle_format=TRAINING_BATTLE_FORMAT,
        server_configuration=TrainingServerConfiguration,
        team=ConstantTeambuilder(team_text),
        max_concurrent_battles=1,
    )
    opp = make_benchmark_player(max_concurrent_battles=1)
    await asyncio.wait_for(me.battle_against(opp, n_battles=n_battles),
                           timeout=120 * n_battles)
    return {"n_battles": n_battles, "wins": me.n_won_battles,
            "win_rate": me.n_won_battles / n_battles if n_battles else 0.0}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    n = int(sys.argv[sys.argv.index("--battles") + 1]) \
        if "--battles" in sys.argv else 20
    if "--myteam" in sys.argv:
        text = build_myteam_text()
        print(f"現在のパーティ (登録型) を{n}戦で評価:")
        result = asyncio.run(evaluate_team_text(text, n_battles=n))
    elif args:
        species = [s for s in re.split(r"[、,]", args[0]) if s.strip()]
        print(f"評価対象: {species} ({n}戦)")
        result = asyncio.run(evaluate_team(species, n_battles=n))
    else:
        print(__doc__)
        return
    print(f"勝率: {result['win_rate']:.0%} ({result['wins']}/{result['n_battles']})")


if __name__ == "__main__":
    main()
