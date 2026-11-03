"""引き継ぎ評価 I-01 (tools/inheritance_eval/conversion.py) の純粋な照合と fixture の形のテスト。

    python -m tests.test_inheritance_conversion

Showdown・使用率 DB・登録 (config/my_team.json) は読まない (CI でも回る)。走査・登録の往復は道具の実行で行い、結果は
logs/inheritance_eval/ に残す (計画書 §9)。
"""
from __future__ import annotations

from pathlib import Path

from champions_agent import config as cfg
from tools.inheritance_eval import conversion as cv

REPO = Path(__file__).resolve().parent.parent


def test_fixture_shape():
    cases = cv.load_cases((REPO / cfg.INH_CONV_FIXTURE).read_text(encoding="utf-8"))
    assert cv.validate_cases(cases, cfg.INH_CONV_FIXTURE_MAX) == [], cv.validate_cases(cases, cfg.INH_CONV_FIXTURE_MAX)
    assert 0 < len(cases) <= cfg.INH_CONV_FIXTURE_MAX
    cats = {}
    for c in cases:
        cats[c["category"]] = cats.get(c["category"], 0) + 1
        e = c["expected"]
        for i in ([e["id"]] if "id" in e else []) + list(e.get("ids") or []) + list(e.get("allowed") or []):
            assert i == cv.toid(i), f"{c['case_id']}: id は Showdown の id の形 ({i})"
        if c["case_id"].startswith("R-"):
            assert c["split"] == "diag", f"{c['case_id']}: 既知の失敗・登録由来は diag (確認集合に混ぜない)"
    # 指示書の境界: 形態・単位・表記揺れ・未知名・欠損値が全部ある
    for need in ("form", "unit", "variant", "unknown", "missing"):
        assert cats.get(need, 0) >= 5, f"{need} のケースが少ない ({cats.get(need, 0)})"
    units = [c for c in cases if c["category"] == "unit"]
    texts = " ".join(str(c["input"]) for c in units)
    assert "32" in texts and "252" in texts and "0" in texts, "能力ポイント 0/32 と努力値 252 の境界"
    print(f"test_fixture_shape OK ({len(cases)} 件, {cats})")


def test_minimal_key():
    k = cv.minimal_key
    assert k("リザードン") == k("りざーどん") == k("ﾘｻﾞｰﾄﾞﾝ") == k(" リザードン　") == k("リザ－ドン")
    assert k("ひけん・ちえなみ") == k("ひけんちえなみ") == k("ﾋｹﾝ･ﾁｴﾅﾐ")
    assert k("１０まんボルト") == k("10まんボルト")
    assert k("メガリザードンＸ") == k("メガリザードンX")
    # 誤読の補正はしない: 長音・濁点・小書きの違いは別のキー
    assert k("シャドボール") != k("シャドーボール")
    assert k("ガブリアス") != k("カブリアス")
    assert k("ミミッキュ") != k("ミミツキュ")
    assert k(None) == "" and k("") == "" and k("　") == ""
    print("test_minimal_key OK")


TS_SAMPLE = '''export const PokedexText: { [id: IDEntry]: SpeciesText } = {
\tcharizard: {
\t\tname: "リザードン",
\t},
\tcharizardmegax: {
\t\tname: "メガリザードンＸ",
\t\tforme: null, // NEEDS TRANSLATION
\t},
\tmeowstic: {
\t\tname: null, // NEEDS TRANSLATION
\t\tbaseSpecies: "ニャオニクス",
\t},
\tmeowsticf: {
\t\tname: null, // NEEDS TRANSLATION
\t\tforme: null, // NEEDS TRANSLATION
\t},
\taerilate: {
\t\tname: "スカイスキン",
\t\tgen6: {
\t\t\tname: "ちがう",
\t\t},
\t},
\t"10000000voltthunderbolt": {
\t\tname: "１０００まんボルト",
\t},
};
'''


def test_parse_ja_text_ts():
    got = cv.parse_ja_text_ts(TS_SAMPLE)
    assert got["charizard"] == {"name": "リザードン"}
    assert got["charizardmegax"] == {"name": "メガリザードンＸ", "forme": None}
    assert got["meowstic"] == {"name": None, "baseSpecies": "ニャオニクス"}
    assert got["aerilate"]["name"] == "スカイスキン", "世代別の入れ子は読まない"
    assert got["10000000voltthunderbolt"]["name"] == "１０００まんボルト"
    assert cv.showdown_species_ja(got["charizard"]) == "リザードン"
    assert cv.showdown_species_ja(got["meowstic"]) == "ニャオニクス", "形態の欄の無い基本形は baseSpecies"
    assert cv.showdown_species_ja(got["meowsticf"]) is None, "形態は名前を組み立てない"
    names = 'export const NatureNames: { [id: string]: TranslationString } = {\n\tadamant: "いじっぱり",\n' \
            '\tbashful: null,\n};\nexport const Other = {\n\tx: "y",\n};\n'
    assert cv.parse_ts_const_map(names, "NatureNames") == {"adamant": "いじっぱり", "bashful": None}
    print("test_parse_ja_text_ts OK")


