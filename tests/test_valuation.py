"""局面評価 V (advisor/valuation.py) と相手の応手の最小モデル (advisor/scoring_transition.opponent_responses) のテスト
(2026-10-09 M2b 段 2、docs/ADVICE_ACCURACY_PLAN_1009.md §2.2・段 B-5)。

受入: V が [−1, 1] に収まる / 全滅で ±1 / 同じ状態 (鏡像) なら 0 / HP 差で単調 / 応手の確率の合計が 1 / 判明技が 100% にならない。
重みの値そのもの (較正前の暫定値) の正しさは主張しない。

    python -m tests.test_valuation
"""
from __future__ import annotations

import os
import random
from dataclasses import replace

os.environ.setdefault("RL_BLEND_WEIGHT", "0")

from advisor import valuation as VA
from advisor.damage import MonView
from advisor.dex import get_dex
from advisor.scoring_transition import make_damage_fn, opponent_responses
from advisor.transition import SimSide
from champions_agent import config as C


def _view(sid, ability=None, item=None, ev=None, **kw):
    sp = get_dex().species(sid)
    return MonView(species_id=sid, name_ja=sid, types=list(sp["types"]), base=dict(sp["baseStats"]),
                   ev=ev or {"atk": 252, "spa": 252, "spe": 252}, ability=ability, item=item, **kw)


def _ctx(my_moves=None, opp_moves=None, opp_unknown=0, my_unknown=0):
    return VA.ValuationContext(my_moves=my_moves or {}, opp_moves=opp_moves or {}, opp_unknown=opp_unknown,
                               my_unknown=my_unknown, damage_fn=make_damage_fn(set(), None, None))


GAR_MOVES = ("earthquake", "dragonclaw", "stoneedge", "swordsdance")


def test_terminal_and_range():
    """全滅で ±1 (未判明の残りがあれば終了しない)。ランダムな局面でも V ∈ [−1, 1]、終了していなければ |V| < 1"""
    ctx = _ctx({"garchomp": GAR_MOVES}, {"garchomp": GAR_MOVES})
    alive = SimSide(_view("garchomp"), 0.5, bench=[(_view("clefable"), 0.0)])
    dead = SimSide(_view("garchomp"), 0.0, bench=[(_view("clefable"), 0.0)])
    assert VA.value(dead, alive, ctx) == -1.0
    assert VA.value(alive, dead, ctx) == 1.0
    assert VA.value(dead, dead, ctx) == 0.0
    assert VA.value(alive, dead, _ctx(opp_unknown=1)) < 1.0            # 相手の未判明の残りが居れば勝ちではない
    assert VA.value(dead, alive, _ctx(my_unknown=1)) > -1.0
    rng = random.Random(7)
    species = ["garchomp", "clefable", "heatran", "dragapult", "kingambit", "slurpuff"]
    for _ in range(60):
        def side():
            return SimSide(_view(rng.choice(species)), rng.choice([0.0, rng.random(), 1.0]),
                           bench=[(_view(rng.choice(species)), rng.choice([0.0, rng.random()])) for _ in range(2)])
        me, opp = side(), side()
        moves = {s: GAR_MOVES for s in species}
        v = VA.value(me, opp, _ctx(moves, moves, opp_unknown=rng.randint(0, 1)))
        assert -1.0 <= v <= 1.0, v
        if VA.terminal_value(me, opp, _ctx(moves, moves)) is None:
            assert abs(v) <= C.VALUATION_NONTERMINAL_MAX
    print("test_terminal_and_range OK")


def test_symmetric_is_zero():
    """鏡像の局面 (同じ個体・同じ HP・同じ技、同速) は M = 0、C = 0 → V = 0"""
    moves = {"garchomp": GAR_MOVES, "clefable": ("moonblast",)}
    ctx = _ctx(moves, moves)
    for hp in (1.0, 0.6, 0.2):
        me = SimSide(_view("garchomp"), hp, bench=[(_view("clefable"), 0.7)])
        opp = SimSide(_view("garchomp"), hp, bench=[(_view("clefable"), 0.7)])
        assert abs(VA.value(me, opp, ctx)) < 1e-9, (hp, VA.value(me, opp, ctx))
    print("test_symmetric_is_zero OK")


