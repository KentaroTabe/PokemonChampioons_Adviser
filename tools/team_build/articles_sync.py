"""構築記事の定期調査 (別リポジトリの routine が push するブログ一覧) → この プロジェクトの一覧 logs/articles/blogs.csv → manifest.json の同期 (2026-10-30)。

調査は GitHub の ARTICLE_SURVEY_REPO / ARTICLE_SURVEY_BRANCH の research/ にある (毎週の routine が WebSearch で調べて push する)。
ここで読むのは一覧の CSV (メタ情報の列だけ) と state.json だけ。research/merged.json は第三者の文章の抜粋が入り得るので読まない。
記事の本文は取得しない (取得はホストごとの方針 host_policy.json が先。articles_fetch の範囲)。GitHub 以外にはアクセスしない。

    python -m tools.team_build.articles_sync                 # dry-run: 差分の表と件数を出すだけ (何も書かない)
    python -m tools.team_build.articles_sync --apply         # blogs.csv を更新 (控え .bak.YYYYMMDD) → manifest.json を再生成 → sync.jsonl に 1 行
    python -m tools.team_build.articles_sync --source-file seeker.csv [--source-state state.json] [--out-dir DIR] [--apply]

差分: 行の鍵は 根拠記事URL (articles_ingest.normalize_url で正規化、1 行に複数あれば URL ごとに展開)。URL の無い行は (ブログ/著者, 記事タイトル)。
種類は 新規 (調査にだけある鍵) / 確度の変化 / シーズンの変化 (articles_ingest.seasons_of で正規化して比べる) / ローカルにだけある鍵 (消さない。報告だけ)。
適用: 調査の行と列をそのまま採用 (追加列も残す)、ローカルにだけある鍵を持つ行は末尾に残す。manifest は articles_ingest.build_manifest で作り直し、
robots.txt の判定は前の manifest から URL ごとに引き継ぐ (ここでは外部の robots.txt を読まない。新しい URL は unchecked のまま)。
純粋関数 (read_table / row_keys / diff_tables / merge_tables / carry_robots) は tests/test_articles_sync.py。
"""
from __future__ import annotations

import argparse
import base64
import csv
import io
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from champions_agent.config import (ARTICLE_SURVEY_BRANCH, ARTICLE_SURVEY_CSV_PATH, ARTICLE_SURVEY_REPO, ARTICLE_SURVEY_STATE_PATH,
                                    ARTICLE_SYNC_BACKUP_SUFFIX, ARTICLE_SYNC_GH_BIN, ARTICLE_SYNC_GH_TIMEOUT_S, ARTICLE_SYNC_LOG_NAME,
                                    ARTICLE_SYNC_SHOW_MAX)
from tools.team_build import articles_ingest as ingest

COL = ingest.COLUMNS          # 英語キー → 日本語の列名 (確度 / ブログ/著者 / 根拠記事URL / 記事タイトル / シーズン …)
BOM = "﻿"


class SyncError(RuntimeError):
    """取得の失敗 (gh が無い・認証が無い・応答が読めない)。main は終了コード 2 で止める"""


# ---------------------------------------------------------------- 純粋な部分

def read_table(text: str) -> tuple:
    """CSV の本文 → (列名の列, 行の列)。行は元の列名 (日本語) のキーの辞書。BOM を落とし、全部空の行は捨てる。
    列名の無い余分なセル (DictReader の None キー) は捨てる"""
    rd = csv.DictReader(io.StringIO((text or "").lstrip(BOM)))
    header = [h for h in (rd.fieldnames or []) if h is not None]
    rows = []
    for r in rd:
        row = {h: (r.get(h) or "") for h in header}
        if any(v.strip() for v in row.values()):
            rows.append(row)
    return header, rows


def _cell(row: dict, key: str) -> str:
    return (row.get(COL[key]) or "").strip()


