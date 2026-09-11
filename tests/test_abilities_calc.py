"""特性を考慮した計算 (素早さ/火力/耐久) の検証。

実戦で「特性未考慮のアドバイス」が散見された報告への再発防止:
すいすい/ようりょくそ等の天候素早さ、ピンチ特性、防御特性など。

使い方: python -m tests.test_abilities_calc
"""
from advisor.damage import (FieldView, MonView, calc_damage, effective_speed)
from advisor.dex import get_dex


def _view(sid, ability=None, ev=None, item=None, hp_frac=1.0, status=None):
    sp = get_dex().species(sid)
    return MonView(species_id=sid, name_ja=sid, types=sp["types"],
                   base=sp["baseStats"], ability=ability, item=item,
                   hp_frac=hp_frac, status=status,
                   ev=ev or {"atk": 252, "spa": 252, "spe": 252})


def test_weather_speed_abilities():
    swampert = _view("swampertmega", ability="swiftswim")
    base = effective_speed(swampert)
    rain = effective_speed(swampert, FieldView(weather="rain"))
    assert rain == base * 2, (base, rain)
    lilligant = _view("lilligant", ability="chlorophyll")
    assert effective_speed(lilligant, FieldView(weather="sun")) == \
        effective_speed(lilligant) * 2
    dre = _view("excadrill", ability="sandrush")
    assert effective_speed(dre, FieldView(weather="sandstorm")) == \
        effective_speed(dre) * 2
    # スカーフ+まひの併算
    scarfed = _view("garchomp", item="choicescarf", status="paralysis")
    assert effective_speed(scarfed) == int(int(_view("garchomp").stat("spe") * 1.5) * 0.5)
    print("test_weather_speed_abilities OK")


def test_pinch_and_type_boost():
    opp = _view("garchomp")
    blaze = _view("charizard", ability="blaze", hp_frac=0.3)
    normal = _view("charizard", ability="blaze", hp_frac=1.0)
    d1 = calc_damage(blaze, opp, "flamethrower")
    d0 = calc_damage(normal, opp, "flamethrower")
    assert d1["avg"] > d0["avg"] * 1.3, (d0["avg"], d1["avg"])
    # トランジスタ (対象は電気が通るペリッパー)
    peli = _view("pelipper", ev={"hp": 252})
    tr = _view("regieleki", ability="transistor")
    no = _view("regieleki")
    assert calc_damage(tr, peli, "thunderbolt")["avg"] > \
        calc_damage(no, peli, "thunderbolt")["avg"]
    print("test_pinch_and_type_boost OK")


def test_defensive_abilities():
    atk = _view("charizard")
    fur = _view("furfrou", ability="furcoat", ev={"hp": 252})
    plain = _view("furfrou", ev={"hp": 252})
    assert calc_damage(atk, fur, "earthquake")["avg"] < \
        calc_damage(atk, plain, "earthquake")["avg"] * 0.6
    # フィルター (抜群0.75)
    filt = _view("mimikyu", ability="filter", ev={"hp": 252})
    pl = _view("mimikyu", ev={"hp": 252})
    d_f = calc_damage(_view("metagross"), filt, "ironhead")
    d_p = calc_damage(_view("metagross"), pl, "ironhead")
    assert abs(d_f["avg"] / d_p["avg"] - 0.75) < 0.05, (d_f["avg"], d_p["avg"])
    print("test_defensive_abilities OK")


