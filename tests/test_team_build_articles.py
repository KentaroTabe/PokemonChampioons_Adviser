"""記事 → structured claims のテスト (モック LLM)。

    python -m tests.test_team_build_articles
"""
import json

from tools.team_build import articles as A
from tools.team_build.llm.provider import MockProvider


def test_articles():
    legal = {"kingambit", "garchomp", "primarina", "gengar"}
    good = json.dumps({"authoritative": {"claims": [{"species_ids": ["kingambit", "garchomp", "gengar"],
                                                      "roles": {"kingambit": "sweeper"},
                                                      "matchups": [{"vs": "primarina", "result": "unfavorable"}],
                                                      "selection_patterns": [["kingambit", "garchomp", "gengar"]]}]},
                       "display": {"summary": "..."}})
    claims = A.extract_claims(MockProvider([good]), "記事本文", legal)
    assert claims and claims[0]["species_ids"][0] == "kingambit"
    cores = A.claims_to_cores(claims, owned={"kingambit", "garchomp"})
    assert cores and cores[0]["core_ids"] == ["garchomp", "kingambit"] and cores[0]["source"] == "article"
    assert A.validate_claims({"claims": [{"species_ids": ["zzz"], "matchups": [{"vs": "x", "result": "?"}]}]}, legal)
    print("test_articles OK")


if __name__ == "__main__":
    test_articles()
