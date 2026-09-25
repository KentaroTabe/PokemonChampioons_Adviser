"""使わないポケモン (config/banned_species.txt) の純粋関数テスト。2026-09-25 ユーザー決定: 所持リストは持たず、
使わないリストだけで管理する。提案される構築にはリストの種を使わず、相手のパーティには適用しない。

    python -m tests.test_team_build_banned
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from tools.team_build import spec as SP

CURRENT = """\tsalamence: {tier: "OU"},
\tgolisopod: {tier: "OU"},
\tgarchomp: {tier: "OU"},
\tmew: {tier: "Illegal"},
\tarboliva: {tier: "OU"},
"""
PREVIOUS = """\tsalamence: {tier: "Illegal"},
\tgolisopod: {tier: "Illegal"},
\tgarchomp: {tier: "OU"},
\tmew: {tier: "Illegal"},
\tarboliva: {tier: "Illegal"},
"""
POKEDEX = """\taegislash: {
\t\tnum: 681,
\t\tname: "Aegislash",
\t\totherFormes: ["Aegislash-Blade"],
\t},
\taegislashblade: {
\t\tnum: 681,
\t\tname: "Aegislash-Blade",
\t\tbattleOnly: "Aegislash",
\t},
\trotomwash: {
\t\tnum: 479,
\t\tname: "Rotom-Wash",
\t},
"""


def test_illegal_and_new_species():
    assert SP.illegal_ids_from_text(CURRENT) == {"mew"}
    assert SP.illegal_ids_from_text(PREVIOUS) == {"mew", "salamence", "golisopod", "arboliva"}
    assert SP.new_species_from_texts(CURRENT, PREVIOUS) == ["arboliva", "golisopod", "salamence"]
    assert SP.new_species_from_texts(CURRENT, CURRENT) == []
    print("test_illegal_and_new_species OK")


def test_parse_and_resolve():
    text = "# 見出し\nハッサム  # scizor\n\ngengar\nScizor\nnotapokemon\n"
    assert SP.parse_banned_text(text) == ["ハッサム", "gengar", "Scizor", "notapokemon"]
    ids, unknown = SP.resolve_banned(["ハッサム", "gengar", "Scizor", "notapokemon"], legal={"scizor", "gengar"})
    assert ids == ["scizor", "gengar"] and unknown == ["notapokemon"], (ids, unknown)      # 日本語名は id に、重複は 1 つ
    ids2, unknown2 = SP.resolve_banned(["gengar", "notapokemon", "ほげほげ"])                 # legal 無しは綴りと解決だけ
    assert ids2 == ["gengar", "notapokemon"] and unknown2 == ["ほげほげ"], (ids2, unknown2)
    print("test_parse_and_resolve OK")


def test_read_file_and_sha():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "banned.txt"
        p.write_text("ゲンガー\nscizor # メガも使わない\n", encoding="utf-8")
        info = SP.read_banned_file(p, legal={"gengar", "scizor", "garchomp"})
        assert info["ids"] == ["gengar", "scizor"] and info["unknown"] == [] and info["exists"]
        assert len(info["sha256"]) == 12 and info["tokens"] == ["ゲンガー", "scizor"]
        p.write_text("ゲンガー\nscizor\ngarchomp\n", encoding="utf-8")
        assert SP.read_banned_file(p)["sha256"] != info["sha256"]                          # 中身が変われば sha も変わる
        missing = SP.read_banned_file(Path(d) / "none.txt")
        assert missing["ids"] == [] and not missing["exists"] and missing["unknown"] == []
    print("test_read_file_and_sha OK")


def test_battle_only_and_selectable():
    assert SP.battle_only_ids_from_text(POKEDEX) == {"aegislashblade"}
    assert SP.battle_only_ids(Path("/nonexistent/pokedex.ts")) == set()
    sel = SP.selectable_species_ids()
    assert "charizard" in sel and "rotomwash" in sel and "persianalola" in sel and sel == sorted(sel)
    assert "charizardmegay" not in sel and "venusaurmega" not in sel                       # メガ後のフォルムは選べない (図鑑から判定)
    if SP.POKEDEX_TS.exists():
        # 戦闘中だけのフォルムは Showdown の pokedex.ts (git 管理外) から判定する。CI には無いのでローカルだけ検査
        assert "aegislashblade" not in sel and "mimikyubusted" not in sel and "darmanitanzen" not in sel
    else:
        print("  (pokedex.ts が無いので戦闘中フォルムの除外は未検査)")
    usable = SP.usable_species_ids(["charizard", "gengar"])
    assert "charizard" not in usable and "gengar" not in usable and "garchomp" in usable
    assert len(usable) == len(sel) - 2
    assert SP.usable_species_ids([], legal={"a", "b"}) == ["a", "b"]
    assert SP.banned_in_members(["a", "gengar", "b", "scizor"], {"gengar", "scizor"}) == ["gengar", "scizor"]
    assert SP.banned_in_members(["a"], set()) == [] and SP.banned_in_members(None, {"a"}) == []
    print("test_battle_only_and_selectable OK")


def test_parse_form_merges_file_and_request():
    legal = {"scizor", "gengar", "a", "b", "c", "d", "e", "f", "g"}
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "banned.txt"
        p.write_text("scizor\ngengar\n", encoding="utf-8")
        # 依頼の banned はその run だけの追加 (extra)、明示の owned からも外す
        spec = SP.parse_form({"banned": "a"}, owned=list("abcdefg"), banned_path=p, legal=legal)
        assert spec.banned == ["scizor", "gengar", "a"] and spec.banned_source["extra"] == ["a"], spec.banned
        assert spec.banned_source["file_count"] == 2 and spec.banned_source["unknown"] == []
        assert spec.owned == list("bcdefg") and spec.provenance["banned"] == "resolved"
        assert SP.validate_spec(spec, legal) == [], SP.validate_spec(spec, legal)
        # 使える候補の既定 = 選べる種 − 使わない
        spec2 = SP.parse_form({}, banned_path=p, legal=legal)
        assert spec2.owned == list("abcdefg") and spec2.provenance["owned"] == "inferred" and spec2.banned == ["scizor", "gengar"]
        # 解決できない名前は validate で止まる
        p.write_text("scizor\nnotapokemon\n", encoding="utf-8")
        spec3 = SP.parse_form({}, owned=list("abcdefg"), banned_path=p, legal=legal)
        assert spec3.banned_source["unknown"] == ["notapokemon"] and spec3.banned == ["scizor"]
        assert any("解決できない" in x and "notapokemon" in x for x in SP.validate_spec(spec3, legal))
        # 読み込んだ古い request.json (banned / owned つき) にもファイルを効かせる
        spec4 = SP.BuildSpec(banned=["a"], owned=list("abcdefg"), provenance={})
        SP.apply_banned_file(spec4, p, legal)
        assert spec4.banned == ["scizor", "a"] and "a" not in spec4.owned and spec4.provenance["banned"] == "resolved"
        # ファイルが無ければ依頼の指定だけ
        spec5 = SP.parse_form({"banned": "gengar"}, owned=list("abcdefg"), banned_path=Path(d) / "none.txt", legal=legal)
        assert spec5.banned == ["gengar"] and not spec5.banned_source["exists"]
    # request.json の往復で banned_source が残る
    with tempfile.TemporaryDirectory() as d:
        SP.save_spec(spec, Path(d))
        back = SP.load_spec(Path(d) / "request.json")
        assert back.banned == ["scizor", "gengar", "a"] and back.banned_source["file_count"] == 2
    print("test_parse_form_merges_file_and_request OK")


def test_install_refuses_banned():
    from tools.team_build.promote import banned_in_team_text
    text = ("Gengar @ gengarite\nAbility: cursedbody\n- shadowball\n\n"
            "Garchomp @ focussash\nAbility: roughskin\n- earthquake\n")
    assert banned_in_team_text(text, {"gengar"}) == ["gengar"]
    assert banned_in_team_text(text, {"scizor"}) == [] and banned_in_team_text("", {"gengar"}) == []
    print("test_install_refuses_banned OK")


if __name__ == "__main__":
    test_illegal_and_new_species()
    test_parse_and_resolve()
    test_read_file_and_sha()
    test_battle_only_and_selectable()
    test_parse_form_merges_file_and_request()
    test_install_refuses_banned()
