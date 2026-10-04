"""役割の雛形からの型生成 (tools/team_build/role_sets) のテスト。純粋関数は表と小さな入力で、生成は実データの煙試験。

    python -m tests.test_role_sets
"""
from __future__ import annotations

from tools.team_build import role_sets as R


def test_template_resolution_and_bulk():
    name, tpl, fld = R.template_of("setup_ace")
    assert name == "sweeper_setup" and tpl["setup_kind"] == "offense" and fld is None
    assert R.template_of("sun_setter")[0] == "weather_setter" and R.template_of("sun_setter")[2] == "sun"
    assert R.template_of("psychic_abuser")[0] == "terrain_ace" and R.template_of("psychic_abuser")[2] == "psychic"
    assert R.template_of("rocks_setter")[0] == "hazard_lead" and R.template_of("regen_wall")[0] == "wall"
    try:
        R.template_of("nosuchrole")
        assert False, "未知の役割は KeyError"
    except KeyError:
        pass
    # 耐久 / 速攻の判定 (D-03): 耐久投資 24 点以上、回復系の持ち物、壁の定型 → 耐久。素早さ投資 + 攻撃的持ち物 → 速攻
    assert R.classify_bulk("32/32/0/0/0/2") == "bulky" and R.classify_bulk("2/32/0/0/0/32") == "fast"
    assert R.classify_bulk("2/32/0/0/0/32", item="leftovers") == "bulky" and R.classify_bulk("0/0/0/0/0/0", item="focussash") == "fast"
    assert R.classify_bulk("2/32/0/0/0/32", template_spread="wall_auto") == "bulky"
    assert R.classify_bulk("10/32/0/0/0/24") == "fast" and R.classify_bulk("16/16/0/0/0/16") == "bulky"
    # 微調整した配分は雛形の性格を保つものだけ採る (速攻: 素早さと主攻撃 ≥ 24、耐久: HP か防御側 ≥ 24)
    assert R.spread_keeps_class("10/32/0/0/0/24", "fast") and not R.spread_keeps_class("8/24/0/0/19/15", "fast")
    assert R.spread_keeps_class("32/0/10/8/0/16", "bulky") and not R.spread_keeps_class("0/32/0/0/16/18", "bulky")
    assert not R.spread_keeps_class("bad", "fast")
    # 攻撃役は耐久側の配分でも主攻撃 ≥ 24 を保つ (2026-10-04: グソクムシャの攻撃 0 など 22 型)
    assert R.spread_keeps_class("32/0/32/0/2/0", "bulky") and not R.spread_keeps_class("32/0/32/0/2/0", "bulky", offensive=True, main_stat="atk")
    assert R.spread_keeps_class("32/24/8/0/2/0", "bulky", offensive=True, main_stat="atk")
    assert not R.spread_keeps_class("32/24/8/0/2/0", "bulky", offensive=True, main_stat="spa")
    # 壁の速さ: 上限ちょうど (95) は可、超えても耐久が十分なら可
    assert R.wall_speed_ok({"hp": 75, "def": 125, "spd": 75, "spe": 95}) and not R.wall_speed_ok({"hp": 65, "def": 60, "spd": 65, "spe": 115})
    assert R.wall_speed_ok({"hp": 100, "def": 100, "spd": 100, "spe": 110}) and R.bulk_product({"hp": 100, "def": 80, "spd": 120}) == 12000
    # 性格と技の分類: 性格が下げる側の技は使わない
    assert R.nature_fits_moves("modest", ["special", "special"]) and not R.nature_fits_moves("modest", ["special", "physical"])
    assert not R.nature_fits_moves("timid", ["physical"]) and R.nature_fits_moves("jolly", ["physical"]) and not R.nature_fits_moves("adamant", ["special"])
    assert R.nature_fits_moves("naive", ["physical", "special"]) and R.nature_fits_moves(None, ["physical"])
    cat = {"heavyslam": "physical", "flashcannon": "special", "earthpower": "special"}
    scored = {"heavyslam": {"t1": 1.0, "t2": 0.2}, "flashcannon": {"t1": 0.5, "t2": 0.9}, "earthpower": {"t1": 0.4, "t2": 0.8}}
    assert R.dominant_category(["heavyslam", "flashcannon"], cat, scored, {"t1": 1.0, "t2": 1.0}) == "physical"      # 同点は先頭の技
    assert R.dominant_category(["heavyslam", "flashcannon"], cat, scored, {"t1": 0.5, "t2": 1.0}) == "special"
    # タイプ強化の持ち物はそのタイプの技があるときだけ
    assert R.type_item_matches("silverpowder", ["Bug", "Grass"]) and not R.type_item_matches("silverpowder", ["Grass", "Ground"])
    assert R.type_item_matches("leftovers", ["Grass"]) and R.type_item_matches(None, [])
    # 固定技を無効にする担当の重みの割合
    eff = lambda t, types: 0.0 if (t == "Dragon" and "Fairy" in types) else 1.0      # noqa: E731
    sh = R.immune_share("Dragon", ["a", "b", "c"], {"a": ["Fairy"], "b": ["Steel"], "c": ["Water", "Fairy"]}, {"a": 1.0, "b": 1.0, "c": 0.5}, eff)
    assert abs(sh - 1.5 / 2.5) < 1e-9 and R.immune_share(None, ["a"], {}, {}, eff) == 0.0 and R.immune_share("Fire", ["a"], {"a": ["Fairy"]}, {}, eff) == 0.0
    print("test_template_resolution_and_bulk OK")