def test_stone_names_and_minimal_table():
    stones = [{"id": "charizarditex", "base": "charizard", "form": "charizardmegax"},
              {"id": "garchompitez", "base": "garchomp", "form": "garchompmegaz"},
              {"id": "garchompite", "base": "garchomp", "form": "garchompmega"},
              {"id": "nobase", "base": "unknownmon", "form": "unknownmonmega"}]
    got = cv.stone_names(stones, {"charizard": "リザードン", "garchomp": "ガブリアス"})
    assert got == {"charizarditex": "リザードンナイトX", "garchompitez": "ガブリアスナイトZ",
                   "garchompite": "ガブリアスナイト"}
    t = cv.MinimalTable({"items": {"leek": "ながねぎ", "stick": "ながねぎ", "choicescarf": "こだわりスカーフ",
                                   "abomasite": None}})
    assert t.to_id("items", "こだわりスカーフ")["id"] == "choicescarf"
    assert t.to_id("items", "ｺﾀﾞﾜﾘｽｶｰﾌ")["id"] == "choicescarf"
    assert t.to_id("items", "こだわりスカーブ")["status"] == "unknown", "近い名前へ寄せない"
    amb = t.to_id("items", "ながねぎ")
    assert amb["status"] == "ambiguous" and amb["candidates"] == ["leek", "stick"]
    assert t.to_id("items", None)["status"] == "empty"
    assert t.to_ja("items", "abomasite") is None
    assert t.duplicates("items") == {"ながねぎ": ["leek", "stick"]}
    print("test_stone_names_and_minimal_table OK")


def test_judge_name():
    j = cv.judge_name
    aliases = {"indeedeef": "indeedeefemale"}
    assert j({"kind": "exact", "id": "garchompitez"}, "garchompitez")[0] == "pass"
    assert j({"kind": "exact", "id": "garchompitez"}, "garchompite")[1] == cv.SEV_MISCONVERT
    assert j({"kind": "exact", "id": "garchompitez"}, None, "unknown")[1] == cv.SEV_MISSING
    assert j({"kind": "exact", "id": "indeedeef"}, "indeedeefemale", aliases=aliases)[0] == "pass", "別名表で同じ個体"
    amb = {"kind": "ambiguous", "ids": ["rotom", "rotomwash"]}
    assert j(amb, None, "ambiguous")[0] == "pass"
    assert j(amb, None, "unknown")[0] == "pass"
    assert j(amb, "rotom")[1] == cv.SEV_AMBIGUITY
    assert j(amb, "pikachu")[1] == cv.SEV_MISCONVERT
    assert j({"kind": "unknown"}, None, "unknown")[0] == "pass"
    assert j({"kind": "unknown"}, "garchompite")[1] == cv.SEV_MISCONVERT
    no = {"kind": "not_other", "allowed": ["finneon"]}
    assert j(no, None, "unknown")[0] == "pass" and j(no, "finneon")[0] == "pass"
    assert j(no, "greninja")[1] == cv.SEV_MISCONVERT
    assert j({"kind": "probe"}, "shadowball")[0] == "excluded"
    print("test_judge_name OK")


