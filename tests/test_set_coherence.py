"""型合成の整合規則のテスト (2026-09-02 ずぶとい+CS極振りロトム問題)。

    scripts/run_test.sh test_set_coherence

meta_sets が性格・配分・持ち物を属性ごとの最多で独立に貼り合わせていたため、
「ずぶとい (受け型の最多性格) + CS極振り (票割れしない攻撃配分) + スカーフ」
という実在しない型が合成されていた。整合ペアの使用率積最大化で
実在の系統が復元されることを、実測分布 (snapshot 22/23) で検証する。
純粋関数のみ。DB・ネットワークは使わない。
"""
from __future__ import annotations

from champions_agent.data.build_meta import (
    choose_coherent_spread, nature_fits, parse_points)

# rotomwash の実測分布 (spread_usage snap22/23)
WASH_NATURES = [("bold", 47.1), ("modest", 24.5), ("timid", 15.5),
                ("calm", 9.7)]
WASH_SPREADS = [("2/0/0/32/0/32", 13.8), ("32/0/32/0/2/0", 9.5),
                ("32/0/14/0/20/0", 5.3), ("32/0/32/2/0/0", 4.4)]
WASH_CATS = ["special", "special", "status", "special"]


def test_parse_points():
    assert parse_points("2/0/0/32/0/32") == {
        "hp": 2, "atk": 0, "def": 0, "spa": 32, "spd": 0, "spe": 32}
    assert parse_points(None) is None
    assert parse_points("32/0/32") is None
    print("test_parse_points OK")


def test_nature_fits_rejects_chimera():
    """ずぶとい (防御↑) は防御0のCS極振りと不整合"""
    assert not nature_fits("bold", "2/0/0/32/0/32", WASH_CATS)
    assert not nature_fits("calm", "2/0/0/32/0/32", WASH_CATS)
    assert nature_fits("modest", "2/0/0/32/0/32", WASH_CATS)
    assert nature_fits("timid", "2/0/0/32/0/32", WASH_CATS)
    assert nature_fits("bold", "32/0/32/0/2/0", WASH_CATS)  # HBなら整合
    print("test_nature_fits_rejects_chimera OK")


def test_nature_fits_offense_by_base_stats():
    """種族値受けのいじっぱりハッサム (H32/A2/B32) は物理技があれば整合"""
    scizor_evs = "32/2/32/0/0/0"
    assert nature_fits("adamant", scizor_evs, ["physical", "status"])
    # 物理技が無いのに攻撃補正は不整合
    assert not nature_fits("adamant", scizor_evs, ["special", "status"])
    # 補正元 (特攻ダウン) に投資があれば不整合
    assert not nature_fits("adamant", "0/32/0/32/0/2", ["physical"])
    print("test_nature_fits_offense_by_base_stats OK")


def test_nature_fits_defense_over_bulk_spread():
    """防御補正+無振りは、配分が攻撃的でなければ整合
    (ずぶとい/のんきHP極振りメタモン、わんぱく+HD振りカバルドン等)。
    攻撃的配分との組み合わせ (bold+CS) だけが不整合"""
    assert nature_fits("relaxed", "32/0/0/0/0/0", [])       # H極振りメタモン
    assert nature_fits("impish", "32/0/2/0/32/0", [])       # HD振りカバルドン
    assert not nature_fits("bold", "2/0/0/32/0/32", [])     # CS極振りは不整合
    print("test_nature_fits_defense_over_bulk_spread OK")


def test_move_categories_uses_dex():
    """技分類は図鑑から引く (DBのmovesテーブルは部分取り込みで欠落があり、
    ハッサムの4技中3技が引けず例外規則が発火しなかった実測に基づく)"""
    from champions_agent.data.build_meta import move_categories
    cats = move_categories(["bulletpunch", "swordsdance", "uturn", "roost"])
    assert cats == ["physical", "status", "physical", "status"], cats
    print("test_move_categories_uses_dex OK")


def test_nature_fits_permissive_when_unknown():
    """配分不明・無補正・未知の性格は棄却しない (実測値を残す)"""
    assert nature_fits(None, "2/0/0/32/0/32", [])
    assert nature_fits("serious", "2/0/0/32/0/32", [])   # 無補正
    assert nature_fits("bold", None, [])                 # 配分不明
    print("test_nature_fits_permissive_when_unknown OK")


def test_bulky_archetype_recovered():
    """たべのこし (非攻撃的持ち物) では ずぶとい+HB の受け型が復元される。
    最多同士の独立合成 (bold+CS) でも、単純な整合先頭 (modest+CS) でもなく、
    使用率積が最大の整合ペア (47.1×9.5) を選ぶ"""
    nature, evs = choose_coherent_spread(
        WASH_NATURES, WASH_SPREADS, WASH_CATS, "leftovers")
    assert (nature, evs) == ("bold", "32/0/32/0/2/0")
    print("test_bulky_archetype_recovered OK")


def test_offensive_item_selects_offensive_archetype():
    """スカーフ (攻撃的持ち物) では ひかえめ+CS のこだわり型になる"""
    nature, evs = choose_coherent_spread(
        WASH_NATURES, WASH_SPREADS, WASH_CATS, "choicescarf")
    assert (nature, evs) == ("modest", "2/0/0/32/0/32")
    print("test_offensive_item_selects_offensive_archetype OK")


def test_offensive_spread_with_neutral_item_kept():
    """メガ石等 (非攻撃的持ち物) でも、攻撃配分が主流の種はそのまま
    (メタグロス: いじっぱり+AS極振り+メタグロスナイト)"""
    nature, evs = choose_coherent_spread(
        [("adamant", 55.0), ("jolly", 20.0)],
        [("2/32/0/0/0/32", 40.0), ("32/32/1/0/1/0", 10.0)],
        ["physical", "physical", "physical", "physical"], "metagrossite")
    assert (nature, evs) == ("adamant", "2/32/0/0/0/32")
    print("test_offensive_spread_with_neutral_item_kept OK")


def test_scarf_ditto_keeps_bulk_spread():
    """攻撃技を持たない種はスカーフでも攻撃的配分に誘導しない
    (へんしんメタモン: のんき+HP極振り+スカーフが実在型)"""
    nature, evs = choose_coherent_spread(
        [("relaxed", 40.0), ("serious", 10.0)],
        [("32/0/0/0/0/0", 30.0), ("32/32/2/0/0/0", 5.0)],
        ["status"], "choicescarf")
    assert (nature, evs) == ("relaxed", "32/0/0/0/0/0")
    print("test_scarf_ditto_keeps_bulk_spread OK")


def test_fallback_when_no_pair_fits():
    """整合ペアが無ければ最多同士 (棄却しない)"""
    nature, evs = choose_coherent_spread(
        [("bold", 50.0)], [("2/0/0/32/0/32", 20.0)], ["special"], None)
    assert (nature, evs) == ("bold", "2/0/0/32/0/32")
    print("test_fallback_when_no_pair_fits OK")


if __name__ == "__main__":
    test_parse_points()
    test_nature_fits_rejects_chimera()
    test_nature_fits_offense_by_base_stats()
    test_nature_fits_defense_over_bulk_spread()
    test_move_categories_uses_dex()
    test_nature_fits_permissive_when_unknown()
    test_bulky_archetype_recovered()
    test_offensive_item_selects_offensive_archetype()
    test_offensive_spread_with_neutral_item_kept()
    test_scarf_ditto_keeps_bulk_spread()
    test_fallback_when_no_pair_fits()
