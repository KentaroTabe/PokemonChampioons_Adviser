"""アドバイザーのユニットテスト (ネットワーク不要、生成済みdex.json前提)。

    python -m tests.test_advisor
"""
from __future__ import annotations

from advisor.dex import get_dex, calc_hp, calc_stat
from advisor.damage import MonView, FieldView, calc_damage
from advisor.engine import evaluate


def approx(a, b, tol):
    assert abs(a - b) <= tol, f"{a} != {b} (+-{tol})"


def test_stats():
    # ガブリアス Lv50 個体値31 努力値0: HP実数値 183, 素早さ122 (無補正)
    dex = get_dex()
    g = dex.species("garchomp")
    assert g["baseStats"]["hp"] == 108, g
    assert calc_hp(108, 0) == 183
    assert calc_stat(102, 0) == 122
    assert calc_stat(102, 252, 1.1) == 169  # 最速
    print("test_stats OK")


def test_type_chart():
    dex = get_dex()
    assert dex.effectiveness("Electric", ["Water", "Flying"]) == 4.0
    assert dex.effectiveness("Electric", ["Ground"]) == 0.0
    assert dex.effectiveness("Ice", ["Dragon", "Ground"]) == 4.0
    assert dex.effectiveness("Fighting", ["Ghost"]) == 0.0
    print("test_type_chart OK")


def test_damage_sanity():
    dex = get_dex()
    # ガブリアス(A252) じしん vs メタグロス(無振り) -- 抜群1タイプ
    atk = MonView(species_id="garchomp", base=dex.species("garchomp")["baseStats"],
                  types=dex.species("garchomp")["types"], ev={"atk": 252})
    dfn = MonView(species_id="metagross", base=dex.species("metagross")["baseStats"],
                  types=dex.species("metagross")["types"])
    d = calc_damage(atk, dfn, "earthquake")
    # 実測(SV準拠): 91.6〜107.7% (A182 vs D150/H155, 威力100 STAB1.5 x2.0)
    assert d["type_mult"] == 2.0
    assert 85 <= d["avg"] <= 110, d
    # 浮遊には無効
    dfn2 = MonView(species_id="rotomheat", base=dex.species("rotomheat")["baseStats"],
                   types=dex.species("rotomheat")["types"], ability="levitate")
    d2 = calc_damage(atk, dfn2, "earthquake")
    assert d2["avg"] == 0.0
    # やけど半減
    atk.status = "burn"
    d3 = calc_damage(atk, dfn, "earthquake")
    approx(d3["avg"], d["avg"] / 2, 1.5)
    print("test_damage_sanity OK")


def test_weather_and_screens():
    dex = get_dex()
    atk = MonView(species_id="charizard", base=dex.species("charizard")["baseStats"],
                  types=dex.species("charizard")["types"], ev={"spa": 252})
    dfn = MonView(species_id="venusaur", base=dex.species("venusaur")["baseStats"],
                  types=dex.species("venusaur")["types"])
    base = calc_damage(atk, dfn, "flamethrower")
    sun = calc_damage(atk, dfn, "flamethrower", FieldView(weather="sun"))
    approx(sun["avg"], base["avg"] * 1.5, 2.0)
    wall = calc_damage(atk, dfn, "flamethrower", FieldView(light_screen=True))
    approx(wall["avg"], base["avg"] * 0.5, 2.0)
    print("test_weather_and_screens OK")


