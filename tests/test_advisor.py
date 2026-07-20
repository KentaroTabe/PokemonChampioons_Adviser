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
    print("--- アドバイス出力例 ---")
    from advisor.service import Advisor
    print(Advisor().format_advice(advice))


if __name__ == "__main__":
    test_stats()
    test_type_chart()
    test_damage_sanity()
    test_weather_and_screens()
    test_evaluate_end_to_end()
