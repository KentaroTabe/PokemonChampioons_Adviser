"""介入 (interventions) と Package の純粋関数テスト。

    python -m tests.test_team_build_interventions
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from tools.team_build import interventions as IV
from tools.team_build.candidates import SpeciesFeature
from tools.team_build.package import selection_patterns_from_records


def test_hypotheses_and_variants():
    owned = {"a", "b", "c", "d", "e", "f", "g", "h"}
    members = ["a", "b", "c", "d", "e", "f"]
    ok = {"hypotheses": [{"id": "h1", "kind": "member", "target_id": "a", "replacement_id": "g", "reason": "no_switch_in"},
                         {"id": "h2", "kind": "set", "target_id": "b", "reason": "outsped"},
                         {"id": "h3", "kind": "pick", "reason": "selection_mismatch"}]}
    assert IV.validate_hypotheses(ok, owned, set(members)) == []
    bad = {"hypotheses": [{"id": "x", "kind": "member", "target_id": "zz", "replacement_id": "a", "reason": "nope"}]}
    assert len(IV.validate_hypotheses(bad, owned, set(members))) >= 3
    feats = {s: SpeciesFeature(s, {"t1": 0.2, "t2": 0.2}) for s in owned}
    feats["g"].coverage = {"t1": 0.9, "t2": 0.1}
    feats["a"].coverage = {"t1": 0.0, "t2": 0.5}
    stats = {"loss_by_opponent_species": [{"key": "t1", "losses": 5}, {"key": "zz", "losses": 3}]}
    muts = IV.rule_mutations(stats, members, feats, ["t1", "t2"])
    assert muts and muts[0]["target_id"] == "a" and muts[0]["replacement_id"] == "g", muts
    variants = IV.make_variants("L00", members, ok["hypotheses"] + muts)
    kinds = [v["kind"] for v in variants]
    assert "A_member" in kinds and "B_set" in kinds and "C_pick" in kinds
    va = next(v for v in variants if v["kind"] == "A_member" and v["changes"][0]["out"] == "a")
    assert "g" in va["members"] and "a" not in va["members"] and va["parent_team_id"] == "L00"
    assert IV.classify_change(members, va["members"]) == "repair"
    assert IV.classify_change(members, ["g", "h", "c", "d", "e", "zz"]) == "new_branch"
    with tempfile.TemporaryDirectory() as d:
        p = IV.record_lineage(Path(d), "L00", variants)
        data = json.loads(p.read_text(encoding="utf-8"))
        assert len(data["nodes"]) == len(variants) and data["nodes"][0]["parent"] == "L00"
    print("test_hypotheses_and_variants OK")


def test_selection_patterns():
    recs = [{"won": True, "lead": {"theirs": "p2a: Gengar"}, "our_selection": ["a", "b", "c"]},
            {"won": False, "lead": {"theirs": "p2a: Gengar"}, "our_selection": ["a", "b", "c"]},
            {"won": True, "lead": {"theirs": "p2a: Gengar"}, "our_selection": ["a", "d", "c"]},
            {"won": True, "lead": {"theirs": "p2a: Garchomp"}, "our_selection": ["b", "d", "e"]}]
    pats = selection_patterns_from_records(recs)
    assert pats["gengar"][0]["selection"] == "a/b/c" and pats["gengar"][0]["n"] == 2
    assert pats["garchomp"][0]["win_rate"] == 1.0
    print("test_selection_patterns OK")


if __name__ == "__main__":
    test_hypotheses_and_variants()
    test_selection_patterns()
