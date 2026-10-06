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


def test_direction_actual_members():
    """軸とメガは並びに実際に居る個体で書く (2026-09-25: arch_0924 の 1 位 L06_C020 は C020 のカイリューをリザードンに入れ替えた近傍)"""
    from tools.team_build.finalists import mega_holders
    ja = lambda s: {"dragonite": "カイリュー", "hydreigon": "サザンドラ", "charizard": "リザードン", "metagross": "メタグロス",
                    "kingambit": "ドドゲザン"}.get(s, s)
    fam = {"family_id": "C020", "core_ids": ["dragonite", "hydreigon"], "mega_id": "dragonite", "win_condition": "cycle_pressure"}
    members = ["charizard", "hippowdon", "hydreigon", "metagross", "primarina", "sneasler"]
    sets = [{"species": "charizard", "item": "charizarditey"}, {"species": "hydreigon", "item": "choicescarf"},
            {"species": "metagross", "item": "metagrossite"}]
    megas = mega_holders(sets, {"charizarditey", "metagrossite"})
    assert megas == ["charizard", "metagross"]
    d = direction_of(fam, members, megas=megas, origin={"kind": "mutation", "parent_concept": "C020", "swap": ["dragonite", "charizard"]})
    assert d["core_ids"] == ["hydreigon"] and d["core_missing"] == ["dragonite"]
    assert d["mega_id"] is None and d["mega_ids"] == ["charizard", "metagross"]
    label = direction_label_ja(d, ja=ja)
    assert label == "サイクルで圧をかける / 軸: サザンドラ (近傍: カイリュー → リザードン) / メガ石: リザードン+メタグロス (1 試合 1 体)", label
    # 由来が分からなければ「含まない」と書く。石が 1 個なら「メガ: 」
    d2 = direction_of(fam, members, megas=["charizard"])
    assert direction_label_ja(d2, ja=ja) == "サイクルで圧をかける / 軸: サザンドラ (系統の カイリュー は含まない) / メガ: リザードン"
    # 系統の core が全部居れば従来どおり (megas を渡さなければ系統の mega_id)
    d3 = direction_of(fam, ["dragonite", "hydreigon", "kingambit"])
    assert d3["core_missing"] == [] and direction_label_ja(d3, ja=ja).endswith("軸: カイリュー+サザンドラ / メガ: カイリュー")
    # 系統の mega が並びに居なければメガは書かない (megas も無い)
    d4 = direction_of(fam, ["hydreigon", "kingambit"])
    assert d4["mega_id"] is None and "メガ" not in direction_label_ja(d4, ja=ja)
    # pick_finalists にも通る
    picks = pick_finalists(["L06_C020"], {"L06_C020": 0.3}, {"L06_C020": members}, {"L06_C020": fam}, k=1, max_shared=3,
                           megas_by={"L06_C020": megas}, origins_by={"L06_C020": d["origin"]})
    assert picks[0]["direction"]["core_missing"] == ["dragonite"] and "近傍" in picks[0]["direction_ja"]
    print("test_direction_actual_members OK")


def test_crossover_and_imported_family():
    """交配 (L07_C028xC026) は親系統の軸の和、持ち込み (…_from_<run>) は元 run の系統"""
    from tools.team_build.finalists import imported_family, parent_families
    fams = [{"family_id": "C028", "core_ids": ["dragonite", "kingambit"], "mega_id": "dragonite", "win_condition": "priority_cleanup"},
            {"family_id": "C026", "core_ids": ["gallade", "slurpuff"], "mega_id": "gallade"}]
    assert family_of("L07_C028xC026", fams) is None
    assert [f["family_id"] for f in parent_families("L07_C028xC026", fams)] == ["C028", "C026"]
    assert parent_families("L06_C020", fams) == [] and parent_families("reference", fams) == []
    members = ["dragonite", "hippowdon", "gallade", "slurpuff", "primarina", "sneasler"]      # kingambit は落ちている
    d = direction_of(None, members, parents=parent_families("L07_C028xC026", fams),
                     origin={"kind": "crossover", "parents": ["C028", "C026"]})
    assert d["core_ids"] == ["dragonite", "gallade", "slurpuff"] and d["core_missing"] == ["kingambit"]
    assert d["family_id"] == "C028xC026" and d["source"] == "crossover" and d["mega_id"] == "dragonite"
    label = direction_label_ja(d, ja=lambda s: s)
    assert label == "交配 (C028×C026) / 軸: dragonite+gallade+slurpuff (系統の kingambit は含まない) / メガ: dragonite", label
    # 持ち込み: 元 run の families を loader で引く。読めなければ (None, [])
    loader = lambda rid: fams if rid == "arch_0918" else []
    fam, par = imported_family({"run_id": "arch_0918", "candidate_id": "L69_C028"}, loader)
    assert fam["family_id"] == "C028" and par == []
    fam, par = imported_family({"run_id": "arch_0918", "candidate_id": "L07_C028xC026"}, loader)
    assert fam is None and [f["family_id"] for f in par] == ["C028", "C026"]
    assert imported_family({"run_id": "missing", "candidate_id": "L69_C028"}, loader) == (None, [])
    assert imported_family(None, loader) == (None, [])
    def boom(rid):
        raise OSError("no such run")
    assert imported_family({"run_id": "x", "candidate_id": "L69_C028"}, boom) == (None, [])
    print("test_crossover_and_imported_family OK")


if __name__ == "__main__":
    test_family_of_and_shared()
    test_direction_label()
    test_pick_finalists_diverse()
    test_direction_actual_members()
    test_crossover_and_imported_family()