def test_field_move_effects():
    """技固有のフィールド/天候の効果 (表 advisor/data/field_effects.json): タイプ変化・威力倍率・接地条件・天候の防御補正"""
    from dataclasses import replace
    from advisor.damage import field_move_effect
    peli = _view("pelipper", ability="drizzle")
    hippo = _view("hippowdon")           # じめん: みず 2 倍、ノーマル 1 倍
    plain = calc_damage(peli, hippo, "weatherball")
    rain = calc_damage(peli, hippo, "weatherball", FieldView(weather="rain"))
    # 雨のウェザーボール: みずタイプ (相性 2 倍・STAB 1.5) × 威力 2 倍 × 雨 1.5 = 9 倍 (乱数と切り捨てで少しずれる)
    assert plain["type_mult"] == 1.0 and rain["type_mult"] == 2.0
    assert 8.0 < rain["avg"] / plain["avg"] < 10.0, (plain["avg"], rain["avg"])
    assert rain["avg"] == calc_damage(peli, hippo, "weatherball", FieldView(weather="rain"), override_move_type="Water")["avg"]
    # ダイチノハドウ: サイコフィールドで接地した使用者ならエスパータイプ・威力 2 倍・フィールド 1.3 倍 (どちらも STAB)
    ind = _view("indeedee", ability="psychicsurge")     # エスパー/ノーマル
    chomp = _view("garchomp")                           # ドラゴン/じめん: エスパーもノーマルも 1 倍
    tp = calc_damage(ind, chomp, "terrainpulse")
    tp_psy = calc_damage(ind, chomp, "terrainpulse", FieldView(terrain="psychic"))
    assert 2.4 < tp_psy["avg"] / tp["avg"] < 2.8, (tp["avg"], tp_psy["avg"])
    # ワイドフォース: 1.5 倍 (+ フィールド 1.3 倍)。使用者が接地していなければどちらも無し
    ef = calc_damage(ind, chomp, "expandingforce")
    ef_psy = calc_damage(ind, chomp, "expandingforce", FieldView(terrain="psychic"))
    assert 1.85 < ef_psy["avg"] / ef["avg"] < 2.05, (ef["avg"], ef_psy["avg"])
    air = replace(ind, types=["Psychic", "Flying"])
    assert calc_damage(air, chomp, "expandingforce", FieldView(terrain="psychic"))["avg"] == calc_damage(air, chomp, "expandingforce")["avg"]
    # ライジングボルト: エレキフィールドで相手が接地していれば 2 倍 (+ 使用者接地のフィールド 1.3 倍)、
    # 浮いている相手には技固有の倍率は無し (フィールドの 1.3 倍だけ)
    prim = _view("primarina")                          # みず/フェアリー: でんき 2 倍、接地
    rv = calc_damage(ind, prim, "risingvoltage")
    rv_ele = calc_damage(ind, prim, "risingvoltage", FieldView(terrain="electric"))
    assert 2.4 < rv_ele["avg"] / rv["avg"] < 2.8, (rv["avg"], rv_ele["avg"])
    bird = _view("corviknight")                        # はがね/ひこう: 浮いている
    rv_b = calc_damage(ind, bird, "risingvoltage")
    rv_b_ele = calc_damage(ind, bird, "risingvoltage", FieldView(terrain="electric"))
    assert 1.2 < rv_b_ele["avg"] / rv_b["avg"] < 1.4, (rv_b["avg"], rv_b_ele["avg"])
    # ソーラービーム: 晴れなら等倍 (1 ターン)、雨なら 0.5 倍
    sb = calc_damage(ind, hippo, "solarbeam")
    assert calc_damage(ind, hippo, "solarbeam", FieldView(weather="sun"))["avg"] == sb["avg"]
    assert 0.4 < calc_damage(ind, hippo, "solarbeam", FieldView(weather="rain"))["avg"] / sb["avg"] < 0.6
    # じしん: グラスフィールドで接地した相手には 0.5 倍 (浮いている相手は元々無効)
    eq = calc_damage(chomp, hippo, "earthquake")
    assert 0.45 < calc_damage(chomp, hippo, "earthquake", FieldView(terrain="grassy"))["avg"] / eq["avg"] < 0.55
    # 天候の防御補正: ゆきでこおりタイプの防御 1.5 倍、砂嵐でいわタイプの特防 1.5 倍
    ice = _view("glaceon", ev={"hp": 252})
    assert 0.6 < calc_damage(chomp, ice, "earthquake", FieldView(weather="snow"))["avg"] / calc_damage(chomp, ice, "earthquake")["avg"] < 0.72
    rock = _view("gigalith", ev={"hp": 252})
    assert 0.6 < calc_damage(ind, rock, "psychic", FieldView(weather="sandstorm"))["avg"] / calc_damage(ind, rock, "psychic")["avg"] < 0.72
    # 純粋関数
    assert field_move_effect("expandingforce", FieldView(terrain="psychic"), True, True) == (1.5, None, True)
    assert field_move_effect("expandingforce", FieldView(terrain="psychic"), False, True) == (1.0, None, False)
    assert field_move_effect("weatherball", FieldView(weather="snow")) == (2.0, "Ice", True)
    assert field_move_effect("solarbeam", FieldView(weather="sandstorm")) == (0.5, None, False)
    assert field_move_effect("psychic", FieldView(terrain="psychic")) == (1.0, None, False)
    assert field_move_effect("expandingforce", None) == (1.0, None, False)
    print("test_field_move_effects OK")


if __name__ == "__main__":
    test_weather_speed_abilities()
    test_pinch_and_type_boost()
    test_defensive_abilities()
    test_field_move_effects()
    print("ALL OK")