def test_evaluate_end_to_end():
    """様子を見る画面相当の状態からアドバイスが出ることを確認"""
    state = {
        "field": {"weather": None, "terrain": None, "trick_room": False},
        "mega_used": {"player": False, "opponent": False},
        "player": {
            "active_index": 0,
            "tailwind": False,
            "hazards": {"stealth_rock": False, "spikes": 0, "toxic_spikes": 0, "sticky_web": False},
            "screens": {"reflect": False, "light_screen": False, "aurora_veil": False},
            "party": [
                {"species_id": "duraludon", "species_ja": "ブリジュラス",
                 "types": ["ドラゴン", "はがね"], "hp_percent": 100.0,
                 "hp_current": 197, "hp_max": 197, "status": None, "boosts": {},
                 "ability_id": "stamina", "item_id": "leftovers",
                 "moves": [
                     {"name_ja": "りゅうのはどう", "move_id": "dragonpulse", "pp": 12, "max_pp": 12, "effectiveness": "neutral"},
                     {"name_ja": "エレクトロビーム", "move_id": "electroshot", "pp": 12, "max_pp": 12, "effectiveness": "super"},
                     {"name_ja": "はどうだん", "move_id": "aurasphere", "pp": 20, "max_pp": 20, "effectiveness": "resist"},
                     {"name_ja": "まもる", "move_id": "protect", "pp": 8, "max_pp": 8, "effectiveness": None},
                 ], "revealed_moves": []},
                {"species_id": "raichu", "species_ja": "ライチュウ", "types": [],
                 "hp_percent": 100.0, "hp_current": 137, "hp_max": 137,
                 "status": None, "boosts": {}, "ability_id": None,
                 "item_id": "megastone", "item_ja": "ライチュウナイトY",
                 "moves": [], "revealed_moves": []},
            ],
        },
        "opponent": {
            "active_index": 0,
            "tailwind": False,
            "hazards": {"stealth_rock": False, "spikes": 0, "toxic_spikes": 0, "sticky_web": False},
            "screens": {"reflect": False, "light_screen": False, "aurora_veil": False},
            "party": [
                {"species_id": "charizard", "species_ja": "リザードン",
                 "types": ["ほのお", "ひこう"], "hp_percent": 100.0,
                 "hp_current": None, "hp_max": None, "status": None, "boosts": {},
                 "ability_id": None, "item_id": None,
                 "moves": [], "revealed_moves": ["フレアドライブ"]},
            ],
        },
    }
    advice = evaluate(state)
    assert advice["ok"], advice
    names = [a["name"] for a in advice["actions"]]
    assert "エレクトロビーム" in names[0:2], names  # 電気4倍が上位に来るはず
    top_move = advice["actions"][0]
    print("test_evaluate_end_to_end OK")

    # わざふうじ: ちょうはつ中は変化技 (まもる) が候補から実質除外される
    import copy
    sealed = copy.deepcopy(state)
    sealed["player"]["party"][0]["volatiles"] = ["taunt"]
    adv2 = evaluate(sealed)
    protect = next(a for a in adv2["actions"] if a["id"] == "protect")
    assert protect["score"] <= -90, protect
    assert "ちょうはつ" in protect["reason"], protect
    # かなしばり: 封じられた技のみ除外される
    sealed["player"]["party"][0]["volatiles"] = ["disable",
                                                 "disable_dragonpulse"]
    adv3 = evaluate(sealed)
    dp = next(a for a in adv3["actions"] if a["id"] == "dragonpulse")
    assert dp["score"] <= -90, dp
    es = next(a for a in adv3["actions"] if a["id"] == "electroshot")
    assert es["score"] > 0, es
    print("test_move_seal_filter OK")

    # アンコール: 直前技が判明していれば、その技以外は候補から実質除外
    sealed = copy.deepcopy(state)
    sealed["player"]["party"][0]["volatiles"] = ["encore"]
    sealed["last_move"] = {"player": "dragonpulse"}
    adv4 = evaluate(sealed)
    es4 = next(a for a in adv4["actions"] if a["id"] == "electroshot")
    assert es4["score"] <= -90, es4
    assert "アンコール" in es4["reason"], es4
    dp4 = next(a for a in adv4["actions"] if a["id"] == "dragonpulse")
    assert dp4["score"] > 0, dp4
    # 直前技が不明なら従来どおり注記のみ (候補は絞らない)
    del sealed["last_move"]
    adv5 = evaluate(sealed)
    es5 = next(a for a in adv5["actions"] if a["id"] == "electroshot")
    assert es5["score"] > 0, es5
    print("test_encore_lock OK")
    print("--- アドバイス出力例 ---")
    from advisor.service import Advisor
    print(Advisor().format_advice(advice))


