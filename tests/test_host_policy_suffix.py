"""ホストの方針の接尾辞照合 (match = suffix)・allowed_from = manifest・方針の版の記録・方針の集計の表のテスト
(tools/team_build/article_bank.host_policy_key / fetch_block_reason / host_allowed / policy_stamp、
tools/team_build/articles_fetch.policy_url_table / run。2026-10-30 開発者承認、docs/ARTICLE_BANK_DESIGN_1006.md §0 の 2・§3.12)。
合成の policy と manifest だけを使う。ネットワークに出ない (取得関数を注入)。

    python -m tests.test_host_policy_suffix
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from tests.test_adapter_gamewith import GW, HTML
from tools.team_build import article_bank as B
from tools.team_build import articles_fetch as F
from tools.team_build.adapters import host_adapters

GW_URL = f"{GW}/555537"
HB1 = "https://alice.hatenablog.com/entry/2026/10/01/120000"
HB2 = "https://bob.hatenablog.com/entry/2026/10/02/120000"
NOTE1 = "https://note.com/carol/n/n0123456789ab"
NOTE_UNSELECTED = "https://note.com/dave/n/nffffffffffff"
SOL = "https://pokesol.app/articles/1"
SEESAA = "https://galliumhf.seesaa.net/article/1.html"
AMEBA = "https://ameblo.jp/someone/entry-1.html"
OTHER = "https://evtetsuo.com/2026/10/01/"

_ALLOW = {"fetch": "allow", "store": "allow", "send_llm": "unknown", "allowed_from": "manifest", "min_interval_s": 10}
POLICY = {
    "_schema": "host_policy/2",
    "_note": "注記",
    "gamewith.jp": {"fetch": "allow", "store": "allow", "send_llm": "unknown", "allowed_urls": [GW_URL],
                    "purposes": ["weakness", "selection", "parser_eval"], "decided_at": "2026-10-06"},
    "note.com": dict(_ALLOW, send_llm="deny", decided_at="2026-10-30"),
    "hatenablog.com": dict(_ALLOW, match="suffix", decided_at="2026-10-30"),
    "pokesol.app": {"fetch": "deny", "store": "deny", "send_llm": "deny", "decided_at": "2026-10-30"},
    "seesaa.net": {"match": "suffix", "fetch": "unknown", "store": "unknown", "send_llm": "unknown"},
    "ameblo.jp": {"fetch": "unknown", "store": "unknown", "send_llm": "unknown"},
    # 最長一致と完全一致の優先を見るための合成の項目
    "example.org": {"match": "suffix", "fetch": "deny", "decided_at": "2026-01-01"},
    "blog.example.org": {"match": "suffix", "fetch": "allow", "decided_at": "2026-02-02"},
    "exact.blog.example.org": {"fetch": "unknown", "decided_at": "2026-03-03"},
    "plain.example": {"fetch": "allow"},                          # match が無い (完全一致だけ)
}
MANIFEST = {"entries": [
    {"url": HB1 + "/", "host": "alice.hatenablog.com", "selected": True},          # 末尾の / は正規化で落ちる
    {"url": HB2, "host": "bob.hatenablog.com", "selected": False},
    {"url": NOTE1 + "?app_launch=false", "host": "note.com", "selected": True},    # 追跡クエリも落ちる
    {"url": NOTE_UNSELECTED, "host": "note.com"},                                   # selected が無い
    {"url": SOL, "host": "pokesol.app", "selected": True},
    {"url": SEESAA, "host": "galliumhf.seesaa.net", "selected": True},
    {"url": AMEBA, "host": "ameblo.jp", "selected": True},
    {"url": OTHER, "host": "evtetsuo.com", "selected": True},
]}


def test_suffix_matching():
    """完全一致を優先し、無ければ match = suffix の最長一致。無関係なホストは None"""
    k = B.host_policy_key
    assert k(POLICY, "alice.hatenablog.com") == "hatenablog.com"
    assert k(POLICY, "a.b.hatenablog.com") == "hatenablog.com"                       # 多段のサブドメイン
    assert k(POLICY, "hatenablog.com") == "hatenablog.com" and k(POLICY, "WWW.Hatenablog.com:443") == "hatenablog.com"
    assert k(POLICY, "evilhatenablog.com") is None                                  # "." の境界でだけ一致する
    assert k(POLICY, "hatenablog.com.evil.example") is None
    assert k(POLICY, "x.example.org") == "example.org"
    assert k(POLICY, "x.blog.example.org") == "blog.example.org"                    # 最長一致 (example.org より長い鍵)
    assert k(POLICY, "blog.example.org") == "blog.example.org"
    assert k(POLICY, "exact.blog.example.org") == "exact.blog.example.org"          # 完全一致 (match なし) を接尾辞より優先
    assert k(POLICY, "y.exact.blog.example.org") == "blog.example.org"              # match の無い項目は接尾辞で照合しない
    assert k(POLICY, "sub.plain.example") is None and k(POLICY, "plain.example") == "plain.example"
    assert k(POLICY, "sub.note.com") is None                                        # note.com は suffix でない
    assert k(POLICY, "unrelated.example") is None and k(POLICY, "") is None and k(POLICY, None) is None
    assert k(POLICY, "_note") is None and B.host_policy_entry(POLICY, "_note") == {}
    assert k(None, "alice.hatenablog.com") is None
    assert B.host_policy_entry(POLICY, "x.blog.example.org")["decided_at"] == "2026-02-02"
    assert B.host_policy_entry(POLICY, "unrelated.example") == {}
    # 項目の順に依らない (長い鍵が先でも後でも同じ)
    rev = dict(reversed(list(POLICY.items())))
    assert all(k(rev, h) == k(POLICY, h) for h in ("x.blog.example.org", "x.example.org", "exact.blog.example.org", "alice.hatenablog.com"))
    print("test_suffix_matching OK")


def test_allowed_from_manifest():
    """allowed_from = manifest: manifest の selected な記事 URL (正規化して比較) だけ True。allowed_urls があれば従来どおり"""
    r = B.fetch_block_reason
    assert r(POLICY, "alice.hatenablog.com", HB1, MANIFEST) is None
    assert r(POLICY, "alice.hatenablog.com", HB1 + "/?utm_source=x#top", MANIFEST) is None
    assert r(POLICY, "bob.hatenablog.com", HB2, MANIFEST) == "not_in_manifest"                     # selected でない
    assert r(POLICY, "alice.hatenablog.com", HB1 + "-other", MANIFEST) == "not_in_manifest"         # manifest に無い
    assert r(POLICY, "alice.hatenablog.com", None, MANIFEST) == "no_url"
    assert r(POLICY, "note.com", NOTE1, MANIFEST) is None
    assert r(POLICY, "note.com", NOTE_UNSELECTED, MANIFEST) == "not_in_manifest"
    assert r(POLICY, "note.com", NOTE1, {"entries": []}) == "not_in_manifest"
    assert r(POLICY, "pokesol.app", SOL, MANIFEST) == "deny"                                        # manifest にあっても deny
    assert r(POLICY, "galliumhf.seesaa.net", SEESAA, MANIFEST) == "unknown"
    assert r(POLICY, "ameblo.jp", AMEBA, MANIFEST) == "unknown"
    assert r(POLICY, "evtetsuo.com", OTHER, MANIFEST) == "no_policy"
    assert r(POLICY, "gamewith.jp", GW_URL, MANIFEST) is None                                       # allowed_urls (従来どおり)
    assert r(POLICY, "gamewith.jp", f"{GW}/555538", MANIFEST) == "not_in_allowed_urls"
    assert r(POLICY, "plain.example", "https://plain.example/x", MANIFEST) is None                  # どちらも無ければ従来どおり通す
    odd = {"odd.example": {"fetch": "allow", "allowed_from": "csv"}}
    assert r(odd, "odd.example", "https://odd.example/x", MANIFEST) == "unknown_allowed_from"      # 知らない値は通さない
    assert set(filter(None, [r(POLICY, h, u, MANIFEST) for h, u in [("bob.hatenablog.com", HB2), ("pokesol.app", SOL)]])) <= set(B.FETCH_BLOCK_REASONS)
    # host_allowed は fetch_block_reason と同じ判定。entry 列・正規化済みの集合も manifest として渡せる
    assert B.host_allowed(POLICY, "alice.hatenablog.com", "fetch", url=HB1, manifest=MANIFEST)
    assert not B.host_allowed(POLICY, "bob.hatenablog.com", "fetch", url=HB2, manifest=MANIFEST)
    assert B.host_allowed(POLICY, "alice.hatenablog.com", "fetch", url=HB1, manifest=MANIFEST["entries"])
    assert B.host_allowed(POLICY, "alice.hatenablog.com", "fetch", url=HB1, manifest=B.manifest_selected_urls(MANIFEST))
    # fetch 以外は従来どおり (接尾辞で照合した項目の値・用途の制限)
    assert B.host_allowed(POLICY, "alice.hatenablog.com", "store") and not B.host_allowed(POLICY, "alice.hatenablog.com", "send_llm")
    assert not B.host_allowed(POLICY, "note.com", "send_llm")
    assert not B.host_allowed(POLICY, "gamewith.jp", "pool") and B.host_allowed(POLICY, "gamewith.jp", "weakness")
    # plan_urls も manifest を見る
    plan = F.plan_urls([HB1, HB2, NOTE1, SOL], POLICY, fetched=set(), manifest=MANIFEST)
    assert [p["action"] for p in plan] == ["fetch", "skip", "fetch", "skip"]
    print("test_allowed_from_manifest OK")


def test_default_manifest_cache():
    """manifest を渡さなければ既定の manifest.json を 1 回だけ読んでキャッシュする (無ければ空 = manifest 由来は全部不可)"""
    saved_path = B.DEFAULT_MANIFEST_PATH
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "manifest.json"
        try:
            B.DEFAULT_MANIFEST_PATH = path
            assert not B.host_allowed(POLICY, "alice.hatenablog.com", "fetch", url=HB1)              # ファイルが無い → 空
            B._MANIFEST_URLS_CACHE.pop(str(path), None)
            path.write_text(json.dumps(MANIFEST), encoding="utf-8")
            assert B.host_allowed(POLICY, "alice.hatenablog.com", "fetch", url=HB1)
            path.write_text(json.dumps({"entries": []}), encoding="utf-8")                          # 書き換えても読み直さない
            assert B.host_allowed(POLICY, "alice.hatenablog.com", "fetch", url=HB1)
        finally:
            B.DEFAULT_MANIFEST_PATH = saved_path
            B._MANIFEST_URLS_CACHE.pop(str(path), None)
    print("test_default_manifest_cache OK")


def test_policy_stamp():
    """方針の版: 内容の sha16 (項目の順・整形に依らない、内容が変われば変わる) と照合した項目・decided_at"""
    v = B.policy_version(POLICY)
    assert len(v) == 16 and v == B.policy_version(dict(reversed(list(POLICY.items()))))
    changed = json.loads(json.dumps(POLICY))
    changed["hatenablog.com"]["fetch"] = "deny"
    assert B.policy_version(changed) != v
    assert B.policy_stamp(POLICY, "alice.hatenablog.com") == {"policy_sha16": v, "policy_key": "hatenablog.com", "policy_decided_at": "2026-10-30"}
    assert B.policy_stamp(POLICY, "galliumhf.seesaa.net") == {"policy_sha16": v, "policy_key": "seesaa.net", "policy_decided_at": None}
    assert B.policy_stamp(POLICY, "evtetsuo.com", "abc") == {"policy_sha16": "abc", "policy_key": None, "policy_decided_at": None}
    print("test_policy_stamp OK")


class FakeFetcher:
    def __init__(self, html):
        self.calls = []
        self.html = html

    def __call__(self, url):
        self.calls.append(url)
        return self.html.encode("utf-8"), "text/html; charset=utf-8"


def test_run_records_policy_version():
    """取得履歴 (access.jsonl) と候補 (candidates/) に方針の版が入る (欄の追加のみ)。dry-run は方針の集計の表を出す"""
    v = B.policy_version(POLICY)
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        kw = dict(state_path=root / "state.jsonl", bank_dir=root / "bank", aliases_path=root / "aliases.json",
                  site_ids_path=root / "site_ids.json", sleeper=lambda _s: None, today="2026-10-30", adapters=host_adapters(),
                  access_path=root / "access.jsonl", candidates_dir=root / "candidates", now="2026-10-30T20:00:00", manifest=MANIFEST)
        fetcher = FakeFetcher(HTML)
        dry = F.run([GW_URL, HB1, HB2, SOL], POLICY, fetcher=fetcher, dry_run=True, **kw)
        assert fetcher.calls == [GW_URL, HB1]                                                    # manifest 外 (HB2) と deny (SOL) は取得しない
        access = [json.loads(ln) for ln in (root / "access.jsonl").read_text(encoding="utf-8").splitlines()]
        assert [(a["host"], a["policy_key"], a["policy_decided_at"], a["policy_sha16"]) for a in access] == [
            ("gamewith.jp", "gamewith.jp", "2026-10-06", v), ("alice.hatenablog.com", "hatenablog.com", "2026-10-30", v)]
        assert set(access[0]) == set(F.ACCESS_ROW_KEYS)
        cands = [F.load_candidates(Path(p)) for p in dry["candidates_paths"]]
        assert [(c["policy_key"], c["policy_decided_at"], c["policy_sha16"]) for c in cands] == [
            ("gamewith.jp", "2026-10-06", v), ("hatenablog.com", "2026-10-30", v)]
        table = {r["group"]: r for r in dry["policy_table"]}
        assert table["hatenablog.com"]["n_allowed"] == 1 and table["hatenablog.com"]["reasons"] == {"not_in_manifest": 1}
        assert table["pokesol.app"]["reasons"] == {"deny": 1} and table["gamewith.jp"]["n_allowed"] == 1
        lines = F._summary_lines(dry)
        assert any(ln.startswith("方針の集計") for ln in lines)
        # 保存のとき (dry-run でない) は表を出さない
        saved = F.run([GW_URL], POLICY, fetcher=fetcher, refetch=True, **kw)
        assert "policy_table" not in saved and not any(ln.startswith("方針の集計") for ln in F._summary_lines(saved))
        access = [json.loads(ln) for ln in (root / "access.jsonl").read_text(encoding="utf-8").splitlines()]
        assert access[-1]["mode"] == "save" and access[-1]["policy_sha16"] == v
    print("test_run_records_policy_version OK")


def test_policy_url_table():
    """ホスト群 (照合した項目、無ければホスト名) ごとの取得できる / できない URL 数と理由。同じ URL は 1 回だけ数える"""
    urls = [HB1, HB1 + "/", HB2, "https://zed.hatenablog.com/entry/x", NOTE1, NOTE_UNSELECTED, SOL, SEESAA, AMEBA, OTHER, GW_URL, ""]
    table = F.policy_url_table(urls, POLICY, MANIFEST)
    got = {r["group"]: (r["matched"], r["n_urls"], r["n_allowed"], r["n_blocked"], r["reasons"]) for r in table}
    assert got == {
        "hatenablog.com": (True, 3, 1, 2, {"not_in_manifest": 2}),
        "note.com": (True, 2, 1, 1, {"not_in_manifest": 1}),
        "pokesol.app": (True, 1, 0, 1, {"deny": 1}),
        "seesaa.net": (True, 1, 0, 1, {"unknown": 1}),
        "ameblo.jp": (True, 1, 0, 1, {"unknown": 1}),
        "evtetsuo.com": (False, 1, 0, 1, {"no_policy": 1}),
        "gamewith.jp": (True, 1, 1, 0, {}),
    }, got
    assert [r["group"] for r in table] == sorted(got)
    lines = F.policy_table_lines(table)
    assert lines[0].startswith("方針の集計") and "evtetsuo.com (方針なし)" in "\n".join(lines)
    assert lines[-1].split()[1:] == ["10", "3", "7"]                                                 # 合計
    assert F.policy_table_lines([]) == []
    # 純粋: 入力の manifest を書き換えない
    before = json.dumps(MANIFEST, sort_keys=True)
    F.policy_url_table(urls, POLICY, MANIFEST)
    assert json.dumps(MANIFEST, sort_keys=True) == before
    print("test_policy_url_table OK")


def test_interval_per_policy_group():
    """待ち時間はホスト群 (照合した方針の項目の鍵) ごと: 同じ群の別サブドメインどうしでも min_interval_s を空け、別の群は待たない。
    方針の項目が無いホストはホスト名ごと (2026-10-30 判断)"""
    policy = POLICY
    sleeps: list = []
    fetcher = FakeFetcher("<html><body><p>x</p></body></html>")
    hb3 = "https://zed.hatenablog.com/entry/x"
    manifest = {"entries": [{"url": u, "selected": True} for u in (HB1, hb3, NOTE1)]}
    # 同じ群 (hatenablog.com) の別サブドメイン → 待つ (1 回、10 秒以下)。note.com は別の群なので待たない
    plan = F.plan_urls([HB1, NOTE1, hb3], policy, fetched=set(), manifest=manifest)
    assert [p["action"] for p in plan] == ["fetch"] * 3
    F.fetch_units(plan, policy, fetcher, sleeper=sleeps.append, today="2026-10-30", adapters=host_adapters())
    assert fetcher.calls == [HB1, NOTE1, hb3] and len(sleeps) == 1 and 0 < sleeps[0] <= 10
    # 別の群どうし (はてな → note) だけなら待たない
    sleeps.clear()
    plan = F.plan_urls([HB1, NOTE1], policy, fetched=set(), manifest=manifest)
    F.fetch_units(plan, policy, fetcher, sleeper=sleeps.append, today="2026-10-30", adapters=host_adapters())
    assert sleeps == []
    # 方針の項目が無いホストはホスト名ごと (plan では skip されるので、fetch_units に直接 fetch 行を渡して確かめる)
    sleeps.clear()
    rows = [{"url": u, "host": h, "url_hash": f"h{i}", "action": "fetch"}
            for i, (u, h) in enumerate([("https://a.nopolicy.example/1", "a.nopolicy.example"),
                                        ("https://b.nopolicy.example/1", "b.nopolicy.example"),
                                        ("https://a.nopolicy.example/2", "a.nopolicy.example")])]
    F.fetch_units(rows, policy, fetcher, sleeper=sleeps.append, today="2026-10-30", adapters=host_adapters())
    assert len(sleeps) == 1                                                                       # 3 本目 (a の 2 回目) の前だけ
    print("test_interval_per_policy_group OK")


def main() -> None:
    test_suffix_matching()
    test_allowed_from_manifest()
    test_default_manifest_cache()
    test_policy_stamp()
    test_policy_url_table()
    test_run_records_policy_version()
    test_interval_per_policy_group()
    print("ALL OK")


if __name__ == "__main__":
    main()
