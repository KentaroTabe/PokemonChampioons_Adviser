"""フォルムIDの日本語表示 (species_ja_name) の検証。

使い方: python -m tests.test_ja_names
"""
from advisor.infer import species_ja_name


def test_form_names():
    cases = {
        "rotomwash": "ウォッシュロトム",
        "rotomheat": "ヒートロトム",
        "typhlosionhisui": "ヒスイバクフーン",
        "slowkinggalar": "ガラルヤドキング",
        "raichualola": "アローラライチュウ",
        "ogerponwellspringmask": "オーガポン(いどのめん)",
        "urshifusinglestrike": "ウーラオス(いちげき)",
        "basculegionmale": "イダイトウ(オス)",
        "landorustherian": "ランドロス(れいじゅう)",
        "swampertmega": "メガラグラージ",
        "charizardmegay": "メガリザードンY",
        "garchomp": "ガブリアス",
    }
    for sid, want in cases.items():
        got = species_ja_name(sid)
        assert got == want, f"{sid}: {got} != {want}"
    print(f"test_form_names OK ({len(cases)}件)")


if __name__ == "__main__":
    test_form_names()
    print("ALL OK")