def test_pivot_over_plain_switch():
    """素の交代が最善のとき、無効化されない交代技が交代より上に来る"""
    import copy
    from advisor.engine import evaluate as _eval
    state = {
        "field": {"weather": None, "terrain": None, "trick_room": False},
        "mega_used": {"player": False, "opponent": False},
        "player": {
            "active_index": 0, "tailwind": False,
            "hazards": {"stealth_rock": False, "spikes": 0,
                        "toxic_spikes": 0, "sticky_web": False},
            "screens": {"reflect": False, "light_screen": False,
                        "aurora_veil": False},
            "party": [
                # ハッサム vs リザードン: 相性最悪で交代が最善になる状況
                {"species_id": "scizor", "species_ja": "ハッサム",
                 "types": ["むし", "はがね"], "hp_percent": 100.0,
                 "hp_current": 145, "hp_max": 145, "status": None,
                 "boosts": {}, "ability_id": None, "item_id": None,
                 "moves": [
                     {"name_ja": "とんぼがえり", "move_id": "uturn",
                      "pp": 20, "max_pp": 20, "effectiveness": "resist"},
                     {"name_ja": "バレットパンチ", "move_id": "bulletpunch",
                      "pp": 30, "max_pp": 30, "effectiveness": "resist"},
                 ], "revealed_moves": []},
                {"species_id": "garchomp", "species_ja": "ガブリアス",
                 "types": ["ドラゴン", "じめん"], "hp_percent": 100.0,
                 "hp_current": 183, "hp_max": 183, "status": None,
                 "boosts": {}, "ability_id": None, "item_id": None,
                 "moves": [], "revealed_moves": []},
            ],
        },
        "opponent": {
            "active_index": 0, "tailwind": False,
            "hazards": {"stealth_rock": False, "spikes": 0,
                        "toxic_spikes": 0, "sticky_web": False},
            "screens": {"reflect": False, "light_screen": False,
                        "aurora_veil": False},
            "party": [
                {"species_id": "charizard", "species_ja": "リザードン",
                 "types": ["ほのお", "ひこう"], "hp_percent": 100.0,
                 "hp_current": None, "hp_max": None, "status": None,
                 "boosts": {}, "ability_id": None, "item_id": None,
                 "moves": [], "revealed_moves": ["フレアドライブ"]},
            ],
        },
    }
    advice = _eval(state)
    assert advice["ok"], advice
    actions = advice["actions"]
    idx = {a["id"]: i for i, a in enumerate(actions) if a.get("id")}
    switch_idx = next(i for i, a in enumerate(actions)
                      if a["kind"] == "switch")
    # 交代が交代技より上に来ているなら、交代技の複合価値が働いていない
    dump = [f"{a['kind']}:{a.get('id') or a['name']}={a['score']}"
            for a in actions]
    assert idx["uturn"] < switch_idx, dump
    top = actions[idx["uturn"]]
    assert "交代するならまずこの技" in top["reason"], dump
    print("test_pivot_over_plain_switch OK")


def _mini_state(my_mon, opp_mon):
    empty_haz = {"stealth_rock": False, "spikes": 0, "toxic_spikes": 0,
                 "sticky_web": False}
    empty_scr = {"reflect": False, "light_screen": False,
                 "aurora_veil": False}
    return {
        "field": {"weather": None, "terrain": None, "trick_room": False},
        "mega_used": {"player": False, "opponent": False},
        "player": {"active_index": 0, "tailwind": False,
                   "hazards": dict(empty_haz), "screens": dict(empty_scr),
                   "party": [my_mon]},
        "opponent": {"active_index": 0, "tailwind": False,
                     "hazards": dict(empty_haz), "screens": dict(empty_scr),
                     "party": [opp_mon]},
    }


