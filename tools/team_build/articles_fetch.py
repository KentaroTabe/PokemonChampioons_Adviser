"""許可した URL だけを取得して記事バンクに入れる (docs/ARTICLE_BANK_DESIGN_1006.md §3.12)。

    python -m tools.team_build.articles_fetch --url <URL> [--url ...] [--dry-run] [--refetch] [--policy ...] [--state ...] [--bank-dir ...]
                                              [--base-version V] [--aliases ...] [--site-ids ...]

規則 (2026-10-06 ユーザー判断):
- ホストの方針 (logs/articles/host_policy.json) で fetch が allow、かつ allowed_urls があればその URL のときだけ取得する (host_allowed)。
  方針に無いホスト・unknown は取得しない。巡回しない (与えられた URL だけ。ページ内のリンクを辿らない)
- 並列取得をしない (1 本ずつ)。同じホストへの要求の間は方針の min_interval_s (無ければ BUILD_ARTICLE_FETCH_MIN_INTERVAL_S) 以上空ける
- 再取得は必要な更新時だけ: 以前に取得した URL (state.jsonl に url_hash がある) は --refetch を付けたときだけ取り直す
- 本文 (HTML・リンクつきの本文) はメモリの中だけで扱い、ディスクに書かない。保存するのは構造化した記録 (バンク)、処理状態 (state.jsonl)、
  別名辞書・サイト固有 id の観測 (ローカル) だけ。例外の文言にも本文を入れない
- 変換層はホストごと (tools/team_build/adapters)。無いホストはページ全体を 1 つの team unit にする (generic)
- LLM は呼ばない (名前の対応は別の段で、送信の方針が allow のホストだけ)
取得の関数 (fetcher) は注入できる (テストはネットワークに出ない)。
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import time
import urllib.request
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

from champions_agent.config import BUILD_ARTICLE_FETCH_MIN_INTERVAL_S, BUILD_ARTICLE_FETCH_TIMEOUT_S
from tools.team_build.adapters import host_adapters
from tools.team_build.article_aliases import DEFAULT_PATH as ALIASES_PATH
from tools.team_build.article_aliases import DEFAULT_SITE_IDS_PATH, SiteIdStore, load_aliases, save_aliases
from tools.team_build.article_bank import DEFAULT_BANK_DIR, host_allowed, host_policy_entry, load_bank, save_bank
from tools.team_build.article_parse import canonical_host, default_dictionary
from tools.team_build.article_units import decode_html, units_for, url_hash
from tools.team_build.articles_ingest import USER_AGENT
from tools.team_build.articles_process import DEFAULT_STATE_PATH, append_state, process_batch

REPO = Path(__file__).resolve().parent.parent.parent
DEFAULT_POLICY_PATH = REPO / "logs" / "articles" / "host_policy.json"
Fetcher = Callable[[str], tuple]          # url -> (body: bytes, content_type: str | None)


def load_policy(path: Path = DEFAULT_POLICY_PATH) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def fetched_url_hashes(state_path: Path = DEFAULT_STATE_PATH) -> set:
    """state.jsonl にある url_hash (以前に取得した URL)"""
    p = Path(state_path)
    if not p.exists():
        return set()
    out = set()
    for ln in p.read_text(encoding="utf-8").splitlines():
        if ln.strip():
            uh = json.loads(ln).get("url_hash")
            if uh:
                out.add(uh)
    return out


def default_fetcher(timeout: int = BUILD_ARTICLE_FETCH_TIMEOUT_S) -> Fetcher:
    def fetch(url: str) -> tuple:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read(), resp.headers.get("Content-Type")
    return fetch


def plan_urls(urls: list, policy: dict, fetched: set, refetch: bool = False) -> list:
    """URL の列 → [{"url", "host", "url_hash", "action": fetch | skip, "reason"}] (純粋)。方針に通らない URL と、取り直さない URL を skip"""
    out = []
    seen = set()
    for u in urls:
        u = (u or "").strip()
        if not u:
            continue
        host = canonical_host(urlparse(u).netloc)
        uh = url_hash(u)
        row = {"url": u, "host": host, "url_hash": uh}
        if uh in seen:
            row.update(action="skip", reason="duplicate")
        elif not host_allowed(policy, host, "fetch", url=u):
            row.update(action="skip", reason="host_or_url_not_allowed")
        elif uh in fetched and not refetch:
            row.update(action="skip", reason="already_fetched")
        else:
            row.update(action="fetch", reason="")
        seen.add(uh)
        out.append(row)
    return out


def fetch_units(plan: list, policy: dict, fetcher: Optional[Fetcher] = None, sleeper: Callable[[float], None] = time.sleep,
                today: Optional[str] = None, adapters: Optional[dict] = None) -> tuple:
    """計画の fetch 行を 1 本ずつ取得して unit に直す。同じホストへの要求の間は min_interval_s 以上空ける。
    → (unit の列, 取得の結果の行 [{"url_hash", "host", "status", "n_units", "body_chars"}])。本文は unit の中だけ (戻り値に生の HTML を入れない)"""
    fetcher = fetcher or default_fetcher()
    adapters = host_adapters() if adapters is None else adapters
    today = today or datetime.date.today().isoformat()
    units: list = []
    results: list = []
    last_at: dict = {}
    for row in plan:
        if row["action"] != "fetch":
            continue
        host = row["host"]
        entry = host_policy_entry(policy, host)
        interval = float(entry.get("min_interval_s", BUILD_ARTICLE_FETCH_MIN_INTERVAL_S))
        if host in last_at:
            wait = interval - (time.monotonic() - last_at[host])
            if wait > 0:
                sleeper(wait)
        try:
            body, ctype = fetcher(row["url"])
        except Exception as e:                                  # 取得の失敗: 本文も URL の中身も文言に入れない
            results.append({"url_hash": row["url_hash"], "host": host, "status": f"fetch_error:{type(e).__name__}", "n_units": 0, "body_chars": 0})
            last_at[host] = time.monotonic()
            continue
        last_at[host] = time.monotonic()
        html_text = decode_html(body, ctype)
        body_hash = hashlib.sha256(html_text.encode("utf-8")).hexdigest()[:16]
        source = {"url": row["url"], "host": host, "url_hash": row["url_hash"], "body_hash": body_hash, "fetched_at": today}
        page_units = units_for(host, html_text, source=source, meta={}, adapters=adapters)
        units.extend(page_units)
        results.append({"url_hash": row["url_hash"], "host": host, "status": "fetched", "n_units": len(page_units), "body_chars": len(html_text)})
    return units, results


def run(urls: list, policy: dict, fetcher: Optional[Fetcher] = None, state_path: Path = DEFAULT_STATE_PATH, bank_dir: Path = DEFAULT_BANK_DIR,
        base_version: Optional[str] = None, aliases_path: Path = ALIASES_PATH, site_ids_path: Path = DEFAULT_SITE_IDS_PATH,
        dry_run: bool = False, refetch: bool = False, sleeper: Callable[[float], None] = time.sleep, today: Optional[str] = None,
        adapters: Optional[dict] = None) -> dict:
    """取得 → unit → process_batch → 保存 (dry_run なら何も書かない)。→ {"plan", "fetch_results", "batch_counts", "records", "saved_to", "state_rows"}"""
    today = today or datetime.date.today().isoformat()
    plan = plan_urls(urls, policy, fetched_url_hashes(state_path), refetch)
    units, fetch_results = fetch_units(plan, policy, fetcher, sleeper, today, adapters)
    dic = default_dictionary()
    aliases = load_aliases(aliases_path)
    store = SiteIdStore.load(site_ids_path)
    res = process_batch(units, dic, aliases, store, llm_resolver=None, policy=policy, today=today)
    saved_to = None
    if not dry_run:
        if res["records"]:
            cases = list(load_bank(base_version, bank_dir)) if base_version else []
            saved_to = save_bank(cases + res["records"], bank_dir)
        if res["state_rows"]:
            append_state([r for r in res["state_rows"] if r], state_path)
        if res["alias_entries"]["confirmed"] or res["alias_entries"]["candidate"]:
            save_aliases(res["aliases"], aliases_path)
        res["site_store"].save(site_ids_path)
    units.clear()                                               # 本文の破棄
    return {"plan": plan, "fetch_results": fetch_results, "batch_counts": res["counts"], "records": res["records"],
            "saved_to": str(saved_to) if saved_to else None, "state_rows": [r for r in res["state_rows"] if r],
            "unresolved_names": res["unresolved_names"], "alias_entries": res["alias_entries"]}


def _summary_lines(out: dict) -> list:
    lines = []
    for p in out["plan"]:
        lines.append(f"{p['action']:<5} {p['reason'] or 'ok':<26} {p['url']}")
    for r in out["fetch_results"]:
        lines.append(f"取得 {r['status']} host={r['host']} units={r['n_units']} chars={r['body_chars']}")
    for rec in out["records"]:
        meta = rec.get("meta") or {}
        members = ", ".join(m["species_id"] for m in rec["members"])
        lines.append(f"記録 {rec['case_id']} {rec['record_kind']} status={rec['status']} reg={meta.get('regulation')} "
                     f"team_code={meta.get('team_code')} members=[{members}] rules={len(rec['selection_rules'])} claims={len(rec['claims'])}")
    lines.append(f"counts: {json.dumps(out['batch_counts'], ensure_ascii=False, sort_keys=True)}")
    lines.append(f"未解決の名前: {len(out['unresolved_names'])} 件、別名 confirmed {len(out['alias_entries']['confirmed'])} / candidate {len(out['alias_entries']['candidate'])}")
    lines.append(f"保存: {out['saved_to'] or '(なし)'}")
    return lines


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="許可した URL だけを取得して記事バンクに入れる (巡回しない・並列しない・本文を保存しない)")
    ap.add_argument("--url", action="append", required=True, help="取得する URL (複数可)。方針で許可された URL だけが取得される")
    ap.add_argument("--policy", default=str(DEFAULT_POLICY_PATH))
    ap.add_argument("--state", default=str(DEFAULT_STATE_PATH))
    ap.add_argument("--bank-dir", default=str(DEFAULT_BANK_DIR))
    ap.add_argument("--base-version", help="この版の記録に足して新しい版を作る")
    ap.add_argument("--aliases", default=str(ALIASES_PATH))
    ap.add_argument("--site-ids", default=str(DEFAULT_SITE_IDS_PATH))
    ap.add_argument("--dry-run", action="store_true", help="取得と解析だけ行い、何も書かない")
    ap.add_argument("--refetch", action="store_true", help="以前に取得した URL も取り直す (必要な更新時だけ)")
    args = ap.parse_args(argv)
    policy = load_policy(Path(args.policy))
    out = run(args.url, policy, state_path=Path(args.state), bank_dir=Path(args.bank_dir), base_version=args.base_version,
              aliases_path=Path(args.aliases), site_ids_path=Path(args.site_ids), dry_run=args.dry_run, refetch=args.refetch)
    for ln in _summary_lines(out):
        print(ln)
    return 0 if any(p["action"] == "fetch" for p in out["plan"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
