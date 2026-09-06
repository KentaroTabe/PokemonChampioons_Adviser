"""候補源 (historical / mutation / crossover / novelty) の純粋関数テスト。

    python -m tests.test_team_build_sources
"""
from tools.team_build import sources as SRC
from tools.team_build.candidates import Lineup, SpeciesFeature
from tools.team_build.families import Team


def test_sources():
    owned = {"a", "b", "c", "d", "e", "f", "g"}
    pool = [Team("t1", frozenset({"a", "b", "c", "x", "y", "z"}), mega="a", rank=1),
            Team("t2", frozenset({"a", "b", "x", "y", "z", "w"}), mega=None, rank=2),
            Team("t3", frozenset({"d", "e", "f", "x", "y", "z"}), mega=None, rank=3)]
    cores = SRC.historical_cores(pool, owned)
    assert [c["core_ids"] for c in cores] == [["a", "b", "c"], ["d", "e", "f"]], cores
    assert cores[0]["mega_id"] == "a"
    th = ["t1", "t2"]
    feats = {s: SpeciesFeature(s, {"t1": 0.5, "t2": 0.5}) for s in owned}
    feats["g"].coverage = {"t1": 0.9, "t2": 0.9}
    l1 = Lineup(("a", "b", "c", "d", "e", "f"), "C1", 0.0, {})
    l2 = Lineup(("a", "b", "c", "d", "e", "g"), "C2", 0.0, {})
    muts = SRC.mutations([l1], list(owned), feats, th, "any")
    assert muts and "g" in muts[0].members and muts[0].tag == "mutation"
    cx = SRC.crossovers([l1, l2], feats, th, "any")
    assert isinstance(cx, list)
    nov = SRC.novelty_pick([l1, l2, Lineup(("a", "b", "g", "x", "y", "z"), "C3", 0.0, {})], [l1], n=1)
    assert nov and nov[0].tag == "novelty" and nov[0].members != l1.members
    print("test_sources OK")


if __name__ == "__main__":
    test_sources()