def test_attack_rules():
    """採用規則 (§7.3): 耐久型はデメリット技不可、速攻型は可 (種類ごとの個別則)、条件つき技は始動源があるときだけ"""
    overheat = {"self_boosts": {"spa": -2}, "accuracy": 90}
    bravebird = {"recoil": 0.33, "accuracy": 100}
    hjk = {"crash": True, "accuracy": 90}
    outrage = {"locked": True, "accuracy": 100}
    solar = {"charge": True, "accuracy": 100, "field_power": {"weather": ["sun"], "mult": 1.0}}
    roller = {"condition": "terrain_required", "accuracy": 100}
    belch = {"condition": "berry_eaten", "accuracy": 90}
    stone = {"accuracy": 80}
    assert R.demerit_kinds(overheat) == ["selfdrop"] and R.demerit_kinds(stone) == ["lowacc"] and R.demerit_kinds({"accuracy": 100}) == []
    hurricane = {"accuracy": 70, "accuracy_weather": {"rain": True, "sun": 50}}
    assert R.demerit_kinds(hurricane) == ["lowacc"] and R.demerit_kinds(hurricane, {"weather": "rain"}) == []     # 雨なら必中
    assert R.effective_accuracy(hurricane, {"weather": "sun"}) == 50 and R.effective_accuracy(hurricane, {}) == 70
    assert R.attack_allowed(overheat, "bulky", None, None, {}, "wall")[0]              # 自己能力低下は耐久型でも「穴」の判定は呼び出し側
    assert not R.attack_allowed(bravebird, "bulky", None, None, {}, "wall")[0]
    assert R.attack_allowed(bravebird, "bulky", "rockhead", None, {}, "wall")[0]       # いしあたまなら反動なし
    assert R.attack_allowed(bravebird, "fast", None, None, {}, "cleaner")[0]
    assert not R.attack_allowed(hjk, "bulky", None, None, {}, "wall")[0] and R.attack_allowed(hjk, "fast", None, None, {}, "cleaner")[0]
    assert not R.attack_allowed(outrage, "bulky", None, None, {}, "breaker")[0]
    assert R.attack_allowed(outrage, "bulky", None, "choiceband", {}, "breaker")[0]    # こだわりなら可
    assert not R.attack_allowed(solar, "fast", None, None, {}, "breaker")[0]
    assert R.attack_allowed(solar, "fast", None, None, {"weather": "sun"}, "breaker")[0]
    assert R.attack_allowed(roller, "fast", None, None, {"terrain": "electric"}, "breaker")[0]
    assert R.attack_allowed(roller, "fast", None, None, {}, "breaker") == (False, "フィールドの始動源なし")
    assert not R.attack_allowed(belch, "fast", None, None, {}, "breaker")[0]
    boom = {"condition": "selfko", "selfdestruct": "always", "accuracy": 100}
    assert not R.attack_allowed(boom, "fast", None, None, {}, "breaker")[0] and R.attack_allowed(boom, "fast", None, None, {}, "suicide_lead", True)[0]
    print("test_attack_rules OK")


