"""tools/diag_scenes (40 局面の診断、M3b) の純粋な部分のテスト: 区分・抽出・照合・順位差の分解・回帰ケースの合成・描画。

    python -m tests.test_diag_scenes
"""
from __future__ import annotations

from tools.diag_scenes import (a_bucket, agree_margin, check_inputs, compare_ranking, decompose, fallback_bucket,
                               iter_decisions, legacy_assumption, regression_expect, regression_kinds_of, render_markdown,
                               remaining_of, repro_verdict, sample_scenes, scene_status, synth_state)

CATS = {"closecombat": "Physical", "swordsdance": "Status", "stealthrock": "Status", "explosion": "Physical",
        "flamethrower": "Special"}


def move_cat(mid):
    return CATS.get(mid)


def _mv(i, score=1.0, **kw):
    return dict({"kind": "move", "id": i, "score": score}, **kw)


def _sw(i, score=1.0, **kw):
    return dict({"kind": "switch", "id": i, "score": score}, **kw)


def _out(mode, actions, fallback=None, ok=True):
    return {"ok": ok, "scoring_mode": mode, "scoring_fallback": fallback, "actions": actions,
            "best": actions[0] if actions else None}


def _state(my_rem=3, opp_fainted=0, picked=(True, True, True, False), legal_moves=("closecombat", "swordsdance")):
    party = [{"species_id": s, "is_picked": p, "status": None, "hp_percent": 100.0,
              "moves": [{"move_id": m} for m in ("closecombat", "swordsdance", "stealthrock")]}
             for s, p in zip(("lucario", "garchomp", "rotomwash", "gholdengo"), picked)]
    party[0]["legal_move_ids"] = list(legal_moves)
    party[0]["move_block_reasons"] = {}
    opp = [{"species_id": "kingambit", "hp_percent": 60.0, "status": None},
           {"species_id": "dragonite", "status": "fainted" if opp_fainted else None}]
    return {"player": {"active_index": 0, "party": party, "remaining": my_rem},
            "opponent": {"active_index": 0, "party": opp, "team_size": 3}}


def test_status_and_buckets():
    diag = {"advice": _out("transition", [_mv("closecombat"), _sw("garchomp")]),
            "other_mode": _out("legacy", [_sw("garchomp"), _mv("closecombat")])}
    st = scene_status(diag)
    assert st["status"] == "differ" and st["a_L"] == ("switch", "garchomp") and st["a_T"] == ("move", "closecombat")
    diag["advice"] = _out("transition", [_sw("garchomp"), _mv("closecombat")], fallback="budget")
    st = scene_status(diag)
    assert st["status"] == "fallback" and st["bucket"] == "B_budget"
    diag["advice"] = _out("transition", [_sw("garchomp"), _mv("closecombat")])
    assert scene_status(diag)["status"] == "agree"
    diag["advice"] = _out("transition", [_sw("garchomp")])
    assert scene_status(diag)["why"] == "single_candidate"
    assert scene_status({"advice": _out("transition", [], ok=False)})["why"] == "no_advice"
    assert fallback_bucket("opp_unevaluated_ambiguous") == "B_unevaluated"
    assert fallback_bucket("unevaluated_ambiguous") == "B_unevaluated"
    assert fallback_bucket("unsupported:effect_unmodeled:trick") == "B_own_unsupported"
    assert fallback_bucket("opp_unsupported:x:y") == "B_other" and fallback_bucket("hp_range") == "B_other"
    print("test_status_and_buckets OK")


