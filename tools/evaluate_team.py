"""構築のセルフプレイ自動対戦評価 (Phase 6.4の結線)。

指定した6体を使用率DBのmeta_sets (型: 特性/持ち物/性格/能力ポイント/技) で
実チーム化し、ローカルShowdown (8100) でベンチマーク (ランクマ上位構築 x
ヒューリスティクス) と実対戦させて勝率を測る。
プレイヤー側も同じヒューリスティクスにすることで**構築の強さだけ**を分離評価する。

使い方:
    python -m tools.evaluate_team ガブリアス,サーフゴー,カイリュー,ドドゲザン,イダイナキバ,テツノブジン
    python -m tools.evaluate_team <6体> --battles 30

前提: bash champions_agent/scripts/setup_showdown.sh 済みで8100が稼働中。
注意: 評価者は SimpleHeuristicsPlayer (定跡AI) のため、雨/すいすい・積み・
メガシンカのタイミング等「プレイング前提の構築」は実力より低く出る。
スタンダードな素の構築の相対比較・generate_teams候補の選抜に向く。

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


def _uniq_name(prefix: str) -> str:
    """Showdownで衝突しない一意なアカウント名 (PID+時刻下位+乱数、18文字以内)。

    学習ループや同時実行の評価とサーバーを共有するため、乱数だけでは
    衝突する (実運用でnametaken観測)。PIDと時刻を混ぜて確実に一意化する。
    """
    import os
    import random
    import time
    base = f"{prefix}{os.getpid() % 100000}{int(time.time() * 10) % 10000}" \
           f"{random.randint(10, 99)}"
    return base[:18]


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


async def evaluate_team(species_list: list, n_battles: int = 20,
                        evaluator: str = "rl") -> dict:
    """チームをベンチマークと対戦させ勝率を返す"""
    team_text = build_team_text(species_list)
    result = await evaluate_team_text(team_text, n_battles=n_battles,
                                      evaluator=evaluator)
    result["team"] = species_list
    return result


_NATURE_EN2JA = {
    "adamant": "いじっぱり", "jolly": "ようき", "modest": "ひかえめ",
    "timid": "おくびょう", "bold": "ずぶとい", "impish": "わんぱく",
    "calm": "おだやか", "careful": "しんちょう", "relaxed": "のんき",
    "sassy": "なまいき", "brave": "ゆうかん", "quiet": "れいせい",
    "naughty": "やんちゃ", "lonely": "さみしがり", "lax": "のうてんき",
    "rash": "うっかりや", "mild": "おっとり", "gentle": "おとなしい",
    "hasty": "せっかち", "naive": "むじゃき", "hardy": "がんばりや",
    "docile": "すなお", "serious": "まじめ", "bashful": "てれや",
    "quirky": "きまぐれ",
}
_EV_EN2JA = {"HP": "H", "Atk": "A", "Def": "B", "SpA": "C", "SpD": "D",
             "Spe": "S"}


def team_text_to_ja(team_text: str) -> str:
    """Showdownチームテキスト (英語) を日本語表示に変換する。

    種族/特性/持ち物/技/性格/能力ポイント表記を日本語化。メガストーンなど
    逆引きに無いアイテムは「{種族}ナイト」で補完する。
    """
    from vision.normalize import NameResolver
    from advisor.infer import species_ja_name
    resolver = NameResolver()

    def _ja(cat, val):
        return resolver.ja_of(cat, val) or None

    out_blocks = []
    for block in team_text.strip().split("\n\n"):
        lines = block.strip().split("\n")
        if not lines:
            continue
        # 1行目: "Species @ item" または "Species"
        head = lines[0]
        item_ja = None
        if " @ " in head:
            sp_en, item_en = head.split(" @ ", 1)
            item_id = _to_id(item_en)
            item_ja = _ja("items", item_id)
            if not item_ja:
                # メガストーン等: 種族名+ナイト で補完
                base = species_ja_name(re.sub(r"(mega[xy]?|primal)$", "",
                                              _to_id(sp_en)))
                item_ja = f"{base}ナイト" if item_id.endswith("ite") else item_en
        else:
            sp_en = head
        sp_ja = species_ja_name(_to_id(sp_en))
        new = [f"{sp_ja}" + (f" @ {item_ja}" if item_ja else "")]
        for ln in lines[1:]:
            if ln.startswith("Ability:"):
                ab = _to_id(ln.split(":", 1)[1])
                new.append(f"特性: {_ja('abilities', ab) or ab}")
            elif ln.startswith("EVs:"):
                parts = ln.split(":", 1)[1].split("/")
                conv = []
                for p in parts:
                    p = p.strip()
                    m = re.match(r"(\d+)\s+(\w+)", p)
                    if m:
                        conv.append(f"{_EV_EN2JA.get(m.group(2), m.group(2))}{m.group(1)}")
                new.append("努力: " + " ".join(conv))
            elif ln.endswith("Nature"):
                nat = ln.replace("Nature", "").strip().lower()
                new.append(f"性格: {_NATURE_EN2JA.get(nat, nat)}")
            elif ln.startswith("- "):
                mv = _to_id(ln[2:])
                new.append(f"- {_ja('moves', mv) or ln[2:].strip()}")
            elif ln.startswith("Level:"):
                new.append("Lv" + ln.split(":", 1)[1].strip())
        out_blocks.append("\n".join(new))
    return "\n\n".join(out_blocks)


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
        if not ability:
            # 特性未登録は種族の代表特性で補完 (poke-envはability=None不可)
            from vision.abilities import _load_forms
            legal = _load_forms().get(_to_id(r[1]))
            ability = next(iter(sorted(legal))) if legal else "noability"
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
            except Exception as e:
                print(f"  ! {ja}: 技補完失敗 ({e})")
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


def _make_player(team_text, tag_suffix, evaluator="rl"):
    """評価用プレイヤーを作る。

    evaluator="rl": 学習済みRL方策 (雨/すいすい・積み・メガのタイミングを
      SimpleHeuristicsより活用でき、プレイング前提構築も測れる)。
      モデルが無ければ自動でヒューリスティクスにフォールバック。
    evaluator="heuristic": SimpleHeuristicsPlayer (定跡AI・固定基準)。
    両サイド同一の評価者にすることで「構築の強さだけ」を分離評価する。
    """
    from poke_env import AccountConfiguration
    from poke_env.player import SimpleHeuristicsPlayer
    from poke_env.teambuilder import ConstantTeambuilder
    from champions_agent.env.showdown_env import (
        TrainingServerConfiguration, TRAINING_BATTLE_FORMAT)
    acc = AccountConfiguration(_uniq_name(f"TE{tag_suffix}"), None)
    kw = dict(account_configuration=acc,
              battle_format=TRAINING_BATTLE_FORMAT,
              server_configuration=TrainingServerConfiguration,
              team=ConstantTeambuilder(team_text),
              max_concurrent_battles=1)
    if evaluator == "rl":
        from champions_agent.train.evaluate import ModelPlayer
        from champions_agent.config import DEFAULT_PLAY_STYLE
        import os
        style = os.environ.get("RL_ADVICE_STYLE", "balance")
        p = ModelPlayer(play_style=style, **kw)
        if getattr(p, "policy", None) is None or p.policy.model is None:
            return SimpleHeuristicsPlayer(**kw), "heuristic"
        return p, "rl"
    return SimpleHeuristicsPlayer(**kw), "heuristic"


async def evaluate_team_text(team_text: str, n_battles: int = 20,
                             opp_text: str = None,
                             evaluator: str = "rl") -> dict:
    """team_text を評価する。opp_text 未指定ならベンチマーク構築群と対戦。

    両サイドを同じ評価者 (RL方策 or ヒューリスティクス) で回し、
    構築の強さだけを比較する。
    """
    me, ev_used = _make_player(team_text, "A", evaluator)
    if opp_text is not None:
        opp, _ = _make_player(opp_text, "B", ev_used)
    else:
        # ベンチマーク: ランクマ上位構築群 (相手も同じ評価者)
        from poke_env import AccountConfiguration
        from champions_agent.env.ranked_teams import RankedTeambuilder
        from champions_agent.env.showdown_env import (
            TrainingServerConfiguration, TRAINING_BATTLE_FORMAT)
        acc = AccountConfiguration(_uniq_name("TEB"), None)
        kw = dict(account_configuration=acc,
                  battle_format=TRAINING_BATTLE_FORMAT,
                  server_configuration=TrainingServerConfiguration,
                  team=RankedTeambuilder(), max_concurrent_battles=1)
        if ev_used == "rl":
            from champions_agent.train.evaluate import ModelPlayer
            import os
            opp = ModelPlayer(
                play_style=os.environ.get("RL_ADVICE_STYLE", "balance"), **kw)
        else:
            from poke_env.player import SimpleHeuristicsPlayer
            opp = SimpleHeuristicsPlayer(**kw)
    await asyncio.wait_for(me.battle_against(opp, n_battles=n_battles),
                           timeout=120 * n_battles)
    return {"n_battles": n_battles, "wins": me.n_won_battles,
            "win_rate": me.n_won_battles / n_battles if n_battles else 0.0,
            "evaluator": ev_used}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    n = int(sys.argv[sys.argv.index("--battles") + 1]) \
        if "--battles" in sys.argv else 20
    evaluator = "heuristic" if "--heuristic" in sys.argv else "rl"
    if "--myteam" in sys.argv:
        text = build_myteam_text()
        print(f"現在のパーティ (登録型) を{n}戦で評価 (評価者={evaluator}):")
        result = asyncio.run(evaluate_team_text(text, n_battles=n,
                                                evaluator=evaluator))
    elif args:
        species = [s for s in re.split(r"[、,]", args[0]) if s.strip()]
        print(f"評価対象: {species} ({n}戦, 評価者={evaluator})")
        result = asyncio.run(evaluate_team(species, n_battles=n,
                                           evaluator=evaluator))
    else:
        print(__doc__)
        return
    print(f"勝率: {result['win_rate']:.0%} ({result['wins']}/{result['n_battles']}) "
          f"[評価者={result['evaluator']}]")


if __name__ == "__main__":
    main()