def test_priority_evaluation():
    # 1) 遅いミミッキュでも、かげうちがKO圏なら「先制技で先に倒せる」扱い
    my = {"species_id": "mimikyu", "species_ja": "ミミッキュ",
          "types": ["ゴースト", "フェアリー"], "hp_percent": 100.0,
          "hp_current": 131, "hp_max": 131, "status": None, "boosts": {},
          "ability_id": "disguise", "item_id": None,
          "moves": [
              {"name_ja": "かげうち", "move_id": "shadowsneak",
               "pp": 20, "max_pp": 20, "effectiveness": "super"},
              {"name_ja": "じゃれつく", "move_id": "playrough",
               "pp": 10, "max_pp": 10, "effectiveness": "neutral"},
          ], "revealed_moves": []}
    opp = {"species_id": "gengar", "species_ja": "ゲンガー",
           "types": ["ゴースト", "どく"], "hp_percent": 15.0,
           "hp_current": None, "hp_max": None, "status": None, "boosts": {},
           "ability_id": None, "item_id": None, "moves": [],
           "revealed_moves": ["シャドーボール"]}
    from vision.normalize import NameResolver
    resolver = NameResolver()
    adv = evaluate(_mini_state(my, opp), resolver)
    sneak = next(a for a in adv["actions"] if a["id"] == "shadowsneak")
    assert "先に倒せる" in sneak["reason"], sneak
    assert sneak["score"] > 40, sneak

    # 2) 相手のKO圏の先制技 (ふいうち判明): 素早さで勝っていても
    #    「先に殴られる」前提の警告が出る
    my2 = {"species_id": "gengar", "species_ja": "ゲンガー",
           "types": ["ゴースト", "どく"], "hp_percent": 30.0,
           "hp_current": 40, "hp_max": 135, "status": None, "boosts": {},
           "ability_id": None, "item_id": None,
           "moves": [
               {"name_ja": "みちづれ", "move_id": "destinybond",
                "pp": 8, "max_pp": 8, "effectiveness": None},
               {"name_ja": "シャドーボール", "move_id": "shadowball",
                "pp": 16, "max_pp": 16, "effectiveness": "resist"},
           ], "revealed_moves": []}
    opp2 = {"species_id": "grimmsnarl", "species_ja": "オーロンゲ",
            "types": ["あく", "フェアリー"], "hp_percent": 100.0,
            "hp_current": None, "hp_max": None, "status": None, "boosts": {},
            "ability_id": None, "item_id": None, "moves": [],
            "revealed_moves": ["ふいうち"]}
    adv2 = evaluate(_mini_state(my2, opp2), resolver)
    assert "先制技" in adv2["speed_note"], adv2["speed_note"]
    db = next(a for a in adv2["actions"] if a["id"] == "destinybond")
    assert "先制技で倒される危険" in db["reason"], db
    print("test_priority_evaluation OK")


def test_fainted_active_switch_only():
    """自分の場のポケモンがひんしなら技を評価せず交代先のみ提案する。

    2026-08-18 接続テスト欠陥#1: 瀕死のムクホークのブレイブバードを
    推奨し続けた (強制交代の決定に技助言が出た)。
    """
    from vision.normalize import NameResolver
    resolver = NameResolver()
    fainted = {"species_id": "staraptor", "species_ja": "ムクホーク",
               "types": ["ノーマル", "ひこう"], "hp_percent": 0.0,
               "hp_current": 0, "hp_max": 165, "status": "fainted",
               "boosts": {},
               "ability_id": None, "item_id": None,
               "moves": [{"name_ja": "ブレイブバード", "move_id": "bravebird",
                          "pp": 15, "max_pp": 15, "effectiveness": "neutral"}],
               "revealed_moves": []}
    bench = {"species_id": "hydreigon", "species_ja": "サザンドラ",
             "types": ["あく", "ドラゴン"], "hp_percent": 100.0,
             "hp_current": 169, "hp_max": 169, "status": None, "boosts": {},
             "ability_id": None, "item_id": None,
             "moves": [{"name_ja": "りゅうせいぐん", "move_id": "dracometeor",
                        "pp": 8, "max_pp": 8, "effectiveness": "neutral"}],
             "revealed_moves": []}
    opp = {"species_id": "garchomp", "species_ja": "ガブリアス",
           "types": ["ドラゴン", "じめん"], "hp_percent": 100.0,
           "hp_current": None, "hp_max": None, "status": None, "boosts": {},
           "ability_id": None, "item_id": None, "moves": [],
           "revealed_moves": []}
    state = _mini_state(fainted, opp)
    state["player"]["party"] = [fainted, bench]
    adv = evaluate(state, resolver)
    assert adv["ok"], adv
    kinds = {a["kind"] for a in adv["actions"]}
    assert kinds == {"switch"}, adv["actions"]
    assert adv["best"]["kind"] == "switch", adv["best"]
    assert "ひんし" in adv["speed_note"], adv["speed_note"]
    print("test_fainted_active_switch_only OK")


