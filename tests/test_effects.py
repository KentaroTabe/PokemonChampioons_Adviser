"""技・特性の効果表の評価 (advisor/effects.py) と、ダメージ計算への統合 (advisor/damage.py) のテスト。

    python -m tests.test_effects
再設計 docs/TEAM_BUILD_REDESIGN_1002.md §7.5 / §7.6 / §9.3。数値は表の倍率から導いた比で検査し、絶対値は従来の計算と
一致する組 (ミミッキュ じゃれつく → ガブリアス) で固定する。
"""
from __future__ import annotations

from advisor import effects as E
from advisor.damage import FieldView, MonView, calc_damage, effective_speed
from advisor.dex import get_dex


def _view(sid, ev=None, nature=None, ability=None, item=None, hp_frac=1.0, status=None, boosts=None, types=None):
    sp = get_dex().species(sid)
    return MonView(species_id=sid, types=list(types or sp["types"]), base=sp["baseStats"], ev=ev or {}, nature=nature or {},
                   ability=ability, item=item, hp_frac=hp_frac, status=status, boosts=boosts or {})


def _avg(a, d, move, fv=None, **ctx):
    return calc_damage(a, d, move, fv, ctx=ctx or None)


def test_pure_evaluators():
    assert E.when_matches({"move_type": "Fire", "category": "Special"}, {"move_type": "Fire", "category": "Special"})
    assert not E.when_matches({"move_flag": "punch"}, {"move_flags": ("contact",)})
    assert E.when_matches({"weather": "sand"}, {"weather": "sandstorm"}) and E.when_matches({"weather": "sun"}, {"weather": "sun"})
    assert not E.when_matches({"unknown_condition": 1}, {})                # 知らない条件語は不成立
    assert E.when_matches({"user_hp_below": 0.3334}, {"user_hp_frac": 0.3}) and not E.when_matches({"user_hp_below": 0.3334}, {"user_hp_frac": 0.5})
    assert E.when_matches({"full_hp": True}, {"target_hp_frac": 1.0}) and not E.when_matches({"full_hp": True}, {"target_hp_frac": 0.9})
    # 特性の式 (実表)
    assert E.ability_known("sheerforce") and not E.ability_known("transistor")      # トランジスタは参戦種が持たない
    m, notes = E.offense_multiplier("sheerforce", {"has_secondary": True})
    assert abs(m - 1.3) < 1e-9 and notes == ["sheerforce×1.3"]
    assert E.offense_multiplier("sheerforce", {"has_secondary": False})[0] == 1.0
    assert abs(E.offense_multiplier("supremeoverlord", {"fainted_allies": 2})[0] - 1.2) < 1e-9
    assert E.offense_multiplier("supremeoverlord", {"fainted_allies": 0})[0] == 1.0
    assert abs(E.offense_multiplier("parentalbond", {})[0] - 1.25) < 1e-9
    assert E.type_change("pixilate", "Normal") == ("Fairy", 1.2) and E.type_change("pixilate", "Fire") == (None, 1.0)
    assert E.type_change("liquidvoice", "Normal", ("sound",)) == ("Water", 1.0)
    assert E.stab_any("protean") and E.stab_any("libero") and not E.stab_any("blaze")
    assert E.is_immune("lightningrod", "Electric") and not E.is_immune("lightningrod", "Water")
    assert E.is_immune("bulletproof", "Fire", ("bullet",)) and E.is_immune("goodasgold", "Normal", (), is_status=True)
    assert abs(E.defense_multiplier("multiscale", {"target_hp_frac": 1.0})[0] - 0.5) < 1e-9
    assert E.defense_multiplier("multiscale", {"target_hp_frac": 0.5})[0] == 1.0
    assert abs(E.defense_multiplier("fluffy", {"move_flags": ("contact",), "move_type": "Fire"})[0] - 1.0) < 1e-9   # 0.5 × 2.0
    assert E.accuracy_multiplier("compoundeyes", None, {}) == (1.3, False) and E.accuracy_multiplier(None, "noguard", {})[1]
    assert abs(E.accuracy_multiplier("hustle", "sandveil", {"category": "Physical", "weather": "sand"})[0] - 0.64) < 1e-9
    assert E.speed_multiplier("swiftswim", {"weather": "rain"}) == 2.0 and E.speed_multiplier("swiftswim", {"weather": "sun"}) == 1.0
    assert E.speed_multiplier("unburden", {"item_consumed": True}) == 2.0
    assert E.has_effect("moldbreaker", "ignore_target_ability") and E.has_effect("unaware", "ignore_foe_boosts")
    assert E.misc_value("megasol", "self_weather", "weather") == "sun"
    assert E.crit_chance("superluck", None, {}, {}) == 1 / 8 and E.crit_chance(None, "battlearmor", {"will_crit": True}, {}) == 0.0
    assert E.crit_chance("merciless", None, {}, {"target_status": "poison"}) == 1.0 and E.crit_multiplier("sniper") == 2.25
    # 技の条件・可変威力
    assert E.move_usable(E.move_entry("steelroller"), {"terrain": None}) == (False, "フィールドが無いと失敗")
    assert E.move_usable(E.move_entry("steelroller"), {"terrain": "electric"})[0]
    assert not E.move_usable(E.move_entry("belch"), {})[0] and E.move_usable(E.move_entry("belch"), {"berry_eaten": True})[0]
    assert E.variable_power(E.move_entry("lowkick"), 0, {"target_weight": 460.0}) == 120
    assert E.variable_power(E.move_entry("heavyslam"), 0, {"user_weight": 550.0, "target_weight": 100.0}) == 120
    assert E.variable_power(E.move_entry("gyroball"), 0, {"user_speed": 50, "target_speed": 200}) == 100
    assert E.variable_power(E.move_entry("hex"), 65, {"target_status": "burn"}) == 130 and E.variable_power(E.move_entry("hex"), 65, {}) == 65
    assert E.variable_power(E.move_entry("storedpower"), 20, {"user_boost_total": 3}) == 80
    assert E.variable_power(E.move_entry("lastrespects"), 50, {"fainted_allies": 2}) == 150
    assert E.variable_power(E.move_entry("acrobatics"), 55, {"user_item": None}) == 110
    assert E.hit_expectation(E.move_entry("bulletseed"), 100) == E.MULTIHIT_2_5_EXPECTED
    assert E.hit_expectation(E.move_entry("bulletseed"), 100, skill_link=True) == 5.0
    assert abs(E.hit_expectation(E.move_entry("tripleaxel"), 90) - (0.9 + 2 * 0.81 + 3 * 0.729)) < 1e-9
    assert E.hit_expectation(E.move_entry("tripleaxel"), None) == 6.0
    print("test_pure_evaluators OK")


