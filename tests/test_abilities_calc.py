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


if __name__ == "__main__":
    test_weather_speed_abilities()
    test_pinch_and_type_boost()
    test_defensive_abilities()
    print("ALL OK")
