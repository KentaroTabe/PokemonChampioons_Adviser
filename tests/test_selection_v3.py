"""選出モデル v3 特徴量 (メガシンカ込み) と探索の相手メガ分岐のテスト。使用率は引数で渡し DB を読まない。

    python -m tests.test_selection_v3
"""
from __future__ import annotations

import numpy as np

from champions_agent.agent import selection_features_v3 as V3
from champions_agent.agent.selection_model import FEATURE_DIM

USAGE = {"charizard": {"charizarditey": 60.0, "charizarditex": 30.0}, "delphox": {"delphoxite": 99.0}}
MY = ["delphox", "garchomp", "kingambit", "mimikyu", "primarina", "sneasler"]
OPP = ["charizard", "garchomp", "gengar", "corviknight", "gholdengo", "dragonite"]


def test_features_v3():
    sets = {"delphox": {"item": "delphoxite"}, "garchomp": {"item": "focussash"}}
    sm = V3.own_stone_map(MY, sets)
    assert sm["delphox"] == "delphoxmega" and sm["garchomp"] is None and sm["mimikyu"] is None
    assert V3.own_stone_map(["charizard"], {"charizard": {"item": "garchompite"}})["charizard"] is None   # 他種の石は無効
    x = V3.build_features_v3(MY, OPP, (0, 1, 2), stone_map=sm, usage=USAGE)
    assert x.shape == (V3.FEATURE_DIM_V3,) and x.dtype == np.float32 and V3.FEATURE_DIM_V3 == FEATURE_DIM + 6
    scal = x[FEATURE_DIM:]
    assert list(scal[:3]) == [1.0, 0.0, 0.0] and scal[3] == 0.0             # 選出 3 体の石フラグ、控えの石持ち数
    probs = V3.opp_stone_probs(OPP, USAGE)
    assert abs(probs[0] - 0.9) < 1e-9 and abs(scal[5] - max(probs)) < 1e-6 and abs(scal[4] - float(np.mean(probs))) < 1e-6
    # 相手の埋め込みは素とメガ後の混合 (メガ後の埋め込みがあれば)。石を持たない種は素のまま
    e_base = V3._emb("charizard")
    e_mix = V3.emb_opp("charizard", usage=USAGE)
    if V3._has_emb("charizardmegay"):
        expected = 0.1 * e_base + 0.6 * V3._emb("charizardmegay") + 0.3 * V3._emb("charizardmegax")
        assert np.allclose(e_mix, expected, atol=1e-5)
    else:
        assert np.allclose(e_mix, e_base)
    assert np.allclose(V3.emb_opp("mimikyu", usage=USAGE), V3._emb("mimikyu"))
    # 自分の石持ちはメガ後の埋め込み (無ければ素)
    e_own = V3.emb_own("delphox", sm)
    assert np.allclose(e_own, V3._emb("delphoxmega") if V3._has_emb("delphoxmega") else V3._emb("delphox"))
    # 収集データに記録した持ち物 (own_items) があればそれで石の所在を決める (プールの型より確か)
    items = ["focussash", "garchompite", "", "", "", ""]
    x2 = V3.build_features_v3(MY, OPP, (0, 1, 2), usage=USAGE, own_items=items)
    assert list(x2[FEATURE_DIM:FEATURE_DIM + 3]) == [0.0, 1.0, 0.0]
    print("test_features_v3 OK")


def test_opp_mega_split_in_search():
    from advisor.damage import MonView
    from advisor.dex import get_dex
    from advisor.search import Action, SimSide, opp_mega_split, simulate_turn
    sp = get_dex().species("charizard")
    zard = MonView(species_id="charizard", types=list(sp["types"]), base=dict(sp["baseStats"]), ability="blaze",
                   ev={"spa": 252, "spe": 252})
    acts = [Action("move", move_id="fireblast", label="fireblast", prob=0.6),
            Action("move", move_id="airslash", label="airslash", prob=0.4)]
    side = SimSide(active=zard, active_hp=1.0, mega_forms={"charizardmegay": 0.6, "charizardmegax": 0.3})
    out = opp_mega_split(acts, side)
    assert len(out) == 4 and abs(sum(a.prob for a in out) - 1.0) < 1e-9
    mega_acts = [a for a in out if a.mega]
    assert [round(a.prob, 3) for a in mega_acts] == [0.54, 0.36] and mega_acts[0].label == "fireblast+メガ"
    # 分岐しない条件: 権利消費済み / 姿が不明 / 確率が閾値未満
    assert opp_mega_split(acts, SimSide(active=zard, active_hp=1.0, mega_forms={"charizardmegay": 0.6}, mega_used=True)) == acts
    assert opp_mega_split(acts, SimSide(active=zard, active_hp=1.0)) == acts
    assert opp_mega_split(acts, SimSide(active=zard, active_hp=1.0, mega_forms={"charizardmegay": 0.1}), min_prob=0.2) == acts
    # 分岐の解決: 技の前にメガ後の姿 (Y = ひでり) になり、権利を消費する
    gsp = get_dex().species("garchomp")
    chomp = MonView(species_id="garchomp", types=list(gsp["types"]), base=dict(gsp["baseStats"]), ev={"atk": 252, "spe": 252})
    me = SimSide(active=chomp, active_hp=1.0)
    _, o = simulate_turn(me, side, Action("move", move_id="stoneedge"), mega_acts[0], None, None, "avg")
    assert o.active.species_id == "charizardmegay" and o.active.ability == "drought" and o.mega_used and not o.mega_forms
    _, o2 = simulate_turn(me, side, Action("move", move_id="stoneedge"), out[0], None, None, "avg")
    assert o2.active.species_id == "charizard" and not o2.mega_used
    print("test_opp_mega_split_in_search OK")


if __name__ == "__main__":
    test_features_v3()
    test_opp_mega_split_in_search()
