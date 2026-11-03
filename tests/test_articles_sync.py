"""構築記事の定期調査の同期 (tools/team_build/articles_sync) のテスト。ネットワークに出ない (gh の実行を注入、出力先は一時ディレクトリ)。

    python -m tests.test_articles_sync
"""
from __future__ import annotations

import base64
import json
import subprocess
import tempfile
from pathlib import Path

from tools.team_build import articles_sync as S

LOCAL = """﻿確度,第2ラウンド,第3ラウンド,ブログ/著者,プラットフォーム,ブログURL,根拠文言,根拠記事URL,記事タイトル,シーズン,留保事項,出典担当
A,,,甲,note,https://note.com/a,最終42位,https://note.com/a/n/n1,【M-4 最終42位】カバルカイリュー,M-4,,x
B,,,乙,note,https://note.com/b,最終80位,https://note.com/b/n/n2?app_launch=false,【M-1 最終80位】スターミー軸,M-1,,x
B,,,丙,はてなブログ系,https://c.hatenablog.com/,最終470位 / 最終286位,https://c.hatenablog.com/entry/1 https://c.hatenablog.com/entry/2/,A / B,"M-3,MCS 2026.06",,x
要追確認,,,丁,,https://d.example.com/,,,,M-5,,x
A,,,戊,note,https://note.com/e,最終10位,https://note.com/e/n/local,ローカルだけの記事,M-2,,x
"""
# 調査の版: 列が 2 つ多い (第4ラウンド / 自動更新)。甲は確度 A → B、乙は M-1 → M-2、丙は URL 2 本を 1 本ずつの 2 行に分けた (同じ鍵)、
# 丁は変化なし (URL なし → 著者と題名の鍵)、戊は無い (ローカルだけ)、己 (URL 2 本) と庚 (URL なし) が新規
SURVEY = """﻿確度,第2ラウンド,第3ラウンド,第4ラウンド,自動更新,ブログ/著者,プラットフォーム,ブログURL,根拠文言,根拠記事URL,記事タイトル,シーズン,留保事項,出典担当
B,,,,,甲,note,https://note.com/a,最終42位,https://note.com/a/n/n1/,【M-4 最終42位】カバルカイリュー,M-4,,x
B,,,,,乙,note,https://note.com/b,最終80位,https://note.com/b/n/n2,【M-1 最終80位】スターミー軸,M-2,,x
B,,,,,丙,はてなブログ系,https://c.hatenablog.com/,最終470位,https://c.hatenablog.com/entry/1,A,"M-3,MCS2026.06",,x
B,,,,,丙,はてなブログ系,https://c.hatenablog.com/,最終286位,https://c.hatenablog.com/entry/2,B,MCS2026.06,,x
要追確認,,,,,丁,,https://d.example.com/,,,,M-5,,x
A,,,R4新規,2026-10-28,己,note,https://note.com/f,最終5位,https://note.com/f/n/x1 https://note.com/f/n/x2,【M-6 最終5位】新しい構築,M-6,,auto
B,,,,2026-10-28,庚,はてなブログ系,https://g.hatenablog.com/,最終300位,,【M-6】題名だけ,M-6,,auto
"""
STATE = {"processed_seasons": ["M-5", "M-6"], "season_end_jst": "2026-10-20T08:59:00+09:00", "last_run": "2026-10-28",
         "last_counts": {"new": 3}}


def test_keys_and_diff():
    lh, lr = S.read_table(LOCAL)
    sh, sr = S.read_table(SURVEY)
    assert len(lr) == 5 and len(sr) == 7 and "第4ラウンド" in sh and "第4ラウンド" not in lh
    assert S.row_keys(lr[2]) == ["url:https://c.hatenablog.com/entry/1", "url:https://c.hatenablog.com/entry/2"]   # URL 複数は URL ごと
    assert S.row_keys(lr[1]) == ["url:https://note.com/b/n/n2"]                    # 追跡クエリを落とす
    assert S.row_keys(lr[3]) == ["title:丁|"]                                       # URL の無い行は著者と題名
    d = S.diff_tables(lr, sr)
    c = d["counts"]
    assert [x["key"] for x in d["new"]] == ["url:https://note.com/f/n/x1", "url:https://note.com/f/n/x2", "title:庚|【M-6】題名だけ"]
    assert c["new"] == 3 and c["new_rows"] == 2
    assert [(x["author"], x["before"], x["after"]) for x in d["confidence"]] == [("甲", "A", "B")]      # 末尾の / の違いは同じ鍵
    # 乙は M-1 → M-2。丙の 1 本目は "MCS 2026.06" と "MCS2026.06" が正規化で同じなので変化なし (2 本目は次のテスト)
    assert [(x["author"], x["before"], x["after"]) for x in d["season"]][0] == ("乙", "M-1", "M-2")
    assert "url:https://c.hatenablog.com/entry/1" not in [x["key"] for x in d["season"]]
    assert [x["key"] for x in d["local_only"]] == ["url:https://note.com/e/n/local"] and c["local_only_rows"] == 1
    print("test_keys_and_diff OK")


