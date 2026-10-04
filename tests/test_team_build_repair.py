"""測定からの戻り (tools/team_build/repair) のテスト: 診断 (純粋) と修理モード (合成の相手プール・候補で閉じる)。

    python -m tests.test_team_build_repair
"""
from __future__ import annotations

import numpy as np

from tests import test_lineup_search as W
from tools.team_build import lineup_search as L
from tools.team_build import repair as RP
from tools.team_build.loss_stats import loss_stats


def _rec(won, fam, opp_sel, our_sel, ko=None, items=None, mega=None):
    return {"won": won, "opponent_family_id": fam, "opponent_selection": opp_sel, "our_selection": our_sel,
            "lead": {"ours": our_sel[0], "theirs": f"p2a: {opp_sel[0].capitalize()}"}, "ko_events": ko or [],
            "resource_usage": items or [], "mega_usage": mega or [], "turn_count": 10}


def test_diagnose():
    members = ["core1", "core2", "fillC", "fillD", "weak", "sun"]
    recs = []
    # F_A に 12 戦 10 敗 (負けに効いた系統)、F_B に 10 戦 8 敗、F_C に 10 戦 1 敗。相手 a1 に負け続け、core1 が a1 の Boom に 5 回倒される。
    # weak は一度も選出されない。fillD の きあいのタスキ は 1 回も発動しない
    for i in range(12):
        recs.append(_rec(i >= 10, "F_A", ["a1", "a2", "x"], ["core1", "core2", "fillD"],
                         ko=[{"fainted": "p1a: Core1", "by": "p2a: A1", "move": "Boom"}] if i < 5 else [],
                         items=[{"turn": 1, "target": "p1a: Core2", "item": "Sitrus Berry", "kind": "-enditem"}]))
    for i in range(10):
        recs.append(_rec(i >= 8, "F_B", ["b1", "b2", "y"], ["core1", "fillC", "fillD"]))
    for i in range(10):
        recs.append(_rec(i >= 1, "F_C", ["c1", "c2", "z"], ["core2", "fillC", "sun"]))
    st = loss_stats(recs, our_species=members)
    items_of = {"core1": "lifeorb", "core2": "sitrusberry", "fillD": "focussash", "fillC": "choicescarf"}
    consumed = RP.resource_counts(recs)
    assert consumed == {"core2": 12}
    d = RP.diagnose(st, members, items_of, consumed, min_n=20, loss_rate_min=0.5, unused_rate=0.05, ko_min_n=3, min_n_share=1.0)
    assert d["must_cover"] == ["F_A", "F_B"] and d["must_cover_n"] == 22 and d["must_cover_enough"] and d["evidence"] == 1.0
    assert d["threat_species"][:2] == ["a1", "a2"] and "c1" not in d["threat_species"]
    assert d["ko"] and d["ko"][0]["ours"] == "core1" and d["ko"][0]["by"] == "a1" and d["ko"][0]["n"] == 5
    assert d["vulnerable"] == ["core1"]
    assert d["replace_candidates"] == ["weak"]
    assert [u["species"] for u in d["unused_items"]] == ["fillD"] and d["unused_items"][0]["consumed"] == 0
    assert d["mega_review"] is False and len(d["notes"]) >= 4
    # 束ねた対戦数が下限に届かなくても must_cover は部分の証拠として残す (must_cover_enough=False、evidence = 届いた比率)
    d2 = RP.diagnose(st, members, items_of, consumed, min_n=40, min_n_share=1.0)
    assert d2["must_cover"] == ["F_A", "F_B"] and d2["must_cover_n"] == 22 and not d2["must_cover_enough"]
    assert d2["min_n"] == 32 and d2["evidence"] == round(22 / 32, 3) and "部分の証拠" in d2["notes"][0]
    # 下限は対戦数に相対 (min(min_n, n × share)): 32 戦 × 0.5 = 16 → F_A (12) + F_B (10) で届く。× 0.06 なら最小 5 → F_A だけ
    d3 = RP.diagnose(st, members, items_of, consumed, min_n=40, min_n_share=0.5)
    assert d3["min_n"] == 16 and d3["must_cover"] == ["F_A", "F_B"] and d3["must_cover_enough"]
    d4 = RP.diagnose(st, members, items_of, consumed, min_n=20)
    assert d4["min_n"] == 5 and d4["must_cover"] == ["F_A"] and d4["must_cover_enough"]
    # 系統の重みの引き上げ: must_cover と負けに効いた相手の居る系統
    pool = W._pool()
    fam_w = pool.family_matrix()[1]
    w, idx = RP.boosted_weights(fam_w, pool.families, pool.sets, ["B"], ["c1"], boost=2.0)
    assert idx == [1, 2] and float(w[1]) == 3.0 and float(w[2]) == 3.0 and float(w[0]) == 1.0
    assert np.allclose(fam_w, [1, 1, 1, 1])
    print("test_diagnose OK")