def test_setup_axes_and_drops():
    """積み技の 3 軸 (§8): 主攻撃が上がるものだけ、トリックルーム計画では素早さ積みを使わない、あまのじゃくは積まない"""
    qd = {"setup_boosts": {"spa": 1, "spd": 1, "spe": 1}}
    np_ = {"setup_boosts": {"spa": 2}}
    sd = {"setup_boosts": {"atk": 2}}
    dd = {"setup_boosts": {"atk": 1, "spe": 1}}
    ag = {"setup_boosts": {"spe": 2}}
    cm = {"setup_boosts": {"spa": 1, "spd": 1}}
    assert R.setup_axes(qd, "offense", "spa", "outspeed", None) == (True, 2.25)
    assert R.setup_axes(qd, "offense", "spa", "neutral", None) == (True, 1.75)
    assert R.setup_axes(np_, "offense", "spa", "neutral", None) == (True, 2.0)
    assert R.setup_axes(sd, "offense", "spa", "neutral", None)[0] is False          # 特殊型につるぎのまいは不要
    assert R.setup_axes(ag, "offense", "atk", "neutral", None)[0] is False          # 主攻撃が上がらない
    assert R.setup_axes(dd, "offense", "atk", "trick_room", None)[0] is False       # トリルでは素早さ積みを使わない
    assert R.setup_axes(sd, "offense", "atk", "trick_room", None) == (True, 2.0)
    assert R.setup_axes(cm, "defense", "spa", "neutral", None)[0] is True and R.setup_axes(sd, "defense", "atk", "neutral", None)[0] is False
    assert R.setup_axes(qd, "offense", "spa", "outspeed", "contrary")[0] is False
    assert R.setup_axes(np_, "none", "spa", "neutral", None)[0] is False
    # 上げた能力を下げる攻撃技
    overheat = {"self_boosts": {"spa": -2}}
    cc = {"self_boosts": {"def": -1, "spd": -1}}
    scale = {"secondary": {"chance": 100, "self_boosts": {"def": -1, "spe": 1}}}
    assert R.drops_boosted_stat(overheat, {"spa": 1, "spd": 1, "spe": 1}) and not R.drops_boosted_stat(cc, {"atk": 2})
    assert R.drops_boosted_stat(cc, {"atk": 1, "def": 1}) and R.drops_boosted_stat(scale, {"def": 2})
    print("test_setup_axes_and_drops OK")


