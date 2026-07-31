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


def _opp_selected(battle) -> list:
    """相手が実際に選出した3体 (対戦後に判明した範囲。長さ3にパディング)。

    選出画面では相手6体が見えるが、実際に出てくるのは3体。
    「相手の選出に条件付けた勝率予測」(選出の読み合いを解く利得行列) の
    学習にはこの3体が要る。対戦が早く終わると3体出きらないことがあり、
    その場合は判明分のみ (残りは空文字)。
    """
    sel = [p.species for p in battle.opponent_team.values()
           if getattr(p, "revealed", False)]
    return (sel + [""] * 3)[:3]


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


def _make_switchable():
    """途中でチームを差し替えられる Teambuilder を1つ作る。

    同じ (自チーム, 相手チーム) の組に対して複数の選出を試すため、
    グループ単位でチームを固定したい。
    """
    from poke_env.teambuilder import Teambuilder

    class _Switchable(Teambuilder):
        def __init__(self):
            self.packed = None

        def set_text(self, text: str) -> None:
            self.packed = self.join_team(self.parse_showdown_team(text))

        def yield_team(self) -> str:
            return self.packed

    return _Switchable()


async def collect_paired(n_groups: int, group_size: int, style: str,
                         teams: str = "ranked") -> dict:
    """対応のある収集: 同じ相手に対して複数の選出を試す。

    従来の収集は候補ごとに相手を引き直していたため、選出間の差が
    相手の引き運に埋もれていた (勝敗0/1の分散0.25に対し、選出間の
    真の差は0.1前後)。同じ (自チーム, 相手チーム) の組で group_size 通りの
    選出を試し、group を記録することで「同条件での比較」が作れる。
    相手の選出も相性ベースで決定的なので、グループ内で固定される。

    残る差異は乱数 (ダメージ乱数・急所) のみ。進化探索で同じ手法を入れた
    ところ、平均回帰が可視化できるようになった実績がある。
    """
    import random
    import types
    from poke_env import AccountConfiguration
    from champions_agent.agent.policy_selection import build_selection_observation
    from champions_agent.agent.spaces import SELECTION_PERMUTATIONS
    from champions_agent.config import TRAINING_BATTLE_FORMAT
    from champions_agent.env.ranked_teams import RankedTeambuilder, build_ranked_teams
    from champions_agent.env.showdown_env import (
        TrainingServerConfiguration, apply_matchup_teampreview,
        make_benchmark_player,
    )
    from champions_agent.train.evaluate import ModelPlayer
    from tools.evaluate_team import build_myteam_text

    pool = build_ranked_teams()
    my_texts = ([build_myteam_text()] if teams == "myteam" else pool)

    records: dict = {}
    state = {"perms": [], "i": 0, "group": 0}

    def _teampreview(self, battle):
        mons = list(battle.team.values())
        opp_mons = list(battle.opponent_team.values())
        perms = state["perms"]
        perm = perms[state["i"] % len(perms)]
        state["i"] += 1
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
                "team": [p.species for p in mons],
                "opp_team": ([p.species for p in opp_mons] + [""] * 6)[:6],
                "group": state["group"],
            }
        except Exception:
            pass
        rest = [i for i in range(len(mons)) if i not in perm]
        return "/team " + "".join(str(i + 1) for i in list(perm) + rest)

    uid = os.getpid() % 10000
    my_tb, opp_tb = _make_switchable(), _make_switchable()
    me = ModelPlayer(
        account_configuration=AccountConfiguration(f"SpD{uid}", None),
        battle_format=TRAINING_BATTLE_FORMAT,
        server_configuration=TrainingServerConfiguration,
        team=my_tb, play_style=style,
        checkpoint="best", max_concurrent_battles=1)
    me.teampreview = types.MethodType(_teampreview, me)
    opp = make_benchmark_player(
        battle_format=TRAINING_BATTLE_FORMAT, team=opp_tb,
        account_configuration=AccountConfiguration(f"SpE{uid}", None))
    apply_matchup_teampreview(opp)

    for g in range(n_groups):
        my_tb.set_text(random.choice(my_texts))
        opp_tb.set_text(random.choice(pool))
        state["perms"] = random.sample(SELECTION_PERMUTATIONS,
                                       min(group_size,
                                           len(SELECTION_PERMUTATIONS)))
        state["i"] = 0
        state["group"] = g
        await me.battle_against(opp, n_battles=len(state["perms"]))

    obs, emb, act, rew, team, opp_team, group, opp_sel = \
        [], [], [], [], [], [], [], []
    for tag, battle in me.battles.items():
        rec = records.get(tag)
        if rec is None or battle.won is None:
            continue
        obs.append(rec["obs"])
        emb.append(rec["emb"])
        act.append(rec["action"])
        rew.append(1.0 if battle.won else 0.0)
        team.append(rec["team"])
        opp_team.append(rec["opp_team"])
        group.append(rec["group"])
        opp_sel.append(_opp_selected(battle))
    return {"obs": np.asarray(obs, dtype=np.float32),
            "emb": np.asarray(emb, dtype=np.float32),
            "action": np.asarray(act, dtype=np.int64),
            "reward": np.asarray(rew, dtype=np.float32),
            "team": np.asarray(team, dtype="<U24"),
            "opp_team": np.asarray(opp_team, dtype="<U24"),
            "group": np.asarray(group, dtype=np.int64),
            "opp_sel": np.asarray(opp_sel, dtype="<U24")}


