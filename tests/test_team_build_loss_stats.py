"""敗因統計 (loss_stats) の純粋関数テスト。

    python -m tests.test_team_build_loss_stats
"""
from __future__ import annotations

from tools.team_build.loss_stats import loss_stats, top_threats


def _rec(won, opp_sel, our_sel, lead_ours, lead_theirs, ko=None, fam="F1", mega=None, turns=8):
    return {"won": won, "opponent_selection": opp_sel, "our_selection": our_sel,
            "lead": {"ours": lead_ours, "theirs": lead_theirs}, "opponent_family_id": fam,
            "ko_events": ko or [], "mega_usage": mega or [], "turn_count": turns}


def test_loss_stats():
    recs = [
        _rec(False, ["gengar", "garchomp", "gyarados"], ["kingambit", "primarina", "metagross"], "primarina", "p2a: Gengar",
             ko=[{"fainted": "p1a: Kingambit", "by": "p2a: Gengar", "move": "Shadow Ball"}], fam="F1"),
        _rec(False, ["gengar", "hippowdon", "gyarados"], ["kingambit", "primarina", "rotomwash"], "primarina", "p2a: Gengar",
             ko=[{"fainted": "p1a: Primarina", "by": "p2a: Gengar", "move": "Shadow Ball"}], fam="F2"),
        _rec(True, ["garchomp", "hippowdon", "mimikyu"], ["kingambit", "primarina", "metagross"], "kingambit", "p2a: Garchomp",
             ko=[{"fainted": "p2a: Garchomp", "by": "p1a: Kingambit", "move": "Sucker Punch"}], fam="F1",
             mega=[{"turn": 2, "target": "p1a: Metagross"}]),
    ]
    s = loss_stats(recs, our_species=["kingambit", "primarina", "metagross", "rotomwash", "archaludon", "mimikyu"])
    assert s["n"] == 3 and s["wins"] == 1
    assert s["loss_by_opponent_species"][0]["key"] == "gengar" and s["loss_by_opponent_species"][0]["losses"] == 2
    assert top_threats(s, 1) == ["gengar"]
    assert s["loss_by_our_lead"][0]["key"] == "primarina" and s["loss_by_our_lead"][0]["losses"] == 2
    assert s["ko_source"][0]["by"] == "gengar" and s["ko_source"][0]["move"] == "Shadow Ball" and s["ko_source"][0]["n"] == 1
    assert s["our_ko"][0]["by"] == "kingambit"
    unused = {u["species"]: u["selected"] for u in s["unused_members"]}
    assert unused["archaludon"] == 0 and unused["mimikyu"] == 0 and unused["kingambit"] == 3
    assert s["mega_usage_vs_outcome"]["used"] == {"wins": 1, "losses": 0}
    assert s["loss_by_opponent_family"][0]["key"] in ("F1", "F2")
    print("test_loss_stats OK")


if __name__ == "__main__":
    test_loss_stats()
