"""記事バンクの記録 (tools/team_build/article_bank) の種類・出典の 2 軸・用途の判定・規制の履歴・合成の門のテスト
(docs/ARTICLE_BANK_DESIGN_1006.md §3.7、2026-10-06 ユーザー判断)。

素材は tests/test_article_parse.py の合成記事 SYNTHETIC と、その 1 体目だけの単体の型 (合成)。

    python -m tests.test_article_bank
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from tests.test_article_parse import SYNTHETIC
from tools.team_build import article_bank as B
from tools.team_build import article_parse as P

L = "https://example.invalid"
SINGLE = f"""# 単体の型 (合成の試験記事)
* [ペリッパー]({L}/zukan/n279)@[しめったいわ]({L}/item?item_s=137)([ひかえめ]({L}/nature#modest))[あめふらし]({L}/ability/2)
* HP:32 / 特攻:29 / 特防:2 / 素早:3
* [ぼうふう]({L}/move/542)[なみのり]({L}/move/57)[とんぼがえり]({L}/move/369)[おいかぜ]({L}/move/366)
"""
REG_MC = "gen9championsbssregmc"
REG_MB = "gen9championsbssregmb"


def _team(source=None, meta=None):
    return B.build_record(P.parse_article(SYNTHETIC), source=source or {}, meta={"regulation": REG_MC} if meta is None else meta)


def _single(source=None, meta=None):
    parsed = P.parse_article(SINGLE, max_members=1)
    return B.build_record(parsed, source=source or {}, meta={"regulation": REG_MC} if meta is None else meta, record_kind="single_set")


def _expect_value_error(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except ValueError as e:
        return str(e)
    raise AssertionError("ValueError が出なかった")


def test_record_kind_branches():
    """team は 6 体・各 4 技で ok、single_set は 1 体・4 技で ok。検査の個体数と基本種の重複は種類ごと"""
    team = _team()
    assert team["record_kind"] == "team" and team["status"] == "ok" and B.validate_record(team) == []
    single = _single()
    assert single["record_kind"] == "single_set" and single["status"] == "ok" and len(single["members"]) == 1
    assert B.validate_record(single) == []
    # 構築の記事を単体の型として読むと、2 体目以降の見出しは警告にして採らない
    p1 = P.parse_article(SYNTHETIC, max_members=1)
    assert p1["counts"]["members"] == 1 and "extra_member_head" in p1["warnings"]
    assert B.build_record(p1, source={}, record_kind="single_set")["status"] == "warnings"
    # 6 体の解析結果を single_set として記録すると、種類の個体数を超える警告と検査の問題になる
    over = B.build_record(P.parse_article(SYNTHETIC), source={}, record_kind="single_set")
    assert over["status"] == "warnings" and "members_exceed_kind" in over["warnings"] and "members:6" in B.validate_record(over)
    # 1 体だけの解析結果は team としては incomplete、技一覧の無い単体の型も incomplete
    assert B.build_record(P.parse_article(SINGLE), source={})["status"] == "incomplete"
    no_moves = SINGLE.rsplit("\n* [ぼうふう]", 1)[0] + "\n"
    assert B.build_record(P.parse_article(no_moves, max_members=1), source={}, record_kind="single_set")["status"] == "incomplete"
    assert B.build_record(P.parse_article("本文だけ", max_members=1), source={}, record_kind="single_set")["status"] == "failed"
    # 基本種の重複は team だけ見る
    dup = json.loads(json.dumps(team))
    dup["members"][1]["base_species_id"] = dup["members"][0]["base_species_id"]
    assert "duplicate_base_species" in B.validate_record(dup)
    one = json.loads(json.dumps(single))
    one["members"].append(json.loads(json.dumps(one["members"][0])))
    probs = B.validate_record(one)
    assert "members:2" in probs and "duplicate_base_species" not in probs
    # record_kind の無い古い記録は team として検査する
    old = {k: v for k, v in team.items() if k != "record_kind"}
    assert B.validate_record(old) == []
    _expect_value_error(B.build_record, P.parse_article(SINGLE), {}, None, None, "pair")
    assert B.llm_payload(single)["record_kind"] == "single_set"
    print("test_record_kind_branches OK")


def test_source_axes():
    """出典の 2 軸 (誰が掲載したか / 使用実績の根拠) は別項目。無指定は unknown、表に無い値は ValueError、synthetic は bool"""
    rec = _team()
    assert rec["source"]["publisher_kind"] == "unknown" and rec["source"]["usage_evidence"] == "unknown"
    assert rec["source"]["synthetic"] is False
    rec2 = _team(source={"host": "a.example", "publisher_kind": "editorial_site", "usage_evidence": "self_report"})
    assert (rec2["source"]["publisher_kind"], rec2["source"]["usage_evidence"]) == ("editorial_site", "self_report")
    msg = _expect_value_error(_team, source={"publisher_kind": "blog"})
    assert "publisher_kind" in msg
    msg = _expect_value_error(_team, source={"usage_evidence": "rank_1"})
    assert "usage_evidence" in msg
    _expect_value_error(_team, source={"synthetic": "yes"})
    # 検査も出典の 2 軸を見る (手で直した記録など)
    bad = json.loads(json.dumps(rec))
    bad["source"]["publisher_kind"] = "blog"
    bad["source"]["synthetic"] = 1
    probs = B.validate_record(bad)
    assert "source_publisher_kind_unknown_value" in probs and "source_synthetic_not_bool" in probs
    print("test_source_axes OK")


def test_usable_for_branches():
    """用途ごとの判定: 合成 / 処理状態 / 規制 / 記録の種類 / 使用実績の根拠"""
    real = {"host": "a.example", "publisher_kind": "personal_blog", "usage_evidence": "self_report"}
    team = _team(source=real)
    for purpose in ("pool", "weakness", "selection", "parser_eval"):
        assert B.usable_for(team, purpose, REG_MC), purpose
    # 合成の記事は parser_eval だけ
    syn = _team(source=dict(real, synthetic=True))
    assert not any(B.usable_for(syn, p, REG_MC) for p in ("pool", "weakness", "selection")) and B.usable_for(syn, "parser_eval")
    # 規制が不明・違う・指定なし
    for meta in ({}, {"regulation": None}, {"regulation": "unknown"}):
        rec = _team(source=real, meta=meta)
        assert not B.usable_for(rec, "weakness", REG_MC) and B.usable_for(rec, "parser_eval")
    assert not B.usable_for(team, "weakness", REG_MB)
    assert not B.usable_for(team, "weakness")                   # regulation を指定しなければ一致しない (規制を問わずに使わない)
    # 処理状態が ok / warnings 以外は使わない
    inc = B.build_record(P.parse_article(SINGLE), source=real, meta={"regulation": REG_MC})
    assert inc["status"] == "incomplete" and not B.usable_for(inc, "weakness", REG_MC) and B.usable_for(inc, "parser_eval")
    warn = B.build_record(P.parse_article(SYNTHETIC, max_members=1), source=real, meta={"regulation": REG_MC}, record_kind="single_set")
    assert warn["status"] == "warnings" and B.usable_for(warn, "weakness", REG_MC)
    # 編集部の記事で使用実績の根拠なし: プール本体には入れないが、弱点の検査と選出には使える
    editorial = _team(source={"publisher_kind": "editorial_site", "usage_evidence": "none"})
    assert not B.usable_for(editorial, "pool", REG_MC)
    assert B.usable_for(editorial, "weakness", REG_MC) and B.usable_for(editorial, "selection", REG_MC)
    # 判定は掲載者ではなく使用実績の根拠: 編集部の記事でも対戦記録で確認できた構築はプールに入る / 個人ブログでも根拠が不明なら入らない
    editorial_logged = _team(source={"publisher_kind": "editorial_site", "usage_evidence": "battle_log_confirmed"})
    assert B.usable_for(editorial_logged, "pool", REG_MC)
    assert B.usable_for(_team(source={"usage_evidence": "battle_log_confirmed"}), "pool", REG_MC)
    assert not B.usable_for(_team(source={"publisher_kind": "personal_blog"}), "pool", REG_MC)
    # 単体の型はプール・選出に使わず、弱点の検査には使える
    single = _single(source=real)
    assert not B.usable_for(single, "pool", REG_MC) and not B.usable_for(single, "selection", REG_MC)
    assert B.usable_for(single, "weakness", REG_MC) and B.usable_for(single, "parser_eval")
    _expect_value_error(B.usable_for, team, "opponent_pool", REG_MC)
    print("test_usable_for_branches OK")


def test_set_regulation_history():
    """規制の更新は根拠と履歴 (旧値・新値・basis) を残し、元の記録は変えない。表に無い basis・未知の規制 id は ValueError"""
    rec = _team(meta={"regulation": "unknown"})
    r1 = B.set_regulation(rec, REG_MB, "article_text")
    r2 = B.set_regulation(r1, REG_MC, "user_confirmed", at="2026-10-06")
    assert rec["meta"] == {"regulation": "unknown"}                        # 元の記録は変えない
    assert r2["meta"]["regulation"] == REG_MC and r2["meta"]["regulation_basis"] == "user_confirmed"
    assert r2["meta"]["regulation_history"] == [{"from": "unknown", "to": REG_MB, "basis": "article_text"},
                                                {"from": REG_MB, "to": REG_MC, "basis": "user_confirmed", "at": "2026-10-06"}]
    B.assert_no_prose(r2)
    assert B.usable_for(r2, "weakness", REG_MC) and not B.usable_for(rec, "weakness", REG_MC)
    _expect_value_error(B.set_regulation, rec, REG_MC, "guess")
    _expect_value_error(B.set_regulation, rec, "gen9championsbssregmz", "article_text")
    print("test_set_regulation_history OK")


def test_save_load_synthetic_gate():
    """合成の記録は保存・読み出しの両方で既定で拒否する (一時ディレクトリへの保存でも許可を別に要る)"""
    real = _team(source={"host": "a.example", "usage_evidence": "self_report"})
    syn = _single(source={"host": "example.invalid", "synthetic": True})
    with tempfile.TemporaryDirectory() as d:
        msg = _expect_value_error(B.save_bank, [real, syn], Path(d))
        assert "allow_synthetic" in msg and not any(Path(d).iterdir())        # 何も書かない
        out = B.save_bank([real, syn], Path(d), allow_synthetic=True)
        man = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        assert man["n_cases"] == 2 and man["n_synthetic"] == 1 and man["by_record_kind"] == {"team": 1, "single_set": 1}
        assert [c["case_id"] for c in B.load_bank(out.name, Path(d))] == [real["case_id"]]          # 既定は合成を除く
        assert [c["case_id"] for c in B.load_bank(out.name, Path(d), allow_synthetic=True)] == [real["case_id"], syn["case_id"]]
        only_real = B.save_bank([real], Path(d) / "real")                     # 合成が無ければ許可なしで保存できる
        assert len(B.load_bank(only_real.name, Path(d) / "real")) == 1
        # 保存の門は source / meta も含めて本文の混入を見る
        leak = json.loads(json.dumps(real))
        leak["meta"]["title"] = "雨パの構築"
        msg = _expect_value_error(B.save_bank, [leak], Path(d) / "leak")
        assert "雨" not in msg and "meta" in msg
    print("test_save_load_synthetic_gate OK")


def test_host_policy_urls_and_purposes():
    """ホストの方針: allowed_urls があれば URL も見る (正規化して比較、URL 無しは不可)、purposes があれば用途を制限する、
    ホスト名は www. とポートを落として比較、unknown は進めない。usable_for も policy を渡せば用途の制限を見る (2026-10-06 ユーザー判断)"""
    policy = {"_schema": "host_policy/2", "_note": "値は allow / unknown / deny",
              "gamewith.jp": {"fetch": "allow", "store": "allow", "send_llm": "unknown",
                              "allowed_urls": ["https://gamewith.jp/pokemon-champions/555537"],
                              "purposes": ["weakness", "selection", "parser_eval"]},
              "open.example": {"fetch": "allow", "send_llm": "allow"},
              "yakkun.com": {"fetch": "deny", "store": "deny", "send_llm": "deny"}}
    gw = "gamewith.jp"
    assert B.host_allowed(policy, gw, "fetch", url="https://gamewith.jp/pokemon-champions/555537")
    assert B.host_allowed(policy, gw, "fetch", url="https://gamewith.jp/pokemon-champions/555537/?utm=x#top")   # 正規化して比較
    assert B.host_allowed(policy, "www.gamewith.jp:443", "fetch", url="https://gamewith.jp/pokemon-champions/555537")
    assert not B.host_allowed(policy, gw, "fetch", url="https://gamewith.jp/pokemon-champions/555538")          # 一覧に無い URL
    assert not B.host_allowed(policy, gw, "fetch")                                                             # URL 無しは不可
    assert B.host_allowed(policy, gw, "store") and not B.host_allowed(policy, gw, "send_llm")                  # unknown は進めない
    assert B.host_allowed(policy, gw, "weakness") and B.host_allowed(policy, gw, "selection") and not B.host_allowed(policy, gw, "pool")
    assert B.host_allowed(policy, "open.example", "fetch") and B.host_allowed(policy, "open.example", "pool")  # 制限が無ければ通る
    assert not B.host_allowed(policy, "yakkun.com", "fetch", url="https://yakkun.com/ch/theory/") and B.host_allowed(policy, "yakkun.com", "weakness")
    assert not B.host_allowed(policy, "unknown.example", "fetch") and not B.host_allowed(None, gw, "fetch", url="https://gamewith.jp/x")
    assert B.host_policy_entry(policy, "_note") == {}                                                          # 注記の項目はホストではない
    editorial = _team(source={"host": "www.gamewith.jp", "publisher_kind": "editorial_site", "usage_evidence": "battle_log_confirmed"})
    assert B.usable_for(editorial, "pool", REG_MC) and not B.usable_for(editorial, "pool", REG_MC, policy=policy)   # 方針の用途の制限
    assert B.usable_for(editorial, "weakness", REG_MC, policy=policy) and B.usable_for(editorial, "parser_eval", policy=policy)
    assert B.usable_for(_team(source={"host": "open.example", "usage_evidence": "self_report"}), "pool", REG_MC, policy=policy)
    print("test_host_policy_urls_and_purposes OK")


def main() -> None:
    test_record_kind_branches()
    test_source_axes()
    test_usable_for_branches()
    test_set_regulation_history()
    test_save_load_synthetic_gate()
    test_host_policy_urls_and_purposes()
    print("ALL OK")


if __name__ == "__main__":
    main()