def test_act_before_ko_discount():
    """後手でKO圏の被弾が確定している局面では、先に動けない大技より
    先制技が上に来る (2026-08-18 接続テスト欠陥#2)。

    遅いミミッキュ vs 高速で抜群高火力のガブリアス (じしん判明):
    非先制のじゃれつく (大ダメージ) は割引され、かげうちが最善になる。
    """
    from vision.normalize import NameResolver
    resolver = NameResolver()
    my = {"species_id": "mimikyu", "species_ja": "ミミッキュ",
          "types": ["ゴースト", "フェアリー"], "hp_percent": 25.0,
          "hp_current": 33, "hp_max": 131, "status": None, "boosts": {},
          "ability_id": None, "item_id": None,
          "moves": [
              {"name_ja": "かげうち", "move_id": "shadowsneak",
               "pp": 20, "max_pp": 20, "effectiveness": "neutral"},
              {"name_ja": "じゃれつく", "move_id": "playrough",
               "pp": 10, "max_pp": 10, "effectiveness": "super"},
          ], "revealed_moves": []}
    opp = {"species_id": "garchomp", "species_ja": "ガブリアス",
           "types": ["ドラゴン", "じめん"], "hp_percent": 100.0,
           "hp_current": None, "hp_max": None, "status": None, "boosts": {},
           "ability_id": None, "item_id": None, "moves": [],
           "revealed_moves": ["じしん"]}
    adv = evaluate(_mini_state(my, opp), resolver)
    sneak = next(a for a in adv["actions"] if a["id"] == "shadowsneak")
    rough = next(a for a in adv["actions"] if a["id"] == "playrough")
    assert "行動前に倒される見込み" in rough["reason"], rough
    assert sneak["score"] > rough["score"], (sneak, rough)
    print("test_act_before_ko_discount OK")


def test_ko_margin_prefers_overkill():
    """同じKO圏の技どうしでは突破余裕 (余剰ダメージ) の大きい技を上位にする。

    2026-08-20 第5回接続テスト: 残24%のライボルト (でんき単) に対し、
    実効ダメージが残HPで頭打ちになり、RLブレンドの揺らぎで
    等倍エナジーボール(53〜63%)が抜群だいちのちから(106〜125%)を上回った。
    RLの寄与を除いた素点で、抜群側が必ず上に来ることを確認する。
    """
    import os
    from vision.normalize import NameResolver
    resolver = NameResolver()
    my = {"species_id": "glimmora", "species_ja": "キラフロル",
          "types": ["いわ", "どく"], "hp_percent": 100.0,
          "hp_current": 159, "hp_max": 159, "status": None, "boosts": {},
          "ability_id": None, "item_id": None,
          "moves": [
              {"name_ja": "エナジーボール", "move_id": "energyball",
               "pp": 10, "max_pp": 10, "effectiveness": "neutral"},
              {"name_ja": "だいちのちから", "move_id": "earthpower",
               "pp": 10, "max_pp": 10, "effectiveness": "super"},
          ], "revealed_moves": []}
    opp = {"species_id": "manectric", "species_ja": "ライボルト",
           "types": ["でんき"], "hp_percent": 24.0,
           "hp_current": None, "hp_max": None, "status": None, "boosts": {},
           "ability_id": None, "item_id": None, "moves": [],
           "revealed_moves": []}
    prev = os.environ.get("RL_BLEND_WEIGHT")
    os.environ["RL_BLEND_WEIGHT"] = "0"
    try:
        adv = evaluate(_mini_state(my, opp), resolver)
    finally:
        if prev is None:
            os.environ.pop("RL_BLEND_WEIGHT", None)
        else:
            os.environ["RL_BLEND_WEIGHT"] = prev
    ep = next(a for a in adv["actions"] if a["id"] == "earthpower")
    eb = next(a for a in adv["actions"] if a["id"] == "energyball")
    assert "抜群" in ep["reason"], ep
    assert ep["score"] > eb["score"], (ep, eb)
    print("test_ko_margin_prefers_overkill OK")