def test_a_bucket():
    L, T = ("switch", "garchomp"), ("move", "closecombat")
    leg = {L: _sw("garchomp", reason="被ダメ予測 30%"), T: _mv("closecombat", reason="予測ダメージ 40〜48% 等倍")}
    tr = {L: _sw("garchomp", branches={"p_faint": 0.0}), T: _mv("closecombat", branches={"p_ko_before_act": 0.1})}
    assert a_bucket(L, T, leg, tr, (3, 3), move_cat) == "A_attack_switch"
    # 変化技が絡む (優先)
    S = ("move", "swordsdance")
    assert a_bucket(L, S, leg, tr, (1, 1), move_cat) == "A_status"
    # 攻撃側が先に倒される見込み (transition) → 捨てる判断
    tr2 = {**tr, T: _mv("closecombat", branches={"p_ko_before_act": 0.7})}
    assert a_bucket(L, T, leg, tr2, (3, 3), move_cat) == "A_sacrifice_endgame"
    # legacy の ×0.25 でも同じ
    leg2 = {**leg, T: _mv("closecombat", reason="予測ダメージ 40〜48% / 行動前に倒される見込み (先制技/交代を優先)")}
    assert a_bucket(L, T, leg2, tr, (3, 3), move_cat) == "A_sacrifice_endgame"
    # 終盤 (両方の残りが 2 以下)
    assert a_bucket(L, T, leg, tr, (2, 2), move_cat) == "A_sacrifice_endgame"
    assert a_bucket(L, T, leg, tr, (2, 3), move_cat) == "A_attack_switch"
    # 自滅
    E = ("move", "explosion")
    leg3 = {**leg, E: _mv("explosion", self_cost={"kind": "selfko", "faints": True})}
    assert a_bucket(T, E, leg3, tr, (3, 3), move_cat) == "A_sacrifice_endgame"
    # 攻撃 対 攻撃 / 交代 対 交代
    F = ("move", "flamethrower")
    assert a_bucket(T, F, leg, tr, (3, 3), move_cat) == "A_other"
    assert a_bucket(L, ("switch", "rotomwash"), leg, tr, (3, 3), move_cat) == "A_other"
    st = _state(my_rem=2, opp_fainted=1)
    assert remaining_of(st) == (2, 2)
    print("test_a_bucket OK")


def test_sample():
    pools = {"A_status": [f"b00d{i:02d}" for i in range(20)], "B_budget": ["b01d00", "b02d00"], "C_agree": ["x"] * 0}
    q = {"A_status": 8, "B_budget": 3, "C_agree": 8}
    c1, s1 = sample_scenes(pools, q, 20261015)
    c2, s2 = sample_scenes(dict(pools, A_status=list(reversed(pools["A_status"]))), q, 20261015)
    assert c1 == c2 and len(c1["A_status"]) == 8 and c1["A_status"] == sorted(c1["A_status"])
    assert c1["B_budget"] == ["b01d00", "b02d00"] and s1 == {"B_budget": 1, "C_agree": 8}
    c3, _ = sample_scenes(pools, q, 1)
    assert c3["A_status"] != c1["A_status"]
    print("test_sample OK")


def test_compare_and_repro():
    rec = [_mv("a", 50.0), _sw("b", 40.0)]
    assert compare_ranking(rec, [_mv("a", 50.0), _sw("b", 40.0)]) == {"same_order": True, "same_scores": True,
                                                                      "max_abs_diff": 0.0}
    c = compare_ranking(rec, [_mv("a", 50.0), _sw("b", 40.5)])
    assert c["same_order"] and not c["same_scores"] and abs(c["max_abs_diff"] - 0.5) < 1e-9
    assert not compare_ranking(rec, [_sw("b", 40.0), _mv("a", 50.0)])["same_order"]
    ok = {"actions": rec}
    assert repro_verdict(ok, {"actions": list(rec)}, "legacy")["status"] == "ok"
    assert repro_verdict({"actions": rec, "scoring_fallback": "budget"}, {"actions": [_mv("x")]},
                         "transition")["status"] == "time_dependent"
    fb = {"actions": rec, "scoring_fallback": "unsupported:effect_unmodeled:trick"}
    assert repro_verdict(fb, dict(fb), "transition")["status"] == "fallback_same"
    assert repro_verdict(fb, {"actions": rec, "scoring_fallback": None}, "transition")["status"] == "mismatch"
    assert repro_verdict(ok, {"actions": [_mv("a", 51.0), _sw("b", 40.0)]}, "transition")["status"] == "mismatch"
    print("test_compare_and_repro OK")