def test_damage_integration():
    garchomp = _view("garchomp", {"hp": 252})
    # 従来と同じ値 (ミミッキュ A252 いじっぱり 珠 じゃれつく → ガブリアス H252): min 85.9 / max 101.1、expected は命中 90% 込み
    mimi = _view("mimikyu", {"atk": 252}, {"atk": 1.1}, item="lifeorb", ability="disguise")
    d = calc_damage(mimi, garchomp, "playrough")
    assert (d["min"], d["max"], d["avg"]) == (85.9, 101.1, 93.5) and abs(d["expected"] - 93.5 * 0.9) < 0.15 and d["accuracy"] == 90.0
    # 条件つき技: アイアンローラーはフィールドが無いと 0、あれば通る
    exca = _view("excadrill", {"atk": 252})
    assert calc_damage(exca, garchomp, "steelroller")["avg"] == 0.0
    assert calc_damage(exca, garchomp, "steelroller")["notes"] == ["フィールドが無いと失敗"]
    assert calc_damage(exca, garchomp, "steelroller", FieldView(terrain="electric"))["avg"] > 50
    # ゲップはきのみを食べた後だけ
    slow = _view("slowkinggalar", {"spa": 252}, ability="regenerator")
    assert calc_damage(slow, garchomp, "belch")["avg"] == 0.0 and calc_damage(slow, garchomp, "belch", ctx={"berry_eaten": True})["avg"] > 0
    # 連続技: トリプルアクセルは 1 発ごとの命中 (期待 6 回分 → 命中込み 4.71 回分)、へんげんじざいでタイプ一致
    meow = _view("meowscarada", {"atk": 252}, ability="protean")
    d = calc_damage(meow, garchomp, "tripleaxel")
    assert d["hits"] == 6.0 and abs(d["expected"] / d["avg"] - (0.9 + 2 * 0.81 + 3 * 0.729) / 6) < 0.01
    assert "特性proteanでタイプ一致" in d["notes"]
    # 確定急所 (トリックフラワー) は 1.5 倍
    d_ft = calc_damage(meow, garchomp, "flowertrick")
    d_plain = calc_damage(_view("meowscarada", {"atk": 252}, ability="protean"), garchomp, "flowertrick")
    assert "急所" in d_ft["notes"] and d_ft["avg"] == d_plain["avg"]
    # スキルリンクで 5 回
    hera = _view("heracrossmega", {"atk": 252}, ability="skilllink")
    assert calc_damage(hera, garchomp, "pinmissile")["hits"] == 5.0
    assert calc_damage(_view("heracrossmega", {"atk": 252}, ability="guts"), garchomp, "pinmissile")["hits"] == 3.1
    # 参照する能力の上書き: ボディプレスは防御、サイコショックは相手の防御、イカサマは相手の攻撃
    arch = _view("archaludon", {"def": 252}, ability="stamina")
    arch_atk = _view("archaludon", {"atk": 252}, ability="stamina")
    assert calc_damage(arch, garchomp, "bodypress")["avg"] > calc_damage(arch_atk, garchomp, "bodypress")["avg"]
    psy = _view("gardevoir", {"spa": 252})
    bulky_def = _view("garchomp", {"hp": 252, "def": 252})
    bulky_spd = _view("garchomp", {"hp": 252, "spd": 252})
    assert calc_damage(psy, bulky_def, "psyshock")["avg"] < calc_damage(psy, bulky_spd, "psyshock")["avg"]
    foul = _view("umbreon", {"hp": 252})
    strong = _view("garchomp", {"atk": 252}, {"atk": 1.1})
    weak = _view("garchomp", {"spa": 252})
    assert calc_damage(foul, strong, "foulplay")["avg"] > calc_damage(foul, weak, "foulplay")["avg"]
    # 可変威力: けたぐり (カビゴン 460kg → 120)、ジャイロボール
    snor = _view("snorlax", {"hp": 252}, ability="thickfat")
    assert calc_damage(_view("garchomp", {"atk": 252}), snor, "lowkick")["avg"] > 50
    # 天候で変わる命中: ぼうふう は雨で必中、晴れで 50
    peli = _view("pelipper", {"spa": 252}, ability="drizzle")
    assert calc_damage(peli, garchomp, "hurricane", FieldView(weather="rain"))["accuracy"] is None
    assert calc_damage(peli, garchomp, "hurricane")["accuracy"] == 70.0
    assert calc_damage(peli, garchomp, "hurricane", FieldView(weather="sun"))["accuracy"] == 50.0
    # 特性: 命中 (ふくがん 1.3 倍、上限 100)、ノーガード必中、きもったま、ちからずく、がんじょうあご、フェアリースキン
    assert calc_damage(_view("vivillon", {"spa": 252}, ability="compoundeyes"), garchomp, "hurricane")["accuracy"] == 91.0
    assert calc_damage(_view("golurk", {"atk": 252}, ability="noguard"), garchomp, "dynamicpunch")["accuracy"] is None
    ghost = _view("gengar", {"hp": 252})
    assert calc_damage(_view("kangaskhan", {"atk": 252}, ability="scrappy"), ghost, "doubleedge")["avg"] > 0
    assert calc_damage(_view("kangaskhan", {"atk": 252}, ability="earlybird"), ghost, "doubleedge")["avg"] == 0.0
    sf = calc_damage(_view("feraligatr", {"atk": 252}, ability="sheerforce"), garchomp, "icepunch")
    plain = calc_damage(_view("feraligatr", {"atk": 252}, ability="torrent"), garchomp, "icepunch")
    assert abs(sf["avg"] / plain["avg"] - 1.3) < 0.02 and "sheerforce×1.3" in sf["notes"]
    bite = calc_damage(_view("tyrantrum", {"atk": 252}, ability="strongjaw"), garchomp, "crunch")
    assert abs(bite["avg"] / calc_damage(_view("tyrantrum", {"atk": 252}, ability="rockhead"), garchomp, "crunch")["avg"] - 1.5) < 0.02
    pix = calc_damage(_view("sylveon", {"spa": 252}, ability="pixilate"), garchomp, "hypervoice")
    assert "特性pixilateでFairyタイプ" in pix["notes"] and pix["type_mult"] == 2.0
    # 防御側: マルチスケイル (満タンだけ)、ファーコート (物理半減)、もふもふ (接触半減・炎 2 倍)、ひらいしん無効
    dnite = _view("dragonite", {"hp": 252}, ability="multiscale")
    full = calc_damage(strong, dnite, "dragonclaw")["avg"]                    # カイリューはひこうで じしん 無効なので竜技で
    hurt = calc_damage(strong, _view("dragonite", {"hp": 252}, ability="multiscale", hp_frac=0.5), "dragonclaw")["avg"]
    assert full > 0 and abs(full / hurt - 0.5) < 0.02
    fur = calc_damage(strong, _view("furfrou", {"hp": 252}, ability="furcoat"), "earthquake")["avg"]
    nofur = calc_damage(strong, _view("furfrou", {"hp": 252}), "earthquake")["avg"]
    assert abs(fur / nofur - 0.5) < 0.02
    fluff = _view("houndstone", {"hp": 252}, ability="fluffy")
    plain_h = _view("houndstone", {"hp": 252})
    assert abs(calc_damage(strong, fluff, "dragonclaw")["avg"] / calc_damage(strong, plain_h, "dragonclaw")["avg"] - 0.5) < 0.02   # 接触は半減
    assert calc_damage(strong, fluff, "earthquake")["avg"] == calc_damage(strong, plain_h, "earthquake")["avg"]             # 非接触は等倍
    fire = _view("charizard", {"spa": 252})
    assert abs(calc_damage(fire, fluff, "flamethrower")["avg"] / calc_damage(fire, plain_h, "flamethrower")["avg"] - 2.0) < 0.02   # 炎は 2 倍
    assert calc_damage(_view("garchomp", {"atk": 252}), _view("raichu", {"hp": 252}, ability="lightningrod"), "earthquake")["avg"] > 0
    assert calc_damage(_view("pawmot", {"atk": 252}), _view("raichu", {"hp": 252}, ability="lightningrod"), "thunderpunch")["avg"] == 0.0
    # かたやぶりは効果表の無効化も無視する
    assert calc_damage(_view("basculegion", {"atk": 252}, ability="moldbreaker"), _view("raichu", {"hp": 252}, ability="lightningrod"),
                       "wavecrash")["avg"] > 0
    # そうだいしょう・はりこみは盤面の文脈 (ctx) で効く
    king = _view("kingambit", {"atk": 252}, ability="supremeoverlord")
    base = calc_damage(king, garchomp, "kowtowcleave")["avg"]
    assert abs(calc_damage(king, garchomp, "kowtowcleave", ctx={"fainted_allies": 3})["avg"] / base - 1.3) < 0.02
    mab = _view("mabosstiff", {"atk": 252}, ability="stakeout")
    assert abs(calc_damage(mab, garchomp, "crunch", ctx={"target_switched_in": True})["avg"] / calc_damage(mab, garchomp, "crunch")["avg"] - 2.0) < 0.02
    # 素早さ: 効果表の speed_mult (すいすい) と かるわざ の発動
    bascu = _view("basculegion", {"spe": 252}, ability="swiftswim")
    assert effective_speed(bascu, FieldView(weather="rain")) == effective_speed(bascu) * 2
    haw = _view("hawlucha", {"spe": 252}, ability="unburden")
    assert effective_speed(haw, item_consumed=True) == effective_speed(haw) * 2
    # 表に無い特性 (参戦外) は従来の辞書で補う (トランジスタ)
    assert calc_damage(_view("regieleki", {"spa": 252}, ability="transistor"), garchomp, "thunderbolt")["avg"] == 0.0   # 地面に無効
    assert calc_damage(_view("regieleki", {"spa": 252}, ability="transistor"), _view("pelipper", {"hp": 252}), "thunderbolt")["avg"] > \
        calc_damage(_view("regieleki", {"spa": 252}), _view("pelipper", {"hp": 252}), "thunderbolt")["avg"]
    print("test_damage_integration OK")


if __name__ == "__main__":
    test_pure_evaluators()
    test_damage_integration()
