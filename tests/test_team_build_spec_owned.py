"""所持の方針 (tools/team_build/spec: 今期の追加種を所持扱い、ブラックリスト方式。2026-09-18 ユーザー決定) の純粋関数テスト。

    python -m tests.test_team_build_spec_owned
"""
from __future__ import annotations

from tools.team_build import spec as SP

CURRENT = """\tsalamence: {tier: "OU"},
\tgolisopod: {tier: "OU"},
\tgarchomp: {tier: "OU"},
\tmew: {tier: "Illegal"},
\tarboliva: {tier: "OU"},
"""
PREVIOUS = """\tsalamence: {tier: "Illegal"},
\tgolisopod: {tier: "Illegal"},
\tgarchomp: {tier: "OU"},
\tmew: {tier: "Illegal"},
\tarboliva: {tier: "Illegal"},
"""


def test_illegal_and_new_species():
    assert SP.illegal_ids_from_text(CURRENT) == {"mew"}
    assert SP.illegal_ids_from_text(PREVIOUS) == {"mew", "salamence", "golisopod", "arboliva"}
    assert SP.new_species_from_texts(CURRENT, PREVIOUS) == ["arboliva", "golisopod", "salamence"]
    assert SP.new_species_from_texts(CURRENT, CURRENT) == []
    print("test_illegal_and_new_species OK")


def test_apply_owned_policy():
    owned = ["garchomp", "mimikyu", "golisopod"]
    new = ["arboliva", "golisopod", "salamence", "garchomp"]
    policy = {"new_species_owned": True, "blacklist": ["golisopod"]}
    out = SP.apply_owned_policy(owned, new, policy)
    assert out == ["garchomp", "mimikyu", "arboliva", "salamence"], out      # 登録 → 追加種の順、重複なし、ブラックリスト除外
    assert SP.apply_owned_policy(owned, new, {"new_species_owned": False, "blacklist": []}) == owned
    assert SP.apply_owned_policy(owned, new, {"new_species_owned": False, "blacklist": ["mimikyu"]}) == ["garchomp", "golisopod"]
    assert SP.apply_owned_policy([], new, policy) == ["arboliva", "salamence", "garchomp"]
    print("test_apply_owned_policy OK")


def test_owned_policy_file_missing():
    p = SP.owned_policy(SP.REPO / "config" / "no_such_policy.json")
    assert p == {"new_species_owned": False, "blacklist": []}
    print("test_owned_policy_file_missing OK")


if __name__ == "__main__":
    test_illegal_and_new_species()
    test_apply_owned_policy()
    test_owned_policy_file_missing()