def test_check_inputs():
    st = _state()
    legal = {"moves": ["closecombat", "swordsdance"], "switches": ["garchomp", "rotomwash"]}
    good = {"legacy": {"actions": [_mv("closecombat"), _sw("garchomp")]},
            "transition": {"actions": [_sw("rotomwash"), _mv("swordsdance")]}}
    inp = {"bench": ["garchomp", "rotomwash"], "my_unknown": 0}
    assert check_inputs(st, legal, good, inp) == []
    bad = {"legacy": {"actions": [_mv("stealthrock"), _sw("gholdengo")]}}
    probs = check_inputs(st, legal, bad, {"bench": ["garchomp", "rotomwash", "gholdengo"], "my_unknown": 1})
    assert any("stealthrock" in p for p in probs)
    assert any("未選出の gholdengo が候補" in p for p in probs)
    assert any("V の残存戦力" in p for p in probs) and any("my_unknown = 1" in p for p in probs)
    print("test_check_inputs OK")


def test_legacy_assumption():
    a = legacy_assumption(_mv("x", reason="予測ダメージ 55〜65% 抜群 (命中90) / 行動前に倒される見込み (先制技/交代を優先)"), 60.0)
    assert a["kba"] and abs(a["ko"] - 0.45) < 1e-9
    assert legacy_assumption(_mv("x", reason="予測ダメージ 60〜70% 等倍"), 60.0)["ko"] == 1.0
    assert legacy_assumption(_mv("x", reason="予測ダメージ 10〜12% 等倍"), 60.0)["ko"] == 0.0
    assert legacy_assumption(_mv("x", reason="積み技"), 60.0)["ko"] is None
    s = legacy_assumption(_sw("y", reason="被ダメ予測 120% / 交代後の打点 約30% / 交代出しで倒される危険あり", incoming=120.0), 60.0)
    assert s["dies"] and s["incoming"] == 120.0
    print("test_legacy_assumption OK")


def test_decompose():
    L, T = ("move", "closecombat"), ("switch", "garchomp")
    leg = {L: _mv("closecombat", 80.0, reason="予測ダメージ 70〜80% 等倍 / 先手で倒せる見込み"),
           T: _sw("garchomp", 30.0, reason="被ダメ予測 20%")}
    # (i) legacy は撃てる前提、transition は先に倒される
    tr = {L: _mv("closecombat", q=-0.2, branches={"p_ko_before_act": 0.8, "p_ko": 0.2}),
          T: _sw("garchomp", q=0.1, branches={"p_faint": 0.0})}
    d = decompose(L, T, leg, tr, 60.0)
    assert d["cause"] == "transition" and abs(d["dq"] - 0.3) < 1e-9, d
    assert any("先に倒される 80%" in e for e in d["evidence"])
    # RL 加点を外すと legacy も a_T → (ii)
    assert decompose(L, T, leg, tr, 60.0, legacy_norl_best=T)["cause"] == "valuation"
    # 食い違い無し → (ii)
    tr2 = {L: _mv("closecombat", q=-0.2, branches={"p_ko_before_act": 0.1, "p_ko": 0.9}),
           T: _sw("garchomp", q=0.1, branches={"p_faint": 0.0})}
    d2 = decompose(L, T, leg, tr2, 60.0)
    assert d2["cause"] == "valuation", d2
    # 両方向 → 判断保留 (a_T の交代先が着地で倒される: legacy は耐える前提 = a_T を高く見積もった)
    tr3 = {L: _mv("closecombat", q=-0.2, branches={"p_ko_before_act": 0.8, "p_ko": 0.2}),
           T: _sw("garchomp", q=0.1, branches={"p_faint": 0.9})}
    assert decompose(L, T, leg, tr3, 60.0)["cause"] == "pending"
    # 未評価の質量
    tr4 = {**tr2, T: _sw("garchomp", q=0.1, branches={}, unevaluated_mass=0.4)}
    assert decompose(L, T, leg, tr4, 60.0)["cause"] == "unevaluated"
    # 候補の欠け
    assert decompose(L, T, leg, {L: tr[L]}, 60.0)["cause"] == "pending"
    # legacy が a_T を ×0.25 で低く見積もったが transition では撃てる (説明する向き)
    leg5 = {L: _mv("closecombat", 20.0, reason="予測ダメージ 70〜80% / 行動前に倒される見込み (先制技/交代を優先)"),
            ("switch", "x"): _sw("x", 30.0, reason="被ダメ予測 20%")}
    tr5 = {L: _mv("closecombat", q=0.3, branches={"p_ko_before_act": 0.1, "p_ko": 0.9}),
           ("switch", "x"): _sw("x", q=0.0, branches={"p_faint": 0.0})}
    d5 = decompose(("switch", "x"), L, leg5, tr5, 60.0)
    assert d5["cause"] == "transition", d5
    print("test_decompose OK")


