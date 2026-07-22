"""RL方策統合 (advisor/rl_bridge) の検証。

- 観測エンコードが227次元で妥当な値域に収まること
- 合法手の列挙 (交代/技/メガ) が正しいこと
- チェックポイントがあれば方策分布と価値が返ること

使い方: python -m tests.test_rl_bridge
"""
import numpy as np

from advisor.rl_bridge import (_legal_actions, encode_state, policy_hint,
                               OBS_DIM)


def _state():
    return {
        "scene": "command", "turn": 4,
        "field": {"weather": "rain", "terrain": None, "trick_room": False},
        "mega_used": {"player": False, "opponent": False},
        "player": {"active_index": 0, "remaining": 3,
                   "hazards": {"stealth_rock": True, "spikes": 0,
                               "toxic_spikes": 0, "sticky_web": False},
                   "screens": {},
                   "party": [
            {"species_id": "swampert", "species_ja": "ラグラージ",
             "types": ["みず", "じめん"], "hp_percent": 80.0, "status": None,
             "boosts": {"atk": 1}, "item_ja": "ラグラージナイト",
             "moves": [{"move_id": "earthquake", "name_ja": "じしん", "pp": 16},
                       {"move_id": "waterfall", "name_ja": "たきのぼり", "pp": 24},
                       {"move_id": "icepunch", "name_ja": "れいとうパンチ", "pp": 0}]},
            {"species_id": "pelipper", "species_ja": "ペリッパー",
             "types": ["みず", "ひこう"], "hp_percent": 55.0, "status": None},
            {"species_id": "mimikyu", "species_ja": "ミミッキュ",
             "types": ["ゴースト", "フェアリー"], "hp_percent": 0.0,
             "status": "fainted"},
        ]},
        "opponent": {"active_index": 0, "remaining": 2,
                     "hazards": {}, "screens": {},
                     "party": [
            {"species_id": "garchomp", "species_ja": "ガブリアス",
             "types": ["ドラゴン", "じめん"], "hp_percent": 100.0,
             "status": "paralysis", "boosts": {},
             "revealed_moves": ["じしん"]},
            {"species_id": "rotomwash", "species_ja": "ウォッシュロトム",
             "types": ["でんき", "みず"], "hp_percent": 60.0},
        ]},
    }


def test_encode():
    obs = encode_state(_state(), my_spe_actual=112)
    assert obs is not None and len(obs) == OBS_DIM, len(obs)
    assert np.all(np.isfinite(obs))
    assert 0 <= obs.min() and obs.max() <= 4.0, (obs.min(), obs.max())
    # v1レイアウト: own75 + opp39 + extra2 + own_bench40 + opp_bench42 +
    #               count1 = 199; my_side8 -> 207; opp_side8 -> 215; field10
    assert obs[199] == 1.0, "自陣SRフラグ位置ずれ (199)"
    assert obs[215 + 1] == 1.0, "雨フラグ位置ずれ (216)"
    # v2拡張 (227以降): 判明技4x9 -> 263; volatiles12 -> 275; mega3 -> 278;
    #                   items12 -> 290; misc2 -> 292; bench_tactics6 -> 298
    # 相手の判明技スロット0 = じしん (威力100 -> 100/150, 物理フラグ)
    assert abs(obs[227] - 100.0 / 150.0) < 1e-5, f"判明技威力位置ずれ: {obs[227]}"
    assert obs[227 + 3] == 1.0, "判明技の物理フラグ位置ずれ"
    # メガ: 自分はラグラージナイト持ち・未使用 -> can_mega=1, used=0
    assert obs[275] == 1.0, f"メガ可能フラグ位置ずれ: {obs[275]}"
    assert obs[276] == 0.0 and obs[277] == 0.0
    # 自分の残数: 3体中ミミッキュひんし -> 2/3
    assert abs(obs[290] - 2.0 / 3.0) < 1e-5, f"残数位置ずれ: {obs[290]}"
    print(f"test_encode OK ({OBS_DIM}dim, 値域正常, v1/v2位置検証OK)")


def test_legal_actions():
    acts = _legal_actions(_state())
    labels = {a[1] for a in acts}
    # PP切れのれいとうパンチは除外、ひんしのミミッキュ交代も除外
    assert "交代:ペリッパー" in labels
    assert not any("ミミッキュ" in l for l in labels), labels
    assert "じしん" in labels and "れいとうパンチ" not in labels, labels
    # メガストーン持ちなので+メガ行動がある
    assert any("+メガ" in l for l in labels), labels
    print(f"test_legal_actions OK: {sorted(labels)}")


def test_policy_hint():
    hint = policy_hint(_state(), my_spe_actual=112)
    if hint is None:
        print("test_policy_hint SKIP (チェックポイント/sb3なし)")
        return
    assert hint["top"], hint
    total = sum(t["prob"] for t in hint["top"])
    assert 0 < total <= 1.01, hint
    print(f"test_policy_hint OK: {[(t['label'], t['prob']) for t in hint['top']]} "
          f"value={hint['value']}")


def test_value_of_sim():
    from advisor.rl_bridge import value_of_sim, _load_model
    from advisor.search import SimSide
    from advisor.damage import MonView
    from advisor.dex import get_dex
    if _load_model() is None:
        print("test_value_of_sim SKIP")
        return

    def v(sid, hp=1.0):
        sp = get_dex().species(sid)
        return MonView(species_id=sid, types=sp["types"], base=sp["baseStats"],
                       ev={"atk": 252})
    winning = SimSide(active=v("swampertmega"), active_hp=1.0,
                      bench=[(v("archaludon"), 1.0)])
    losing_opp = SimSide(active=v("heatran"), active_hp=0.05)
    val_good = value_of_sim(winning, losing_opp, ["earthquake"])
    # 逆に自分が瀕死1体 vs 相手満タン2体
    bad_me = SimSide(active=v("heatran"), active_hp=0.05)
    strong_opp = SimSide(active=v("swampertmega"), active_hp=1.0,
                         bench=[(v("garchomp"), 1.0)])
    val_bad = value_of_sim(bad_me, strong_opp, ["flamethrower"])
    assert val_good is not None and val_bad is not None
    assert -1 <= val_bad <= val_good <= 1, (val_bad, val_good)
    print(f"test_value_of_sim OK: 有利局面{val_good:+.2f} > 不利局面{val_bad:+.2f}")


def test_engine_blend():
    # RLブレンド後もアドバイスが正常に出て、reasonにRL%が付くこと
    from advisor.service import Advisor
    adv = Advisor()
    result = adv.advise(_state())
    assert result.get("ok"), result.get("reason")
    if result.get("rl_hint"):
        assert any("RL" in (a.get("reason") or "") for a in result["actions"]), \
            [a.get("reason") for a in result["actions"]]
        print("test_engine_blend OK:",
              [(a["name"], a["score"]) for a in result["actions"][:3]])
    else:
        print("test_engine_blend OK (RLなし環境)")


if __name__ == "__main__":
    test_encode()
    test_legal_actions()
    test_policy_hint()
    test_value_of_sim()
    test_engine_blend()
    print("ALL OK")
