"""選出方策の比較評価 (選出だけを変えて勝率を測る)。

戦闘方策・チーム・相手分布を固定し、**選出の決め方だけ**を差し替えて
勝率を比べる。学習型の選出モデルを作る前に、現行ヒューリスティクスの
ベースラインを数値で押さえるためのハーネス。

    python -m tools.check_selection --battles 100
    python -m tools.check_selection --strategies random,matchup --battles 200
    python -m tools.check_selection --team-file my.txt

戦略:
  random   : poke-env既定のランダム選出 (2026-07-28以前の学習/評価の条件)
  matchup  : タイプ相性ベース (現行。search_expert.teampreview_order)
  statsum  : 種族値合計が高い3体 (policy_selection の素朴なベースライン)

注意: 同一チーム同士の反復対戦は展開が相関するため、100戦でも±0.10程度は
振れる。戦略間の差がその範囲に収まるなら「有意差なし」と読むこと。
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
from pathlib import Path

logging.getLogger("poke-env").setLevel(logging.ERROR)

REPO = Path(__file__).resolve().parent.parent


def _make_teampreview(strategy: str):
    """戦略名 -> teampreview メソッド (self, battle) -> str"""
    if strategy == "random":
        def _fn(self, battle):
            return self.random_teampreview(battle)
    elif strategy == "matchup":
        def _fn(self, battle):
            try:
                from champions_agent.env.search_expert import teampreview_order
                return teampreview_order(battle)
            except Exception:
                return self.random_teampreview(battle)
    elif strategy == "statsum":
        def _fn(self, battle):
            try:
                mons = list(battle.team.values())
                order = sorted(range(len(mons)),
                               key=lambda i: -sum(
                                   (mons[i].base_stats or {}).values()))
                return "/team " + "".join(str(i + 1) for i in order)
            except Exception:
                return self.random_teampreview(battle)
    else:
        raise SystemExit(f"未知の選出戦略: {strategy}")
    return _fn


async def run_strategy(strategy: str, team_text: str, n_battles: int,
                       style: str) -> dict:
    """指定の選出戦略で n_battles 戦し、勝率と選出内訳を返す。

    自分の選出だけを変え、相手 (ベンチマーク: 上位構築×ヒューリスティクス) は
    常に相性選出で固定する。操縦はどちらも同じ_best方策。
    """
    import types
    from collections import Counter
    from poke_env import AccountConfiguration
    from poke_env.teambuilder import ConstantTeambuilder
    from champions_agent.config import TRAINING_BATTLE_FORMAT
    from champions_agent.env.showdown_env import (
        TrainingServerConfiguration, apply_matchup_teampreview,
        make_benchmark_player,
    )
    from champions_agent.train.evaluate import ModelPlayer

    uid = f"{os.getpid() % 10000}{strategy[:3]}"
    me = ModelPlayer(
        account_configuration=AccountConfiguration(f"SelA{uid}"[:18], None),
        battle_format=TRAINING_BATTLE_FORMAT,
        server_configuration=TrainingServerConfiguration,
        team=ConstantTeambuilder(team_text),
        play_style=style, checkpoint="best", max_concurrent_battles=1)
    me.teampreview = types.MethodType(_make_teampreview(strategy), me)

    opp = make_benchmark_player(
        battle_format=TRAINING_BATTLE_FORMAT,
        account_configuration=AccountConfiguration(f"SelB{uid}"[:18], None))
    apply_matchup_teampreview(opp)     # 相手は固定条件

    await me.battle_against(opp, n_battles=n_battles)

    trios = Counter()
    for b in me.battles.values():
        names = tuple(sorted(p.species for p in b.team.values()))
        if len(names) == 3:
            trios[names] += 1
    return {"strategy": strategy, "n": n_battles, "wins": me.n_won_battles,
            "win_rate": me.n_won_battles / max(1, n_battles),
            "trios": trios}


async def run(strategies: list, n_battles: int, team_file: str | None,
              style: str) -> None:
    from advisor.infer import species_ja_name
    from tools.evaluate_team import build_myteam_text

    team_text = (Path(team_file).read_text(encoding="utf-8") if team_file
                 else build_myteam_text())
    print(f"[check_selection] 各戦略 {n_battles}戦 / 操縦={style}の_best / "
          f"相手=ベンチマーク(相性選出で固定)", flush=True)

    results = []
    for s in strategies:
        r = await run_strategy(s, team_text, n_battles, style)
        results.append(r)
        print(f"  {s}: 勝率 {r['win_rate']:.2f} ({r['wins']}/{r['n']})",
              flush=True)

    print("\n=== 比較 ===")
    base = next((r for r in results if r["strategy"] == "random"), results[0])
    for r in results:
        diff = r["win_rate"] - base["win_rate"]
        mark = "" if r is base else f" (randomとの差 {diff:+.2f})"
        print(f"{r['strategy']}: {r['win_rate']:.2f}{mark}")
        top = r["trios"].most_common(3)
        for trio, n in top:
            names = "/".join(species_ja_name(s) or s for s in trio)
            print(f"    よく出した3体: {names} ({n}回)")
    print("\n※ 100戦でも±0.10程度は振れる。差がこの範囲なら有意差なしと読む")


def main() -> None:
    ap = argparse.ArgumentParser(description="選出方策の比較評価")
    ap.add_argument("--strategies", default="random,matchup,statsum")
    ap.add_argument("--battles", type=int, default=100)
    ap.add_argument("--team-file", default=None,
                    help="Showdownチームテキスト (省略時はmy_team.json)")
    ap.add_argument("--style", default="balance")
    args = ap.parse_args()
    strategies = [s.strip() for s in args.strategies.split(",") if s.strip()]
    asyncio.run(run(strategies, args.battles, args.team_file, args.style))


if __name__ == "__main__":
    main()