def test_margin():
    m = agree_margin([_mv("a", 80.0), _mv("b", 40.0)], [_mv("a", q=0.3), _mv("b", q=0.1)])
    assert m["label"] == "差が大きい" and m["gap_q"] == 0.2 and m["gap_legacy"] == 40.0
    m = agree_margin([_mv("a", 80.0), _mv("b", 78.0)], [_mv("a", q=0.3), _mv("b", q=0.1)])
    assert m["label"] == "僅差"
    m = agree_margin([_mv("a", 80.0), _mv("b", 40.0)], [_mv("a", q=0.3), _mv("b", q=0.29)])
    assert m["label"] == "僅差"
    print("test_margin OK")


def test_regression_synth():
    from champions_agent.env.advisor_player import SIM_REASON_LOCKED, SIM_REASON_TRAPPED
    st = _state()
    p = synth_state(st, "pivot")
    assert p["pending_pivot_switch"] and p["player"]["party"][0]["legal_move_ids"] == []
    assert "pivot" in regression_kinds_of(p) and "pending_pivot_switch" not in st
    lk = synth_state(st, "locked")
    act = lk["player"]["party"][0]
    assert act["legal_move_ids"] == ["closecombat"] and act["move_block_reasons"]["swordsdance"] == SIM_REASON_LOCKED
    assert "locked" in regression_kinds_of(lk)
    tp = synth_state(st, "trapped")
    assert tp["player"]["legal_switch_indices"] == [] and tp["player"]["switch_block_reason"] == SIM_REASON_TRAPPED
    assert "trapped" in regression_kinds_of(tp)
    pm = synth_state(st, "picked_missing")
    assert all(x["is_picked"] for x in pm["player"]["party"]) and pm["player"]["remaining"] == 4
    assert not st["player"]["party"][3]["is_picked"]
    outs = {"legacy": {"actions": [_mv("closecombat"), _sw("garchomp")]}}
    assert regression_expect("pivot", p, outs) == ["legacy: 交代だけの場面で技が候補"]
    assert regression_expect("trapped", tp, outs) == ["legacy: とらわれで交代が候補"]
    assert regression_expect("locked", lk, {"legacy": {"actions": [_mv("swordsdance")]}})
    assert regression_expect("locked", lk, outs) == []
    print("test_regression_synth OK")


def test_iter_and_render():
    diag = {"advice": _out("transition", [_mv("closecombat"), _sw("garchomp")]),
            "other_mode": _out("legacy", [_sw("garchomp"), _mv("closecombat")]), "state": _state()}
    recs = [{"battle_id": "x", "diag_own_team_text": "T", "our_selection": ["lucario"],
             "turns": [{"turn": 1}, {"turn": 2, "diag": diag}]}]
    decs = iter_decisions(recs)
    assert [d["key"] for d in decs] == ["b00d01"] and decs[0]["own_team_text"] == "T"
    sc = {"turn": 2, "board": "lucario 100% / kingambit 60%", "a_L": ("switch", "garchomp"),
          "a_T": ("move", "closecombat"), "repro": {"legacy": "ok", "transition": "ok"}, "input_problems": [],
          "decompose": {"cause": "transition", "dq": 0.3, "evidence": ["根拠"], "notes": []}}
    doc = {"conditions": {"seed": 1}, "n_battles": 1, "n_decisions": 2, "n_diag": 1, "accept": {"best_illegal": 0},
           "accept_violations": [], "rules": ["規則"], "pool_sizes": {"A_attack_switch": 1}, "quotas": {"A_attack_switch": 8},
           "chosen": {"A_attack_switch": ["b00d01"]}, "short": {"A_attack_switch": 7}, "scenes": {"b00d01": sc},
           "regression": [{"kind": "pivot", "source": "合成", "key": "b00d01", "problems": [], "candidates": "-"}],
           "aggregate": {"lines": ["集計"]}, "unconfirmed": ["未確認"]}
    md = render_markdown(doc)
    assert "| b00d01 | 2 |" in md and "(i) 遷移の違い" in md and "交代:garchomp" in md
    assert "| A: 攻撃 対 交代 | 1 | 8 | 1 | 7 |" in md
    assert "全項目 0 (合格)" in md and "交代技の後の繰り出し" in md
    assert md.index("## 1. 要点 (集計)") < md.index("## 2. 条件") and "- 集計" in md
    print("test_iter_and_render OK")


