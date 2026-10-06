"""記事専用の別名辞書 (tools/team_build/article_aliases) のテスト (docs/ARTICLE_BANK_DESIGN_1006.md §3.8、2026-10-06 ユーザー判断)。

自動確定は known_transform と site_id_verified だけ。往復一致は必須だが確定の条件ではない (LLM だけが根拠の対応は candidate)。
LLM はすべて偽の resolver / MockProvider (ネットワークに出ない)。

    python -m tests.test_article_aliases
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from tests.test_article_parse import SYNTHETIC
from tools.team_build import article_aliases as A
from tools.team_build import article_parse as P
from tools.team_build.llm.provider import MockProvider
from vision.normalize import JP_NAMES_PATH

TODAY = "2026-10-06"
Y = "https://yakkun.com/ch"
YAKKUN_SET = (f"* [バンギラス]({Y}/zukan/n248)@[オボンのみ]({Y}/item?item_s=14)([しんちょう]({Y}/nature))"
              f"[すなおこし]({Y}/zukan/search/?tokusei=45)\n"
              "* HP:32 / 攻撃:4 / 特防:30\n"
              f"* [{{eq}}]({Y}/move?move=89)[はたきおとす]({Y}/move?move=282)[ストーンエッジ]({Y}/move?move=444)[ちょうはつ]({Y}/move?move=269)\n")


def _raw():
    return json.loads(JP_NAMES_PATH.read_text(encoding="utf-8"))


def _item(text, category="moves", host=None, site_key=None):
    return {"category": category, "text": text, "host": host, "site_key": site_key}


def _data(*entries):
    return {"schema": A.SCHEMA, "entries": [json.loads(json.dumps(e)) for e in entries]}


def test_known_transform():
    """既知の変換 (万 → まん) で厳密一致すれば confirmed (basis = known_transform)。変換が当たらない表記は確定しない"""
    dic = P.default_dictionary()
    e = A.known_transform_entry(dic, _item("10万ボルト"), TODAY)
    assert (e["status"], e["basis"], e["id"], e["canonical"]) == ("confirmed", "known_transform", "thunderbolt", "10まんボルト")
    assert e["history"] == [{"at": TODAY, "from": None, "to": "confirmed", "basis": "known_transform"}] and "source" not in e
    assert A.known_transform_entry(dic, _item("10万ばりき"), TODAY)["id"] == "highhorsepower"
    assert A.known_transform_entry(dic, _item("地震"), TODAY) is None                     # 変換が当たらない
    assert A.known_transform_entry(dic, _item("100万ボルト"), TODAY) is None              # 変換しても辞書に無い
    assert A.known_transform_entry(dic, _item("10万ボルト", category="items"), TODAY) is None   # 種別が違えば解決しない
    assert A.apply_known_transforms("10万ボルト") == "10まんボルト"
    got = A.auto_entries([_item("10万ボルト"), _item("10万ボルト"), _item("地震")], dic, None, TODAY)
    assert [x["alias"] for x in got] == ["10万ボルト"]                                     # 同じ表記は 1 つ、確定できないものは出さない
    print("test_known_transform OK")


def test_site_id_verified():
    """サイト固有 id: 単一の id に 2 記事以上で対応していれば確定。1 記事は確定しない。同じ key に別の id → ambiguous で以後も確定しない"""
    dic = P.default_dictionary()
    item = _item("地震", host="yakkun.com", site_key="89")
    obs = [{"host": "yakkun.com", "category": "moves", "key": "89", "id": "earthquake"}]
    s0 = A.SiteIdStore()
    s1 = s0.observe(obs, source_id="article_a")
    assert s0.state == A.empty_site_ids()                                                 # 更新は新しい置き場を返す (純粋)
    assert s1.confirmed_id("yakkun.com", "moves", "89") is None and A.site_id_entry(dic, s1, item, TODAY) is None
    s1b = s1.observe(obs, source_id="article_a")                                          # 同じ記事の同じ観測は 2 度数えない
    assert s1b.state["keys"]["yakkun.com|moves|89"]["ids"] == {"earthquake": 1} and A.site_id_entry(dic, s1b, item, TODAY) is None
    s2 = s1b.observe(obs, source_id="article_b")
    e = A.site_id_entry(dic, s2, item, TODAY)
    assert (e["status"], e["basis"], e["id"], e["canonical"]) == ("confirmed", "site_id_verified", "earthquake", "じしん")
    assert e["source"] == {"host": "yakkun.com", "site_key": "89"}
    assert A.site_id_entry(dic, s2.state, item, TODAY)["id"] == "earthquake"              # 状態の dict でもよい
    assert A.site_id_entry(dic, s2, _item("地震", host="yakkun.com", site_key="90"), TODAY) is None   # 観測の無い key
    assert A.site_id_entry(dic, s2, _item("地震"), TODAY) is None                         # リンクの無い表記
    # 同じ key に別の id (yakkun の item_s=200 はラグラージナイトとムクホークナイトで共有) → ambiguous、後から片方が増えても確定しない
    st = A.SiteIdStore()
    st = st.observe([{"host": "yakkun.com", "category": "items", "key": "200", "id": "swampertite"}], source_id="a")
    st = st.observe([{"host": "yakkun.com", "category": "items", "key": "200", "id": "staraptite"}], source_id="b")
    for src in ("c", "d", "e"):
        st = st.observe([{"host": "yakkun.com", "category": "items", "key": "200", "id": "swampertite"}], source_id=src)
    assert st.is_ambiguous("yakkun.com", "items", "200") and st.confirmed_id("yakkun.com", "items", "200") is None
    assert A.site_id_entry(dic, st, _item("ラグナイト", category="items", host="yakkun.com", site_key="200"), TODAY) is None
    # 記事の解析から: 2 記事で表示名の厳密一致から観測 → 3 記事目の未解決の「地震」がリンクの id で確定 → 再解析で解決する
    store = A.SiteIdStore()
    for k in ("x1", "x2"):
        parsed = P.parse_article(YAKKUN_SET.format(eq="じしん"), max_members=1)
        assert {"host": "yakkun.com", "category": "moves", "key": "89", "id": "earthquake"} in parsed["site_id_observations"]
        store = store.observe(parsed["site_id_observations"], source_id=k)
    target = YAKKUN_SET.format(eq="地震")
    before = P.parse_article(target, max_members=1)
    assert before["members"][0]["moves"] == [] and before["unresolved_names"] == [_item("地震", host="yakkun.com", site_key="89")]
    entries = A.auto_entries(before["unresolved_names"], P.default_dictionary(), store, TODAY)
    dec = A.decide(entries, A.empty_aliases())
    dic2 = P.default_dictionary().with_aliases(dec["data"])
    after = P.parse_article(target, dic2, max_members=1)
    assert after["members"][0]["moves"] == ["earthquake", "knockoff", "stoneedge", "taunt"] and after["unresolved_names"] == []
    with tempfile.TemporaryDirectory() as d:
        path = store.save(Path(d) / "site_ids.json")
        assert A.SiteIdStore.load(path).confirmed_id("yakkun.com", "moves", "89") == "earthquake"
        assert A.SiteIdStore.load(Path(d) / "missing.json").state == A.empty_site_ids()
    print("test_site_id_verified OK")


def test_llm_only_candidates():
    """LLM だけが根拠の対応は candidate。往復一致を通らないもの・送っていない表記への対応は捨てて件数だけ。送るのは種別と表記だけ"""
    dic = P.default_dictionary()
    seen = []

    def resolver(payload):
        seen.append(json.loads(json.dumps(payload)))
        return {"mappings": [{"category": "moves", "text": "地震", "canonical": "じしん", "id": "earthquake"},
                             {"category": "moves", "text": "謎技", "canonical": "なぞのわざ", "id": "mysterymove"},   # 往復一致しない
                             {"category": "moves", "text": "未送信", "canonical": "じしん", "id": "earthquake"},     # 送っていない表記
                             {"category": "moves", "text": "地震", "canonical": "じならし", "id": "bulldoze"},       # 同じ表記の 2 つ目
                             "not-a-mapping"]}
    items = [_item("地震", host="yakkun.com", site_key="89"), _item("謎技"), _item("地震")]
    res = A.propose_with_llm(items, resolver, dic=dic, today=TODAY)
    assert seen == [{"names": [{"category": "moves", "text": "地震"}, {"category": "moves", "text": "謎技"}]}]   # ホスト・key は送らない
    assert [(e["alias"], e["id"], e["status"], e["basis"]) for e in res["entries"]] == [("地震", "earthquake", "candidate", "llm_only")]
    assert res["entries"][0]["source"] == {"host": "yakkun.com", "site_key": "89"}
    c = res["counts"]
    assert (c["calls"], c["sent"], c["accepted"], c["roundtrip_failed"], c["unknown_text"], c["invalid"]) == (1, 2, 1, 1, 2, 1)
    # 「地震」に「じならし」と bulldoze の正しい組を返す偽の resolver: 往復一致は通るが candidate のまま (自動確定しない)
    wrong = A.propose_with_llm([_item("地震")], lambda p: {"mappings": [{"category": "moves", "text": "地震", "canonical": "じならし",
                                                                          "id": "bulldoze"}]}, dic=dic, today=TODAY)
    e = wrong["entries"][0]
    assert A.roundtrip_ok(dic, "moves", "じならし", "bulldoze") and (e["status"], e["basis"], e["id"]) == ("candidate", "llm_only", "bulldoze")
    dec = A.decide(wrong["entries"], A.empty_aliases())
    assert dec["added"] and P.ArticleDictionary(_raw(), dec["data"]).lookup("moves", "地震") is None    # candidate は辞書で使わない
    # 呼び出しの失敗は件数だけ。名前 1 語でない表記 (長すぎる・文) は送らない
    def boom(_payload):
        raise RuntimeError("network")
    failed = A.propose_with_llm([_item("地震")], boom, dic=dic, today=TODAY)
    assert failed["entries"] == [] and failed["counts"]["errors"] == 1 and failed["counts"]["calls"] == 1
    long_text = "あ" * 21
    calls = []
    skipped = A.propose_with_llm([_item(long_text), _item("雨を降らせる。")], lambda p: calls.append(p) or {}, dic=dic, today=TODAY)
    assert calls == [] and skipped["counts"]["not_name_token"] == 2 and skipped["counts"]["calls"] == 0
    for bad in ({"names": [{"category": "moves", "text": long_text}]}, {"names": [{"category": "moves", "text": "地震", "host": "x"}]},
                {"names": [], "body": "x"}):
        try:
            A.assert_name_tokens(bad)
            raise AssertionError("門が効いていない")
        except ValueError as ex:
            assert "あ" not in str(ex)                                                      # 表記を例外文言に入れない
    print("test_llm_only_candidates OK")


def test_llm_limit_and_dedupe():
    """1 回の処理で最大 50 語を 1 回の呼び出しで送る。種別と正規化した表記で重複を除く"""
    sent = []

    def resolver(payload):
        sent.append(len(payload["names"]))
        return {"mappings": []}
    items = [_item(f"技{i}") for i in range(60)] + [_item("技0"), _item("技 0"), _item("技0", category="items")]
    res = A.propose_with_llm(items, resolver, dic=P.default_dictionary(), today=TODAY)
    assert A.BUILD_ARTICLE_ALIAS_LLM_MAX_NAMES == 50 and A.BUILD_ARTICLE_ALIAS_LLM_MAX_CALLS == 1
    assert sent == [50] and res["counts"]["names"] == 61 and res["counts"]["over_limit"] == 11 and res["counts"]["calls"] == 1
    sent.clear()
    A.propose_with_llm(items, resolver, max_names=20, max_calls=2, dic=P.default_dictionary(), today=TODAY)
    assert sent == [20, 20]
    print("test_llm_limit_and_dedupe OK")


def test_llm_resolver_with_mock_provider():
    """llm_resolver は provider.call を 1 回だけ呼ぶ (再試行なし)。不正な出力なら対応なし"""
    good = json.dumps({"authoritative": {"mappings": [{"category": "moves", "text": "地震", "canonical": "じしん", "id": "earthquake"}]}},
                      ensure_ascii=False)
    prov = MockProvider([good])
    out = A.llm_resolver(prov)({"names": [{"category": "moves", "text": "地震"}]})
    assert out == {"mappings": [{"category": "moves", "text": "地震", "canonical": "じしん", "id": "earthquake"}]}
    assert len(prov.calls) == 1 and prov.calls[0]["stage"] == A.ALIAS_STAGE and prov.calls[0]["tier"] == A.BUILD_ARTICLE_ALIAS_LLM_TIER
    bad = MockProvider([json.dumps({"authoritative": {"mappings": "none"}})])
    assert A.llm_resolver(bad)({"names": [{"category": "moves", "text": "地震"}]}) == {"mappings": []} and len(bad.calls) == 1
    res = A.propose_with_llm([_item("地震")], A.llm_resolver(MockProvider([good])), dic=P.default_dictionary(), today=TODAY)
    assert [e["status"] for e in res["entries"]] == ["candidate"]
    print("test_llm_resolver_with_mock_provider OK")


def test_decide_conflict_and_upgrade():
    """既存の有効な対応と id が違えば conflict (反映しない)。candidate と同じ id の confirmed は格上げ。却下済みの同じ対応は足さない"""
    eq_conf = A.make_entry("moves", "地震", "じしん", "earthquake", "confirmed", "site_id_verified", TODAY)
    eq_cand = A.make_entry("moves", "地震", "じしん", "earthquake", "candidate", "llm_only", TODAY)
    bd_cand = A.make_entry("moves", "地震", "じならし", "bulldoze", "candidate", "llm_only", TODAY)
    bd_conf = A.make_entry("moves", "地震", "じならし", "bulldoze", "confirmed", "known_transform", TODAY)
    existing = _data(eq_conf)
    dec = A.decide([bd_conf, bd_cand], existing)
    assert len(dec["conflicts"]) == 2 and dec["added"] == [] and dec["data"] == existing
    assert dec["conflicts"][0] == {"category": "moves", "alias": "地震", "existing_id": "earthquake", "existing_status": "confirmed",
                                   "new_id": "bulldoze", "new_basis": "known_transform"}
    # candidate (llm_only の誤り) があるところへ別の id の confirmed が来ても上書きしない (人が先に却下する)
    dec = A.decide([eq_conf], _data(bd_cand))
    assert len(dec["conflicts"]) == 1 and [e["id"] for e in dec["data"]["entries"]] == ["bulldoze"]
    # 同じ id の candidate → confirmed に格上げ (history に残る)
    dec = A.decide([eq_conf], _data(eq_cand))
    up = dec["data"]["entries"][0]
    assert dec["upgraded"] and up["status"] == "confirmed" and up["basis"] == "site_id_verified"
    assert up["history"][-1] == {"at": TODAY, "from": "candidate", "to": "confirmed", "basis": "site_id_verified"}
    assert A.decide([eq_cand], _data(eq_conf))["duplicates"] == 1
    # 却下済みの同じ対応は足さない、別の id なら候補として足す
    rejected = dict(bd_cand, status="rejected", basis="human")
    assert A.decide([bd_cand], _data(rejected))["skipped_rejected"] == 1
    dec = A.decide([eq_cand], _data(rejected))
    assert [e["id"] for e in dec["added"]] == ["earthquake"] and len(dec["data"]["entries"]) == 2
    # 同じ処理の中の矛盾 (先に入れたものと id が違う) も conflict
    dec = A.decide([eq_cand, bd_cand], A.empty_aliases())
    assert len(dec["added"]) == 1 and len(dec["conflicts"]) == 1
    assert A.decide([dict(eq_cand, alias="地震。")], A.empty_aliases())["invalid"] == 1     # 名前 1 語でない表記は入れない
    print("test_decide_conflict_and_upgrade OK")


def test_confirm_reject_history():
    """人の確認: 確定・却下は history に basis = human で残す。往復一致を通らないものは確定しない。CLI も同じ"""
    dic = P.default_dictionary()
    cand = A.make_entry("moves", "地震", "じしん", "earthquake", "candidate", "llm_only", TODAY)
    new, code = A.confirm_alias(_data(cand), "moves", "地震", "2026-10-07", dic)
    e = new["entries"][0]
    assert code == "confirmed" and (e["status"], e["basis"]) == ("confirmed", "human")
    assert e["history"] == [{"at": TODAY, "from": None, "to": "candidate", "basis": "llm_only"},
                            {"at": "2026-10-07", "from": "candidate", "to": "confirmed", "basis": "human"}]
    assert A.confirm_alias(new, "moves", "地震", TODAY, dic)[1] == "already_confirmed"
    rej, code = A.reject_alias(new, "moves", "地震", "2026-10-08")
    assert code == "rejected" and rej["entries"][0]["history"][-1] == {"at": "2026-10-08", "from": "confirmed", "to": "rejected",
                                                                       "basis": "human"}
    assert A.reject_alias(rej, "moves", "地震", TODAY)[1] == "not_found"
    assert A.confirm_alias(_data(cand), "moves", "冷パン", TODAY, dic)[1] == "not_found"
    broken = A.make_entry("moves", "地震", "なぞのわざ", "earthquake", "candidate", "llm_only", TODAY)
    assert A.confirm_alias(_data(broken), "moves", "地震", TODAY, dic)[1] == "roundtrip_failed"
    # 却下済みを確定し直す: 有効な別の対応があれば conflict、却下済みが複数なら --id で選ぶ
    r1 = dict(cand, status="rejected", basis="human")
    r2 = A.make_entry("moves", "地震", "じならし", "bulldoze", "rejected", "human", TODAY)
    assert A.confirm_alias(_data(r1, r2), "moves", "地震", TODAY, dic)[1] == "ambiguous"
    assert A.confirm_alias(_data(r1, r2), "moves", "地震", TODAY, dic, ident="earthquake")[1] == "confirmed"
    bd = A.make_entry("moves", "地震", "じならし", "bulldoze", "candidate", "llm_only", TODAY)
    assert A.confirm_alias(_data(r1, bd), "moves", "地震", TODAY, dic, ident="earthquake")[1] == "conflict"
    # CLI (別名辞書のファイルを指定)
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "aliases.json"
        A.save_aliases(_data(cand, A.make_entry("moves", "冷パン", "れいとうパンチ", "icepunch", "candidate", "llm_only", TODAY)), path)
        assert A.main(["--list", "--status", "candidate", "--path", str(path)]) == 0
        assert A.main(["--confirm", "moves", "地震", "--path", str(path)]) == 0
        assert A.main(["--reject", "moves", "冷パン", "--path", str(path)]) == 0
        assert A.main(["--confirm", "moves", "無い技", "--path", str(path)]) == 1
        saved = A.load_aliases(path)
        assert [(e["alias"], e["status"], e["history"][-1]["basis"]) for e in saved["entries"]] == [("地震", "confirmed", "human"),
                                                                                                   ("冷パン", "rejected", "human")]
        assert P.ArticleDictionary(_raw(), saved).lookup("moves", "地震") == "earthquake"
        # 形の崩れた辞書は保存しない・読まない (同じ表記に有効な対応が 2 つ等)
        try:
            A.save_aliases(_data(cand, bd), path)
            raise AssertionError("有効な対応の重複を保存した")
        except ValueError:
            pass
        path.write_text(json.dumps({"schema": "other", "entries": []}), encoding="utf-8")
        try:
            A.load_aliases(path)
            raise AssertionError("schema の違う辞書を読んだ")
        except ValueError:
            pass
    print("test_confirm_reject_history OK")


def test_dictionary_uses_confirmed_only():
    """ArticleDictionary は厳密一致の表に無い表記を confirmed の別名だけで引く (candidate / rejected は使わない)"""
    raw = _raw()
    aliases = _data(A.make_entry("moves", "地震", "じしん", "earthquake", "confirmed", "human", TODAY),
                    A.make_entry("moves", "冷パン", "れいとうパンチ", "icepunch", "candidate", "llm_only", TODAY),
                    A.make_entry("moves", "瓦割り", "かわらわり", "brickbreak", "rejected", "human", TODAY),
                    A.make_entry("species", "バンギ", "バンギラス", "tyranitar", "confirmed", "human", TODAY))
    dic = P.ArticleDictionary(raw, aliases)
    assert dic.lookup("moves", "地震") == "earthquake" and dic.lookup_exact("moves", "地震") is None
    assert dic.lookup("moves", "冷パン") is None and dic.lookup("moves", "瓦割り") is None
    assert dic.lookup("species", "バンギ") == {"id": "tyranitar", "num": 248} and dic.species_id("バンギ") == "tyranitar"
    assert dic.lookup("moves", "じしん") == "earthquake"                                   # 厳密一致の表が先
    assert dic.alias_count == 2 and "aliases:2:" in dic.version
    base = P.ArticleDictionary(raw)
    assert base.alias_count == 0 and base.version.endswith("/aliases:0") and dic.with_aliases(None).version == base.version
    assert P.default_dictionary().alias_count == 0                                         # 同梱の別名辞書は空から始める
    # 解析: confirmed の別名で説明文の「地震」が技 id になり、未解決の名前から消える
    parsed = P.parse_article(SYNTHETIC, dic)
    assert parsed["unresolved_names"] == [] and parsed["dictionary_version"] == dic.version
    assert ("m3", "garchomp", "earthquake") in {(c["subject"], c["object"], c["move"]) for c in parsed["claims"] if c["kind"] == "survives"}
    # 同じ表記に別の id の confirmed (手で編集した等) はその表記を使わない
    clash = {"schema": A.SCHEMA, "entries": [A.make_entry("moves", "地震", "じしん", "earthquake", "confirmed", "human", TODAY),
                                             A.make_entry("moves", "地震", "じならし", "bulldoze", "confirmed", "human", TODAY)]}
    assert A.confirmed_map(clash) == {} and P.ArticleDictionary(raw, clash).lookup("moves", "地震") is None
    print("test_dictionary_uses_confirmed_only OK")


def main() -> None:
    test_known_transform()
    test_site_id_verified()
    test_llm_only_candidates()
    test_llm_limit_and_dedupe()
    test_llm_resolver_with_mock_provider()
    test_decide_conflict_and_upgrade()
    test_confirm_reject_history()
    test_dictionary_uses_confirmed_only()
    print("ALL OK")


if __name__ == "__main__":
    main()
