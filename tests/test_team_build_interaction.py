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
    assert I.coverage_value(prow) >= prow["lead"]
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


if __name__ == "__main__":
    test_rows_are_bounded_and_sensible()
    test_points_conversion_and_matrix()