def row_keys(row: dict) -> list:
    """行の鍵: 根拠記事URL を正規化して URL ごとに "url:<URL>"。URL が無い行は "title:<ブログ/著者>|<記事タイトル>" 1 つ (両方空なら鍵なし)"""
    urls = ingest.split_urls(_cell(row, "article_urls"))
    if urls:
        return [f"url:{u}" for u in urls]
    author, title = _cell(row, "author"), _cell(row, "title")
    return [f"title:{author}|{title}"] if (author or title) else []


def index_rows(rows: list) -> dict:
    """鍵 → 行番号 (0 始まり)。同じ鍵が複数行にあれば最初の行 (articles_ingest.build_manifest と同じ扱い)"""
    idx: dict = {}
    for i, r in enumerate(rows):
        for k in row_keys(r):
            idx.setdefault(k, i)
    return idx


def _item(key: str, row: dict, row_no: int) -> dict:
    return {"key": key, "row": row_no, "author": _cell(row, "author"), "confidence": _cell(row, "confidence"),
            "season": _cell(row, "season"), "title": _cell(row, "title")}


def diff_tables(local_rows: list, survey_rows: list) -> dict:
    """ローカル と 調査 の差分 (純粋)。鍵 (URL ごと) の単位で数える。行の単位の件数も参考に出す。
    new: 調査にだけある鍵 / confidence: 両方にあって確度が違う / season: 両方にあって正規化したシーズンの集合が違う /
    local_only: ローカルにだけある鍵 (消さない)"""
    li, si = index_rows(local_rows), index_rows(survey_rows)
    new = [_item(k, survey_rows[i], i) for k, i in si.items() if k not in li]
    local_only = [_item(k, local_rows[i], i) for k, i in li.items() if k not in si]
    conf, season = [], []
    for k, i in si.items():
        if k not in li:
            continue
        s, l = survey_rows[i], local_rows[li[k]]
        if _cell(s, "confidence") != _cell(l, "confidence"):
            conf.append({**_item(k, s, i), "before": _cell(l, "confidence"), "after": _cell(s, "confidence")})
        if set(ingest.seasons_of(_cell(s, "season"))) != set(ingest.seasons_of(_cell(l, "season"))):
            season.append({**_item(k, s, i), "before": _cell(l, "season"), "after": _cell(s, "season")})
    new_rows = sorted({i for i, r in enumerate(survey_rows) if row_keys(r) and not any(k in li for k in row_keys(r))})
    local_only_rows = sorted({i for i, r in enumerate(local_rows) if row_keys(r) and not any(k in si for k in row_keys(r))})
    counts = {"local_rows": len(local_rows), "survey_rows": len(survey_rows), "local_keys": len(li), "survey_keys": len(si),
              "new": len(new), "confidence_changed": len(conf), "season_changed": len(season), "local_only": len(local_only),
              "new_rows": len(new_rows), "local_only_rows": len(local_only_rows)}
    return {"counts": counts, "new": new, "confidence": conf, "season": season, "local_only": local_only}


def merge_tables(local_header: list, local_rows: list, survey_header: list, survey_rows: list) -> tuple:
    """併合 (純粋): 調査の列と行をそのまま採用し、ローカルにだけある鍵を 1 つでも持つ行を末尾に残す (鍵の無い行も残す)。
    列は 調査の列 + ローカルにだけある列。戻り値 (列名の列, 行の列, 末尾に残したローカルの行の数)"""
    header = list(survey_header) + [h for h in local_header if h not in survey_header]
    si = index_rows(survey_rows)
    kept = [r for r in local_rows if not row_keys(r) or any(k not in si for k in row_keys(r))]
    rows = [{h: r.get(h, "") for h in header} for r in list(survey_rows) + kept]
    return header, rows, len(kept)


def render_table(header: list, rows: list) -> str:
    """列名と行 → CSV の本文 (BOM つき、改行は LF。既存の blogs.csv と同じ形)"""
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=header, lineterminator="\n", extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return BOM + buf.getvalue()


