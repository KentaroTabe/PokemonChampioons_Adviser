"""Interaction Matrix (対面特徴) のテスト。図鑑データ (advisor/data/dex.json) で実種族を計算する。

    python -m tests.test_team_build_interaction
"""
from __future__ import annotations

from tools.team_build import interaction as I


def _set(item, nature, evs, moves, ability=None):
    return {"item": item, "nature": nature, "evs": evs, "moves": moves, "ability": ability}


def test_rows_are_bounded_and_sensible():
    garchomp, gmoves = I.view_from_set("garchomp", _set("focussash", "jolly", "2/32/0/0/0/32",
                                                        ["earthquake", "stealthrock", "scaleshot", "swordsdance"]))
    kingambit, kmoves = I.view_from_set("kingambit", _set("blackglasses", "adamant", "32/32/0/0/0/2",
                                                          ["suckerpunch", "kowtowcleave", "ironhead", "swordsdance"]))
    gengar, gemoves = I.view_from_set("gengar", _set("choicescarf", "timid", "2/0/0/32/0/32",
                                                     ["shadowball", "sludgebomb", "focusblast", "thunderbolt"]))
    primarina, pmoves = I.view_from_set("primarina", _set("leftovers", "modest", "32/0/0/32/0/2",
                                                          ["moonblast", "sparklingaria", "aquajet", "encore"]))
    stones = {"metagrossite"}
    row = I.interaction_row("kingambit", kingambit, kmoves, "garchomp", garchomp, gmoves, stones)
    for k in ("lead", "switch_in", "revenge"):
        assert row[k] is None or 0.0 <= row[k] <= 1.0, (k, row[k])
    assert row["speed_control"] == "slower"                      # ドドゲザン S2 < ガブリアス S32
    assert row["hazard"] == {"sets": False, "removes": False}
    assert row["uses_mega"] is False and 0.0 <= row["resource_cost"]["hp"] <= 1.0
    assert row["setup_stop"] is not None                          # ガブリアスは剣舞持ち
    # 地震 2 倍を受けるドドゲザンは後投げしにくく、対面でも不利寄り
    assert row["switch_in"] <= 0.5, row
    # 削れたゲンガー (50%) にはふいうちで確実に上から落とせる
    rev = I.revenge_score(kingambit, kmoves, gengar, gemoves)
    assert rev == 1.0, rev
    # ガブリアスの行: ステロ持ち
    grow = I.interaction_row("garchomp", garchomp, gmoves, "kingambit", kingambit, kmoves, stones)
    assert grow["hazard"]["sets"] is True and grow["speed_control"] == "faster"
    assert grow["lead"] is not None and grow["lead"] >= 0.5, grow   # 地震で有利
    # アシレーヌ vs ガブリアス: フェアリー技で対面有利、切り返しもできる
    prow = I.interaction_row("primarina", primarina, pmoves, "garchomp", garchomp, gmoves, stones)
    assert prow["lead"] >= 0.5 and prow["revenge"] > 0.0, prow
    cv = I.coverage_value(prow)          # 対面 / 後投げ / 切り返しの加重平均 (飽和しない)
    assert 0.0 <= cv <= 1.0 and cv <= max(prow["lead"], prow["switch_in"], prow["revenge"]), cv
    # メガ石の型はメガ後の種族で評価される
    meta, mmoves = I.view_from_set("metagross", _set("metagrossite", "adamant", "2/32/0/0/0/32",
                                                     ["bulletpunch", "psychicfangs", "earthquake", "icepunch"]))
    assert meta.species_id == "metagrossmega", meta.species_id
    print("test_rows_are_bounded_and_sensible OK")


def test_points_conversion_and_matrix():
    assert I._points_to_ev("2/32/0/0/0/32") == {"hp": 16, "atk": 252, "spe": 252}
    assert I._points_to_ev({"h": 32, "c": 32, "s": 2}) == {"hp": 252, "spa": 252, "spe": 16}
    # my_team.json と同じ規約: 値ごとに 32 以下は能力ポイント (×8)、33 以上は努力値そのまま
    assert I._points_to_ev({"hp": 252, "spe": 4}) == {"hp": 252, "spe": 32}
    my = {"garchomp": I.view_from_set("garchomp", _set("focussash", "jolly", "2/32/0/0/0/32",
                                                        ["earthquake", "stealthrock", "scaleshot", "swordsdance"]))}
    th = {"gengar": I.view_from_set("gengar", _set("choicescarf", "timid", "2/0/0/32/0/32",
                                                    ["shadowball", "sludgebomb", "focusblast", "thunderbolt"])),
          "nosuchmon": (None, [])}
    m = I.matrix(my, th)
    assert "lead" in m["garchomp"]["gengar"] and "error" in m["garchomp"]["nosuchmon"]
    print("test_points_conversion_and_matrix OK")