def test_monotone_in_hp():
    """自分の HP が減ると V は増えない、相手の HP が減ると V は減らない (場の個体・控えのどちらでも)"""
    moves = {"garchomp": GAR_MOVES, "heatran": ("magmastorm", "earthpower"), "clefable": ("moonblast",),
             "kingambit": ("kowtowcleave", "suckerpunch", "ironhead")}
    ctx = _ctx(moves, moves)
    grid = [1.0, 0.9, 0.75, 0.6, 0.45, 0.3, 0.2, 0.1, 0.05]
    for mine, theirs in (("garchomp", "heatran"), ("clefable", "kingambit"), ("heatran", "garchomp")):
        prev = None
        for hp in grid:
            me = SimSide(_view(mine), hp, bench=[(_view("clefable"), 0.5)])
            opp = SimSide(_view(theirs), 0.8, bench=[(_view("garchomp"), 0.5)])
            v = VA.value(me, opp, ctx)
            assert prev is None or v <= prev + 1e-12, (mine, theirs, hp, v, prev)
            prev = v
        prev = None
        for hp in grid:
            me = SimSide(_view(mine), 0.8, bench=[(_view("clefable"), 0.5)])
            opp = SimSide(_view(theirs), hp, bench=[(_view("garchomp"), 0.5)])
            v = VA.value(me, opp, ctx)
            assert prev is None or v >= prev - 1e-12, (mine, theirs, hp, v, prev)
            prev = v
        # 控えの HP も単調
        a = VA.value(SimSide(_view(mine), 0.8, bench=[(_view("clefable"), 0.9)]), SimSide(_view(theirs), 0.8), ctx)
        b = VA.value(SimSide(_view(mine), 0.8, bench=[(_view("clefable"), 0.3)]), SimSide(_view(theirs), 0.8), ctx)
        assert a > b
    print("test_monotone_in_hp OK")


def test_ko_prob_survive_effects():
    """倒せる確率: タスキ・がんじょう (HP 満タン) と ばけのかわ は単発では 0、連続技は 2 発以上で倒せる。HP が減っていれば通常どおり"""
    ctx = _ctx()
    gar = SimSide(_view("garchomp"), 1.0)
    sash = SimSide(_view("heatran", item="focussash"), 1.0)
    assert VA.ko_prob(gar, sash, "earthquake", None, ctx) == 0.0
    assert VA.ko_prob(gar, replace(sash, active_hp=0.99), "earthquake", None, ctx) == 1.0
    strong = SimSide(replace(_view("garchomp"), boosts={"atk": 6}), 1.0)
    zard = SimSide(_view("charizard", item="focussash"), 1.0)
    assert abs(VA.ko_prob(strong, zard, "rockblast", None, ctx) - 0.9) < 1e-9   # 2 発目で倒れる (ロックブラストの命中 90)
    mim = SimSide(_view("mimikyu", ability="disguise"), 1.0)
    gar2 = SimSide(replace(_view("garchomp"), boosts={"atk": 2}), 1.0)        # じしん 1 発で倒せる量
    assert VA.ko_prob(gar2, mim, "earthquake", None, ctx) == 0.0
    assert VA.ko_prob(SimSide(replace(_view("garchomp", ability="moldbreaker"), boosts={"atk": 2}), 1.0), mim,
                      "earthquake", None, ctx) == 1.0                          # かたやぶりは ばけのかわ を無視
    # 命中: きあいだま (70) で必ず倒せる量なら 0.7
    assert abs(VA.ko_prob(SimSide(replace(_view("lucario"), boosts={"spa": 6}), 1.0), SimSide(_view("kingambit"), 0.3),
                          "focusblast", None, ctx) - 0.7) < 1e-9
    print("test_ko_prob_survive_effects OK")