def carry_robots(man: dict, prev: Optional[dict]) -> dict:
    """前の manifest の robots の判定を URL ごとに引き継ぐ (純粋。man を書き換えて返す)。disallowed は selected = False。
    robots_hosts は今の entries にある host の分だけ引き継ぐ。n_to_fetch は articles_ingest.main と同じく引き継ぎの後に数え直す"""
    prev_robots = {e.get("url"): e.get("robots") for e in (prev or {}).get("entries", [])}
    n_carried = 0
    for e in man["entries"]:
        r = prev_robots.get(e["url"])
        if r and r != "unchecked":
            e["robots"] = r
            n_carried += 1
            if r == "disallowed":
                e["selected"] = False
    prev_hosts = (prev or {}).get("robots_hosts") or {}
    hosts = {e["host"] for e in man["entries"]}
    if prev_hosts:
        man["robots_hosts"] = {h: v for h, v in prev_hosts.items() if h in hosts}
    man["n_to_fetch"] = sum(1 for e in man["entries"] if e["selected"])
    man["n_robots_carried"] = n_carried
    man["n_robots_unchecked"] = sum(1 for e in man["entries"] if e["robots"] == "unchecked")
    return man


def format_diff(diff: dict, show_max: int = ARTICLE_SYNC_SHOW_MAX) -> str:
    """差分の表 (人が読む用)"""
    c = diff["counts"]
    lines = [f"ローカル {c['local_rows']} 行 ({c['local_keys']} 鍵) / 調査 {c['survey_rows']} 行 ({c['survey_keys']} 鍵)",
             f"新規 {c['new']} 鍵 ({c['new_rows']} 行) / 確度の変化 {c['confidence_changed']} / シーズンの変化 {c['season_changed']} / "
             f"ローカルだけ {c['local_only']} 鍵 ({c['local_only_rows']} 行、消さない)"]
    sections = (("新規", "new", False), ("確度の変化", "confidence", True), ("シーズンの変化", "season", True), ("ローカルだけ", "local_only", False))
    for label, name, change in sections:
        items = diff[name]
        if not items:
            continue
        lines.append(f"\n## {label} ({len(items)})")
        for it in items[:show_max]:
            head = f"  [{it['confidence'] or '-'}] {it['author'] or '-'} | {it['season'] or '-'}"
            if change:
                head += f" | {it['before'] or '(空)'} → {it['after'] or '(空)'}"
            lines.append(f"{head} | {it['key']}")
        if len(items) > show_max:
            lines.append(f"  … ほか {len(items) - show_max} 件")
    return "\n".join(lines)


# ---------------------------------------------------------------- 取得 (GitHub だけ、読み取りだけ)

Runner = Callable[..., subprocess.CompletedProcess]