def test_season_change_on_split_row():
    """URL 複数の行が調査で分かれたとき、鍵ごとに比べる (丙の 2 本目は M-3 が落ちたのでシーズンの変化)"""
    _, lr = S.read_table(LOCAL)
    _, sr = S.read_table(SURVEY)
    d = S.diff_tables(lr, sr)
    hei = [x for x in d["season"] if x["author"] == "丙"]
    assert [x["key"] for x in hei] == ["url:https://c.hatenablog.com/entry/2"] and hei[0]["after"] == "MCS2026.06"
    assert d["counts"]["season_changed"] == 2
    print("test_season_change_on_split_row OK")


def test_merge_keeps_columns_and_local_rows():
    lh, lr = S.read_table(LOCAL)
    sh, sr = S.read_table(SURVEY)
    header, rows, kept = S.merge_tables(lh, lr, sh, sr)
    assert header == sh and kept == 1                                   # 調査の列 (追加列を含む) をそのまま、ローカルだけの行を 1 つ残す
    assert len(rows) == 8 and rows[-1]["ブログ/著者"] == "戊" and rows[-1]["第4ラウンド"] == ""
    assert rows[5]["第4ラウンド"] == "R4新規" and rows[5]["自動更新"] == "2026-10-28"
    assert rows[0]["確度"] == "B"                                        # 調査の値を採用
    text = S.render_table(header, rows)
    assert text.startswith("﻿確度,") and "\r" not in text
    assert S.read_table(text) == (header, rows)                          # 書いた CSV を読み戻すと同じ
    assert S.render_table(*S.read_table(SURVEY)) == SURVEY               # 調査の CSV はそのまま再現する
    # ローカルにだけある列は末尾に足す
    h2, _, _ = S.merge_tables(lh + ["メモ"], [{**r, "メモ": "m"} for r in lr], sh, sr)
    assert h2 == sh + ["メモ"]
    print("test_merge_keeps_columns_and_local_rows OK")


def test_carry_robots():
    man = {"entries": [{"url": "https://a/1", "host": "a", "robots": "unchecked", "selected": True},
                       {"url": "https://b/1", "host": "b", "robots": "unchecked", "selected": True},
                       {"url": "https://c/1", "host": "c", "robots": "unchecked", "selected": True}]}
    prev = {"entries": [{"url": "https://a/1", "robots": "allowed"}, {"url": "https://b/1", "robots": "disallowed"}],
            "robots_hosts": {"a": "ok", "b": "ok", "z": "ok"}}
    out = S.carry_robots(man, prev)
    assert [e["robots"] for e in out["entries"]] == ["allowed", "disallowed", "unchecked"]
    assert [e["selected"] for e in out["entries"]] == [True, False, True]
    assert out["n_to_fetch"] == 2 and out["n_robots_carried"] == 2 and out["n_robots_unchecked"] == 1
    assert out["robots_hosts"] == {"a": "ok", "b": "ok"}
    assert S.carry_robots({"entries": []}, None)["n_to_fetch"] == 0
    print("test_carry_robots OK")


def _write_local(d: Path) -> Path:
    local = d / "articles" / "blogs.csv"
    local.parent.mkdir(parents=True)
    local.write_text(LOCAL, encoding="utf-8")
    prev = {"entries": [{"url": "https://note.com/a/n/n1", "robots": "allowed"}], "robots_hosts": {"note.com": "ok"}}
    (local.parent / "manifest.json").write_text(json.dumps(prev), encoding="utf-8")
    return local