def test_units():
    cap, total, ev_max = cfg.BUILD_GEN_EV_POINT_CAP, cfg.BUILD_GEN_POINT_BUDGET, cfg.INH_CONV_EV_MAX
    r = cv.read_points_minimal
    assert r({"能力ポイント": {"h": 2, "c": 32, "s": 32}}, cap, total) == \
        {"status": "ok", "points": {"hp": 2, "spa": 32, "spe": 32}, "reason": ""}
    assert r({"能力ポイント": {"a": 0, "s": 32}}, cap, total)["points"] == {"spe": 32}, "0 は落とす"
    assert r({"能力ポイント": {"a": 33}}, cap, total)["status"] == "reject", "32 を超える点数"
    assert r({"能力ポイント": {"a": 32, "s": 32, "h": 32}}, cap, total)["status"] == "reject", "合計 66 を超える"
    assert r({"evs": {"atk": 32}}, cap, total)["status"] == "reject", "単位の決まらない欄"
    assert r({"能力ポイント": {"x": 3}}, cap, total)["status"] == "reject"
    assert r(None, cap, total)["status"] == "unknown"
    assert r({"能力ポイント": {}}, cap, total)["points"] == {}
    assert cv.points_to_ev({"atk": 32, "hp": 1, "def": 0}, cap, ev_max) == {"atk": 252, "hp": 8}
    assert cv.points_from_entry_keys({"h": 2, "C": 32, "spe": 0, "zz": 5}) == {"hp": 2, "spa": 32}
    assert cv.parse_ev_line_points("EVs: 4 HP / 252 Atk / 252 Spe") == {"hp": 4, "atk": 252, "spe": 252}
    jv = cv.judge_values
    assert jv({"kind": "ev", "ev": {"atk": 252}}, {"atk": 252, "hp": 0}, "ok", "ev")[0] == "pass"
    assert jv({"kind": "ev", "ev": {"atk": 252}}, {"atk": 32}, "ok", "ev")[1] == cv.SEV_MISCONVERT
    assert jv({"kind": "ambiguous"}, {"atk": 252}, "ok", "ev")[1] == cv.SEV_AMBIGUITY
    assert jv({"kind": "ambiguous"}, None, "reject", "ev")[0] == "pass"
    assert jv({"kind": "reject"}, {"atk": 252}, "ok", "points")[1] == cv.SEV_MISCONVERT
    assert jv({"kind": "reject"}, None, "reject", "points")[0] == "pass"
    assert jv({"kind": "points", "points": {"atk": 32}}, None, "reject", "points")[1] == cv.SEV_UNSUPPORTED
    assert jv({"kind": "unknown"}, None, "unknown", "ev")[0] == "pass"
    probs = ["X has more than 32 Stat Points in HP.", "X has 70 total Stat Points, which is more than this format's limit",
             "X has exactly 0 Stat Points - did you forget to invest it?", "X has no moves"]
    assert cv.points_problems(probs) == probs[:2], "0 点の警告と他の問題は範囲違反に数えない"
    print("test_units OK")


def test_name_pair_and_summary():
    c = cv.classify_name_pair
    assert c("リザードン", "リザードン") == "一致"
    assert c("メガリザードンX", "メガリザードンＸ") == "表記の差"
    assert c("カイリューナイト", "カイリュナイト") == "名前の差"
    assert c("ガブリアスナイトZ", None) == "現行のみ"
    assert c(None, "ながねぎ") == "Showdownのみ"
    assert c(None, None) == "両方なし"
    lo, hi = cv.wilson(0, 10)
    assert lo == 0.0 and 0.25 < hi < 0.35
    assert cv.wilson(0, 0) is None
    rows = [cv.measurement("I-01", "a", "S fixture", "合成結果", {}, {}, "src", "confirm", "pass", "", 0.0,
                           path="current", case_category="form"),
            cv.measurement("I-01", "b", "S fixture", "合成結果", {}, {}, "src", "confirm", "fail", "", 0.0,
                           path="current", severity=cv.SEV_MISCONVERT, case_category="form"),
            cv.measurement("I-01", "c", "S fixture", "合成結果", {}, {}, "src", "confirm", "pass", "", 0.0,
                           path="current", severity=cv.SEV_NOTATION, case_category="reverse"),
            cv.measurement("I-01", "d", "名前の表", "規則参照", {"id": "x"}, {}, "src", "confirm", "excluded", "", 0.0,
                           scope="regmc", cat="species", detail={"name_class": "一致", "minimal_back": "other:y"})]
    for r in rows:
        assert set(("test_id", "case_id", "asset", "category", "input", "expected", "expected_source", "split",
                    "result", "reason", "elapsed_sec")) <= set(r)
    s = cv.summarize(rows)
    d = s["by_asset_path"]["S fixture|current"]
    assert (d["pass"], d["fail"], d["severity"], d.get("notation")) == (2, 1, {cv.SEV_MISCONVERT: 1}, 1)
    assert [r["case_id"] for r in s["misconvert"]] == ["b"]
    sc = s["scan"]["regmc|species"]
    assert (sc["n"], sc["excluded"], sc["minimal_back"]) == (1, 1, {"other": 1})
    print("test_name_pair_and_summary OK")


def main() -> None:
    test_fixture_shape()
    test_minimal_key()
    test_parse_ja_text_ts()
    test_stone_names_and_minimal_table()
    test_judge_name()
    test_units()
    test_name_pair_and_summary()
    print("ALL OK")


if __name__ == "__main__":
    main()