async def collect(n_battles: int, explore: float, style: str,
                  teams: str = "myteam") -> dict:
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
    if teams == "ranked":
        # 他プレイヤーの実構築 (ラダー上位) を毎バトル引き直す。
        # 単一チームのデータだとモデルがそのチーム専用になるため、
        # 「チーム一般の選出判断」を学ぶには多数の構築が要る
        from champions_agent.env.ranked_teams import RankedTeambuilder
        own_teambuilder = RankedTeambuilder()
    else:
        from poke_env.teambuilder import ConstantTeambuilder
        own_teambuilder = ConstantTeambuilder(build_myteam_text())

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
                # 行動インデックスを後から3体の名前へ戻せるよう並び順も保存する
                "team": [p.species for p in mons],
                # 相手6体も選出画面では見えている (battle.opponent_team は
                # 対戦前は teampreview の6体を返す)。これを保存しないと
                # 「相手に応じた選出」が学習できない
                "opp_team": ([p.species for p in opp_mons] + [""] * 6)[:6],
            }
        except Exception:
            pass
        return "/team " + "".join(str(i + 1) for i in perm)

    uid = os.getpid() % 10000
    me = ModelPlayer(
        account_configuration=AccountConfiguration(f"SelD{uid}", None),
        battle_format=TRAINING_BATTLE_FORMAT,
        server_configuration=TrainingServerConfiguration,
        team=own_teambuilder, play_style=style,
        checkpoint="best", max_concurrent_battles=1)
    me.teampreview = types.MethodType(_teampreview, me)
    # 相手のチームも構築プール全体から引き直す。make_benchmark_player の既定は
    # 評価基準を動かさないため上位60構築に固定してあるが、選出モデルにとって
    # 「相手構築の種類」は汎化の主因なので、収集時はここだけ広げる
    # (実戦では毎回違う相手に当たる。60種の相手しか見ないと条件付けを学べない)
    from champions_agent.env.ranked_teams import RankedTeambuilder
    opp = make_benchmark_player(
        battle_format=TRAINING_BATTLE_FORMAT,
        team=RankedTeambuilder(),
        account_configuration=AccountConfiguration(f"SelE{uid}", None))
    apply_matchup_teampreview(opp)

    await me.battle_against(opp, n_battles=n_battles)

    obs, emb, act, rew, team, opp_team, opp_sel = [], [], [], [], [], [], []
    for tag, battle in me.battles.items():
        rec = records.get(tag)
        if rec is None or battle.won is None:
            continue
        obs.append(rec["obs"])
        emb.append(rec["emb"])
        act.append(rec["action"])
        rew.append(1.0 if battle.won else 0.0)
        team.append(rec["team"])
        opp_team.append(rec["opp_team"])
        opp_sel.append(_opp_selected(battle))
    return {"obs": np.asarray(obs, dtype=np.float32),
            "emb": np.asarray(emb, dtype=np.float32),
            "action": np.asarray(act, dtype=np.int64),
            "reward": np.asarray(rew, dtype=np.float32),
            "team": np.asarray(team, dtype="<U24"),
            "opp_team": np.asarray(opp_team, dtype="<U24"),
            # -1 = 対応なし (毎戦チームを引き直しているので比較相手がいない)
            "group": np.full(len(rew), -1, dtype=np.int64),
            "opp_sel": np.asarray(opp_sel, dtype="<U24")}


def _default_column(sample: np.ndarray, n: int) -> np.ndarray:
    """新しく増えた列を旧データぶん埋めるための既定値"""
    if sample.dtype.kind in "iu":
        return np.full((n,) + sample.shape[1:], -1, dtype=sample.dtype)
    if sample.dtype.kind == "f":
        return np.zeros((n,) + sample.shape[1:], dtype=sample.dtype)
    return np.full((n,) + sample.shape[1:], "", dtype=sample.dtype)


def _migrate(old, new: dict) -> dict:
    """旧データを新スキーマへ寄せる (増えた列は既定値、消えた列は捨てる)"""
    n = len(old["action"]) if "action" in old.files else 0
    out = {}
    for k, v in new.items():
        out[k] = old[k] if k in old.files else _default_column(v, n)
    return out