def test_ability_fit_and_utility_and_items():
    infos = {"flamebody": {"tags": ["contact_punish", "wall"]}, "swarm": {"tags": ["offense_pinch"]},
             "protean": {"tags": ["offense", "type_change"]}, "overgrow": {"tags": ["offense_pinch"]},
             "intimidate": {"tags": ["intimidate", "defense"]}, "frisk": {"tags": [], "value_zero": True},
             "klutz": {"tags": ["no_item"]}, "limber": {"tags": ["status_immune"]}}
    tpl = {"ability_tags": ("speed", "offense", "priority", "type_change")}
    ab, score, why = R.choose_ability_fit(["overgrow", "protean"], tpl, {}, infos.get)
    assert ab == "protean" and why == "offense/type_change"
    # 雛形に合うタグが無ければ同点 → 使用率で決める (使用率は同点のときだけ)
    ab, _s, _w = R.choose_ability_fit(["swarm", "flamebody"], tpl, {}, infos.get, {"flamebody": 99.0, "swarm": 1.0})
    assert ab == "flamebody"
    ab, _s, _w = R.choose_ability_fit(["frisk", "intimidate"], {"ability_tags": ()}, {"intimidate": 1.0}, infos.get)
    assert ab == "intimidate"
    ab, _s, _w = R.choose_ability_fit(["klutz", "limber"], {"ability_tags": ()}, {}, infos.get)
    assert ab == "limber"                                                           # ぶきよう は減点
    # 天候依存のタグは並びの天候と特性の条件が一致するときだけ (晴れの始動役にすながくれを選ばない)
    infos2 = {"sandveil": {"tags": ["weather_user"], "formula": [{"kind": "evasion_mult", "when": {"weather": "sand"}}]},
              "roughskin": {"tags": ["contact_punish"]},
              "chlorophyll": {"tags": ["weather_user", "speed"], "formula": [{"kind": "speed_mult", "when": {"weather": "sun"}}]}}
    tpl_w = {"ability_tags": ("weather_setter", "defense")}
    ab, _s, _w = R.choose_ability_fit(["sandveil", "roughskin"], tpl_w, {"weather_user": 1.0}, infos2.get,
                                      {"roughskin": 99.0, "sandveil": 1.0}, field={"weather": "sun"})
    assert ab == "roughskin"
    ab, _s, _w = R.choose_ability_fit(["sandveil", "roughskin"], tpl_w, {"weather_user": 1.0}, infos2.get,
                                      {"roughskin": 99.0, "sandveil": 1.0}, field={"weather": "sandstorm"})
    assert ab == "sandveil"
    ab, _s, w = R.choose_ability_fit(["chlorophyll", "roughskin"], {"ability_tags": ("weather_user",)}, {}, infos2.get,
                                     field={"weather": "sun"})
    assert ab == "chlorophyll" and w == "weather_user"
    assert R.ability_field_ok(infos2["sandveil"], "weather_user", None) and R.ability_field_ok(infos2["roughskin"], "weather_user", {"weather": "sun"})
    # 補助枠
    ls = {"stealthrock", "spikes", "willowisp", "thunderwave", "recover", "protect", "uturn", "trickroom", "tailwind"}
    prefs = {"status_order": ["thunderwave", "willowisp"], "speed_control_order": ["trickroom", "tailwind"], "setup_order": []}
    assert R.pick_utility("hazard", ls, [], prefs) == "stealthrock"
    assert R.pick_utility("hazard", ls, ["stealthrock"], prefs) == "spikes"
    assert R.pick_utility("status|protect", ls, [], prefs) == "thunderwave"
    assert R.pick_utility("heal|pivot", ls, [], prefs) == "recover" and R.pick_utility("removal|pivot", ls, [], prefs) == "uturn"
    assert R.pick_utility("speed_control", ls, [], prefs) == "trickroom" and R.pick_utility("setup", ls, [], prefs) is None
    assert R.pick_utility("field", {"sunnyday"}, [], prefs, role_field="sun") == "sunnyday"
    # 持ち物: クラスの順、使用済みを除く、使用率がある持ち物はクラス内で先、積み技ありはこだわりを外す
    legal = lambda i: i != "illegalitem"
    items = R.pick_item(("setup_berry", "sash", "orb", "stone"), "physical", ["Fire"], {"focussash"}, {"lumberry": 30.0, "sitrusberry": 10.0},
                        legal, None, None, "charizarditey", True, True, False)
    assert items == ["lumberry", "sitrusberry", "lifeorb", "charizarditey"]
    items = R.pick_item(("choice", "orb"), "special", ["Fire"], set(), {}, legal, None, None, None, False, False, False)
    assert items == ["choicespecs", "choicescarf", "lifeorb"]
    items = R.pick_item(("choice", "orb"), "physical", ["Fire"], set(), {}, legal, None, None, None, False, True, False)
    assert items == ["lifeorb"]                                                     # 積み技ありはこだわり無し
    items = R.pick_item(("weather_rock", "type_item"), "physical", ["Fire", "Flying"], set(), {}, legal, "sun", None, None, False, False, False)
    assert items == ["heatrock", "charcoal", "sharpbeak"]
    items = R.pick_item(("sash", "setup_berry"), "physical", [], set(), {}, legal, None, "unburden", None, False, False, True)
    assert items[0] == "whiteherb" and "sitrusberry" in items                        # かるわざ + 自己低下技 → しろいハーブ
    # クラスの持ち物が全部使用済み → 予備 (BUILD_ITEM_FALLBACK) から。持ち物なしにはしない
    items = R.pick_item(("orb", "sash"), "physical", [], {"lifeorb", "focussash"}, {}, legal, None, None, None, False, False, False)
    assert items and items[0] == "leftovers" and "lifeorb" not in items and "focussash" not in items
    # 補助枠: 分類の違う先制技は選ばない (おくびょうにでんこうせっか)、除外 (攻撃役のねむる) は飛ばす
    ls2 = {"quickattack", "vacuumwave", "rest", "recover"}
    cat_of = {"quickattack": "physical", "vacuumwave": "special"}.get
    assert R.pick_utility("priority", ls2, [], dict(prefs, category_of=cat_of, category="special")) == "vacuumwave"
    assert R.pick_utility("priority", ls2, [], dict(prefs, category_of=cat_of, category="physical")) == "quickattack"
    assert R.pick_utility("priority", ls2, [], dict(prefs, category_of=cat_of, category=None)) == "quickattack"
    assert R.pick_utility("heal", {"rest"}, [], dict(prefs, exclude={"rest"})) is None and R.pick_utility("heal", {"rest"}, [], prefs) == "rest"
    print("test_ability_fit_and_utility_and_items OK")


