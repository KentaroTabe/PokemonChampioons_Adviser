"""複数の方向性の最終候補 (tools/team_build/finalists、2026-09-17)。

    python -m tests.test_team_build_finalists
"""
from tools.team_build.finalists import (direction_label_ja, direction_of, family_of, pick_finalists, shared_members,
                                        skipped_as_similar)


def test_family_of_and_shared():
    fams = [{"family_id": "C003", "win_condition": "setup_sweep"}, {"family_id": "C026", "win_condition": "priority_cleanup"}]
    assert family_of("L07_C026", fams)["win_condition"] == "priority_cleanup"
    assert family_of("L26_C003_from_rule_0910", fams) is None          # 持ち込んだ並びは別 run の系統
    assert family_of("reference", fams) is None
    assert shared_members(["a", "b", "c"], ["c", "b", "z"]) == 2 and shared_members([], None) == 0
    print("test_family_of_and_shared OK")


def test_direction_label():
    d = direction_of({"win_condition": "setup_sweep", "source": "llm:offense", "core_ids": ["delphox", "garchomp"],
                      "mega_id": "delphox", "family_id": "C003"}, ["delphox", "garchomp", "kingambit"])
    label = direction_label_ja(d, ja=lambda s: {"delphox": "マフォクシー", "garchomp": "ガブリアス"}.get(s, s))
    assert label == "積んで全抜き / 攻め (LLM) / 軸: マフォクシー+ガブリアス / メガ: マフォクシー", label
    assert direction_label_ja(direction_of(None, []), ja=lambda s: s) == "方向性ラベルなし"
    assert direction_label_ja(direction_of({"win_condition": "mystery", "source": "rule:mega"}, []), ja=lambda s: s) == "mystery / メガ軸"
    print("test_direction_label OK")


def test_pick_finalists_diverse():
    members = {
        "A": ["delphox", "garchomp", "kingambit", "mimikyu", "rotomwash", "sneasler"],
        "B": ["delphox", "garchomp", "kingambit", "mimikyu", "primarina", "sneasler"],   # A と 5 体共通 → 近い
        "C": ["charizard", "gallade", "hippowdon", "hydreigon", "polteageist", "primarina"],   # 別方向
        "D": ["delphox", "charizard", "kingambit", "hydreigon", "rotomwash", "staraptor"],     # A と 3 体 / C と 2 体 → 違う
        "E": ["delphox", "garchomp", "kingambit", "mimikyu", "rotomwash", "primarina"],    # A と 5 体共通
    }
    deltas = {"A": 0.09, "B": 0.08, "C": 0.05, "D": 0.04, "E": 0.03}
    fams = {"A": {"win_condition": "setup_sweep", "source": "rule:mega", "core_ids": ["delphox"]},
            "C": {"win_condition": "offense_trade", "source": "llm:offense", "core_ids": ["charizard"]}}
    eligible = ["A", "B", "C", "D", "E"]
    picks = pick_finalists(eligible, deltas, members, fams, k=3, max_shared=3)
    assert [p["candidate_id"] for p in picks] == ["A", "C", "D"], picks
    assert [p["rank"] for p in picks] == [1, 2, 3] and picks[0]["delta_s10"] == 0.09
    assert picks[0]["direction"]["win_condition"] == "setup_sweep" and picks[2]["direction"]["win_condition"] is None
    skipped = skipped_as_similar(eligible, picks, members, 3)
    assert [s["candidate_id"] for s in skipped] == ["B", "E"] and skipped[0]["similar_to"] == ["A"]
    # k=1 なら勝者だけ、k=0 なら空。eligible が空なら空
    assert [p["candidate_id"] for p in pick_finalists(eligible, deltas, members, fams, k=1, max_shared=3)] == ["A"]
    assert pick_finalists(eligible, deltas, members, fams, k=0, max_shared=3) == []
    assert pick_finalists([], deltas, members, fams, k=3, max_shared=3) == []
    # 共通メンバーの上限を緩めれば B も入る
    assert [p["candidate_id"] for p in pick_finalists(eligible, deltas, members, fams, k=3, max_shared=5)] == ["A", "B", "C"]
    print("test_pick_finalists_diverse OK")


if __name__ == "__main__":
    test_family_of_and_shared()
    test_direction_label()
    test_pick_finalists_diverse()
