"""実戦の相手バンク (tools/real_opponents, champions_agent/env/real_opponents, advisor/real_prior) の純粋部分のテスト。

    python -m tests.test_real_opponents
"""
from __future__ import annotations

import random

import numpy as np

from tools import real_opponents as RO


def _battle(roster, fielded, lead, outcome="loss", events=(), mega=()):
    return {"opp_roster": roster, "opp_fielded": fielded, "opp_lead": lead, "outcome": outcome,
            "opp_mega_ja": list(mega),
            "events": [{"id": eid, "opp": opp} for eid, opp in events]}


def test_build_bank_counts_picks_leads_and_revealed():
    ja2id = {"A": "a", "B": "b", "C": "c", "D": "d", "E": "e", "F": "f", "X": None, "メガC": "cmega"}
    battles = [
        _battle(["A", "B", "C", "メガC", "D", "E", "F"], ["A", "B", "C"], "A", "loss",     # メガ形態は基本種に丸める
                events=[("move_opponent_earthquake", "A"), ("item_opponent_leftovers", "B"), ("ability_opponent_intimidate", "A")]),
        _battle(["F", "E", "D", "C", "B", "A"], ["A", "D", "E"], "D", "win", events=[("move_opponent_earthquake", "A")],
                mega=["C"]),
        _battle(["A", "B", "X"], ["A"], "A"),                       # 既知 2 体 → 最小既知数未満で除外
        _battle(["A", "B", "C", "D"], ["B", "C"], "B", "win"),      # 4 体 (部分ロースター、本文は作らない)
    ]
    bank = RO.build_bank(battles, lambda ja: ja2id.get(ja), min_roster=4)
    assert bank["n_battles"] == 3 and len(bank["teams"]) == 2
    full = next(t for t in bank["teams"] if t["full"])
    partial = next(t for t in bank["teams"] if not t["full"])
    assert full["roster"] == ["a", "b", "c", "d", "e", "f"] and full["n"] == 2 and len(partial["roster"]) == 4
    assert full["picks"] == {"a": 2, "b": 1, "c": 1, "d": 1, "e": 1} and full["leads"] == {"a": 1, "d": 1}
    assert full["results"] == {"loss": 1, "win": 1}
    assert full["revealed"]["a"]["moves"] == {"earthquake": 2} and full["revealed"]["a"]["abilities"] == {"intimidate": 1}
    assert full["revealed"]["b"]["items"] == {"leftovers": 1} and full["revealed"]["c"]["mega"] == 2
    sa = bank["species"]["a"]
    assert sa["appear"] == 3 and sa["picked"] == 2 and sa["lead"] == 1 and sa["moves"] == {"earthquake": 2}
    assert bank["species"]["f"]["picked"] == 0 and bank["species"]["f"]["appear"] == 2
    assert bank["species"]["c"]["mega"] == 2 and "cmega" not in bank["species"]
    assert RO.base_species("lopunnymega") == ("lopunny", True) and RO.base_species("raichumegay") == ("raichu", True)
    assert RO.base_species("mega") == ("mega", False) and RO.base_species("garchomp") == ("garchomp", False)
    print("test_build_bank_counts_picks_leads_and_revealed OK")


def test_merge_set_and_pick_distribution():
    from tools.team_build.sets import SetCandidate
    rep = SetCandidate("garchomp", "roughskin", "focussash", "jolly", "2/32/0/0/0/32",
                       ["earthquake", "stealthrock", "scaleshot", "swordsdance"], "representative", 0.0, [])
    merged = RO.merge_set(rep, {"moves": {"dragonclaw": 2, "earthquake": 1}, "items": {"lifeorb": 1}, "abilities": {}})
    assert merged.item == "lifeorb" and merged.ability == "roughskin"
    assert merged.moves[:2] == ["dragonclaw", "earthquake"] and len(merged.moves) == 4 and "swordsdance" not in merged.moves
    assert merged.source.startswith("real+") and "real:items" in merged.notes and "real:moves" in merged.notes
    same = RO.merge_set(rep, None)
    assert same.moves == rep.moves and same.item == rep.item
    dist = RO.pick_distribution({"roster": ["a", "b", "c"], "n": 4, "picks": {"a": 4, "b": 1}}, floor=0.05)
    assert dist == {"a": 1.0, "b": 0.25, "c": 0.05}
    print("test_merge_set_and_pick_distribution OK")


