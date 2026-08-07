"""負け型分類 (loss_probe) のテスト。

    python -m tests.test_loss_probe
"""
from __future__ import annotations


def test_classify_loss():
    from champions_agent.env.loss_probe import classify_loss
    assert classify_loss({"won": True}) is None
    assert classify_loss({"won": False, "max_opp_boost": 2,
                          "opp_remaining": 3}) == "setup_loss"
    # 積まれていて僅差でも「積まれ負け」を優先 (原因側で分類)
    assert classify_loss({"won": False, "max_opp_boost": 2,
                          "opp_remaining": 1}) == "setup_loss"
    assert classify_loss({"won": False, "max_opp_boost": 1,
                          "opp_remaining": 1}) == "close_loss"
    assert classify_loss({"won": False, "max_opp_boost": 0,
                          "opp_remaining": 3}) == "outmatched"
    print("test_classify_loss OK")


def test_probe_records_max_boost():
    from champions_agent.env.loss_probe import attach_loss_probe

    class _Opp:
        def __init__(self, species, boosts):
            self.species = species
            self.boosts = boosts

    class _B:
        battle_tag = "b1"
        opponent_active_pokemon = None

    class _P:
        battles = {}

        def choose_move(self, battle):
            return "order"

    p = _P()
    attach_loss_probe(p)
    b = _B()
    b.opponent_active_pokemon = _Opp("lucario", {"atk": 0})
    p.choose_move(b)
    b.opponent_active_pokemon = _Opp("lucario", {"atk": 2, "spe": 1})
    p.choose_move(b)
    b.opponent_active_pokemon = _Opp("garchomp", {"atk": 1})
    p.choose_move(b)
    rec = p.loss_probe["b1"]
    assert rec["max_boost"] == 2 and rec["boost_sp"] == "lucario", rec
    print("test_probe_records_max_boost OK")


def test_summarize():
    from champions_agent.env.loss_probe import summarize
    rows = []
    # 苦手構築X: 1勝5敗 (積まれ負け中心)
    for i in range(6):
        rows.append({"won": i == 0, "turns": 20,
                     "opp_team": ["lucario", "gyarados", "hippowdon"],
                     "my_remaining": 0 if i else 2, "opp_remaining": 3,
                     "max_opp_boost": 2 if i else 0,
                     "boost_species": "lucario" if i else None})
    # 通常構築Y: 4勝1敗 (僅差負け1)
    for i in range(5):
        rows.append({"won": i != 0, "turns": 25,
                     "opp_team": ["delphox", "toxapex", "mamoswine"],
                     "my_remaining": 1, "opp_remaining": 1,
                     "max_opp_boost": 0, "boost_species": None})
    s = summarize(rows)
    assert s["losses"] == 6
    assert s["categories"]["setup_loss"] == 5
    assert s["categories"]["close_loss"] == 1
    assert s["sweepers"][0] == ("lucario", 5), s["sweepers"]
    assert s["worst_teams"][0][0].startswith("gyarados|hippowdon|lucario")
    print("test_summarize OK")


if __name__ == "__main__":
    test_classify_loss()
    test_probe_records_max_boost()
    test_summarize()
    print("\nALL OK")
