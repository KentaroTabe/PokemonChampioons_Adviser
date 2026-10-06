"""記事バンクの記録 (tools/team_build/article_bank) の種類・出典の 2 軸・用途の判定・規制の履歴・合成の門のテスト
(docs/ARTICLE_BANK_DESIGN_1006.md §3.7、2026-10-06 ユーザー判断)。facets (用途ごとに必要な情報) と status (情報不足 / 矛盾) の区別も見る。
記録の系列の鍵 (lineage_key)・保存時の重複処理と版の関係 (merge_cases / extend_bank)・最新版だけの読み出し (latest_only)・manifest の
件数・旧版を使わない用途の判定 (§5.1、2026-10-06 ユーザー判断) も見る。

素材は tests/test_article_parse.py の合成記事 SYNTHETIC と、その 1 体目だけの単体の型 (合成)、代表 1 体の型の記事 REP と
種名だけ分かる 5 体 NAMED_FIVE。系列の試験は型 1 体の最小の記録 (_mini)。

    python -m tests.test_article_bank
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from tests.test_article_parse import NAMED_FIVE, REP, SYNTHETIC
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
    # 1 体だけの構築 (incomplete)。2026-10-06 ユーザー判断で incomplete を一律に除外せず、用途ごとの必要な情報で判定する。
    # この記録にはペリッパーの型 (4 技) が 1 つあるので weakness の要件 (any_set_or_claim) を満たす → 期待を「使える」に変えた。
    # 6 体の種が分からないので pool / selection には使えない (従来の「情報不足の記録を構築・選出に使わない」は保つ)
    inc = B.build_record(P.parse_article(SINGLE), source=real, meta={"regulation": REG_MC})
    assert inc["status"] == "incomplete" and inc["members"][0]["moves"] and inc["claims"] == []
    assert B.usable_for(inc, "weakness", REG_MC) and B.usable_for(inc, "parser_eval")
    assert not B.usable_for(inc, "pool", REG_MC) and not B.usable_for(inc, "selection", REG_MC)
    # 型も主張も無い incomplete (技一覧の無い単体の型) は weakness にも使わない (従来の期待の意図)
    no_moves = SINGLE.rsplit("\n* [ぼうふう]", 1)[0] + "\n"
    bare = B.build_record(P.parse_article(no_moves, max_members=1), source=real, meta={"regulation": REG_MC}, record_kind="single_set")
    assert bare["status"] == "incomplete" and not B.usable_for(bare, "weakness", REG_MC) and B.usable_for(bare, "parser_eval")
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
        assert man["n_lineages"] == 2 and man["n_superseded"] == 0                # 出典 URL の無い 2 記録は内容ごとの別の系列
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


def test_merge_selection_rules():
    """同じ条件・個体・初手・推奨の複数記述は 1 つにまとめ (source_refs に根拠を全部残す)、どれかに「など」があれば example / exact_trio False。
    条件や初手が違えば別の規則のまま (2026-10-06 ユーザー判断)"""
    base = {"kind": "author_selection_rule", "condition": None, "selected_members": ["m1", "m2", "m3"], "selected_species": [], "lead": "m1",
            "lead_species": None, "recommendation": "default", "exact_trio": True, "example": False, "free_slots": 0}
    table = dict(base, example=True, exact_trio=False, source_ref="team:s1")                  # 表の文 (など)
    prose = dict(base, source_ref="team:s3", selected_members=["m3", "m1", "m2"])            # 本文の文 (順が違う、など無し)
    other_lead = dict(base, lead="m2", source_ref="team:s4")
    cond = dict(base, condition={"predicate": "species_present"}, recommendation="preferred", source_ref="team:s5")
    merged = B.merge_selection_rules([table, prose, other_lead, cond])
    assert len(merged) == 3
    assert merged[0]["source_refs"] == ["team:s1", "team:s3"] and merged[0]["source_ref"] == "team:s1"
    assert merged[0]["example"] is True and merged[0]["exact_trio"] is False                  # 本文に「など」が無くても格上げしない
    assert merged[1]["source_refs"] == ["team:s4"] and merged[1]["exact_trio"] is True and merged[2]["source_refs"] == ["team:s5"]
    assert prose["source_ref"] == "team:s3" and "source_refs" not in prose                     # 元の列は変えない
    assert B.merge_selection_rules([]) == []
    rec = B.build_record({"members": [], "selection_rules": [table, prose]}, source={"synthetic": True})
    assert len(rec["selection_rules"]) == 1 and rec["selection_rules"][0]["source_refs"] == ["team:s1", "team:s3"]
    print("test_merge_selection_rules OK")


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


def test_facets_and_status():
    """facets (用途ごとに必要な情報) と status (情報不足 incomplete / 検査の矛盾 conflict / 個体なし failed) の区別 (2026-10-06 ユーザー判断)"""
    real = {"host": "a.example", "publisher_kind": "personal_blog", "usage_evidence": "self_report"}
    meta = {"regulation": REG_MC}
    # 6 体・各 4 技・選出規則あり: 全部揃う
    team = B.build_validated_record(P.parse_article(SYNTHETIC), real, meta)
    assert team["facets"] == {"members_known": True, "sets_known": True, "selection_readable": True, "any_set_or_claim": True}
    assert team["status"] == "ok" and team["problems"] == [] and team["members_named_only"] == []
    # 代表 1 体 + 選出規則: 6 体の種が分からない → selection 不可 (照合の条件を満たさない)、型があるので weakness 可。
    # 個体が足りないのは情報不足なので incomplete (矛盾ではない)
    rep = B.build_validated_record(P.parse_article(REP), real, meta)
    assert rep["facets"] == {"members_known": False, "sets_known": False, "selection_readable": True, "any_set_or_claim": True}
    assert rep["status"] == "incomplete" and rep["problems"] == ["members:1"]
    assert not B.usable_for(rep, "selection", REG_MC) and B.usable_for(rep, "weakness", REG_MC) and not B.usable_for(rep, "pool", REG_MC)
    # 代表 1 体の型 + 5 体の種名 (変換層が渡す) + 選出規則: 6 体の種が分かる → selection 可、型が 1 体だけなので pool 不可
    named = B.build_validated_record(P.parse_article(REP, members_named_only=list(NAMED_FIVE)), real, meta)
    assert named["facets"] == {"members_known": True, "sets_known": False, "selection_readable": True, "any_set_or_claim": True}
    assert named["status"] == "incomplete" and named["problems"] == ["members:1"] and len(named["members"]) == 1
    assert [n["species_id"] for n in named["members_named_only"]] == ["pelipper", "archaludon", "gholdengo", "salamencemega", "tyranitar"]
    assert all("id" not in n for n in named["members_named_only"])                  # 架空の個体 id (m2 …) を作らない
    assert B.usable_for(named, "selection", REG_MC) and not B.usable_for(named, "pool", REG_MC) and B.usable_for(named, "weakness", REG_MC)
    B.assert_no_prose(named)
    assert B.llm_payload(named)["members_named_only"] == named["members_named_only"]
    # 検査の矛盾 (ポイント合計 67・252 表示との対応・実数値の再計算) → conflict。用途は parser_eval 以外すべて不可
    bad = SYNTHETIC.replace("HP:32 / 特攻:29 / 特防:2 / 素早:3", "HP:32 / 特攻:30 / 特防:2 / 素早:3")
    conflict = B.build_validated_record(P.parse_article(bad), real, meta)
    assert conflict["status"] == "conflict" and "m1:point_total:67" in conflict["problems"] and "m1:ev252_mismatch:spa" in conflict["problems"]
    assert not any(B.usable_for(conflict, p, REG_MC) for p in ("pool", "weakness", "selection")) and B.usable_for(conflict, "parser_eval")
    # 問題を渡さなければ従来どおり (status は解析の結果だけで決まり、problems の項目は作らない)
    plain = B.build_record(P.parse_article(bad), real, meta)
    assert plain["status"] == "ok" and "problems" not in plain
    # 情報不足だけ (技が 3 つ) → incomplete (矛盾ではない)。渡さなければ従来どおり warnings。6 体の種と選出規則はあるので selection は可
    three = P.parse_article(SYNTHETIC.replace(f"[ラスターカノン]({L}/move/430)", ""))
    assert B.build_record(three, real, meta)["status"] == "warnings"
    rec3 = B.build_validated_record(three, real, meta)
    assert rec3["status"] == "incomplete" and rec3["problems"] == ["m3:moves:3"] and rec3["facets"]["sets_known"] is False
    assert not B.usable_for(rec3, "pool", REG_MC) and B.usable_for(rec3, "selection", REG_MC) and B.usable_for(rec3, "weakness", REG_MC)
    # 矛盾と情報不足が両方あれば conflict
    assert B.build_record(three, real, meta, problems=["m3:moves:3", "m1:point_total:67"])["status"] == "conflict"
    # 個体が無い → failed。種名だけ 6 体 (型・主張・選出規則なし) は failed ではなく incomplete で、どの用途にも足りない
    assert B.build_record(P.parse_article("本文だけ"), real, meta)["status"] == "failed"
    six_named = [{"species_id": "swampertmega", "base_species_id": "swampert", "mega_stone": "swampertite"}] + list(NAMED_FIVE)
    only_named = B.build_validated_record(P.parse_article("本文だけ", members_named_only=six_named), real, meta)
    assert only_named["status"] == "incomplete" and only_named["problems"] == ["members:0"]
    assert only_named["facets"] == {"members_known": True, "sets_known": False, "selection_readable": False, "any_set_or_claim": False}
    assert not any(B.usable_for(only_named, p, REG_MC) for p in ("pool", "weakness", "selection"))
    # 情報不足の分類 (個体・技が足りない) と矛盾 (多すぎる・整合しない)
    assert B.problem_is_insufficient("members:1") and B.problem_is_insufficient("m2:moves:3")
    assert not B.problem_is_insufficient("members:7") and not B.problem_is_insufficient("m2:moves:5")
    assert not B.problem_is_insufficient("members:2", "single_set") and not B.problem_is_insufficient("m1:point_total:67")
    assert not B.problem_is_insufficient("rule0:members_outside_team")
    # facets の無い古い記録は中身から計算する
    old = {k: v for k, v in team.items() if k != "facets"}
    assert B.record_facets(old) == team["facets"] and B.usable_for(old, "selection", REG_MC)
    print("test_facets_and_status OK")


def test_validate_named_and_selected_species():
    """検査: 種名だけの個体 (重複・数の超過・メガ石)、選出規則の種 (型のある個体の種・形態の違い・構築の外の種)、先発の種"""
    real = {"host": "a.example", "usage_evidence": "self_report"}
    named = B.build_record(P.parse_article(REP, members_named_only=list(NAMED_FIVE)), real, {"regulation": REG_MC})
    assert B.validate_record(named) == ["members:1"]
    dup = json.loads(json.dumps(named))
    dup["members_named_only"].append({"species_id": "swampert", "base_species_id": "swampert", "mega_stone": None})   # 型のある個体と同じ基本種
    probs = B.validate_record(dup)
    assert "duplicate_base_species" in probs and "members_total:7" in probs
    stone = json.loads(json.dumps(named))
    stone["members_named_only"][3]["mega_stone"] = "swampertite"              # メガボーマンダに別のメガ石
    stone["members_named_only"][0]["mega_stone"] = "salamencite"              # メガ形態でない種にメガ石
    probs = B.validate_record(stone)
    assert "named3:mega_stone:swampertite" in probs and "named0:stone_without_form" in probs
    rule = json.loads(json.dumps(named))
    r = rule["selection_rules"][0]
    r["selected_species"] = [{"species_id": "swampert", "base_species_id": "swampert"},       # 型のある個体 (m1) の基本種 → 個体 id で書くべき
                             {"species_id": "salamence", "base_species_id": "salamence"},     # 種名だけの個体はメガボーマンダ (形態の違い)
                             {"species_id": "dragonite", "base_species_id": "dragonite"}]     # 6 体が分かっている構築の外
    r["lead"], r["lead_species"] = "m1", "garchomp"
    probs = B.validate_record(rule)
    for p in ("rule0:selected_species_is_member:swampert", "rule0:selected_species_form_mismatch:salamence",
              "rule0:species_outside_team:dragonite", "rule0:lead_species_outside_selection", "rule0:two_leads"):
        assert p in probs, p
    assert B.build_record(P.parse_article(REP, members_named_only=list(NAMED_FIVE)), real, {"regulation": REG_MC},
                          problems=probs)["status"] == "conflict"
    # 6 体が分かっていなければ、型の無い種を選出に挙げても矛盾にしない (残りの枠のどれかかもしれない)
    rep = B.build_record(P.parse_article(REP.replace("サーフゴー、ボーマンダ、メガラグラージ。", "サーフゴーやカイリューを選出します。")),
                         real, {"regulation": REG_MC})
    assert [s["species_id"] for s in rep["selection_rules"][0]["selected_species"]] == ["gholdengo", "dragonite"]
    assert B.validate_record(rep) == ["members:1"]
    print("test_validate_named_and_selected_species OK")


def _mini(url_hash="u1", meta=None, move="hurricane", problems=None, kind="team", **src):
    """系列・重複の試験用の最小の記録 (型 1 体。move で内容 = case_id を変える。problems で処理状態を変える。src は出典の追加項目)"""
    member = {"id": "m1", "species_id": "pelipper", "base_species_id": "pelipper", "mega_stone": None, "item": "damprock",
              "nature": "modest", "ability": "drizzle", "points": {"hp": 32, "spa": 29, "spd": 2, "spe": 3}, "ev252": None,
              "actual": None, "moves": ["surf", "uturn", "tailwind", move], "warnings": []}
    source = dict({"url_hash": url_hash, "host": "a.example", "usage_evidence": "self_report", "fetched_at": "2026-10-06"}, **src)
    return B.build_record({"members": [member]}, source=source, meta=dict({"regulation": REG_MC}, **(meta or {})), record_kind=kind,
                          problems=problems)


def test_lineage_key():
    """系列の鍵 (2026-10-06 ユーザー判断): 同じ記事 (url_hash) の同じ構築 (team_code、無ければ unit_index)。手入力は型の内容
    (種・持ち物・技・能力ポイント)。出典の URL も手入力の印も無ければ内容そのもの (case_id)"""
    # team_code があればそれ (unit_index より先)。並びの番号や内容が変わっても同じ系列
    a = _mini(meta={"team_code": "E2E9MW0BQ7", "unit_index": 3})
    assert B.lineage_key(a) == "team:url:u1:team_code:E2E9MW0BQ7"
    assert B.lineage_key(_mini(meta={"team_code": "E2E9MW0BQ7", "unit_index": 5}, move="raindance")) == B.lineage_key(a)
    # unit_index だけ (0 も番号として使う)
    assert B.lineage_key(_mini(meta={"unit_index": 2})) == "team:url:u1:unit_index:2"
    assert B.lineage_key(_mini(meta={"unit_index": 0})) == "team:url:u1:unit_index:0"
    # どちらも無ければ url だけ (ページ全体が 1 つの構築)
    assert B.lineage_key(_mini()) == "team:url:u1"
    # 違う記事なら同じチーム ID でも別の系列。構築と単体の型も別の系列
    assert B.lineage_key(_mini(url_hash="u2", meta={"team_code": "E2E9MW0BQ7"})) != B.lineage_key(a)
    assert B.lineage_key(_mini(kind="single_set")) == "single_set:url:u1"
    # url_hash が無ければ source.url を正規化して作る (追跡クエリ・断片が違っても同じ記事)
    from tools.team_build.article_units import url_hash
    u = "https://a.example/blog/1"
    via_url = _mini(url_hash=None, url=u + "?utm_source=x#top", meta={"team_code": "T1"})
    assert B.lineage_key(via_url) == f"team:url:{url_hash(u)}:team_code:T1"
    assert B.lineage_key(via_url) == B.lineage_key(_mini(url_hash=url_hash(u), meta={"team_code": "T1"}))
    # 出典の URL も手入力の印も無ければ内容そのもの: 同一内容の重複だけを除き、更新の関係は作らない
    bare = _mini(url_hash=None)
    assert B.lineage_key(bare) == f"team:case:{bare['case_id']}"
    assert B.lineage_key(_mini(url_hash=None, move="raindance")) != B.lineage_key(bare)
    # 手入力: 型の内容のハッシュ (出典 URL は見ない。技の順・能力ポイントの 0 の書き方は区別しない)
    manual = _mini(url_hash=None, kind="single_set", entry_method="manual", url="https://yakkun.com/x")
    k = B.lineage_key(manual)
    assert k.startswith("single_set:manual:") and k == B.lineage_key(_mini(url_hash="zz", kind="single_set", entry_method="manual"))
    reordered = json.loads(json.dumps(manual))
    reordered["members"][0]["moves"] = list(reversed(reordered["members"][0]["moves"]))
    reordered["members"][0]["points"]["atk"] = 0
    assert B.lineage_key(reordered) == k
    for field, value in (("points", {"hp": 32, "spa": 28, "spd": 3, "spe": 3}), ("item", "leftovers"), ("species_id", "politoed"),
                         ("moves", ["surf", "uturn", "tailwind", "raindance"])):
        changed = json.loads(json.dumps(manual))
        changed["members"][0][field] = value
        assert B.lineage_key(changed) != k, field                       # 種・持ち物・性格・特性・技・能力ポイントが違えば別の型
    other = json.loads(json.dumps(manual))
    other["members"][0]["nature"] = "calm"
    other["members"][0]["ability"] = "keeneye"
    assert B.lineage_key(other) != k                                    # 性格・特性も鍵に入れる: 違えば別の型 (運用側の判断 10/6。BUILD_ARTICLE_LINEAGE_MANUAL_FIELDS)
    print("test_lineage_key OK")


def test_merge_cases_branches():
    """併合の規則 (2026-10-06 ユーザー判断): 同じ系列で同一内容 → 足さない (duplicate。記録は旧いまま)、同じ系列で内容が違う → 足して
    supersedes / superseded_by で結ぶ (旧版は消さない)、系列が違う → 足す (同じ case_id でも)。conflict / failed は入れない。
    内容が旧版に戻ったら足さず最新も変えない (reverted)。入力は変えない。併合の結果をもう一度併合しても同じ"""
    t1 = "team:url:u1:team_code:T1"
    a = _mini(meta={"team_code": "T1"})                                       # 系列 T1 の最初の内容
    a_again = _mini(meta={"team_code": "T1"}, fetched_at="2026-10-07")        # 同じ内容の再取得 (取得日だけ違う)
    b = _mini(meta={"team_code": "T1"}, move="raindance")                     # 系列 T1 の更新
    c = _mini(meta={"team_code": "T2"})                                       # 別の系列 (内容は a と同じ)
    assert a["case_id"] == a_again["case_id"] == c["case_id"] != b["case_id"]
    first = B.merge_cases([], [a, c])
    assert first["outcomes"] == ["added", "added"] and len(first["cases"]) == 2
    snapshot = json.dumps([a, a_again, b, c], sort_keys=True)
    m = B.merge_cases(first["cases"], [a_again, b])
    assert m["outcomes"] == ["duplicate", "updated"]
    assert m["duplicates"] == [{"lineage": t1, "case_id": a["case_id"], "matched": "latest"}]
    # same_body_hash: 本文のハッシュが新旧どちらかに無ければ None (この試験の記録には無い)
    assert m["added"] == [{"lineage": t1, "case_id": b["case_id"], "supersedes": a["case_id"], "same_body_hash": None}]
    assert m["superseded"] == [{"lineage": t1, "old": a["case_id"], "new": b["case_id"], "same_body_hash": None}]
    assert (m["counts"]["added"], m["counts"]["duplicates"], m["counts"]["superseded"], m["counts"]["rejected"]) == (1, 1, 1, 0)
    old, other, new = m["cases"]
    assert old["case_id"] == a["case_id"] and old["superseded_by"] == b["case_id"] and old["source"]["fetched_at"] == "2026-10-06"
    assert new["case_id"] == b["case_id"] and new["supersedes"] == a["case_id"] and "superseded_by" not in new
    assert other["case_id"] == c["case_id"] and not any(k in other for k in B.LINK_KEYS)        # 別の系列は同じ case_id でも結ばない
    assert json.dumps([a, a_again, b, c], sort_keys=True) == snapshot and "superseded_by" not in first["cases"][0]   # 入力は変えない
    # 更新が続けば鎖になる (a → b → d)。各系列の最新はいつも 1 つ
    d = _mini(meta={"team_code": "T1"}, move="protect")
    m2 = B.merge_cases(m["cases"], [d])
    assert m2["outcomes"] == ["updated"]
    assert m2["superseded"] == [{"lineage": t1, "old": b["case_id"], "new": d["case_id"], "same_body_hash": None}]
    chain = {x["case_id"]: x for x in m2["cases"] if B.lineage_key(x) == t1}
    assert chain[b["case_id"]]["supersedes"] == a["case_id"] and chain[b["case_id"]]["superseded_by"] == d["case_id"]
    assert [x["case_id"] for x in m2["cases"] if not x.get("superseded_by")] == [c["case_id"], d["case_id"]]
    # 内容が旧版に戻った (a と同じ) → 足さず、最新 (d) も変えない (reverted。duplicates にも数える。最新を戻すかは判断待ち)
    m3 = B.merge_cases(m2["cases"], [a_again])
    assert m3["outcomes"] == ["reverted"] and m3["cases"] == m2["cases"]
    assert m3["reverted"] == [{"lineage": t1, "case_id": a["case_id"], "matched": "superseded", "latest": d["case_id"]}]
    assert (m3["counts"]["duplicates"], m3["counts"]["reverted"], m3["counts"]["added"]) == (1, 1, 0)
    # conflict / failed は入れない (rejected。lineage_in_bank = その系列の今の最新版が残る)
    bad = _mini(meta={"team_code": "T1"}, move="substitute", problems=["m1:point_total:67"])
    failed = B.build_record({"members": []}, source={"url_hash": "u1"}, meta={"team_code": "T9"})
    assert bad["status"] == "conflict" and failed["status"] == "failed"
    m4 = B.merge_cases(m2["cases"], [bad, failed])
    assert m4["outcomes"] == ["rejected", "rejected"] and m4["cases"] == m2["cases"]
    assert [(r["status"], r["lineage_in_bank"]) for r in m4["rejected"]] == [("conflict", True), ("failed", False)]
    # 1 回の入力の中の重複・更新も同じ規則 (順に置く)
    m5 = B.merge_cases([], [a, a_again, b])
    assert m5["outcomes"] == ["added", "duplicate", "updated"] and len(m5["cases"]) == 2
    assert set(m5["outcomes"]) <= set(B.MERGE_OUTCOMES)
    # 本文のハッシュが同じで内容が違う更新 = 記事ではなく解析器・辞書・変換層の変更 (same_body_hash True)。本文が違えば False
    h1 = _mini(meta={"team_code": "T5"}, body_hash="h1")
    h1_parser = _mini(meta={"team_code": "T5"}, move="raindance", body_hash="h1")
    h2_page = _mini(meta={"team_code": "T5"}, move="protect", body_hash="h2")
    m6 = B.merge_cases([], [h1, h1_parser, h2_page])
    assert m6["outcomes"] == ["added", "updated", "updated"] and [s["same_body_hash"] for s in m6["superseded"]] == [True, False]
    # 併合の結果をもう一度併合しても同じ (save_bank の正規化はこれに頼る)
    again = B.merge_cases([], m2["cases"])
    assert again["cases"] == m2["cases"] and again["counts"]["duplicates"] == 0
    assert B.merge_cases(m2["cases"], [])["counts"]["existing_relinked"] == 0
    # 旧い形式のバンク (重複・結ばれていない更新・conflict を含む) も同じ規則で正し、正した数を数える
    legacy = B.merge_cases([a, a_again, b, bad], [])
    assert len(legacy["cases"]) == 2 and legacy["counts"]["existing_dropped"] == 2 and legacy["counts"]["existing_relinked"] == 2
    assert legacy["cases"][0]["superseded_by"] == b["case_id"] and legacy["cases"][1]["supersedes"] == a["case_id"]
    # case_id の無い記録は入れない (同一内容を判定できない)
    _expect_value_error(B.merge_cases, [], [{"source": {}, "status": "ok"}])
    print("test_merge_cases_branches OK")


def test_bank_latest_only_and_manifest():
    """保存は merge_cases で正規化する (併合済みの列は変わらない)。manifest に系列の数と旧版の数。読み出しの既定は各系列の最新の記録だけ
    (latest_only=False で全部)。conflict / failed を保存しようとすると何も書かない。extend_bank は基の版を旧版も含めて読んで併合する"""
    a = _mini(meta={"team_code": "T1"})
    b = _mini(meta={"team_code": "T1"}, move="raindance")
    c = _mini(meta={"team_code": "T2"}, move="protect")
    merged = B.merge_cases([], [a, c, b])["cases"]
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        out = B.save_bank(merged, root)
        man = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        assert (man["n_cases"], man["n_lineages"], man["n_superseded"]) == (3, 2, 1) and man["schema"] == B.SCHEMA_VERSION
        assert [x["case_id"] for x in B.load_bank(out.name, root)] == [c["case_id"], b["case_id"]]                 # 既定は最新だけ
        assert [x["case_id"] for x in B.load_bank(out.name, root, latest_only=False)] == [a["case_id"], c["case_id"], b["case_id"]]
        # 併合していない列を渡しても保存の前に正規化する (同一内容は 1 件、更新は結ぶ) → 併合済みの列と同じ版
        assert B.save_bank([a, c, a, b], root).name == out.name
        # 処理状態が conflict / failed の記録は保存しない (何も書かない)
        bad = _mini(meta={"team_code": "T3"}, problems=["m1:point_total:67"])
        msg = _expect_value_error(B.save_bank, [a, bad], root / "bad")
        assert "merge_cases" in msg and not (root / "bad").exists()
        # extend_bank: 基の版を旧版も含めて読んで併合する (旧版を消さない)。同じ内容だけなら版も同じ
        same = B.extend_bank([b, c], root, base_version=out.name)
        assert same["saved_to"].name == out.name and same["merge"]["outcomes"] == ["duplicate", "duplicate"] and same["n_cases"] == 3
        e = _mini(meta={"team_code": "T2"}, move="haze")
        ext = B.extend_bank([e], root, base_version=out.name)
        assert ext["merge"]["outcomes"] == ["updated"] and ext["n_cases"] == 4 and ext["saved_to"].name != out.name
        assert [x["case_id"] for x in B.load_bank(ext["saved_to"].name, root, latest_only=False)] == [a["case_id"], c["case_id"],
                                                                                                    b["case_id"], e["case_id"]]
        assert [x["case_id"] for x in B.load_bank(ext["saved_to"].name, root)] == [b["case_id"], e["case_id"]]
        man2 = json.loads((ext["saved_to"] / "manifest.json").read_text(encoding="utf-8"))
        assert (man2["n_cases"], man2["n_lineages"], man2["n_superseded"]) == (4, 2, 2)
        assert len(B.load_bank(out.name, root, latest_only=False)) == 3                                         # 基の版は変わらない
        # 記録が 1 件も残らなければ保存しない
        assert B.extend_bank([bad], root / "none")["saved_to"] is None and not (root / "none").exists()
    print("test_bank_latest_only_and_manifest OK")


def test_usable_for_skips_superseded():
    """系列の旧版 (superseded_by の付いた記録) は parser_eval 以外の用途に使わない。新版 (supersedes だけ) は使う (2026-10-06 ユーザー判断)"""
    real = {"host": "a.example", "publisher_kind": "personal_blog", "usage_evidence": "self_report"}
    team = _team(source=real)
    assert all(B.usable_for(team, p, REG_MC) for p in ("pool", "weakness", "selection"))
    old_team = dict(team, superseded_by="case_newer")
    assert not any(B.usable_for(old_team, p, REG_MC) for p in ("pool", "weakness", "selection")) and B.usable_for(old_team, "parser_eval")
    assert all(B.usable_for(dict(team, supersedes="case_older"), p, REG_MC) for p in ("pool", "weakness", "selection"))
    old, new = B.merge_cases([], [_mini(meta={"team_code": "T1"}), _mini(meta={"team_code": "T1"}, move="raindance")])["cases"]
    assert not B.usable_for(old, "weakness", REG_MC) and B.usable_for(new, "weakness", REG_MC)
    print("test_usable_for_skips_superseded OK")


def main() -> None:
    test_record_kind_branches()
    test_source_axes()
    test_usable_for_branches()
    test_set_regulation_history()
    test_save_load_synthetic_gate()
    test_merge_selection_rules()
    test_host_policy_urls_and_purposes()
    test_facets_and_status()
    test_validate_named_and_selected_species()
    test_lineage_key()
    test_merge_cases_branches()
    test_bank_latest_only_and_manifest()
    test_usable_for_skips_superseded()
    print("ALL OK")


if __name__ == "__main__":
    main()
