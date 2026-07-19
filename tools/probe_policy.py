"""学習済み戦闘方策の行動品質を測定するプローブ。

vs Random でNバトル実施し、勝率に加えて「行動の質」を集計する:
- atk_rate:  攻撃技を選んだ割合 (変化技/交代との比率)
- se_rate:   抜群技が利用可能だった場面で、実際に抜群技を選んだ割合
- best_rate: 「威力x相性xSTAB」が最大の技を選んだ割合
ランダム方策の理論値と比較することで、学習が意味のある方策を
獲得しているかを判定する (アドバイス機能としての信頼性チェック)。

    python -m tools.probe_policy [--play-style balance] [--battles 30] [--timeout 900]
"""
from __future__ import annotations

import argparse
import asyncio
import signal
import sys

from champions_agent.config import TRAINING_BATTLE_FORMAT, TRAINING_TEAM_SIZE


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--play-style", type=str, default="balance")
    parser.add_argument("--battles", type=int, default=30)
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()

    signal.signal(signal.SIGALRM,
                  lambda s, f: (print("[probe] TIMEOUT"), sys.exit(1)))
    signal.alarm(args.timeout)

    from poke_env.player import Player, RandomPlayer
    from champions_agent.env.showdown_env import TrainingServerConfiguration
    from champions_agent.env.team_builder import ChampionsTeambuilder
    from champions_agent.agent.policy_battle import BattlePolicy

    stats = {"decisions": 0, "atk": 0, "se_avail": 0, "se_chosen": 0,
             "best_avail": 0, "best_chosen": 0, "switch": 0, "default": 0,
             "rand_se": 0.0, "rand_best": 0.0}

    def move_score(mon, move, opp):
        power = float(move.base_power or 0)
        if power <= 0:
            return 0.0
        try:
            eff = float(opp.damage_multiplier(move)) if opp else 1.0
        except Exception:
            eff = 1.0
        stab = 1.5 if (move.type and mon and
                       any(t and t.name == move.type.name for t in mon.types)) else 1.0
        return power * eff * stab

    class ProbePlayer(Player):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.policy = BattlePolicy(play_style=args.play_style)

        def choose_move(self, battle):
            order = self.policy.choose_order(battle)
            self._record(battle, order)
            return order

        def _record(self, battle, order):
            moves = battle.available_moves
            if not moves:
                return
            mon = battle.active_pokemon
            opp = battle.opponent_active_pokemon
            stats["decisions"] += 1

            chosen = getattr(order, "order", None)
            from poke_env.battle import Move, Pokemon
            is_move = isinstance(chosen, Move)
            if isinstance(chosen, Pokemon):
                stats["switch"] += 1
            elif not is_move:
                # DefaultBattleOrder等 (無効アクションの丸め込み)
                stats["default"] += 1

            n_actions = len(moves) + len(battle.available_switches)
            se_moves = []
            scored = []
            for m in moves:
                s = move_score(mon, m, opp)
                scored.append((s, m.id))
                try:
                    if (m.base_power or 0) > 0 and opp and opp.damage_multiplier(m) >= 2:
                        se_moves.append(m.id)
                except Exception:
                    pass

            if is_move and (chosen.base_power or 0) > 0:
                stats["atk"] += 1
            if se_moves:
                stats["se_avail"] += 1
                stats["rand_se"] += len(se_moves) / max(1, n_actions)
                if is_move and chosen.id in se_moves:
                    stats["se_chosen"] += 1
            if scored and max(s for s, _ in scored) > 0:
                best_id = max(scored)[1]
                stats["best_avail"] += 1
                stats["rand_best"] += 1.0 / max(1, n_actions)
                if is_move and chosen.id == best_id:
                    stats["best_chosen"] += 1

    async def run():
        me = ProbePlayer(
            battle_format=TRAINING_BATTLE_FORMAT,
            server_configuration=TrainingServerConfiguration,
            team=ChampionsTeambuilder(size=TRAINING_TEAM_SIZE,
                                       play_style=args.play_style),
        )
        opp = RandomPlayer(
            battle_format=TRAINING_BATTLE_FORMAT,
            server_configuration=TrainingServerConfiguration,
            team=ChampionsTeambuilder(size=TRAINING_TEAM_SIZE),
        )
        await me.battle_against(opp, n_battles=args.battles)
        return me.n_won_battles

    wins = asyncio.run(run())

    d = max(1, stats["decisions"])
    se_a = max(1, stats["se_avail"])
    b_a = max(1, stats["best_avail"])
    model_loaded = "あり" if __import__(
        "pathlib").Path(f"champions_agent/train/checkpoints/battle_policy_{args.play_style}.zip").exists() else "なし"
    print(f"\n=== probe_policy [{args.play_style}] (チェックポイント: {model_loaded}) ===")
    print(f"勝率 vs Random: {wins}/{args.battles} = {wins / args.battles:.2f}")
    print(f"意思決定数: {stats['decisions']} "
          f"(交代率 {stats['switch'] / d:.2f} / デフォルト丸め率 {stats['default'] / d:.2f})")
    print(f"攻撃技選択率: {stats['atk'] / d:.2f}")
    print(f"抜群技の選択率: {stats['se_chosen'] / se_a:.2f} "
          f"(利用可能場面 {stats['se_avail']}回 / ランダム基準 {stats['rand_se'] / se_a:.2f})")
    print(f"最大打点技の選択率: {stats['best_chosen'] / b_a:.2f} "
          f"(ランダム基準 {stats['rand_best'] / b_a:.2f})")


if __name__ == "__main__":
    main()
