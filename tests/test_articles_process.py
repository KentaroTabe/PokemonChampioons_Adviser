"""記事のバッチ処理の骨格 (tools/team_build/articles_process) のテスト (docs/ARTICLE_BANK_DESIGN_1006.md §3.9)。
HTTP はしない。LLM は偽の resolver (ネットワークに出ない)。素材は tests/test_article_parse.py の合成記事 SYNTHETIC。

    python -m tests.test_articles_process
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from tests.test_article_parse import SYNTHETIC
from tools.team_build import article_aliases as A
from tools.team_build import article_bank as B
from tools.team_build import article_parse as P
from tools.team_build import articles_process as R
from tools.team_build.article_units import make_unit

TODAY = "2026-10-06"
HOST = "example.invalid"
POLICY = {HOST: {"fetch": "allow", "send_llm": "allow"}, "fetch-only.example": {"fetch": "allow", "send_llm": "unknown"},
          "blocked.example": {"fetch": "deny", "send_llm": "allow"}}
META = {"regulation": "gen9championsbssregmc"}
# 技一覧の 1 語を「10万ボルト」にした合成記事 (既知の変換で確定できる表記)
SYNTHETIC_MAN = SYNTHETIC.replace("[10まんボルト]", "[10万ボルト]")
BODY_FRAGMENTS = ("雨を降らせて", "退く役", "早めに処理", "大体物理アタッカー", "変更前です", "苦手なので採用")


def _unit(marked, url_hash, host=HOST, kind="team"):
    return make_unit(kind, marked, META, {"host": host, "url_hash": url_hash, "synthetic": True, "publisher_kind": "personal_blog"})


class FakeResolver:
    """偽の LLM: 「地震」→ じしん / earthquake (往復一致は通る。LLM だけが根拠なので candidate になる)"""

    def __init__(self):
        self.payloads = []

    def __call__(self, payload):
        self.payloads.append(json.loads(json.dumps(payload)))
        return {"mappings": [{"category": n["category"], "text": n["text"], "canonical": "じしん", "id": "earthquake"}
                             for n in payload["names"] if n["text"] == "地震"]}


def _survives(rec):
    return {(c["subject"], c["object"], c["move"]) for c in rec["claims"] if c["kind"] == "survives"}


def test_batch_aliases_and_reparse():
    """未解決「地震」は偽の resolver で candidate になり再解析では解決されない。既知の変換で確定した「10万ボルト」は再解析で解決される"""
    assert SYNTHETIC_MAN != SYNTHETIC
    plain = P.parse_article(SYNTHETIC_MAN)                                                   # 別名なしでは m5 の技一覧が読めない
    assert next(m for m in plain["members"] if m["id"] == "m5")["moves"] == []
    assert {"category": "moves", "text": "10万ボルト", "host": HOST, "site_key": None} in plain["unresolved_names"]
    units = [_unit(SYNTHETIC, "page_a"), _unit(SYNTHETIC_MAN, "page_b")]
    fake = FakeResolver()
    aliases_in = A.empty_aliases()
    res = R.process_batch(units, None, aliases_in, A.SiteIdStore(), llm_resolver=fake, policy=POLICY, today=TODAY)
    assert aliases_in == A.empty_aliases()                                                  # 入力の別名辞書は変えない
    conf, cand = res["alias_entries"]["confirmed"], res["alias_entries"]["candidate"]
    assert [(e["alias"], e["id"], e["basis"]) for e in conf] == [("10万ボルト", "thunderbolt", "known_transform")]
    assert [(e["alias"], e["id"], e["basis"], e["status"]) for e in cand] == [("地震", "earthquake", "llm_only", "candidate")]
    assert fake.payloads == [{"names": [{"category": "moves", "text": "地震"}]}]             # 確定した名前は送らず、重複は 1 語にする
    rec_a, rec_b = res["records"]
    m5_b = next(m for m in rec_b["members"] if m["id"] == "m5")
    assert m5_b["moves"] == ["nastyplot", "thunderbolt", "makeitrain", "shadowball"] and rec_b["status"] == "ok"
    assert res["counts"]["reparsed"] == 1                                                    # 確定した別名に関係する unit だけ
    assert rec_a["status"] == "ok" and ("m3", "garchomp", None) in _survives(rec_a)          # candidate は辞書で使わない
    assert ("m3", "garchomp", None) in _survives(rec_b)
    assert [(u["url_hash"], u["unit"], u["text"]) for u in res["unresolved_names"]] == [("page_a", 0, "地震"), ("page_b", 0, "地震")]
    assert [e["alias"] for e in res["aliases"]["entries"]] == ["10万ボルト", "地震"]
    # 本文が結果・state 行に含まれない
    for rec in res["records"]:
        B.assert_no_prose(rec)
    B.assert_no_prose(res["state_rows"])
    text = json.dumps({k: v for k, v in res.items() if k != "site_store"}, ensure_ascii=False)
    assert not any(w in text for w in BODY_FRAGMENTS) and '"marked"' not in text
    assert [r["status"] for r in res["state_rows"]] == ["ok", "ok"] and all(set(r) == set(R.STATE_ROW_KEYS) for r in res["state_rows"])
    assert res["state_rows"][0]["parser_version"] and res["state_rows"][1]["n_unresolved_names"] == 1
    # 次の処理: 確認待ちの候補がある名前は送り直さない。確定済みの別名は最初の解析で解決する
    fake2 = FakeResolver()
    res2 = R.process_batch([_unit(SYNTHETIC_MAN, "page_c")], None, res["aliases"], res["site_store"], llm_resolver=fake2,
                           policy=POLICY, today=TODAY)
    assert fake2.payloads == [] and res2["counts"].get("reparsed", 0) == 0 and res2["records"][0]["status"] == "ok"
    assert res2["alias_entries"] == {"confirmed": [], "candidate": []}
    print("test_batch_aliases_and_reparse OK")


def test_batch_limits_defer():
    """上限を超えたページの unit は処理しない (deferred。本文を退避しない)。同じページの unit は分けない"""
    units = [_unit(SYNTHETIC, "page_a"), _unit(SYNTHETIC_MAN, "page_b")]
    res = R.process_batch(units, None, None, None, policy=POLICY, today=TODAY, limits={"max_articles": 1})
    assert [r["status"] for r in res["state_rows"]] == ["ok", "deferred"] and len(res["records"]) == 1
    assert res["state_rows"][1]["parser_version"] is None and res["counts"]["deferred"] == 1
    res = R.process_batch(units, None, None, None, policy=POLICY, today=TODAY, limits={"max_body_chars": len(SYNTHETIC) + 1})
    assert [r["status"] for r in res["state_rows"]] == ["ok", "deferred"] and res["counts"]["body_chars"] == len(SYNTHETIC)
    # 同じページ (url_hash) の 2 unit は一緒に受け入れるか、一緒に後回しにする
    same_page = [_unit(SYNTHETIC, "page_x"), _unit(SYNTHETIC, "page_x", kind="single_set"), _unit(SYNTHETIC_MAN, "page_y")]
    res = R.process_batch(same_page, None, None, None, policy=POLICY, today=TODAY, limits={"max_body_chars": len(SYNTHETIC) * 2})
    assert [(r["unit"], r["kind"], r["status"]) for r in res["state_rows"]] == [(0, "team", "ok"), (1, "single_set", "warnings"),
                                                                              (0, "team", "deferred")]
    res = R.process_batch(same_page, None, None, None, policy=POLICY, today=TODAY, limits={"max_body_chars": len(SYNTHETIC) + 1})
    assert [r["status"] for r in res["state_rows"]] == ["deferred", "deferred", "ok"]
    assert R.batch_limits() == {"max_articles": R.BUILD_ARTICLE_BATCH_MAX_ARTICLES, "max_body_chars": R.BUILD_ARTICLE_BATCH_MAX_BODY_CHARS}
    try:
        R.batch_limits({"max_usd": 1})
        raise AssertionError("知らない上限の名前を通した")
    except ValueError:
        pass
    print("test_batch_limits_defer OK")


def test_batch_host_gates():
    """fetch が許可されていないホストの unit は解析しない。send_llm が許可されていないホストの名前は LLM に送らない"""
    fake = FakeResolver()
    units = [_unit(SYNTHETIC, "page_blocked", host="blocked.example"), _unit(SYNTHETIC, "page_unlisted", host="unlisted.example"),
             _unit(SYNTHETIC, "page_fetch_only", host="fetch-only.example")]
    res = R.process_batch(units, None, None, None, llm_resolver=fake, policy=POLICY, today=TODAY)
    assert [r["status"] for r in res["state_rows"]] == ["host_not_allowed", "host_not_allowed", "ok"]
    assert len(res["records"]) == 1 and res["records"][0]["source"]["host"] == "fetch-only.example"
    assert fake.payloads == [] and res["counts"]["llm_names_blocked_by_host"] == 1 and res["alias_entries"]["candidate"] == []
    assert [(u["url_hash"], u["unit"]) for u in res["unresolved_names"]] == [("page_fetch_only", 0)]
    assert res["counts"]["host_not_allowed"] == 2
    # policy を渡さなければどこも許可しない
    none = R.process_batch(units[2:], None, None, None, llm_resolver=fake, today=TODAY)
    assert [r["status"] for r in none["state_rows"]] == ["host_not_allowed"] and none["records"] == []
    print("test_batch_host_gates OK")


def test_batch_input_gates_and_state_file():
    """入力の source / meta に本文があれば処理の前に止める (LLM も呼ばない)。state 行は本文なしで追記する"""
    fake = FakeResolver()
    leak = _unit(SYNTHETIC, "page_a")
    leak["meta"]["title"] = "雨パの構築"
    for bad in (leak, dict(_unit(SYNTHETIC, "page_b"), kind="pair")):
        try:
            R.process_batch([_unit(SYNTHETIC, "page_ok"), bad], None, None, None, llm_resolver=fake, policy=POLICY, today=TODAY)
            raise AssertionError("入力の門が効いていない")
        except ValueError as e:
            assert "雨" not in str(e)
    bad_src = _unit(SYNTHETIC, "page_c")
    bad_src["source"]["usage_evidence"] = "rank_1"
    try:
        R.process_batch([bad_src], None, None, None, policy=POLICY, today=TODAY)
        raise AssertionError("出典の 2 軸の検査が効いていない")
    except ValueError:
        pass
    assert fake.payloads == []
    res = R.process_batch([_unit(SYNTHETIC, "page_a")], None, None, None, policy=POLICY, today=TODAY)
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "articles" / "state.jsonl"
        R.append_state(res["state_rows"], path)
        R.append_state(res["state_rows"], path)
        lines = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()]
        assert len(lines) == 2 and lines[0] == res["state_rows"][0]
        try:
            R.append_state([dict(res["state_rows"][0], note="x")], path)
            raise AssertionError("知らない項目を書いた")
        except ValueError:
            pass
        try:
            R.append_state([dict(res["state_rows"][0], status="雨")], path)
            raise AssertionError("本文の混入を書いた")
        except ValueError:
            pass
        assert len(path.read_text(encoding="utf-8").splitlines()) == 2
    print("test_batch_input_gates_and_state_file OK")


def main() -> None:
    test_batch_aliases_and_reparse()
    test_batch_limits_defer()
    test_batch_host_gates()
    test_batch_input_gates_and_state_file()
    print("ALL OK")


if __name__ == "__main__":
    main()