def _merge_save(new: dict) -> dict:
    """既存データへ追記して保存する (収集を分割して積み増せる)"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    if OUT.exists():
        try:
            old = np.load(OUT)
            # 列が増えただけなら旧データを捨てずに既定値で埋めて引き継ぐ。
            # 完全一致を要求していたため、group列を足したときに25000件を
            # 失った (2026-07-30)。観測次元が変わった場合だけ作り直す
            old = _migrate(old, new)
            same_schema = (set(old.keys()) == set(new)
                           and old["obs"].shape[1] == new["obs"].shape[1])
            if len(old["action"]) and same_schema:
                # グループIDは収集回ごとに0から振り直されるので、既存の
                # 最大値の先へずらす (別の回の対戦が同一グループとして
                # 対応づけられ、比較にならないのを防ぐ)
                if "group" in new and len(old["group"]):
                    base = int(old["group"].max()) + 1
                    g = new["group"].copy()
                    g[g >= 0] += base
                    new["group"] = g
                new = {k: np.concatenate([old[k], new[k]]) for k in new}
            elif len(old["action"]):
                print("[collect_selection] 形式が変わったため既存データは"
                      "引き継がず作り直します")
        except Exception as e:
            print(f"[collect_selection] 既存データを引き継げません ({e})")
    # 一時ファイル + rename で置き換える。直接上書きすると、収集中に
    # 学習や統計表示が読みに来たとき書きかけを掴んで BadZipFile になる
    # 末尾は .npz にしておく (numpy は .npz でない名前へ勝手に付け足す)
    tmp = OUT.with_name(OUT.name + ".tmp.npz")
    np.savez_compressed(tmp, **new)
    tmp.replace(OUT)
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

    from collections import Counter
    from champions_agent.agent.spaces import SELECTION_PERMUTATIONS
    from advisor.infer import species_ja_name

    def _label(action: int, row: int) -> str:
        """行動インデックス -> 「先発/2番手/3番手」の日本語名"""
        try:
            perm = SELECTION_PERMUTATIONS[action]
            team = d["team"][row] if "team" in d.files else None
            if team is None:
                return f"行動{action}"
            names = [species_ja_name(str(team[i])) or str(team[i])
                     for i in perm]
            return f"★{names[0]} → {names[1]} → {names[2]}"
        except Exception:
            return f"行動{action}"

    cnt = Counter(d["action"].tolist())
    first_row = {}
    for i, a in enumerate(d["action"].tolist()):
        first_row.setdefault(a, i)
    # 出現5回以上の選出だけ勝率を出す (少数は判断材料にならない)
    rows = [(a, c, float(d["reward"][d["action"] == a].mean()))
            for a, c in cnt.items() if c >= 5]
    rows.sort(key=lambda x: -x[2])
    if rows:
        print(f"\n■ 勝率の高い選出 (5回以上、★=先発):")
        for a, c, wr in rows[:8]:
            print(f"  {wr:.2f} ({c:>3}回)  {_label(a, first_row[a])}")
        print(f"\n■ 勝率の低い選出:")
        for a, c, wr in rows[-5:]:
            print(f"  {wr:.2f} ({c:>3}回)  {_label(a, first_row[a])}")
        best = rows[0][2]
        print(f"\n選出による勝率の幅: {rows[-1][2]:.2f} 〜 {best:.2f} "
              f"(全体平均 {d['reward'].mean():.2f})")
    print("\n※ 学習には数千件規模が必要 (1選出あたり数十件は欲しい)")


def main() -> None:
    ap = argparse.ArgumentParser(description="選出学習のデータ収集 (実対戦)")
    ap.add_argument("--battles", type=int, default=300)
    ap.add_argument("--explore", type=float, default=0.5,
                    help="ランダム選出にする確率 (未経験の組み合わせを踏むため)")
    ap.add_argument("--style", default="balance")
    ap.add_argument("--teams", default="myteam", choices=["myteam", "ranked"],
                    help="myteam=自分の登録チーム固定 / ranked=他プレイヤーの実構築を毎回引き直す")
    ap.add_argument("--show", action="store_true", help="集計表示のみ")
    ap.add_argument("--paired", action="store_true",
                    help="対応のある収集: 同じ(自チーム,相手チーム)の組に対して"
                         "複数の選出を試す。選出間の差が相手の引き運に"
                         "埋もれるのを防ぐ")
    ap.add_argument("--group-size", type=int, default=6,
                    help="--paired のとき、1組あたり何通りの選出を試すか")
    args = ap.parse_args()
    if args.show:
        show()
        return
    import time
    t0 = time.time()
    if args.paired:
        groups = max(1, args.battles // args.group_size)
        print(f"[collect_selection] 対応のある収集: {groups}組 x "
              f"{args.group_size}選出 = {groups * args.group_size}戦")
        data = asyncio.run(collect_paired(groups, args.group_size,
                                          args.style, args.teams))
    else:
        data = asyncio.run(collect(args.battles, args.explore, args.style,
                                   args.teams))
    if not len(data["action"]):
        raise SystemExit("記録できたエピソードがありません")
    merged = _merge_save(data)
    print(f"[collect_selection] 今回{len(data['action'])}件 / "
          f"累計{len(merged['action'])}件 ({time.time() - t0:.0f}s) → {OUT}")
    show()


if __name__ == "__main__":
    main()