def test_repair_variants():
    """親の並びに対して、型だけの変種 (B) と入替 (A) を作る。エース・核・固定枠は入替えず、親より点が上がるものだけ"""
    s = W._search()
    cfg = L.SearchConfig(species_k=10, favorites=("core1",))
    pool_species = ["core1", "core2", "fillC", "fillD", "tune", "weak", "sun", "rain", "stone2"]
    ents = [W._entry("core1", "breaker", "core1_x", "lifeorb", False, {}),
            W._entry("core2", "sweeper_setup", "core2_stone", "core2ite", True, {}),
            W._entry("tune", "breaker", "tune_x", "expertbelt", False, {}),
            W._entry("weak", "breaker", "weak_x", "leftovers", False, {}),
            W._entry("sun", "sun_setter", "sun_x", "heatrock", False, {"weather": "sun"}),
            W._entry("rain", "rain_setter", "rain_x", "damprock", False, {"weather": "rain"})]
    combo = [s.lib.add(e) for e in ents]
    sc, _ = s.score_of(combo, [], cfg)
    parent = s._finalize(sc, combo, {}, "C001", [], cfg)
    # 診断: 系統 C / D が穴、weak は選出されない、tune が c1 に倒される
    diag = {"must_cover": ["C", "D"], "threat_species": ["c1"], "ko": [{"ours": "tune", "by": "c1", "move": "x", "n": 4}],
            "vulnerable": ["tune"], "replace_candidates": ["weak"], "unused_items": [], "mega_review": False, "notes": ["t"]}
    vs = RP.repair_variants(s, parent, diag, cfg, pool_species, W._roles_of, [], fixed={"core1", "core2"}, parent_id="L01_C001",
                            round_no=1, max_changes=2, max_arms=6, boost=2.0, min_gain=0.001)
    assert vs, "変種が出る"
    kinds = [v.origin["variant"] for v in vs]
    assert "B" in kinds and "A" in kinds, kinds
    for v in vs:
        assert v.tag == "repair" and v.origin["kind"] == "repair" and v.origin["parent"] == "L01_C001" and v.origin["round"] == 1
        assert v.origin["repair_score"] > v.origin["parent_repair_score"]
        assert {"core1", "core2"} <= set(v.members), "固定した個体は残る"
        assert sum(1 for e in v.entries if e.stone) == 1
        items = [e.item for e in v.entries if e.item]
        assert len(items) == len(set(items))
    b = next(v for v in vs if v.origin["variant"] == "B")
    assert b.origin["changes"][0]["species"] == "tune" and b.members == parent.members
    assert [e.key[0] for e in b.entries if e.species_id == "tune"] == ["tune_t"], "担当向けの型に変わる"
    a = next(v for v in vs if v.origin["variant"] == "A")
    assert a.origin["changes"][0]["out"] == "weak" and "weak" not in a.members
    assert a.origin["changes"][0]["in"] in ("fillC", "fillD")
    # 「c1 に倒される」への受け: 入替先は c1 の型への被覆が BUILD_REPAIR_ANSWER_MIN 以上の種 (fillC は c 0.9、fillD は d だけ → fillC)
    a_in = [v.origin["changes"][0]["in"] for v in vs if v.origin["variant"] == "A" and len(v.origin["changes"]) == 1]
    assert a_in and all(x == "fillC" for x in a_in), a_in
    assert all(c.get("answers_ko") for v in vs if v.origin["variant"] == "A" for c in v.origin["changes"] if "in" in c)
    # 入替先を散らす: ko の無い診断では、入れる種が初出の変種が同じ種の 2 つ目より先に並ぶ
    vs_d = RP.repair_variants(s, parent, dict(diag, ko=[], vulnerable=[]), cfg, pool_species, W._roles_of, [],
                              fixed={"core1", "core2"}, parent_id="L01_C001", round_no=1, max_changes=2, max_arms=6, boost=2.0,
                              min_gain=0.001)
    ins_d = [tuple(sorted(c["in"] for c in v.origin["changes"] if "in" in c)) for v in vs_d if v.origin["variant"] == "A"]
    first_seen = []
    for x in ins_d:
        if x not in first_seen:
            first_seen.append(x)
    assert len(first_seen) >= 2, ins_d
    # 上限と順序: 点の降順、最大 max_arms
    assert [v.origin["repair_score"] for v in vs] == sorted((v.origin["repair_score"] for v in vs), reverse=True)
    vs2 = RP.repair_variants(s, parent, diag, cfg, pool_species, W._roles_of, [], fixed={"core1", "core2"}, parent_id="L01_C001",
                             round_no=1, max_changes=2, max_arms=1, boost=2.0, min_gain=0.001)
    assert len(vs2) == 1
    # 差し替え対象が無ければ担当の少ない個体 (固定でない) を入替える。重みは元に戻っている
    diag2 = dict(diag, replace_candidates=[])
    vs3 = RP.repair_variants(s, parent, diag2, cfg, pool_species, W._roles_of, [], fixed={"core1", "core2"}, parent_id="L01_C001",
                             round_no=2, max_changes=1, max_arms=6, boost=2.0, min_gain=0.001)
    assert any(v.origin["variant"] == "A" for v in vs3)
    assert np.allclose(s.fam_w, [1, 1, 1, 1])
    # 親が石 2 個 (現行チーム) でエース指定があっても、その親の修理ではエースの規則を当てず石の数は親のまま (変種が出る。新しい石は足さない)
    ents2 = [W._entry("core1", "breaker", "core1_x", "lifeorb", False, {}),
             W._entry("core2", "sweeper_setup", "core2_stone", "core2ite", True, {}),
             W._entry("stone2", "breaker", "stone2_s", "stone2ite", True, {}),
             W._entry("weak", "breaker", "weak_x", "leftovers", False, {}),
             W._entry("sun", "sun_setter", "sun_x", "heatrock", False, {"weather": "sun"}),
             W._entry("rain", "rain_setter", "rain_x", "damprock", False, {"weather": "rain"})]
    for e in ents2:
        e.locked = True
    combo2 = [s.lib.add(e) for e in ents2]
    sc2, _ = s.score_of(combo2, [], cfg)
    parent2 = s._finalize(sc2, combo2, {}, "INC", [], cfg, tag="incumbent")
    cfg_ace = L.SearchConfig(species_k=10, ace="core2", favorites=("core2",))
    vs5 = RP.repair_variants(s, parent2, dict(diag, replace_candidates=["weak"]), cfg_ace, pool_species, W._roles_of, [],
                             fixed={"core2"}, parent_id="L00_INC", round_no=1, max_changes=1, max_arms=6, boost=2.0, min_gain=0.001)
    assert vs5, "石 2 個の現行チームからも変種が出る"
    for v in vs5:
        assert sum(1 for e in v.entries if e.stone) == 2 and "weak" not in v.members
    # 外した種は候補に戻さない: 親で C を見ている fillC を外すと、最良の候補は fillC 自身だが戻さず別の種を入れる。
    # 親と同じ型の組は変種にしない (戻せる候補が無ければ変種なし)
    ents3 = [W._entry("core1", "breaker", "core1_x", "lifeorb", False, {}),
             W._entry("core2", "sweeper_setup", "core2_x", "sitrusberry", False, {}),
             W._entry("fillC", "breaker", "fillC_x", "choicescarf", False, {}),
             W._entry("fillD", "hazard_lead", "fillD_x", "focussash", False, {}),
             W._entry("weak", "breaker", "weak_x", "leftovers", False, {}),
             W._entry("sun", "sun_setter", "sun_x", "heatrock", False, {"weather": "sun"})]
    combo3 = [s.lib.add(e) for e in ents3]
    sc3, _ = s.score_of(combo3, [], cfg)
    parent3 = s._finalize(sc3, combo3, {}, "C001", [], cfg)
    diag3 = dict(diag, replace_candidates=["fillC"], vulnerable=[], must_cover=["C"], threat_species=["c1"], ko=[])
    vs6 = RP.repair_variants(s, parent3, diag3, cfg, pool_species, W._roles_of, [], fixed={"core1", "core2"}, parent_id="L02_C001",
                             round_no=2, max_changes=1, max_arms=6, boost=2.0, min_gain=0.001)
    pkey = tuple(sorted(e.key for e in parent3.entries))
    for v in vs6:
        assert tuple(sorted(e.key for e in v.entries)) != pkey, "親と同じ変種は作らない"
        if v.origin["variant"] == "A":
            assert "fillC" not in v.members, "外した種を戻さない"
    # 点が上がる変種が無くても最良の 1 つは forced で測る
    vs4 = RP.repair_variants(s, parent, diag, cfg, pool_species, W._roles_of, [], fixed={"core1", "core2"}, parent_id="L01_C001",
                             round_no=1, max_changes=2, max_arms=6, boost=2.0, min_gain=10.0)
    assert len(vs4) == 1 and vs4[0].origin["forced"] is True and all(v.origin["forced"] is False for v in vs)
    assert RP.variant_id("L01_C001", 1, "B", 2) == "L01_C001-R1B2"
    print("test_repair_variants OK")


def main() -> None:
    test_diagnose()
    test_repair_variants()
    print("ALL OK")


if __name__ == "__main__":
    main()