def test_decompose_refinements():
    L, T = ("move", "closecombat"), ("switch", "garchomp")
    leg = {L: _mv("closecombat", 80.0, reason="予測ダメージ 70〜80% 等倍"), T: _sw("garchomp", 30.0, reason="被ダメ予測 20%")}
    # Q が同点 → 判断保留 (RL の規則より先)
    tr = {L: _mv("closecombat", q=-0.9, branches={"p_ko_before_act": 0.0, "p_ko": 1.0}),
          T: _sw("garchomp", q=-0.9, branches={"p_faint": 0.0})}
    d = decompose(L, T, leg, tr, 60.0, legacy_norl_best=T)
    assert d["cause"] == "pending" and "同点" in d["evidence"][0], d
    # 倒せる見込みは「行動できた分岐」で比べる: p_ko 0.4 / (1 − p_kba 0.6) = 1.0 → legacy の 100% と食い違わない
    tr2 = {L: _mv("closecombat", q=-0.2, branches={"p_ko_before_act": 0.6, "p_ko": 0.4}),
           T: _sw("garchomp", q=0.1, branches={"p_faint": 0.0})}
    ev = decompose(L, T, leg, tr2, 60.0)["evidence"]
    assert not any("倒せる見込み" in e for e in ev) and any("先に倒される 60%" in e for e in ev), ev
    # 変化技の −15 (倒される危険あり) は先に倒される前提として読む
    a = legacy_assumption(_mv("swordsdance", reason="積み技 / 相手の攻撃で倒される危険あり"), 60.0)
    assert a["kba"] and a["ko"] is None
    # 変化技が絡む (ii) には固定点の注記
    S = ("move", "swordsdance")
    leg3 = {S: _mv("swordsdance", 40.0, reason="積み技"), T: _sw("garchomp", 30.0, reason="被ダメ予測 20%")}
    tr3 = {S: _mv("swordsdance", q=0.0, branches={"p_ko_before_act": 0.0}), T: _sw("garchomp", q=0.2, branches={})}
    d3 = decompose(S, T, leg3, tr3, 60.0, move_cat=move_cat)
    assert d3["cause"] == "valuation" and any("固定点" in n for n in d3["notes"]), d3
    # 複数の組の鍵
    recs = [{"turns": [{"turn": 1, "diag": {"state": {}}}]}]
    assert iter_decisions(recs, prefix="rl:")[0]["key"] == "rl:b00d00" and iter_decisions(recs, prefix="rl:")[0]["set"] == "rl"
    # 両方の候補に同じ食い違い (legacy が両方を先に倒される前提) は相殺 → (ii)
    leg6 = {L: _mv("closecombat", 20.0, reason="予測ダメージ 10〜12% / 行動前に倒される見込み (先制技/交代を優先)"),
            ("move", "swordsdance"): _mv("swordsdance", 10.0, reason="積み技 / 相手の攻撃で倒される危険あり")}
    tr6 = {L: _mv("closecombat", q=-0.5, branches={"p_ko_before_act": 0.0, "p_ko": 0.0}),
           ("move", "swordsdance"): _mv("swordsdance", q=-0.4, branches={"p_ko_before_act": 0.0})}
    d6 = decompose(L, ("move", "swordsdance"), leg6, tr6, 60.0)
    assert d6["cause"] == "valuation" and any("相殺" in n for n in d6["notes"]), d6
    print("test_decompose_refinements OK")


if __name__ == "__main__":
    test_status_and_buckets()
    test_a_bucket()
    test_sample()
    test_compare_and_repro()
    test_check_inputs()
    test_legacy_assumption()
    test_decompose()
    test_decompose_refinements()
    test_margin()
    test_regression_synth()
    test_iter_and_render()
