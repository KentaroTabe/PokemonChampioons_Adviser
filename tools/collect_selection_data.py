"""選出学習のデータ収集 (実対戦ベース)。

selfplay.generate_selection_episode はダミー報酬のプレースホルダだったため、
実際にShowdownで対戦し「選出 → 戦闘 → 勝敗」を記録する収集器を用意する。

    python -m tools.collect_selection_data --battles 300
    python -m tools.collect_selection_data --battles 300 --explore 1.0  # 全ランダム
    python -m tools.collect_selection_data --show                        # 集計だけ見る

記録: champions_agent/train/logs/selection_data.npz
  obs    : 選出時点の観測 (自分6体 + 相手6体)
  emb    : 機能埋め込みによる表現 (自分6体+相手6体 × メタ対面ベクトル)
  action : 選んだ組み合わせのインデックス (6P3=120通り)
  reward : 勝ち1 / 負け0
  ※ obs と emb の両方を残すのは、どちらの表現が学習しやすいか比較するため
    (emb は未知種族へ汎化しやすく、組合せ爆発の圧縮になる)

探索方針: 既定は50%をランダム選出にする (ε-greedy風)。相性選出だけを
記録すると「選ばれなかった3体」のデータが永久に得られず学習できないため。
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
from pathlib import Path

import numpy as np

logging.getLogger("poke-env").setLevel(logging.ERROR)

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "champions_agent" / "train" / "logs" / "selection_data.npz"


def _to_id(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def _emb_of(species_list: list) -> np.ndarray:
    """種族列 -> 機能埋め込みの連結 (未収録種族はゼロ埋め)"""
    from tools.species_embedding import load, vector
    dim = len(load()["functional"]["basis"])
    out = []
    for sp in species_list:
        v = vector(_to_id(sp or ""), "functional") if sp else None
        out.append(np.array(v, dtype=np.float32) if v
                   else np.zeros(dim, dtype=np.float32))
    return np.concatenate(out) if out else np.zeros(0, dtype=np.float32)


async def collect(n_battles: int, explore: float, style: str) -> dict:
    import random
    import types
    from poke_env import AccountConfiguration
    from poke_env.teambuilder import ConstantTeambuilder
    from champions_agent.agent.policy_selection import build_selection_observation
    from champions_agent.agent.spaces import SELECTION_PERMUTATIONS
    from champions_agent.config import TRAINING_BATTLE_FORMAT
    from champions_agent.env.showdown_env import (
        TrainingServerConfiguration, apply_matchup_teampreview,
        make_benchmark_player,
    )
    from champions_agent.train.evaluate import ModelPlayer
    from tools.evaluate_team import build_myteam_text

    records: dict = {}      # battle_tag -> {obs, emb, action}
    team_text = build_myteam_text()

    def _teampreview(self, battle):
        mons = list(battle.team.values())
        opp_mons = list(battle.opponent_team.values())
        # 探索: 一定確率でランダム選出にして未経験の組み合わせを踏む
        if random.random() < explore or len(mons) < 6:
            perm = random.choice(SELECTION_PERMUTATIONS)
        else:
            from champions_agent.env.search_expert import teampreview_order
            order = teampreview_order(battle)          # "/team 123456"
            digits = [int(c) - 1 for c in order.split()[-1]]
            perm = tuple(digits[:3])
        try:
            own = [{"species": p.species, "hp_percent": 1.0,
                    "status": "none"} for p in mons]
            opp = [{"species": p.species} for p in opp_mons]
            records[battle.battle_tag] = {
                "obs": build_selection_observation(own, opp),
                "emb": np.concatenate([
                    _emb_of([p.species for p in mons]),
                    _emb_of([p.species for p in opp_mons])]),
                "action": SELECTION_PERMUTATIONS.index(perm),
            }
        except Exception:
            pass
        return "/team " + "".join(str(i + 1) for i in perm)

    uid = os.getpid() % 10000
    me = ModelPlayer(
        account_configuration=AccountConfiguration(f"SelD{uid}", None),
        battle_format=TRAINING_BATTLE_FORMAT,
        server_configuration=TrainingServerConfiguration,
        team=ConstantTeambuilder(team_text), play_style=style,
        checkpoint="best", max_concurrent_battles=1)
    me.teampreview = types.MethodType(_teampreview, me)
    opp = make_benchmark_player(
        battle_format=TRAINING_BATTLE_FORMAT,
        account_configuration=AccountConfiguration(f"SelE{uid}", None))
    apply_matchup_teampreview(opp)

    await me.battle_against(opp, n_battles=n_battles)

    obs, emb, act, rew = [], [], [], []
    for tag, battle in me.battles.items():
        rec = records.get(tag)
        if rec is None or battle.won is None:
            continue
        obs.append(rec["obs"])
        emb.append(rec["emb"])
        act.append(rec["action"])
        rew.append(1.0 if battle.won else 0.0)
    return {"obs": np.asarray(obs, dtype=np.float32),
            "emb": np.asarray(emb, dtype=np.float32),
            "action": np.asarray(act, dtype=np.int64),
            "reward": np.asarray(rew, dtype=np.float32)}


def _merge_save(new: dict) -> dict:
    """既存データへ追記して保存する (収集を分割して積み増せる)"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    if OUT.exists():
        try:
            old = np.load(OUT)
            if len(old["action"]) and old["obs"].shape[1] == new["obs"].shape[1]:
                new = {k: np.concatenate([old[k], new[k]]) for k in new}
        except Exception as e:
            print(f"[collect_selection] 既存データを引き継げません ({e})")
    np.savez_compressed(OUT, **new)
    return new


def show() -> None:
    if not OUT.exists():
        print("収集データがありません")
        return
    d = np.load(OUT)
    n = len(d["action"])
    print(f"=== 選出データ {n}件 ({OUT.name}) ===")
    print(f"観測次元: obs={d['obs'].shape[1]} / emb={d['emb'].shape[1]}")
    print(f"全体勝率: {d['reward'].mean():.3f}")
    uniq = len(set(d["action"].tolist()))
    print(f"出現した選出パターン: {uniq}/120 通り")
    # 出現5回以上の選出だけ勝率を出す (少数は判断材料にならない)
    from collections import Counter
    cnt = Counter(d["action"].tolist())
    rows = []
    for a, c in cnt.items():
        if c >= 5:
            rows.append((a, c, float(d["reward"][d["action"] == a].mean())))
    rows.sort(key=lambda x: -x[2])
    if rows:
        print("選出別の勝率 (5回以上):")
        for a, c, wr in rows[:10]:
            print(f"  行動{a}: {wr:.2f} ({c}回)")
    print("\n※ 学習には数千件規模が必要 (1選出あたり数十件は欲しい)")


def main() -> None:
    ap = argparse.ArgumentParser(description="選出学習のデータ収集 (実対戦)")
    ap.add_argument("--battles", type=int, default=300)
    ap.add_argument("--explore", type=float, default=0.5,
                    help="ランダム選出にする確率 (未経験の組み合わせを踏むため)")
    ap.add_argument("--style", default="balance")
    ap.add_argument("--show", action="store_true", help="集計表示のみ")
    args = ap.parse_args()
    if args.show:
        show()
        return
    import time
    t0 = time.time()
    data = asyncio.run(collect(args.battles, args.explore, args.style))
    if not len(data["action"]):
        raise SystemExit("記録できたエピソードがありません")
    merged = _merge_save(data)
    print(f"[collect_selection] 今回{len(data['action'])}件 / "
          f"累計{len(merged['action'])}件 ({time.time() - t0:.0f}s) → {OUT}")
    show()


if __name__ == "__main__":
    main()