def gh_api(endpoint: str, runner: Runner = subprocess.run, gh: str = ARTICLE_SYNC_GH_BIN,
           timeout: float = ARTICLE_SYNC_GH_TIMEOUT_S):
    """gh api <endpoint> (GET) の JSON。gh が無い・認証が無い・失敗は SyncError"""
    try:
        p = runner([gh, "api", "-X", "GET", endpoint], capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as ex:
        raise SyncError(f"gh が見つからない ({gh})。GitHub CLI を入れて gh auth login するか、--source-file で読む") from ex
    except subprocess.TimeoutExpired as ex:
        raise SyncError(f"gh api {endpoint} が {timeout} 秒で終わらない") from ex
    if p.returncode != 0:
        msg = (p.stderr or p.stdout or "").strip().splitlines()
        raise SyncError(f"gh api {endpoint} が失敗 (終了コード {p.returncode}): {msg[-1] if msg else '(出力なし)'}"
                        " — 認証が無ければ gh auth login")
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError as ex:
        raise SyncError(f"gh api {endpoint} の応答が JSON でない") from ex


def gh_head_sha(repo: str, branch: str, **kw) -> str:
    data = gh_api(f"repos/{repo}/commits?sha={branch}&per_page=1", **kw)
    if not isinstance(data, list) or not data or not data[0].get("sha"):
        raise SyncError(f"{repo} のブランチ {branch} のコミットが取れない")
    return data[0]["sha"]


def gh_read_file(repo: str, path: str, ref: str, **kw) -> str:
    """repos/<repo>/contents/<path>?ref=<ref> の base64 → 文字列 (UTF-8)"""
    data = gh_api(f"repos/{repo}/contents/{path}?ref={ref}", **kw)
    if not isinstance(data, dict) or data.get("encoding") != "base64" or "content" not in data:
        raise SyncError(f"{repo}:{path} の内容が base64 で返らない (encoding={data.get('encoding') if isinstance(data, dict) else None})")
    return base64.b64decode(data["content"]).decode("utf-8")


def fetch_survey(repo: str = ARTICLE_SURVEY_REPO, branch: str = ARTICLE_SURVEY_BRANCH, csv_path: str = ARTICLE_SURVEY_CSV_PATH,
                 state_path: str = ARTICLE_SURVEY_STATE_PATH, **kw) -> dict:
    """先にブランチの先頭のコミットを取り、その sha で CSV と state.json を読む (途中で push されても同じ版を読む)"""
    sha = gh_head_sha(repo, branch, **kw)
    text = gh_read_file(repo, csv_path, sha, **kw)
    state = None
    if state_path:
        try:
            state = json.loads(gh_read_file(repo, state_path, sha, **kw))
        except (SyncError, json.JSONDecodeError) as ex:
            print(f"注意: state.json が読めない ({ex})", file=sys.stderr)
    return {"csv": text, "state": state, "source": {"repo": repo, "branch": branch, "csv_path": csv_path, "sha": sha}}


def load_local_source(csv_file: str, state_file: Optional[str]) -> dict:
    state = json.loads(Path(state_file).read_text(encoding="utf-8")) if state_file else None
    return {"csv": Path(csv_file).read_text(encoding="utf-8"), "state": state, "source": {"file": str(csv_file), "sha": None}}


# ---------------------------------------------------------------- 適用 (書き込み)

def _backup(src: Path, out_dir: Path, now: datetime) -> Optional[str]:
    """src の写しを out_dir/<名前><.bak.YYYYMMDD> に置く。同じ名前の控えがあれば上書きせず .HHMMSS を足す"""
    if not src.exists():
        return None
    dst = out_dir / (src.name + now.strftime(ARTICLE_SYNC_BACKUP_SUFFIX))
    if dst.exists():
        dst = dst.with_name(dst.name + now.strftime(".%H%M%S"))
    shutil.copy2(src, dst)
    return str(dst)


def state_summary(state: Optional[dict]) -> dict:
    s = state or {}
    return {"last_run": s.get("last_run"), "processed_seasons": s.get("processed_seasons")}


def apply_sync(local_csv: Path, local_manifest: Path, out_dir: Path, survey: dict, diff: dict, merged_text: str,
               fetched_at: datetime) -> dict:
    """blogs.csv と manifest.json を out_dir に書き (控えを残す)、out_dir/sync.jsonl に 1 行足す。
    併合の結果が出力先の CSV と同じ文字列なら書き換えない (applied = False で記録だけ)"""
    out_dir.mkdir(parents=True, exist_ok=True)
    out_csv, out_man = out_dir / local_csv.name, out_dir / local_manifest.name
    unchanged = out_csv.exists() and out_csv.read_text(encoding="utf-8") == merged_text
    rec = {"t": fetched_at.isoformat(timespec="seconds"), "source": survey["source"], "state": state_summary(survey.get("state")),
           "counts": diff["counts"], "applied": not unchanged, "out_dir": str(out_dir)}
    if rec["applied"]:
        rec["backup_csv"] = _backup(local_csv, out_dir, fetched_at)
        rec["backup_manifest"] = _backup(local_manifest, out_dir, fetched_at)
        out_csv.write_text(merged_text, encoding="utf-8")
        prev = json.loads(local_manifest.read_text(encoding="utf-8")) if local_manifest.exists() else None
        man = carry_robots(ingest.build_manifest(ingest.parse_rows(merged_text)), prev)
        man["source_csv"] = str(out_csv)
        man["sync"] = {"t": rec["t"], "source": survey["source"]}
        out_man.write_text(json.dumps(man, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        rec["manifest"] = {k: man[k] for k in ("n_rows", "n_selected_rows", "n_urls", "n_to_fetch", "n_regulation_unknown",
                                               "n_robots_carried", "n_robots_unchecked")}
    with (out_dir / ARTICLE_SYNC_LOG_NAME).open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


# ---------------------------------------------------------------- 入口

def main(argv: Optional[list] = None, runner: Runner = subprocess.run) -> int:
    ap = argparse.ArgumentParser(description="構築記事の定期調査 (GitHub の一覧 CSV) → logs/articles/blogs.csv → manifest.json の同期。本文は取得しない")
    ap.add_argument("--apply", action="store_true", help="blogs.csv を更新し manifest.json を作り直す (既定は dry-run: 何も書かない)")
    ap.add_argument("--source-file", help="調査の CSV をローカルのファイルから読む (gh を使わない。テスト・手動用)")
    ap.add_argument("--source-state", help="--source-file のときの state.json (任意)")
    ap.add_argument("--repo", default=ARTICLE_SURVEY_REPO)
    ap.add_argument("--branch", default=ARTICLE_SURVEY_BRANCH)
    ap.add_argument("--local-csv", default=str(ingest.DEFAULT_CSV), help="比べるローカルの一覧 (既定 logs/articles/blogs.csv)")
    ap.add_argument("--out-dir", help="書き出し先 (既定はローカルの一覧と同じディレクトリ)。本体に書かずに確かめるとき")
    ap.add_argument("--show-max", type=int, default=ARTICLE_SYNC_SHOW_MAX)
    args = ap.parse_args(argv)

    fetched_at = datetime.now().astimezone()
    try:
        survey = (load_local_source(args.source_file, args.source_state) if args.source_file
                  else fetch_survey(args.repo, args.branch, runner=runner))
    except SyncError as ex:
        print(f"エラー: {ex}", file=sys.stderr)
        return 2
    local_csv = Path(args.local_csv)
    local_manifest = local_csv.with_name(ingest.DEFAULT_OUT.name)
    l_header, l_rows = read_table(local_csv.read_text(encoding="utf-8") if local_csv.exists() else "")
    s_header, s_rows = read_table(survey["csv"])
    if not s_rows or COL["confidence"] not in s_header or COL["article_urls"] not in s_header:
        print(f"エラー: 調査の CSV に行か必須の列 ({COL['confidence']} / {COL['article_urls']}) が無い", file=sys.stderr)
        return 2
    diff = diff_tables(l_rows, s_rows)
    src = survey["source"]
    print(f"調査: {src.get('repo', src.get('file'))} {src.get('branch', '')} {('@' + src['sha'][:12]) if src.get('sha') else ''}"
          f" (取得 {fetched_at.isoformat(timespec='seconds')})")
    st = state_summary(survey.get("state"))
    if survey.get("state") is not None:
        print(f"state.json: last_run={st['last_run']} processed_seasons={st['processed_seasons']}")
    print(format_diff(diff, args.show_max))
    header, rows, n_kept = merge_tables(l_header, l_rows, s_header, s_rows)
    added_cols = [h for h in s_header if h not in l_header]
    print(f"\n併合すると {len(rows)} 行 (調査 {len(s_rows)} + ローカルだけ {n_kept})、列の追加 {added_cols or 'なし'}")
    if not args.apply:
        print("dry-run (何も書いていない)。反映は --apply")
        return 0
    out_dir = Path(args.out_dir) if args.out_dir else local_csv.parent
    rec = apply_sync(local_csv, local_manifest, out_dir, survey, diff, render_table(header, rows), fetched_at)
    if rec["applied"]:
        m = rec["manifest"]
        print(f"適用: {out_dir / local_csv.name} (控え {rec['backup_csv']})、manifest 記事 URL {m['n_urls']} 取得対象 {m['n_to_fetch']} "
              f"規制が不明 {m['n_regulation_unknown']} robots 引き継ぎ {m['n_robots_carried']} / 未確認 {m['n_robots_unchecked']}")
    else:
        print("変更なし (書き換えていない)")
    print(f"記録: {out_dir / ARTICLE_SYNC_LOG_NAME}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