def test_dry_run_and_apply():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        local = _write_local(d)
        survey, state = d / "survey.csv", d / "state.json"
        survey.write_text(SURVEY, encoding="utf-8")
        state.write_text(json.dumps(STATE), encoding="utf-8")
        before = sorted(p.name for p in local.parent.iterdir())
        # dry-run: 何も書かない
        assert S.main(["--source-file", str(survey), "--source-state", str(state), "--local-csv", str(local)]) == 0
        assert sorted(p.name for p in local.parent.iterdir()) == before and local.read_text(encoding="utf-8") == LOCAL
        # --apply --out-dir: 本体 (local の場所) には書かない
        out = d / "out"
        assert S.main(["--source-file", str(survey), "--source-state", str(state), "--local-csv", str(local),
                       "--out-dir", str(out), "--apply"]) == 0
        assert sorted(p.name for p in local.parent.iterdir()) == before and local.read_text(encoding="utf-8") == LOCAL
        names = sorted(p.name for p in out.iterdir())
        bak = [n for n in names if n.startswith("blogs.csv.bak.")]
        assert len(bak) == 1 and (out / bak[0]).read_text(encoding="utf-8") == LOCAL          # 控え = 更新前のローカル
        assert any(n.startswith("manifest.json.bak.") for n in names)
        merged = (out / "blogs.csv").read_text(encoding="utf-8")
        assert merged.splitlines()[0] == SURVEY.splitlines()[0] and "戊" in merged
        man = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        urls = [e["url"] for e in man["entries"]]
        assert "https://note.com/f/n/x2" in urls and "https://note.com/e/n/local" in urls      # 新規とローカルだけの行の両方が入る
        assert man["n_rows"] == 8 and man["source_csv"] == str(out / "blogs.csv")
        assert man["entries"][0]["robots"] == "allowed" and man["n_robots_carried"] == 1        # 前の manifest から引き継ぐ
        logs = [json.loads(x) for x in (out / "sync.jsonl").read_text(encoding="utf-8").splitlines()]
        assert len(logs) == 1 and logs[0]["applied"] is True
        assert logs[0]["state"] == {"last_run": "2026-10-28", "processed_seasons": ["M-5", "M-6"]}
        assert logs[0]["counts"]["new"] == 3 and logs[0]["source"]["file"] == str(survey) and logs[0]["t"]
        # 同じ内容でもう一度 (出力先の CSV が併合の結果と同じ) → 書き換えず、記録だけ applied = False
        out_csv_before = (out / "blogs.csv").stat().st_mtime_ns
        assert S.main(["--source-file", str(survey), "--local-csv", str(out / "blogs.csv"), "--apply"]) == 0
        logs = [json.loads(x) for x in (out / "sync.jsonl").read_text(encoding="utf-8").splitlines()]
        assert len(logs) == 2 and logs[1]["applied"] is False and logs[1]["counts"]["new"] == 0
        assert (out / "blogs.csv").stat().st_mtime_ns == out_csv_before
        assert len([p for p in out.iterdir() if p.name.startswith("blogs.csv.bak.")]) == 1
    print("test_dry_run_and_apply OK")


class FakeGh:
    """gh api の代役。呼ばれた引数を記録し、エンドポイントごとの応答を返す"""

    def __init__(self, files: dict, sha: str = "abc123def4567890"):
        self.files, self.sha, self.calls = files, sha, []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        assert cmd[:4] == ["gh", "api", "-X", "GET"]                          # 読み取りだけ
        ep = cmd[4]
        if ep.startswith("repos/o/r/commits?sha=br&per_page=1"):
            return subprocess.CompletedProcess(cmd, 0, json.dumps([{"sha": self.sha}]), "")
        for path, text in self.files.items():
            if ep == f"repos/o/r/contents/{path}?ref={self.sha}":
                body = {"encoding": "base64", "content": base64.b64encode(text.encode("utf-8")).decode()}
                return subprocess.CompletedProcess(cmd, 0, json.dumps(body), "")
        return subprocess.CompletedProcess(cmd, 1, "", "gh: Not Found (HTTP 404)")


def test_fetch_via_gh():
    fake = FakeGh({"research/blogs.csv": SURVEY, "research/state.json": json.dumps(STATE)})
    got = S.fetch_survey("o/r", "br", "research/blogs.csv", "research/state.json", runner=fake)
    assert got["csv"] == SURVEY and got["state"]["last_run"] == "2026-10-28"
    assert got["source"] == {"repo": "o/r", "branch": "br", "csv_path": "research/blogs.csv", "sha": fake.sha}   # 読んだ版の sha
    assert all(f"ref={fake.sha}" in c[4] for c in fake.calls[1:])                              # CSV と state は同じ sha で読む
    assert len(fake.calls) == 3 and all("merged.json" not in c[4] for c in fake.calls)       # merged.json は読まない
    print("test_fetch_via_gh OK")


def test_gh_errors():
    def missing(cmd, **kw):
        raise FileNotFoundError(cmd[0])

    def unauth(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 4, "", "To get started with GitHub CLI, please run:  gh auth login")

    for runner, word in ((missing, "gh が見つからない"), (unauth, "gh auth login")):
        try:
            S.fetch_survey("o/r", "br", runner=runner)
            raise AssertionError("SyncError にならない")
        except S.SyncError as ex:
            assert word in str(ex), ex
    # main は終了コード 2 で止め、何も書かない
    with tempfile.TemporaryDirectory() as td:
        local = _write_local(Path(td))
        before = sorted(p.name for p in local.parent.iterdir())
        assert S.main(["--repo", "o/r", "--branch", "br", "--local-csv", str(local), "--apply"], runner=missing) == 2
        assert S.main(["--repo", "o/r", "--branch", "br", "--local-csv", str(local), "--apply"], runner=unauth) == 2
        assert sorted(p.name for p in local.parent.iterdir()) == before
    # state.json が無くても CSV は読める
    fake = FakeGh({"research/blogs.csv": SURVEY})
    got = S.fetch_survey("o/r", "br", "research/blogs.csv", "research/state.json", runner=fake)
    assert got["state"] is None and got["csv"] == SURVEY
    print("test_gh_errors OK")


def main() -> None:
    test_keys_and_diff()
    test_season_change_on_split_row()
    test_merge_keeps_columns_and_local_rows()
    test_carry_robots()
    test_dry_run_and_apply()
    test_fetch_via_gh()
    test_gh_errors()
    print("ALL OK")


if __name__ == "__main__":
    main()
