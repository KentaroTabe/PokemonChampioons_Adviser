"""単体の型の手入力 (tools/team_build/article_manual) のテスト (docs/ARTICLE_BANK_DESIGN_1006.md §3.10、2026-10-06 ユーザー判断)。

    python -m tests.test_article_manual
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from tools.team_build import article_bank as B
from tools.team_build import article_manual as M

REG_MC = "gen9championsbssregmc"
GOOD = {"species": "メガラグラージ", "item": "ラグラージナイト", "ability": "すいすい", "nature": "ようき",
        "points": {"H": 2, "A": 32, "S": 32}, "moves": ["ウェーブタックル", "じしん", "れいとうパンチ", "どくづき"],
        "actual": [177, 202, 130, 103, 130, 134],
        "source": {"publisher_kind": "user_submission_site", "usage_evidence": "none", "host": "yakkun.com"},
        "regulation": "M-C", "note": "手で写した型 (記録に入れない)"}


def test_points_and_names():
    assert M.points_from({"H": 2, "A": 32, "S": 32}) == {"hp": 2, "atk": 32, "spe": 32}
    assert M.points_from("32/0/0/29/2/3") == {"hp": 32, "spa": 29, "spd": 2, "spe": 3}
    assert M.points_from({"hp": 32, "spa": 29, "spd": 2, "spe": 3}) == {"hp": 32, "spa": 29, "spd": 2, "spe": 3}
    assert M.points_from("32/0/0") is None and M.points_from({"X": 1}) is None and M.points_from({"H": "32"}) is None
    assert M.points_from(None) is None
    dic = M.default_dictionary()
    assert M.resolve_name(dic, "moves", "じしん") == "earthquake" and M.resolve_name(dic, "moves", "earthquake") == "earthquake"
    assert M.resolve_name(dic, "moves", "地震") is None and M.resolve_name(dic, "species", "メガラグラージ") == "swampertmega"
    assert M.regulation_id("M-C") == REG_MC and M.regulation_id(REG_MC) == REG_MC and M.regulation_id(None) == "unknown"
    assert M.regulation_id("M-Z") is None
    print("test_points_and_names OK")


def test_record_from_entry():
    """正しい入力 → single_set の記録 (規制は user_confirmed、実数値の再計算と一致、補完しない、note は入れない)"""
    rec, probs = M.record_from_entry(GOOD, today="2026-10-06")
    assert probs == [] and rec["record_kind"] == "single_set" and rec["status"] == "ok"
    m = rec["members"][0]
    assert (m["species_id"], m["base_species_id"], m["mega_stone"], m["item"]) == ("swampertmega", "swampert", "swampertite", "swampertite")
    assert m["points"] == {"hp": 2, "atk": 32, "spe": 32} and m["moves"] == ["wavecrash", "earthquake", "icepunch", "poisonjab"]
    assert rec["meta"]["regulation"] == REG_MC and rec["meta"]["regulation_basis"] == "user_confirmed"
    assert rec["meta"]["regulation_history"][0]["at"] == "2026-10-06"
    assert rec["source"]["entry_method"] == "manual" and rec["source"]["redistributable"] is False and "note" not in rec["source"]
    assert rec["claims"] == [] and rec["selection_rules"] == []
    B.assert_no_prose(rec)
    assert B.usable_for(rec, "weakness", REG_MC) and not B.usable_for(rec, "pool", REG_MC) and not B.usable_for(rec, "selection", REG_MC)
    assert B.validate_record(rec) == []
    # 実数値の記載が再計算と違えば問題
    bad_actual = dict(GOOD, actual=[177, 200, 130, 103, 130, 134])
    rec2, probs2 = M.record_from_entry(bad_actual, today="2026-10-06")
    assert rec2 is None and any(p.startswith("m1:actual_mismatch:atk") for p in probs2)
    print("test_record_from_entry OK")


def test_problems_reported_not_guessed():
    """解決できない名前・足りない項目・手入力で許されない根拠・知らない規制は問題として返し、推測で埋めない"""
    _rec, probs = M.record_from_entry(dict(GOOD, moves=["ウェーブタックル", "地震", "れいとうパンチ", "どくづき"]))
    assert "unresolved:move2" in probs
    _rec, probs = M.record_from_entry(dict(GOOD, species="メガラグラージZ"))
    assert "unresolved:species" in probs
    _rec, probs = M.record_from_entry(dict(GOOD, ability="すいすいい"))
    assert "unresolved:ability" in probs
    _rec, probs = M.record_from_entry(dict(GOOD, source={"usage_evidence": "battle_log_confirmed"}))
    assert "usage_evidence_not_allowed_for_manual" in probs
    _rec, probs = M.record_from_entry(dict(GOOD, regulation="M-Z"))
    assert "regulation_unknown_value" in probs
    _rec, probs = M.record_from_entry(dict(GOOD, moves=["じしん", "どくづき"]))
    assert "moves:2" in probs
    _rec, probs = M.record_from_entry(dict(GOOD, points={"H": 32, "A": 32, "S": 32}))
    assert "m1:point_total:96" in probs
    _rec, probs = M.record_from_entry({"species": "ガブリアス"})
    assert set(probs) == {"missing:item", "missing:nature", "missing:moves"}
    # 規制なしは unknown で記録はできるが、用途には使えない
    rec, probs = M.record_from_entry(dict(GOOD, regulation=None), today="2026-10-06")
    assert probs == [] and rec["meta"]["regulation"] == "unknown" and not B.usable_for(rec, "weakness", REG_MC)
    # 無指定の usage_evidence は none (実戦で観測した型にしない)
    rec, probs = M.record_from_entry(dict(GOOD, source={"publisher_kind": "personal_blog"}), today="2026-10-06")
    assert probs == [] and rec["source"]["usage_evidence"] == "none"
    print("test_problems_reported_not_guessed OK")


def test_check_and_import_file():
    """検査は結果を返すだけ。取り込みは問題が 1 件でもあれば保存しない。base_version に足せる"""
    results = M.check_entries([GOOD, dict(GOOD, moves=["じしん"]), "x"])
    assert [bool(r["problems"]) for r in results] == [False, True, True] and results[2]["problems"] == ["not_object"]
    with tempfile.TemporaryDirectory() as d:
        out, res = M.import_entries([GOOD, dict(GOOD, moves=["じしん"])], Path(d), today="2026-10-06")
        assert out is None and not any(Path(d).iterdir())
        out, res = M.import_entries([GOOD], Path(d), today="2026-10-06")
        assert out is not None and len(B.load_bank(out.name, Path(d))) == 1
        second = dict(GOOD, species="ペリッパー", item="しめったいわ", ability="あめふらし", nature="ひかえめ",
                      points="32/0/0/29/2/3", moves=["ぼうふう", "なみのり", "とんぼがえり", "おいかぜ"], actual=[167, 63, 120, 158, 92, 88])
        out2, _res = M.import_entries([second], Path(d), base_version=out.name, today="2026-10-06")
        assert out2 is not None and len(B.load_bank(out2.name, Path(d))) == 2
        p = Path(d) / "in.json"
        p.write_text(json.dumps([GOOD], ensure_ascii=False), encoding="utf-8")
        assert M.main(["--check", str(p)]) == 0
        p.write_text(json.dumps([dict(GOOD, regulation="M-Z")], ensure_ascii=False), encoding="utf-8")
        assert M.main(["--check", str(p)]) == 1
        assert M.main(["--import", str(p), "--bank-dir", str(Path(d) / "b2")]) == 1 and not (Path(d) / "b2").exists()
    print("test_check_and_import_file OK")


def test_validation_reflected_in_status():
    """validate_record の結果が記録の status に反映される: 実数値の再計算と矛盾する入力 → conflict (2026-10-06 ユーザー判断)。
    既定では従来どおり記録を返さず、検査 (check_entries) と keep_rejected=True で status を見られる。保存はしない"""
    bad_actual = dict(GOOD, actual=[177, 200, 130, 103, 130, 134])
    rec, probs = M.record_from_entry(bad_actual, today="2026-10-06", keep_rejected=True)
    assert rec["status"] == "conflict" and rec["problems"] == probs and any(p.startswith("m1:actual_mismatch:atk") for p in probs)
    assert not any(B.usable_for(rec, p, REG_MC) for p in ("pool", "weakness", "selection")) and B.usable_for(rec, "parser_eval")
    assert M.record_from_entry(bad_actual, today="2026-10-06")[0] is None                 # 既定は従来どおり
    good, good_probs = M.record_from_entry(GOOD, today="2026-10-06")
    assert good_probs == [] and good["status"] == "ok" and good["problems"] == [] and good["facets"]["sets_known"] is True
    results = M.check_entries([GOOD, bad_actual, dict(GOOD, moves=["じしん"])], today="2026-10-06")
    assert [r["status"] for r in results] == ["ok", "conflict", None]                     # 入力の問題 (技が 1 つ) は記録を作らない
    assert results[1]["record"] is None and results[0]["record"]["status"] == "ok"
    with tempfile.TemporaryDirectory() as d:
        out, _res = M.import_entries([GOOD, bad_actual], Path(d), today="2026-10-06")
        assert out is None and not any(Path(d).iterdir())                                # 矛盾のある入力があれば保存しない
    print("test_validation_reflected_in_status OK")


def main() -> None:
    test_points_and_names()
    test_record_from_entry()
    test_problems_reported_not_guessed()
    test_check_and_import_file()
    test_validation_reflected_in_status()
    print("ALL OK")


if __name__ == "__main__":
    main()