def test_choice_lock():
    """こだわり系を持って技を使った後は、直前の技以外を推奨しない (第2回#C)"""
    from vision.normalize import NameResolver
    resolver = NameResolver()
    my = {"species_id": "staraptor", "species_ja": "ムクホーク",
          "types": ["ノーマル", "ひこう"], "hp_percent": 100.0,
          "hp_current": 161, "hp_max": 161, "status": None, "boosts": {},
          "ability_id": None, "item_id": "choicescarf",
          "moves": [
              {"name_ja": "ブレイブバード", "move_id": "bravebird",
               "pp": 15, "max_pp": 15, "effectiveness": "neutral"},
              {"name_ja": "でんこうせっか", "move_id": "quickattack",
               "pp": 20, "max_pp": 20, "effectiveness": "neutral"},
          ], "revealed_moves": []}
    opp = {"species_id": "garchomp", "species_ja": "ガブリアス",
           "types": ["ドラゴン", "じめん"], "hp_percent": 100.0,
           "hp_current": None, "hp_max": None, "status": None, "boosts": {},
           "ability_id": None, "item_id": None, "moves": [],
           "revealed_moves": []}
    state = _mini_state(my, opp)
    state["last_move"] = {"player": "bravebird", "opponent": None}
    adv = evaluate(state, resolver)
    qa = next(a for a in adv["actions"] if a["id"] == "quickattack")
    assert qa["score"] == -99.0 and "こだわり" in qa["reason"], qa
    bb = next(a for a in adv["actions"] if a["id"] == "bravebird")
    assert bb["score"] > -90, bb
    assert "こだわり中" in adv["speed_note"], adv["speed_note"]

    # 交代直後 (last_moveなし) はロックされない
    state2 = _mini_state(dict(my), opp)
    adv2 = evaluate(state2, resolver)
    qa2 = next(a for a in adv2["actions"] if a["id"] == "quickattack")
    assert qa2["score"] > -90, qa2
    print("test_choice_lock OK")


def test_registered_moves_fallback():
    """画面から技が未読取でも、my_team登録の技で行動評価する (第2回#A)"""
    from vision.normalize import NameResolver
    from advisor import my_team as mt
    resolver = NameResolver()
    my = {"species_id": "staraptor", "species_ja": "ムクホーク",
          "types": ["ノーマル", "ひこう"], "hp_percent": 100.0,
          "hp_current": 161, "hp_max": 161, "status": None, "boosts": {},
          "ability_id": None, "item_id": None,
          "moves": [], "revealed_moves": []}
    opp = {"species_id": "garchomp", "species_ja": "ガブリアス",
           "types": ["ドラゴン", "じめん"], "hp_percent": 100.0,
           "hp_current": None, "hp_max": None, "status": None, "boosts": {},
           "ability_id": None, "item_id": None, "moves": [],
           "revealed_moves": []}
    orig = mt.get_my_moves
    mt.get_my_moves = lambda ja: (["ブレイブバード", "インファイト"]
                                  if ja == "ムクホーク" else [])
    try:
        adv = evaluate(_mini_state(my, opp), resolver)
    finally:
        mt.get_my_moves = orig
    ids = {a["id"] for a in adv["actions"] if a["kind"] == "move"}
    assert "bravebird" in ids and "closecombat" in ids, ids
    print("test_registered_moves_fallback OK")


