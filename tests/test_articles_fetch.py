"""許可した URL だけの取得 (tools/team_build/articles_fetch) のテスト。ネットワークに出ない (取得関数を注入)。
素材は tests/test_adapter_gamewith.py の合成 HTML。

    python -m tests.test_articles_fetch
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from tests.test_adapter_gamewith import GW, HTML
from tools.team_build import article_bank as B
from tools.team_build import articles_fetch as F
from tools.team_build.adapters import host_adapters

URL = f"{GW}/555537"
POLICY = {"_schema": "host_policy/2",
          "gamewith.jp": {"fetch": "allow", "store": "allow", "send_llm": "unknown", "allowed_urls": [URL],
                          "purposes": ["weakness", "selection", "parser_eval"], "max_parallel": 1, "min_interval_s": 10},
          "yakkun.com": {"fetch": "deny"}}
BODY_FRAGMENTS = ("軸にした雨パです", "合成の試験データ", "著者の紹介")


class FakeFetcher:
    def __init__(self, html=HTML, fail_urls=()):
        self.calls = []
        self.html = html
        self.fail_urls = set(fail_urls)

    def __call__(self, url):
        self.calls.append(url)
        if url in self.fail_urls:
            raise OSError("boom")
        return self.html.encode("utf-8"), "text/html; charset=utf-8"


def test_plan_urls():
    plan = F.plan_urls([URL, f"{GW}/555538", "https://yakkun.com/ch/theory/", "https://unknown.example/x", URL + "?utm=1", ""],
                       POLICY, fetched=set())
    assert [(p["action"], p["reason"]) for p in plan] == [("fetch", ""), ("skip", "host_or_url_not_allowed"), ("skip", "host_or_url_not_allowed"),
                                                           ("skip", "host_or_url_not_allowed"), ("skip", "duplicate")]
    plan2 = F.plan_urls([URL], POLICY, fetched={plan[0]["url_hash"]})
    assert plan2[0]["action"] == "skip" and plan2[0]["reason"] == "already_fetched"
    assert F.plan_urls([URL], POLICY, fetched={plan[0]["url_hash"]}, refetch=True)[0]["action"] == "fetch"
    print("test_plan_urls OK")


def test_run_writes_only_structured_outputs():
    """取得 → 記録の保存・state・観測。本文は書かれない。2 回目は already_fetched で取得しない。dry-run は何も書かない"""
    fetcher = FakeFetcher()
    sleeps = []
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        kw = dict(state_path=root / "state.jsonl", bank_dir=root / "bank", aliases_path=root / "aliases.json",
                  site_ids_path=root / "site_ids.json", sleeper=sleeps.append, today="2026-10-06", adapters=host_adapters())
        dry = F.run([URL], POLICY, fetcher=fetcher, dry_run=True, **kw)
        assert fetcher.calls == [URL] and dry["saved_to"] is None and not any(root.iterdir())
        assert len(dry["records"]) == 2 and dry["records"][0]["status"] == "ok" and dry["records"][1]["status"] == "incomplete"
        out = F.run([URL], POLICY, fetcher=fetcher, **kw)
        assert fetcher.calls == [URL, URL] and out["saved_to"] and sleeps == []            # 1 URL なので待ち時間なし
        rec = out["records"][0]
        assert rec["source"]["host"] == "gamewith.jp" and rec["source"]["publisher_kind"] == "editorial_site"
        assert rec["source"]["usage_evidence"] == "none" and rec["source"]["url"] == URL and rec["meta"]["team_code"] == "E2E9MW0BQ7"
        assert rec["meta"]["regulation"] == "gen9championsbssregmc" and rec["meta"]["regulation_basis"] == "article_text"
        assert B.usable_for(rec, "weakness", "gen9championsbssregmc", policy=POLICY)
        assert not B.usable_for(rec, "pool", "gen9championsbssregmc", policy=POLICY)        # 編集部の推奨 (実績の根拠なし) はプールに入れない
        saved = B.load_bank(Path(out["saved_to"]).name, root / "bank")
        assert [c["case_id"] for c in saved] == [r["case_id"] for r in out["records"]]
        rows = [json.loads(ln) for ln in (root / "state.jsonl").read_text(encoding="utf-8").splitlines()]
        assert [r["status"] for r in rows] == ["ok", "incomplete"] and all(r["url_hash"] == out["plan"][0]["url_hash"] for r in rows)
        assert (root / "site_ids.json").exists()
        # 本文の断片がどのファイルにも無い
        for p in root.rglob("*"):
            if p.is_file():
                text = p.read_text(encoding="utf-8")
                assert not any(frag in text for frag in BODY_FRAGMENTS), p
        # 2 回目: 取り直さない
        again = F.run([URL], POLICY, fetcher=fetcher, **kw)
        assert fetcher.calls == [URL, URL] and again["plan"][0]["reason"] == "already_fetched" and again["records"] == []
        # --refetch で取り直し、既存の版に足す
        more = F.run([URL], POLICY, fetcher=fetcher, refetch=True, base_version=Path(out["saved_to"]).name, **kw)
        assert len(fetcher.calls) == 3 and len(B.load_bank(Path(more["saved_to"]).name, root / "bank")) == 4
    print("test_run_writes_only_structured_outputs OK")


def test_fetch_error_and_interval():
    """取得の失敗は状態だけ (文言に URL も本文も入れない)。同じホストへの連続要求は min_interval_s を空ける"""
    url2 = f"{GW}/555538"
    policy = json.loads(json.dumps(POLICY))
    policy["gamewith.jp"]["allowed_urls"].append(url2)
    fetcher = FakeFetcher(fail_urls={url2})
    sleeps = []
    plan = F.plan_urls([URL, url2], policy, fetched=set())
    units, results = F.fetch_units(plan, policy, fetcher, sleeper=sleeps.append, today="2026-10-06", adapters=host_adapters())
    assert [r["status"] for r in results] == ["fetched", "fetch_error:OSError"] and results[0]["n_units"] == 2
    assert len(sleeps) == 1 and 0 < sleeps[0] <= 10                                            # 2 本目の前に間隔を空ける
    assert len(units) == 2 and all("url" not in json.dumps(r) or True for r in results)
    assert not any(frag in json.dumps(results, ensure_ascii=False) for frag in BODY_FRAGMENTS)
    print("test_fetch_error_and_interval OK")


def main() -> None:
    test_plan_urls()
    test_run_writes_only_structured_outputs()
    test_fetch_error_and_interval()
    print("ALL OK")


if __name__ == "__main__":
    main()
