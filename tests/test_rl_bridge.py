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
        "field": {"weather": "rain", "weather_turns": 5, "terrain": None,
                  "trick_room": False},
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
    # v3拡張 (298以降): 自技効果4x8 -> 330; 相手技効果4x8 -> 362;
    #   プロファイル4 -> 366; 効用4 -> 370; 天候残り2 -> 372; 控え同士4 -> 376
    # 自分の技スロット2 = れいとうパンチ (副次10%こおり) -> 状態異常率0.1
    assert abs(obs[298 + 2 * 8 + 6] - 0.1) < 1e-5, \
        f"技効果(状態異常率)位置ずれ: {obs[320]}"
    # スロット0 = じしん (付随効果なし) -> 全0
    assert np.all(obs[298:306] == 0.0), obs[298:306]
    # 攻撃プロファイル: 相手ガブリアス判明技=じしん(物理) -> 物理シェア1.0
    assert abs(obs[362] - 1.0) < 1e-5, f"相手物理シェア位置ずれ: {obs[362]}"
    # 自分も物理技3つ -> 物理シェア1.0
    assert abs(obs[364] - 1.0) < 1e-5, f"自分物理シェア位置ずれ: {obs[364]}"
    # 天候残りターン: weather_turns=5 -> 5/8
    assert abs(obs[370] - 5.0 / 8.0) < 1e-5, f"天候残り位置ずれ: {obs[370]}"
    print(f"test_encode OK ({OBS_DIM}dim, 値域正常, v1/v2/v3位置検証OK)")


def test_boost_utility_context():
    # ユーザー指摘の検証: B上げ (てっぺき) は相手が物理型なら効用が高く、
    # 特殊型なら効用ゼロに近づく。A上げ (つるぎのまい) は自分が物理型なら
    # 相手の型に依存しない
    from poke_env.battle import Move
    from champions_agent.agent.encoders import _boost_utility
    iron = Move("irondefense", gen=9)     # B+2
    swords = Move("swordsdance", gen=9)   # A+2
    # 相手が物理100%: B上げの効用が高い
    u_phys = _boost_utility(iron, 1.0, 0.0, 1.0, 0.0, is_slower=False)
    # 相手が特殊100%: B上げの効用ゼロ
    u_spec = _boost_utility(iron, 1.0, 0.0, 0.0, 1.0, is_slower=False)
    assert u_phys > 0.4 and u_spec == 0.0, (u_phys, u_spec)
    # A上げは自分が物理型なら相手の型に関係なく高効用
    a1 = _boost_utility(swords, 1.0, 0.0, 1.0, 0.0, is_slower=False)
    a2 = _boost_utility(swords, 1.0, 0.0, 0.0, 1.0, is_slower=False)
    assert a1 == a2 > 0.4, (a1, a2)
    # りゅうのまい: 相手より遅いときの方がS上げ分だけ効用が高い
    dd = Move("dragondance", gen=9)
    d_slow = _boost_utility(dd, 1.0, 0.0, 0.5, 0.5, is_slower=True)
    d_fast = _boost_utility(dd, 1.0, 0.0, 0.5, 0.5, is_slower=False)
    assert d_slow > d_fast, (d_slow, d_fast)
    print(f"test_boost_utility_context OK: てっぺき vs物理{u_phys:.2f}/vs特殊{u_spec:.2f}, "
          f"りゅうのまい 遅{d_slow:.2f}>速{d_fast:.2f}")


def test_contrary():
    # あまのじゃく (メガムクホーク等): ランク変化が反転する
    from poke_env.battle import Move
    from champions_agent.agent.encoders import (_move_effect_vec,
                                                _boost_utility)
    cc = Move("closecombat", gen=9)   # 通常: 自分B/D-1
    normal = _move_effect_vec(cc)
    inverted = _move_effect_vec(cc, contrary=True)
    # BOOST_STAT_KEYS = (atk, def, spa, spd, spe): B=idx1, D=idx3
    assert normal[1] == -0.5 and normal[3] == -0.5, normal[:5]
    assert inverted[1] == 0.5 and inverted[3] == 0.5, inverted[:5]
    # あまのじゃく+インファイトは「相手が物理型」のとき効用が出る
    u = _boost_utility(cc, 1.0, 0.0, 1.0, 0.0, is_slower=False, contrary=True)
    assert u > 0.2, u
    # 逆にりゅうのまいは自傷になる -> 効用0
    dd = Move("dragondance", gen=9)
    u_dd = _boost_utility(dd, 1.0, 0.0, 0.5, 0.5, is_slower=True, contrary=True)
    assert u_dd == 0.0, u_dd
    # rl_bridge側: メガムクホーク (固定特性あまのじゃく) の判定
    from advisor.rl_bridge import _has_contrary_dict
    assert _has_contrary_dict({"species_id": "staraptor", "is_mega": True,
                               "item_id": "staraptorite"}) or \
        _has_contrary_dict({"species_id": "staraptor",
                            "ability_id": "contrary"})
    print(f"test_contrary OK: インファイト反転 {normal[:4]} -> {inverted[:4]}")


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
    test_boost_utility_context()
    test_contrary()
    test_legal_actions()
    test_policy_hint()
    test_value_of_sim()
    test_engine_blend()
    print("ALL OK")