def test_sample_picks_and_teampreview_patch():
    from champions_agent.env import real_opponents as ER
    rng = random.Random(1)
    roster = ["a", "b", "c", "d", "e", "f"]
    picks, leads = {"a": 10, "b": 8, "c": 6, "d": 1}, {"a": 9, "b": 1}
    counts, lead_counts = {}, {}
    for _ in range(300):
        order = ER.sample_picks(roster, picks, leads, 10, rng)
        assert len(order) == 3 and len(set(order)) == 3 and all(s in roster for s in order)
        lead_counts[order[0]] = lead_counts.get(order[0], 0) + 1
        for s in order:
            counts[s] = counts.get(s, 0) + 1
    assert lead_counts.get("a", 0) > lead_counts.get("b", 0) and set(lead_counts) <= {"a", "b"}
    assert counts["a"] > counts["c"] > counts.get("f", 0)                    # 選出率の順、未観測は floor
    # teampreview の差し替え: バンクの構成に一致すれば観測分布、しなければ元の選出
    bank = {"teams": [{"roster": roster, "n": 10, "picks": picks, "leads": leads, "legal": True, "text": "x"}]}

    class _Mon:
        def __init__(self, s):
            self.species = s

    class _Battle:
        def __init__(self, species):
            self.team = {f"p{i}": _Mon(s) for i, s in enumerate(species)}

    class _Player:
        def teampreview(self, battle):
            return "/team 123456"

    pl = _Player()
    assert ER.apply_real_pick_teampreview(pl, bank, random.Random(3)) is True
    out = pl.teampreview(_Battle(["A", "B", "C", "D", "E", "F"]))
    assert out.startswith("/team ") and len(out) == 12 and out[6] in "12"      # 先発は a か b
    assert pl.teampreview(_Battle(["A", "B", "C", "D", "E", "Z"])) == "/team 123456"   # 不一致は元のまま
    assert ER.apply_real_pick_teampreview(_Player(), None) is False
    assert ER.usable_teams(bank) == bank["teams"] and ER.usable_teams({"teams": [{"legal": False}]}) == []
    print("test_sample_picks_and_teampreview_patch OK")


def test_blamed_species_and_species_pick_weights():
    from champions_agent.env import real_opponents as ER
    errs = ["Garchomp can't learn Flip Turn.", "Rotom-Wash can't have Leftovers.", "Kingambit has multiple copies of X.",
            "\"marble\" is an invalid item."]
    assert RO.blamed_species(errs, ["garchomp", "rotomwash", "kingambit", "mimikyu"]) == {"garchomp", "rotomwash", "kingambit"}
    bank = {"species": {"a": {"appear": 5, "picked": 5, "lead": 3}, "b": {"appear": 5, "picked": 1, "lead": 0},
                        "c": {"appear": 4, "picked": 2, "lead": 1}, "d": {"appear": 1, "picked": 1, "lead": 1}}}
    got = ER.species_pick_weights(bank, ["a", "b", "c", "d", "e", "f"], min_appear=3)
    assert got is not None
    picks, leads = got
    assert picks["a"] == 1.0 and picks["b"] == 0.2 and abs(picks["d"] - (1.0 + 0.2 + 0.5) / 3) < 1e-9   # 情報なしは平均
    assert set(leads) == {"a", "c"} and "b" not in leads
    assert ER.species_pick_weights(bank, ["a", "x", "y"], min_appear=3) is None                       # 足りる種が 3 未満
    # 種ごとの傾向で選出: a はほぼ必ず、b はまれに
    rng = random.Random(5)
    cnt = {}
    for _ in range(200):
        for s in ER.sample_picks(["a", "b", "c", "d", "e", "f"], picks, leads, 1, rng):
            cnt[s] = cnt.get(s, 0) + 1
    # a (選出率 1.0) は 6 体から 3 体を非復元で引くので約 9 割、b (0.2) は c (0.5) より少ない
    assert cnt["a"] >= 160 and cnt.get("b", 0) < cnt["c"], cnt
    print("test_blamed_species_and_species_pick_weights OK")


def test_slot_weights_and_combo_prior():
    from advisor.real_prior import slot_weights
    from champions_agent.agent.selection_model import combo_prior, expected_best
    w = slot_weights([0.9, 0.5, 0.1, None], mix=0.5, clip=(0.5, 1.5))
    assert w[3] == 1.0 and w[0] > w[1] > w[2] and abs(sum(w[:3]) / 3 - 1.0) < 1e-6      # 平均 1、情報なしは 1
    assert slot_weights([None, None]) == [1.0, 1.0] and slot_weights([0.5, 0.5]) == [1.0, 1.0]
    assert slot_weights([0.9, 0.1], mix=0.0) == [1.0, 1.0]
    from itertools import combinations
    opp = ["a", "b", "c", "d"]
    combos = list(combinations(range(4), 3))
    q = combo_prior(opp, {"a": 0.9, "b": 0.9, "c": 0.9, "d": 0.05}, combos)
    assert abs(q.sum() - 1.0) < 1e-9 and q[combos.index((0, 1, 2))] > 0.8      # d 抜きの 3 体組が支配的
    assert np.allclose(combo_prior(opp, {}, combos), 0.25)
    M = np.array([[0.5, 0.9, 0.2, 0.2], [0.6, 0.4, 0.6, 0.6]])
    i, v, ev = expected_best(M, np.array([1.0, 0.0, 0.0, 0.0]))
    assert i == 1 and abs(v - 0.6) < 1e-9
    i2, _v2, _ = expected_best(M, np.array([0.0, 1.0, 0.0, 0.0]))
    assert i2 == 0
    print("test_slot_weights_and_combo_prior OK")


if __name__ == "__main__":
    test_build_bank_counts_picks_leads_and_revealed()
    test_merge_set_and_pick_distribution()
    test_sample_picks_and_teampreview_patch()
    test_blamed_species_and_species_pick_weights()
    test_slot_weights_and_combo_prior()
