"""1 試合 1 回の資源 (メガシンカ) の推定 (advisor/gimmick) のテスト。使用率は引数で渡し DB を読まない。

    python -m tests.test_gimmick
"""
from __future__ import annotations

from advisor import gimmick as G

USAGE = {"charizard": {"charizarditey": 60.0, "charizarditex": 30.0, "focussash": 5.0},
         "delphox": {"delphoxite": 99.0}, "garchomp": {"focussash": 40.0, "garchompite": 1.0}}


def test_forms_and_priors():
    assert set(G.mega_forms("charizard")) == {"charizardmegax", "charizardmegay"}
    assert G.mega_forms("charizardmegay") == ["charizardmegay"] and G.mega_forms("garchomp") and G.mega_forms("mimikyu") == []
    assert G.is_mega_form("delphoxmega") and not G.is_mega_form("delphox")
    p = G.stone_prior("charizard", usage=USAGE)
    assert abs(p["charizardmegay"] - 0.6) < 1e-9 and abs(p["charizardmegax"] - 0.3) < 1e-9
    assert G.stone_prior("delphox", usage=USAGE) == {"delphoxmega": 0.99}
    assert G.stone_prior("garchomp", usage=USAGE) == {}                     # 1% は下限 2% 未満 → 無視
    # 石を持てるが使用率が無い種は既定値を姿の数で按分、持てない種は空
    d = G.stone_prior("gengar", usage={})
    assert d and abs(sum(d.values()) - 0.5) < 1e-9 and G.stone_prior("mimikyu", usage={}) == {}
    print("test_forms_and_priors OK")


def test_expected_mega_overrides():
    # 判明情報で上書き: 石と判明 → 1.0、石以外 → 空、メガ後 → 空、権利消費済み → 空、不明 → 事前分布
    assert G.expected_mega("charizard", item_id="charizarditex", usage=USAGE) == {"charizardmegax": 1.0}
    assert G.expected_mega("charizard", item_id="focussash", usage=USAGE) == {}
    assert G.expected_mega("charizard", is_mega=True, usage=USAGE) == {} and G.expected_mega("charizardmegay", usage=USAGE) == {}
    assert G.expected_mega("charizard", mega_used=True, usage=USAGE) == {}
    assert abs(G.mega_probability("charizard", usage=USAGE) - 0.9) < 1e-9
    assert G.mega_probability("delphox", item_id="delphoxite", usage=USAGE) == 1.0
    assert G.expected_mega("delphox", item_id="garchompite", usage=USAGE) == {}    # 他種の石は無効
    rows = G.team_mega_probs([("charizard", None, False), ("delphox", "delphoxite", False), ("mimikyu", None, False)], usage=USAGE)
    assert [round(r["p"], 2) for r in rows] == [0.9, 1.0, 0.0] and rows[0]["forms"]["charizardmegay"] == 0.6
    print("test_expected_mega_overrides OK")


def test_mega_view_and_mixture():
    from advisor.damage import MonView
    from advisor.dex import get_dex
    sp = get_dex().species("charizard")
    v = MonView(species_id="charizard", types=list(sp["types"]), base=dict(sp["baseStats"]), ability="blaze")
    my = G.mega_view(v, "charizardmegay")
    assert my.species_id == "charizardmegay" and my.base["spa"] == 159 and my.ability == "drought" and my.types == ["Fire", "Flying"]
    mx = G.mega_view(v, "charizardmegax")
    assert mx.types == ["Fire", "Dragon"] and mx.ability == "toughclaws"
    assert G.mega_view(v, "nosuchmega") is v
    mix = G.mixture_base_stats("charizard", {"charizardmegay": 0.5})
    assert abs(mix["spa"] - (0.5 * 109 + 0.5 * 159)) < 1e-9 and mix["hp"] == 78
    assert G.mixture_base_stats("charizard", {}) == sp["baseStats"]
    print("test_mega_view_and_mixture OK")


def test_stone_form_of():
    """石 → メガ後のフォルム: id は requiredItem の表、名前は末尾 X/Y/Z。Z 石 (M-C) を通常メガに倒さない (2026-09-18)"""
    from advisor.infer import _base_species_id
    assert "garchompmegaz" in G.mega_forms("garchomp") and "garchompmega" in G.mega_forms("garchomp")
    assert G.stone_form_of("garchomp", "garchompitez") == "garchompmegaz"
    assert G.stone_form_of("garchomp", "garchompite") == "garchompmega"
    assert G.stone_form_of("garchomp", None, "ガブリアスナイトZ") == "garchompmegaz"
    assert G.stone_form_of("garchomp", None, "ガブリアスナイト") == "garchompmega"
    assert G.stone_form_of("lucario", "lucarionitez") == "lucariomegaz" and G.stone_form_of("absol", "absolitez") == "absolmegaz"
    assert G.stone_form_of("charizard", None, "リザードナイトY") == "charizardmegay"
    assert G.stone_form_of("charizard", "charizarditex", "リザードナイトY") == "charizardmegax"   # id が分かれば id を優先
    assert G.stone_form_of("charizard", None, "リザードナイト") == "charizardmegax"           # 無印: 無印のメガが無ければ X
    assert G.stone_form_of("charizard", "garchompitez") == "charizardmegax"                    # 他種の石は名前の推定に落ちる
    assert G.stone_form_of("mimikyu", "focussash") is None and G.stone_form_of("mimikyu", None, "ミミッキュナイト") is None
    assert _base_species_id("garchompmegaz") == "garchomp" and _base_species_id("raichumegay") == "raichu"
    print("test_stone_form_of OK")


if __name__ == "__main__":
    test_forms_and_priors()
    test_expected_mega_overrides()
    test_mega_view_and_mixture()
    test_stone_form_of()
