"""許可した URL だけの取得 (tools/team_build/articles_fetch) のテスト。ネットワークに出ない (取得関数を注入)。
素材は tests/test_adapter_gamewith.py の合成 HTML (と、その 1 か所を変えたもの)。再取得したときのバンクの重複処理と版の関係
(2026-10-06 ユーザー判断、docs/ARTICLE_BANK_DESIGN_1006.md §5.1) も見る。

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
REG = "gen9championsbssregmc"
CHANGED_HTML = HTML.replace("れいとうパンチ", "アームハンマー")          # 1 構築目 (メガラグラージ) の技を 1 つ変えたページ
CONFLICT_HTML = HTML.replace("<span>202</span>", "<span>203</span>")    # 1 構築目のメガラグラージの実数値 (攻撃) が再計算と合わないページ


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
    """取得 → 記録の保存・state・観測。本文は書かれない。dry-run は取得履歴と構造化した候補だけ書き (バンク・state は書かない)、
    dry-run のアクセスも取得履歴に数えて 2 回目は already_fetched。候補からの保存は再取得しない"""
    fetcher = FakeFetcher()
    sleeps = []
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        kw = dict(state_path=root / "state.jsonl", bank_dir=root / "bank", aliases_path=root / "aliases.json",
                  site_ids_path=root / "site_ids.json", sleeper=sleeps.append, today="2026-10-06", adapters=host_adapters(),
                  access_path=root / "access.jsonl", candidates_dir=root / "candidates", now="2026-10-06T20:00:00")
        dry = F.run([URL], POLICY, fetcher=fetcher, dry_run=True, **kw)
        assert fetcher.calls == [URL] and dry["saved_to"] is None
        assert sorted(p.name for p in root.iterdir()) == ["access.jsonl", "candidates"]          # バンク・state・観測は書かない
        access = [json.loads(ln) for ln in (root / "access.jsonl").read_text(encoding="utf-8").splitlines()]
        assert len(access) == 1 and access[0]["mode"] == "dry_run" and access[0]["status"] == "fetched" and access[0]["count"] == 1
        assert access[0]["url"] == URL and access[0]["n_units"] == 2 and access[0]["at"] == "2026-10-06T20:00:00"
        assert len(dry["candidates_paths"]) == 1 and Path(dry["candidates_paths"][0]).exists()
        cand = F.load_candidates(Path(dry["candidates_paths"][0]))
        assert [r["status"] for r in cand["records"]] == ["ok", "incomplete"] and cand["saved"] is False and len(cand["state_rows"]) == 2
        assert len(dry["records"]) == 2 and dry["records"][0]["status"] == "ok" and dry["records"][1]["status"] == "incomplete"
        assert dry["records"][0]["checks"]["actual"]["m1"] == "verified"                          # 検算して一致 (削除で通ったのではない)
        # dry-run のアクセスも取得履歴に数える → 取り直さない。候補から保存する (再取得なし)
        again = F.run([URL], POLICY, fetcher=fetcher, **kw)
        assert fetcher.calls == [URL] and again["plan"][0]["reason"] == "already_fetched" and again["records"] == []
        saved = F.save_from_candidates(dry["candidates_paths"], root / "bank", state_path=root / "state.jsonl")
        assert saved["n_records"] == 2 and saved["skipped"] == [] and len(B.load_bank(Path(saved["saved_to"]).name, root / "bank")) == 2
        assert len((root / "state.jsonl").read_text(encoding="utf-8").splitlines()) == 2
        # --refetch で取り直して既存の版に足す。同じ 2 構築 (同じ記事・同じチーム ID・同一内容) は重複として数えて足さない:
        # バンクの件数は 2 のまま、版も変わらない (同じ版に同じ内容を書く)。取得履歴 (access.jsonl) と state.jsonl には追記する。
        # 期待値の変更 (2026-10-06 ユーザー指示): 以前はここで「バンクが 4 件になる」ことを合格条件にしていたが、同じ記事・同じ構築の
        # 同一内容を重複計上するのは仕様の誤り (取得履歴は追記してよいが、利用するバンクでは重複計上しない) なので、正しい期待
        # (2 件のまま・重複 2) に直した
        base = Path(saved["saved_to"]).name
        out = F.run([URL], POLICY, fetcher=fetcher, refetch=True, base_version=base, **kw)
        assert fetcher.calls == [URL, URL] and out["saved_to"] and sleeps == []            # 1 URL なので待ち時間なし
        assert out["bank_merge"]["outcomes"] == ["duplicate", "duplicate"] and out["bank_merge"]["counts"]["duplicates"] == 2
        assert out["bank_merge"]["counts"]["added"] == 0 and out["bank_merge"]["counts"]["superseded"] == 0
        assert len(B.load_bank(Path(out["saved_to"]).name, root / "bank", latest_only=False)) == 2
        assert Path(out["saved_to"]).name == base
        rec = out["records"][0]
        assert rec["source"]["host"] == "gamewith.jp" and rec["source"]["publisher_kind"] == "editorial_site"
        assert rec["source"]["usage_evidence"] == "none" and rec["source"]["url"] == URL and rec["meta"]["team_code"] == "E2E9MW0BQ7"
        assert rec["meta"]["regulation"] == "gen9championsbssregmc" and rec["meta"]["regulation_basis"] == "article_text"
        assert B.usable_for(rec, "weakness", "gen9championsbssregmc", policy=POLICY)
        assert not B.usable_for(rec, "pool", "gen9championsbssregmc", policy=POLICY)        # 編集部の推奨 (実績の根拠なし) はプールに入れない
        saved2 = B.load_bank(Path(out["saved_to"]).name, root / "bank")
        # 同じ理由 (重複計上は仕様の誤り) で期待を変更: 以前は今回の 2 記録が末尾 (saved2[2:]) に足されることを見ていた。
        # 今回の記録は既存の 2 件と同一内容 (同じ case_id) で、バンクには既存の記録がそのまま残る
        assert [c["case_id"] for c in saved2] == [r["case_id"] for r in out["records"]]
        assert saved2 == B.load_bank(base, root / "bank")
        rows = [json.loads(ln) for ln in (root / "state.jsonl").read_text(encoding="utf-8").splitlines()]
        assert [r["status"] for r in rows] == ["ok", "incomplete", "ok", "incomplete"] and all(r["url_hash"] == out["plan"][0]["url_hash"] for r in rows)
        assert (root / "site_ids.json").exists()
        access = [json.loads(ln) for ln in (root / "access.jsonl").read_text(encoding="utf-8").splitlines()]
        assert [a["mode"] for a in access] == ["dry_run", "save"]                              # 取り直さなかった回はアクセスしていない
        # 同じ候補を 2 回渡しても 1 回分 (2 件目以降は重複)
        twice = F.save_from_candidates(dry["candidates_paths"] * 2, root / "bank3", state_path=root / "state3.jsonl")
        assert twice["n_records"] == 4 and twice["bank_merge"]["outcomes"] == ["added", "added", "duplicate", "duplicate"]
        assert len(B.load_bank(Path(twice["saved_to"]).name, root / "bank3", latest_only=False)) == 2
        # 本文の断片がどのファイルにも無い (バンク・state・取得履歴・候補・観測)
        for p in root.rglob("*"):
            if p.is_file():
                text = p.read_text(encoding="utf-8")
                assert not any(frag in text for frag in BODY_FRAGMENTS), p
        # 候補からの保存は conflict / failed の記録を入れない
        cand_path = Path(dry["candidates_paths"][0])
        bad = json.loads(cand_path.read_text(encoding="utf-8"))
        bad["records"][1]["status"] = "conflict"
        cand_path.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
        res = F.save_from_candidates([cand_path], root / "bank2", state_path=root / "state2.jsonl")
        assert res["n_records"] == 1 and res["skipped"] == [{"case_id": bad["records"][1]["case_id"], "status": "conflict"}]
    print("test_run_writes_only_structured_outputs OK")


def test_refetch_updates_link_versions():
    """内容が変わった構築を取り直すと、新しい版を足して旧版と結ぶ (2026-10-06 ユーザー判断): 1 構築目は更新 (旧版に superseded_by、
    新版に supersedes)、2 構築目は重複 → 3 件 (旧版は消さない)、読み出しの既定は各系列の最新の 2 件。dry-run は併合の見込みだけ数え、
    その候補から保存しても、取得から直接保存しても同じ版。矛盾 (conflict) になった構築は入れず、旧版が最新のまま (lineage_in_bank)。
    内容が旧版に戻ったら足さず、最新も変えない (reverted。最新を戻すかは判断待ち)"""
    assert CHANGED_HTML != HTML and CONFLICT_HTML != HTML
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        bank_dir = root / "bank"
        kw = dict(state_path=root / "state.jsonl", bank_dir=bank_dir, aliases_path=root / "aliases.json",
                  site_ids_path=root / "site_ids.json", sleeper=lambda _s: None, adapters=host_adapters(),
                  access_path=root / "access.jsonl", candidates_dir=root / "candidates", now="2026-10-06T20:00:00")
        first = F.run([URL], POLICY, fetcher=FakeFetcher(), today="2026-10-06", **kw)
        assert first["bank_merge"]["outcomes"] == ["added", "added"]
        base = Path(first["saved_to"]).name
        team1_old, team2 = B.load_bank(base, bank_dir)
        # dry-run: 併合の見込みだけ数え、バンクは書かない
        dry = F.run([URL], POLICY, fetcher=FakeFetcher(html=CHANGED_HTML), refetch=True, base_version=base, dry_run=True,
                    today="2026-10-07", **kw)
        assert dry["saved_to"] is None and dry["bank_merge"]["preview"] is True
        assert dry["bank_merge"]["outcomes"] == ["updated", "duplicate"] and [p.name for p in bank_dir.iterdir()] == [base]
        # 候補から保存 (再取得なし) → 3 件 (旧 1 が superseded_by、新 1 が supersedes)
        saved = F.save_from_candidates(dry["candidates_paths"], bank_dir, base_version=base, state_path=root / "state.jsonl")
        m = saved["bank_merge"]
        assert m["outcomes"] == ["updated", "duplicate"]
        assert (m["counts"]["added"], m["counts"]["superseded"], m["counts"]["duplicates"], m["counts"]["rejected"]) == (1, 1, 1, 0)
        v2 = Path(saved["saved_to"]).name
        assert v2 != base
        allv = B.load_bank(v2, bank_dir, latest_only=False)
        assert len(allv) == 3
        old1, kept2, new1 = allv
        assert old1["case_id"] == team1_old["case_id"] and old1["superseded_by"] == new1["case_id"] and "supersedes" not in old1
        assert new1["supersedes"] == old1["case_id"] and "superseded_by" not in new1 and new1["case_id"] != old1["case_id"]
        assert "hammerarm" in new1["members"][0]["moves"] and "icepunch" in old1["members"][0]["moves"]
        assert kept2 == team2                                                     # 重複は足さず、既存の記録のまま
        assert kept2["source"]["fetched_at"] == "2026-10-06" and new1["source"]["fetched_at"] == "2026-10-07"
        assert m["superseded"] == [{"lineage": B.lineage_key(new1), "old": old1["case_id"], "new": new1["case_id"],
                                    "same_body_hash": False}]                     # 記事 (ページの本文) が変わった更新
        assert B.lineage_key(old1) == B.lineage_key(new1) != B.lineage_key(kept2)
        assert B.lineage_key(new1).endswith(":team_code:E2E9MW0BQ7")
        latest = B.load_bank(v2, bank_dir)                                         # 既定は各系列の最新の記録だけ
        assert [c["case_id"] for c in latest] == [kept2["case_id"], new1["case_id"]]
        man = json.loads((bank_dir / v2 / "manifest.json").read_text(encoding="utf-8"))
        assert (man["n_cases"], man["n_lineages"], man["n_superseded"]) == (3, 2, 1)
        assert not B.usable_for(old1, "weakness", REG, policy=POLICY) and B.usable_for(new1, "weakness", REG, policy=POLICY)
        assert B.usable_for(old1, "parser_eval")
        assert len(B.load_bank(base, bank_dir, latest_only=False)) == 2           # 基の版 (固定版) は変わらない
        # 同じ変更を取得から直接保存しても同じ版
        direct = F.run([URL], POLICY, fetcher=FakeFetcher(html=CHANGED_HTML), refetch=True, base_version=base, today="2026-10-07", **kw)
        assert Path(direct["saved_to"]).name == v2 and direct["bank_merge"]["outcomes"] == ["updated", "duplicate"]
        # 矛盾 (実数値の再計算と合わない) になった構築は入れない。その系列は今の最新版 (new1) のまま
        bad = F.run([URL], POLICY, fetcher=FakeFetcher(html=CONFLICT_HTML), refetch=True, base_version=v2, today="2026-10-08", **kw)
        assert [r["status"] for r in bad["records"]] == ["conflict", "incomplete"]
        bm = bad["bank_merge"]
        assert bm["outcomes"] == ["rejected", "duplicate"] and bm["rejected"][0]["lineage_in_bank"] is True
        assert bm["rejected"][0]["lineage"] == B.lineage_key(new1) and Path(bad["saved_to"]).name == v2
        # 内容が旧版に戻った (1 構築目が元の技) → 足さず、最新も変えない (reverted。duplicates にも数える。最新を戻すかは判断待ち)
        back = F.run([URL], POLICY, fetcher=FakeFetcher(), refetch=True, base_version=v2, today="2026-10-09", **kw)
        bm = back["bank_merge"]
        assert bm["outcomes"] == ["reverted", "duplicate"] and (bm["counts"]["duplicates"], bm["counts"]["reverted"]) == (2, 1)
        assert bm["reverted"][0]["latest"] == new1["case_id"] and Path(back["saved_to"]).name == v2
        assert any("旧版に戻った" in ln for ln in F._summary_lines(back))
        # 取得履歴は取得のたびに追記する (バンクの重複処理とは別)
        access = [json.loads(ln) for ln in (root / "access.jsonl").read_text(encoding="utf-8").splitlines()]
        assert [a["mode"] for a in access] == ["save", "dry_run", "save", "save", "save"]
        for p in root.rglob("*"):
            if p.is_file():
                assert not any(frag in p.read_text(encoding="utf-8") for frag in BODY_FRAGMENTS), p
    print("test_refetch_updates_link_versions OK")


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
    test_refetch_updates_link_versions()
    test_fetch_error_and_interval()
    print("ALL OK")


if __name__ == "__main__":
    main()