def test_generate_smoke():
    """実データの煙試験: 1002d の脅威 (meta_snapshot) を担当に、ウルガモスの積みエース型とガラルヤドキングの交代役の型"""
    from pathlib import Path
    from tools.team_build.meta_snapshot import load_snapshot, threat_sets, threat_weight
    p = Path("/Users/kenta/GitHub/PokemonChampioons_Adviser/logs/build_search/runs/ace_lopunny_1002d/meta_snapshot.json")
    if not p.exists():
        print("test_generate_smoke SKIP (meta_snapshot なし)")
        return
    doc = load_snapshot(p)
    tv = threat_sets(doc, 20)
    weights = {t["id"]: threat_weight(t) for t in doc["top"] if t["id"] in tv}
    ctx = R.RoleContext(threat_views=tv, weights=weights, ability_pct={"flamebody": 99.0, "swarm": 1.0})
    sets = R.generate_role_sets("volcarona", "setup_ace", ctx)
    assert sets and all("quiverdance" in s.moves for s in sets), [s.moves for s in sets]
    assert all("overheat" not in s.moves for s in sets)                               # 積みで上げた特攻を下げる技は使わない
    assert sets[0].ability == "flamebody" and sets[0].item in ("sitrusberry", "lumberry", "focussash")
    assert all("belch" not in s.moves for s in R.generate_role_sets("slowkinggalar", "pivot", ctx))
    assert R.generate_role_sets("whimsicott", "wall", ctx) == []                       # 速い種は壁型にしない (D-17)
    tr = R.RoleContext(threat_views=tv, weights=weights, speed_plan="trick_room")
    for s in R.generate_role_sets("kingambit", "setup_ace", tr):
        assert "swordsdance" in s.moves and s.evs.split("/")[5] == "0"                 # トリル計画: 剣舞、素早さ 0
    # 技の指定 (--moves): バシャーモ つるぎのまい + バトンタッチ は必ず入り、残りの 2 枠は攻撃技。変化技の指定があるのでこだわり系は持たない
    bp = R.RoleContext(threat_views=tv, weights=weights, required_moves=["swordsdance", "batonpass"])
    for role in ("setup_ace", "breaker"):
        sets_b = R.generate_role_sets("blaziken", role, bp)
        assert sets_b, role
        for s in sets_b:
            assert {"swordsdance", "batonpass"} <= set(s.moves) and len(s.moves) == 4 and len(set(s.moves)) == 4, s.moves
            assert not (s.item or "").startswith("choice"), s.item
            assert "req:swordsdance" in s.notes and "req:batonpass" in s.notes
    assert R.generate_role_sets("blaziken", "setup_ace", R.RoleContext(threat_views=tv, weights=weights, required_moves=["spore"])) == []
    print("test_generate_smoke OK")


def test_required_moves_helpers():
    """技の指定が雛形の補助枠を兼ねるかの判定 (純粋)"""
    assert R.utility_satisfied_by("pivot|status", ["swordsdance", "batonpass"]) == "batonpass"
    assert R.utility_satisfied_by("setup", ["swordsdance", "batonpass"], is_setup=lambda m: m == "swordsdance") == "swordsdance"
    assert R.utility_satisfied_by("setup", ["batonpass"], is_setup=lambda m: m == "swordsdance") is None
    assert R.utility_satisfied_by("hazard", ["batonpass"]) is None
    assert R.utility_satisfied_by("field|status", ["sunnyday"], role_field="sun") == "sunnyday"
    assert R.utility_satisfied_by("field", ["sunnyday"], role_field="rain") is None
    print("test_required_moves_helpers OK")


if __name__ == "__main__":
    test_template_resolution_and_bulk()
    test_attack_rules()
    test_setup_axes_and_drops()
    test_ability_fit_and_utility_and_items()
    test_required_moves_helpers()
    test_generate_smoke()