def test_mega_stone_xy_maps_to_form():
    """X/Y のメガ石は対応するフォルムで評価する (ライチュウナイトY → raichumegay。以前は megax に倒れていた)"""
    y, _ = I.view_from_set("raichu", _set("raichunitey", "timid", "2/0/0/32/0/32", ["zapcannon"]))
    x, _ = I.view_from_set("raichu", _set("raichunitex", "adamant", "2/32/0/0/0/32", ["voltt tackle".replace(" ", "")]))
    assert y.species_id == "raichumegay" and y.base["spa"] == 160, (y.species_id, y.base)
    assert x.species_id == "raichumegax" and x.base["atk"] == 135, (x.species_id, x.base)
    m, _ = I.view_from_set("metagross", _set("metagrossite", "adamant", "2/32/0/0/0/32", ["bulletpunch"]))
    assert m.species_id == "metagrossmega", m.species_id
    print("test_mega_stone_xy_maps_to_form OK")


def test_mega_ability_and_duel_field():
    """メガ石の型はメガ後の特性で評価し (ひでり/ちからもち)、自分の特性で張れる天候/フィールドを対面の場にする"""
    from dataclasses import replace
    ymoves = ["fireblast", "solarbeam", "airslash", "roost"]
    y, _ = I.view_from_set("charizard", _set("charizarditey", "timid", "2/0/0/32/0/32", ymoves, ability="blaze"))
    assert y.species_id == "charizardmegay" and y.ability == "drought", (y.species_id, y.ability)
    assert I.mega_ability("mawilemega") == "hugepower" and I.mega_ability("nosuchmega") is None
    plain, _ = I.view_from_set("charizard", _set("focussash", "timid", "2/0/0/32/0/32", ymoves, ability="blaze"))
    assert plain.ability == "blaze"                                       # メガ石が無ければそのまま
    chomp, cmoves = I.view_from_set("garchomp", _set("focussash", "jolly", "2/32/0/0/0/32",
                                                     ["earthquake", "outrage", "stoneedge", "swordsdance"]))
    fv = I.duel_field(y, chomp)
    assert fv is not None and fv.weather == "sun" and fv.terrain is None
    assert I.duel_field(chomp, y).weather == "sun" and I.duel_field(chomp, chomp) is None   # 相手の特性でも場になる
    # 晴れでほのお技 1.5 倍: 同じ実数値でメガ前の特性 (もうか) のままより最大打点が大きい
    y_blaze = replace(y, ability="blaze")
    d_sun = I._best_dmg(y, chomp, ["fireblast"], I.duel_field(y, chomp))
    d_plain = I._best_dmg(y_blaze, chomp, ["fireblast"], I.duel_field(y_blaze, chomp))
    assert d_plain > 0 and 1.4 < d_sun / d_plain < 1.6, (d_sun, d_plain)
    row = I.interaction_row("charizard", y, ymoves, "garchomp", chomp, cmoves, {"charizarditey"})
    assert row["uses_mega"] is True and row["lead"] is not None and row["field"] == {"terrain": None, "weather": "sun"}
    assert row["lead"] >= I.interaction_row("charizard", y_blaze, ymoves, "garchomp", chomp, cmoves, set())["lead"]
    print("test_mega_ability_and_duel_field OK")


def test_field_override_and_resolve():
    """並びの場 (設置役が張るサイコフィールド等) の指定: 無い種別は両者の特性の場で埋める。ワイドフォース使いの行が上がる"""
    from advisor.damage import FieldView
    imoves = ["expandingforce", "dazzlinggleam", "mysticalfire", "protect"]
    ind, _ = I.view_from_set("indeedee", _set("focussash", "timid", "2/0/0/32/0/32", imoves, ability="synchronize"))
    peli, pmoves = I.view_from_set("pelipper", _set("leftovers", "modest", "32/0/0/32/0/2",
                                                    ["hurricane", "surf", "icebeam", "roost"], ability="drizzle"))
    psy = FieldView(terrain="psychic")
    fv = I.resolve_field(ind, peli, psy)
    assert fv.terrain == "psychic" and fv.weather == "rain"          # 指定の場 + 相手の特性の雨
    assert I.resolve_field(ind, peli, None).weather == "rain" and I.resolve_field(ind, peli, None).terrain is None
    assert I.resolve_field(ind, ind, None) is None and I.resolve_field(ind, ind, psy) is psy
    chomp, cmoves = I.view_from_set("garchomp", _set("focussash", "jolly", "2/32/0/0/0/32",
                                                     ["earthquake", "outrage", "stoneedge", "swordsdance"]))
    plain = I.interaction_row("indeedee", ind, imoves, "garchomp", chomp, cmoves, set())
    under = I.interaction_row("indeedee", ind, imoves, "garchomp", chomp, cmoves, set(), fieldv=psy)
    assert under["field"] == {"terrain": "psychic", "weather": None} and plain["field"] == {"terrain": None, "weather": None}
    assert I._best_dmg(ind, chomp, ["expandingforce"], psy) > I._best_dmg(ind, chomp, ["expandingforce"]) * 1.8
    assert under["lead"] >= plain["lead"] and under["revenge"] >= plain["revenge"]
    m = I.matrix({"indeedee": (ind, imoves)}, {"garchomp": (chomp, cmoves)}, fieldv=psy)
    assert m["indeedee"]["garchomp"]["field"]["terrain"] == "psychic"
    print("test_field_override_and_resolve OK")


if __name__ == "__main__":
    test_rows_are_bounded_and_sensible()
    test_points_conversion_and_matrix()
    test_mega_ability_and_duel_field()
    test_field_override_and_resolve()
    test_mega_stone_xy_maps_to_form()