def test_matchup_order_and_status():
    """後続の対面: 先に動いて倒せる側が有利。ねむりは行動できない (相手の脅威が消える)、先制技は素早さを覆す"""
    moves = {"garchomp": ("earthquake",), "heatran": ("magmastorm",), "kingambit": ("suckerpunch", "ironhead")}
    ctx = _ctx(moves, moves)
    me = SimSide(_view("garchomp"), 1.0)
    opp = SimSide(_view("heatran"), 1.0)
    c = VA.matchup(me, opp, ctx)
    assert c > 0, c          # ガブリアスが先に じしん で倒す
    asleep = VA.matchup(SimSide(replace(_view("garchomp"), status="sleep"), 1.0), opp, ctx)
    assert asleep <= 0 < c
    # ドドゲザンの ふいうち (+1) は 素早さで勝つ側より先に動く
    gam = SimSide(replace(_view("kingambit"), boosts={"atk": 6}), 1.0)
    low = SimSide(_view("garchomp"), 0.2)
    assert VA.matchup(low, gam, ctx) < 0
    print("test_matchup_order_and_status OK")


def test_opponent_responses():
    """応手の確率の合計は 1。判明技 (重み 100) は所持の確定であって 100% ではない。交代は OPP_SWITCH_PROB を控えに等分。
    技は相手にとっての期待ダメージの高い順に重い。控えが無ければ技だけで 1"""
    me = SimSide(_view("garchomp"), 1.0)
    opp = SimSide(_view("heatran"), 1.0, bench=[(_view("clefable"), 1.0), (_view("dragapult"), 0.5)])
    pool = [("magmastorm", 100.0), ("earthpower", 40.0), ("stealthrock", 30.0), ("flashcannon", 10.0)]
    dmg = make_damage_fn(set(), None, None)
    rs = opponent_responses(opp, me, pool, dmg, None, mega_min_prob=1.1)
    assert abs(sum(p for p, _a in rs) - 1.0) < 1e-9
    probs = {a.label: p for p, a in rs}
    assert probs["magmastorm"] < 1.0 and probs["magmastorm"] > 0
    assert abs(probs["交代:clefable"] + probs["交代:dragapult"] - C.OPP_SWITCH_PROB) < 1e-9
    assert abs(probs["交代:clefable"] - probs["交代:dragapult"]) < 1e-12
    # 期待ダメージの高い技 (だいちのちから: ガブリアスに抜群) が、判明している マグマストームより重い
    # (所持の事前分布 0.4 × 1 位の重み 1 > 1.0 × 2 位の重み 0.5 × ... ではなく、比で比べる)
    assert probs["earthpower"] / 0.4 > probs["magmastorm"] / 1.0
    assert probs["stealthrock"] < probs["magmastorm"]
    # 控えが無く、技が 1 つだけなら その技 100% (配る先が無い)。技が 2 つあれば判明技も 100% にならない
    solo = opponent_responses(SimSide(_view("heatran"), 1.0), me, [("magmastorm", 100.0)], dmg, None, mega_min_prob=1.1)
    assert [(round(p, 9), a.label) for p, a in solo] == [(1.0, "magmastorm")]
    two = opponent_responses(SimSide(_view("heatran"), 1.0), me, [("magmastorm", 100.0), ("earthpower", 100.0)], dmg, None,
                             mega_min_prob=1.1)
    assert all(p < 1.0 for p, _a in two) and abs(sum(p for p, _a in two) - 1.0) < 1e-9
    print("test_opponent_responses OK")


if __name__ == "__main__":
    test_terminal_and_range()
    test_symmetric_is_zero()
    test_monotone_in_hp()
    test_ko_prob_survive_effects()
    test_matchup_order_and_status()
    test_opponent_responses()
    print("ALL OK")
