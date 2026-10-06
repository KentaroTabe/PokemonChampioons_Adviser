"""パーティ構築システムの評価基盤 (M0) の純粋関数テスト。

    python -m tests.test_team_build_core

- verdict: 対応差の 4 状態判定、次の戦数、実戦重み
- families: 系統化 (Jaccard + メガ軸)、系統単位の層化分割 (互いに素・系統不分割・配分)、cross-fit、封印
- registry: 登録の冪等性、status 遷移の制約、production の一意性、rollback
"""
from __future__ import annotations

import json
import random
import tempfile
from pathlib import Path

from tools.team_build import verdict as V
from tools.team_build import families as F
from tools.team_build.registry import Registry
from tools.team_build.manifest import build_manifest


def test_verdict_states():
    n = 400
    base = [1 if i % 2 == 0 else 0 for i in range(n)]
    # 明確に改善: a が b より 10pt 上 (対応差)
    a = [1 if (i % 10) < 6 else 0 for i in range(n)]
    b = [1 if (i % 10) < 5 else 0 for i in range(n)]
    r = V.verdict4(a, b)
    assert r.state == V.IMPROVED, r
    r2 = V.verdict4(b, a)
    assert r2.state == V.DEGRADED, r2
    # 同一列 → 差 0、SE 0 → equivalent
    r3 = V.verdict4(base, base)
    assert r3.state == V.EQUIVALENT and r3.mean == 0.0, r3
    # 小標本のランダム → uncertain が出やすい (状態は 4 つのどれか)
    rng = random.Random(1)
    x = [rng.randint(0, 1) for _ in range(30)]
    y = [rng.randint(0, 1) for _ in range(30)]
    r4 = V.verdict4(x, y)
    assert r4.state in (V.IMPROVED, V.DEGRADED, V.EQUIVALENT, V.UNCERTAIN)
    assert V.verdict4([1], [0]).state == V.UNCERTAIN          # n < 2
    assert V.classify(0.021, 0.05) == V.IMPROVED
    assert V.classify(-0.01, 0.01) == V.EQUIVALENT
    assert V.classify(-0.05, -0.021) == V.DEGRADED
    assert V.classify(-0.01, 0.05) == V.UNCERTAIN
    print("test_verdict_states OK")


def test_next_step_and_weights():
    assert V.next_step(0) == 100
    assert V.next_step(100) == 300
    assert V.next_step(2400, cap=2400) is None
    assert V.next_step(2400) == 4800
    assert V.next_step(9600) is None
    assert V.still_contender(V.verdict4([1, 0, 1, 0], [1, 0, 1, 0]))
    assert abs(V.binomial_halfwidth(0.5, 200) - 0.0693) < 0.001
    assert V.real_weight(0, 0.1) == 0.0
    assert V.real_weight(200, 0.069) < 0.2
    assert V.real_weight(1000, 0.03) == 1.0
    assert 0.4 < V.real_weight(1000, 0.06) < 0.6
    assert V.blended_score(None, 0.6, 0.5) == 0.6
    assert abs(V.blended_score(0.5, 0.7, 0.25) - 0.65) < 1e-9
    print("test_next_step_and_weights OK")


def _teams(n=60, seed=0):
    rng = random.Random(seed)
    pool = [f"sp{i:02d}" for i in range(40)]
    teams = []
    for i in range(n):
        core = rng.sample(pool, 6)
        teams.append(F.Team(team_id=f"t{i:03d}", species=frozenset(core),
                            mega=rng.choice(["m1", "m2", None]), rank=i + 1, usage=1.0))
        # 同系統 (4 体共通、同メガ) の兄弟を作る
        if i % 3 == 0:
            sib = set(core[:4]) | set(rng.sample([p for p in pool if p not in core], 2))
            teams.append(F.Team(team_id=f"t{i:03d}b", species=frozenset(sib),
                                mega=teams[-1].mega, rank=i + 1, usage=1.0))
    return teams


