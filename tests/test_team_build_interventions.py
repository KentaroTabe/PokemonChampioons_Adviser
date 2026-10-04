"""介入 (interventions) と Package の純粋関数テスト。

    python -m tests.test_team_build_interventions
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from tools.team_build import interventions as IV
from tools.team_build.package import selection_patterns_from_records


def test_lineage_helpers():
    """残っているのは変更の分類と系譜の記録だけ (LLM の仮説の経路は 2026-10-05 に削除)"""
    members = ["a", "b", "c", "d", "e", "f"]
    assert IV.classify_change(members, ["g", "b", "c", "d", "e", "f"]) == "repair"
    assert IV.classify_change(members, ["g", "h", "c", "d", "e", "zz"]) == "new_branch"
    variants = [{"variant_id": "L00-R1A1", "kind": "A_member", "members": ["g", "b", "c", "d", "e", "f"], "changes": [{"out": "a", "in": "g"}]}]
    with tempfile.TemporaryDirectory() as d:
        p = IV.record_lineage(Path(d), "L00", variants)
        data = json.loads(p.read_text(encoding="utf-8"))
        assert len(data["nodes"]) == 1 and data["nodes"][0]["parent"] == "L00" and data["nodes"][0]["kind"] == "A_member"
        IV.record_lineage(Path(d), "L00", variants)
        assert len(json.loads(p.read_text(encoding="utf-8"))["nodes"]) == 2
    assert not hasattr(IV, "llm_hypotheses") and not hasattr(IV, "rule_mutations")
    print("test_lineage_helpers OK")


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
    test_lineage_helpers()
    test_selection_patterns()