def test_rl_sees_registered_move_fallback():
    """技未読取のフレームでは、RLヒントにも登録技フォールバックを見せる。

    2026-08-18 第3回: stateの技が空だとRLの合法手が交代のみに縮退し、
    RL確率が交代に集中→技画面を開いた瞬間に推奨が反転した。
    """
    from vision.normalize import NameResolver
    from advisor import my_team as mt
    from advisor import rl_bridge as rb
    resolver = NameResolver()
    my = {"species_id": "staraptor", "species_ja": "ムクホーク",
          "types": ["ノーマル", "ひこう"], "hp_percent": 100.0,
          "hp_current": 161, "hp_max": 161, "status": None, "boosts": {},
          "ability_id": None, "item_id": None,
          "moves": [], "revealed_moves": []}
    opp = {"species_id": "garchomp", "species_ja": "ガブリアス",
           "types": ["ドラゴン", "じめん"], "hp_percent": 100.0,
           "hp_current": None, "hp_max": None, "status": None, "boosts": {},
           "ability_id": None, "item_id": None, "moves": [],
           "revealed_moves": []}
    seen = {}

    def fake_hint(state, my_spe_actual=None):
        mi = state["player"]["active_index"]
        seen["moves"] = [m.get("move_id") for m in
                         (state["player"]["party"][mi].get("moves") or [])]
        return None

    orig_moves, orig_hint = mt.get_my_moves, rb.policy_hint
    mt.get_my_moves = lambda ja: (["ブレイブバード"] if ja == "ムクホーク" else [])
    rb.policy_hint = fake_hint
    try:
        state = _mini_state(my, opp)
        evaluate(state, resolver)
    finally:
        mt.get_my_moves = orig_moves
        rb.policy_hint = orig_hint
    assert seen.get("moves") == ["bravebird"], seen
    # 元のstateは汚染しない (浅いコピーへの注入)
    assert my["moves"] == [], my["moves"]
    print("test_rl_sees_registered_move_fallback OK")


def test_uncertain_bench_switch_penalty():
    """交代を見逃した (hp_uncertain) 控えへの交代は減点+警告する (第2回#E)"""
    from vision.normalize import NameResolver
    resolver = NameResolver()
    my = {"species_id": "mimikyu", "species_ja": "ミミッキュ",
          "types": ["ゴースト", "フェアリー"], "hp_percent": 100.0,
          "hp_current": 131, "hp_max": 131, "status": None, "boosts": {},
          "ability_id": None, "item_id": None,
          "moves": [{"name_ja": "じゃれつく", "move_id": "playrough",
                     "pp": 10, "max_pp": 10, "effectiveness": "neutral"}],
          "revealed_moves": []}
    bench = {"species_id": "hydreigon", "species_ja": "サザンドラ",
             "types": ["あく", "ドラゴン"], "hp_percent": 100.0,
             "hp_current": 169, "hp_max": 169, "status": None, "boosts": {},
             "ability_id": None, "item_id": None, "moves": [],
             "revealed_moves": [], "hp_uncertain": True}
    opp = {"species_id": "garchomp", "species_ja": "ガブリアス",
           "types": ["ドラゴン", "じめん"], "hp_percent": 100.0,
           "hp_current": None, "hp_max": None, "status": None, "boosts": {},
           "ability_id": None, "item_id": None, "moves": [],
           "revealed_moves": []}
    state = _mini_state(my, opp)
    state["player"]["party"] = [my, bench]
    adv = evaluate(state, resolver)
    sw = next(a for a in adv["actions"] if a["kind"] == "switch")
    assert "HP不明" in sw["reason"], sw
    print("test_uncertain_bench_switch_penalty OK")


if __name__ == "__main__":
    test_stats()
    test_type_chart()
    test_damage_sanity()
    test_weather_and_screens()
    test_evaluate_end_to_end()
    test_pivot_over_plain_switch()
    test_priority_evaluation()
    test_fainted_active_switch_only()
    test_act_before_ko_discount()
    test_ko_margin_prefers_overkill()
    test_choice_lock()
    test_registered_moves_fallback()
    test_rl_sees_registered_move_fallback()
    test_uncertain_bench_switch_penalty()