def test_families_and_split():
    assert F.jaccard({"a", "b"}, {"a", "b"}) == 1.0
    assert abs(F.jaccard({"a", "b", "c", "d", "e", "f"}, {"a", "b", "c", "d", "x", "y"}) - 0.5) < 1e-9
    teams = _teams()
    fams = F.cluster_families(teams)
    ids = [t.team_id for f in fams for t in f.teams]
    assert sorted(ids) == sorted(t.team_id for t in teams)          # 全構築がちょうど 1 系統
    by_id = {t.team_id: t for t in teams}
    for f in fams:
        assert len({t.mega for t in f.teams}) == 1                   # メガ軸は系統内で同一
    # 兄弟は同じ系統
    fam_of = {t.team_id: f.family_id for f in fams for t in f.teams}
    assert fam_of["t000"] == fam_of["t000b"] and fam_of["t003"] == fam_of["t003b"]

    split = F.stratified_split(fams, seed=7)
    tiers = ["search", "selection", "holdout"]
    all_ids = [i for k in tiers for i in split[k]]
    assert sorted(all_ids) == sorted(by_id)                          # 互いに素で全被覆
    assert len(set(all_ids)) == len(all_ids)
    for f in fams:                                                   # 系統は分割されない
        assert len({split["families"][f.family_id]}) == 1
        tier = split["families"][f.family_id]
        assert all(t.team_id in split[tier] for t in f.teams)
    n = len(all_ids)
    share = {k: len(split[k]) / n for k in tiers}
    assert abs(share["search"] - 0.5) < 0.12 and abs(share["selection"] - 0.3) < 0.12 \
        and abs(share["holdout"] - 0.2) < 0.12, share
    # 上位が特定階層に偏らない: 各階層の best_rank は上位 10 位以内
    summ = F.summarize(split, fams)
    assert all(summ[k]["best_rank"] is not None and summ[k]["best_rank"] <= 10 for k in tiers), summ
    # 決定的
    assert F.stratified_split(fams, seed=7) == split

    search_fams = [f for f in fams if split["families"][f.family_id] == "search"]
    folds = F.cross_fit_folds(search_fams, k=2, seed=1)
    assert len(folds) == 2 and not (set(folds[0]) & set(folds[1]))
    assert sorted(folds[0] + folds[1]) == sorted(split["search"])

    with tempfile.TemporaryDirectory() as d:
        meta = F.seal_holdout(split["holdout"], "run_test", Path(d) / "sealed")
        assert meta["sealed_id"] == F.sealed_id(split["holdout"])
        assert (Path(d) / "sealed" / f"holdout_{meta['sealed_id']}.json").exists()
    print("test_families_and_split OK")


def test_registry_status_machine():
    with tempfile.TemporaryDirectory() as d:
        reg = Registry(Path(d) / "registry")
        src = Path(d) / "team.json"
        src.write_text('{"text": "Garchomp @ focussash"}', encoding="utf-8")
        r1 = reg.register("team", src, meta={"label": "A"}, run_id="run1")
        assert r1["status"] == "candidate" and reg.resolve(r1["id"]).exists()
        assert reg.register("team", src)["id"] == r1["id"]            # 同内容は冪等
        src2 = Path(d) / "team2.json"
        src2.write_text('{"text": "Kingambit @ blackglasses"}', encoding="utf-8")
        r2 = reg.register("team", src2, run_id="run1")
        assert r2["id"] != r1["id"]
        # 許可されない遷移
        try:
            reg.set_status(r1["id"], "production")
            raise AssertionError("candidate → production が通ってしまった")
        except ValueError:
            pass
        reg.set_status(r1["id"], "validation")
        reg.set_status(r1["id"], "canary")
        p1 = reg.set_status(r1["id"], "production")
        assert reg.production("team")["id"] == r1["id"] and p1["previous_production"] is None
        for st in ("validation", "canary", "production"):
            reg.set_status(r2["id"], st)
        assert reg.production("team")["id"] == r2["id"]
        assert reg.get(r1["id"])["status"] == "retired"               # 前の production は退役
        assert reg.get(r2["id"])["previous_production"] == r1["id"]
        back = reg.rollback("team")
        assert back["id"] == r1["id"] and reg.production("team")["id"] == r1["id"]
        assert reg.get(r2["id"])["status"] == "retired"
        assert len(reg.list(kind="team")) == 2
        m = build_manifest("run1", {"seeds": {"search": [1]}})
        assert m["schema_version"] and m["run_id"] == "run1" and m["seeds"]["search"] == [1]
    print("test_registry_status_machine OK")


if __name__ == "__main__":
    test_verdict_states()
    test_next_step_and_weights()
    test_families_and_split()
    test_registry_status_machine()
