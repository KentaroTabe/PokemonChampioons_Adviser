"""許可した URL だけを取得して記事バンクに入れる (docs/ARTICLE_BANK_DESIGN_1006.md §3.12)。

    python -m tools.team_build.articles_fetch --url <URL> [--url ...] [--dry-run] [--refetch] [--policy ...] [--state ...] [--bank-dir ...]
                                              [--base-version V] [--aliases ...] [--site-ids ...]

規則 (2026-10-06 ユーザー判断):
- ホストの方針 (logs/articles/host_policy.json) で fetch が allow、かつ allowed_urls があればその URL のときだけ取得する (host_allowed)。
  方針に無いホスト・unknown は取得しない。巡回しない (与えられた URL だけ。ページ内のリンクを辿らない)
- 並列取得をしない (1 本ずつ)。同じホストへの要求の間は方針の min_interval_s (無ければ BUILD_ARTICLE_FETCH_MIN_INTERVAL_S) 以上空ける
- 再取得は必要な更新時だけ: 以前に取得した URL (state.jsonl か access.jsonl に url_hash がある。dry-run のアクセスも数える) は
  --refetch を付けたときだけ取り直す。照合のやり直しは --dry-run が書いた構造化した候補 (candidates/) で行い、保存は
  --from-candidates で再取得なしに行う (2026-10-06 ユーザー判断: 確認のたびの再取得を減らす)
- 本文 (HTML・リンクつきの本文) はメモリの中だけで扱い、ディスクに書かない。保存するのは構造化した記録 (バンク)、処理状態 (state.jsonl)、
  取得履歴 (access.jsonl: 日時・URL・ハッシュ・回数)、構造化した候補 (candidates/)、別名辞書・サイト固有 id の観測 (ローカル) だけ。
  例外の文言にも本文を入れない
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
DEFAULT_ACCESS_PATH = REPO / "logs" / "articles" / "access.jsonl"          # 取得履歴 (dry-run を含む全部のアクセス。本文なし)
DEFAULT_CANDIDATES_DIR = REPO / "logs" / "articles" / "candidates"         # 取得から作った構造化した候補 (照合用。本文なし)
Fetcher = Callable[[str], tuple]          # url -> (body: bytes, content_type: str | None)
ACCESS_ROW_KEYS = ("at", "url", "url_hash", "host", "mode", "status", "body_hash", "body_chars", "n_units", "count")


def load_policy(path: Path = DEFAULT_POLICY_PATH) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _jsonl_url_hashes(path: Path) -> set:
    p = Path(path)
    if not p.exists():
        return set()
    out = set()
    for ln in p.read_text(encoding="utf-8").splitlines():
        if ln.strip():
            uh = json.loads(ln).get("url_hash")
            if uh:
                out.add(uh)
    return out


def fetched_url_hashes(state_path: Path = DEFAULT_STATE_PATH, access_path: Path = DEFAULT_ACCESS_PATH) -> set:
    """以前に取得した URL の url_hash: state.jsonl (保存した処理) と access.jsonl (dry-run を含む全部のアクセス) の両方
    (2026-10-06 ユーザー判断: dry-run のアクセスも取得履歴に数え、再取得の防止を効かせる)"""
    return _jsonl_url_hashes(state_path) | _jsonl_url_hashes(access_path)


def append_access(rows: list, path: Path = DEFAULT_ACCESS_PATH) -> Path:
    """取得履歴に追記する (本文なし。ACCESS_ROW_KEYS 以外の項目は書かない)"""
    for r in rows:
        if set(r) - set(ACCESS_ROW_KEYS):
            raise ValueError("access 行に知らない項目がある")
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    return p


def save_candidates(url_hash_: str, payload: dict, out_dir: Path = DEFAULT_CANDIDATES_DIR) -> Path:
    """取得から作った構造化した候補 (記録・state 行・件数) を照合用に保存する (本文なし: assert_no_prose を通す)。
    次の確認・保存は --from-candidates でこのファイルを使い、再取得しない"""
    from tools.team_build.article_bank import assert_no_prose
    assert_no_prose(payload.get("records") or [], "$.records")
    assert_no_prose(payload.get("state_rows") or [], "$.state_rows")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{url_hash_}_{payload.get('fetched_at', 'na')}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return path


def load_candidates(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


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
        adapters: Optional[dict] = None, access_path: Path = DEFAULT_ACCESS_PATH, candidates_dir: Path = DEFAULT_CANDIDATES_DIR,
        now: Optional[str] = None) -> dict:
    """取得 → unit → process_batch → 保存。dry_run でもアクセスは取得履歴 (access.jsonl) に残し、構造化した候補 (candidates/) を書く
    (本文は書かない。2026-10-06 ユーザー判断: 確認のたびの再取得を減らす)。バンク・state・別名・観測は dry_run でなければ書く。
    → {"plan", "fetch_results", "batch_counts", "records", "saved_to", "state_rows", "candidates_paths", ...}"""
    today = today or datetime.date.today().isoformat()
    now = now or datetime.datetime.now().isoformat(timespec="seconds")
    plan = plan_urls(urls, policy, fetched_url_hashes(state_path, access_path), refetch)
    units, fetch_results = fetch_units(plan, policy, fetcher, sleeper, today, adapters)
    dic = default_dictionary()
    aliases = load_aliases(aliases_path)
    store = SiteIdStore.load(site_ids_path)
    res = process_batch(units, dic, aliases, store, llm_resolver=None, policy=policy, today=today)
    # 取得履歴 (dry-run も含む)。本文なし
    url_of = {p["url_hash"]: p["url"] for p in plan}
    body_hash_of = {(u.get("source") or {}).get("url_hash"): (u.get("source") or {}).get("body_hash") for u in units}
    append_access([{"at": now, "url": url_of.get(r["url_hash"]), "url_hash": r["url_hash"], "host": r["host"],
                    "mode": "dry_run" if dry_run else "save", "status": r["status"], "body_hash": body_hash_of.get(r["url_hash"]),
                    "body_chars": r["body_chars"], "n_units": r["n_units"], "count": 1} for r in fetch_results], access_path)
    # 構造化した候補 (照合用。--from-candidates で再取得なしに保存できる)
    candidates_paths = []
    for r in fetch_results:
        if r["status"] != "fetched":
            continue
        recs = [rec for rec in res["records"] if (rec.get("source") or {}).get("url_hash") == r["url_hash"]]
        rows = [row for row in res["state_rows"] if row and row.get("url_hash") == r["url_hash"]]
        payload = {"fetched_at": today, "at": now, "url": url_of.get(r["url_hash"]), "url_hash": r["url_hash"], "host": r["host"],
                   "body_hash": body_hash_of.get(r["url_hash"]), "records": recs, "state_rows": rows,
                   "counts": {k: v for k, v in res["counts"].items() if not str(k).startswith("llm_")}, "saved": not dry_run}
        candidates_paths.append(str(save_candidates(r["url_hash"], payload, candidates_dir)))
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
            "unresolved_names": res["unresolved_names"], "alias_entries": res["alias_entries"], "candidates_paths": candidates_paths}


def save_from_candidates(paths: list, bank_dir: Path = DEFAULT_BANK_DIR, base_version: Optional[str] = None,
                         state_path: Path = DEFAULT_STATE_PATH, only_statuses: tuple = ("ok", "warnings", "incomplete")) -> dict:
    """保存済みの構造化した候補 (dry-run の出力) をバンクに入れる (再取得しない)。conflict / failed の記録は入れない。
    → {"saved_to", "n_records", "skipped"}"""
    records, rows, skipped = [], [], []
    for p in paths:
        c = load_candidates(Path(p))
        for rec in c.get("records") or []:
            if rec.get("status") in only_statuses:
                records.append(rec)
            else:
                skipped.append({"case_id": rec.get("case_id"), "status": rec.get("status")})
        rows.extend(r for r in (c.get("state_rows") or []) if r)
    saved_to = None
    if records:
        cases = list(load_bank(base_version, bank_dir)) if base_version else []
        saved_to = save_bank(cases + records, bank_dir)
        if rows:
            append_state(rows, state_path)
    return {"saved_to": str(saved_to) if saved_to else None, "n_records": len(records), "skipped": skipped}


def _rule_ja(rule: dict, members: dict) -> str:
    """選出規則 1 つの日本語の短い表示 (名前は表からの逆引き)"""
    from advisor.ja_names import species_ja
    names = [species_ja(members.get(m, m)) for m in rule.get("selected_members") or []]
    names += [species_ja(s.get("species_id")) for s in rule.get("selected_species") or []]
    lead = rule.get("lead") or rule.get("lead_species")
    lead_ja = species_ja(members.get(lead, lead)) if lead else None
    cond = rule.get("condition")
    cond_s = ""
    if cond:
        preds = [c.get("predicate") for c in (cond.get("any_of") or cond.get("all_of") or [cond])]
        cond_s = f" 条件={'/'.join(str(p) for p in preds)}({cond.get('evaluation')})"
    return (f"{rule.get('recommendation')}{cond_s}: {'・'.join(names)}" + (f" 初手={lead_ja}" if lead_ja else "")
            + (" (例示)" if rule.get("example") else "") + (f" 自由枠{rule['free_slots']}" if rule.get("free_slots") else ""))


def _summary_lines(out: dict, ja: bool = False) -> list:
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
        if ja:
            from advisor.ja_names import set_line_ja
            id_map = {m["id"]: m["species_id"] for m in rec["members"]}
            for m in rec["members"]:
                lines.append("    " + set_line_ja(m))
            for r in rec["selection_rules"]:
                lines.append("    選出: " + _rule_ja(r, id_map))
            if rec.get("problems"):
                lines.append("    問題: " + ", ".join(rec["problems"]))
    lines.append(f"counts: {json.dumps(out['batch_counts'], ensure_ascii=False, sort_keys=True)}")
    lines.append(f"未解決の名前: {len(out['unresolved_names'])} 件、別名 confirmed {len(out['alias_entries']['confirmed'])} / candidate {len(out['alias_entries']['candidate'])}")
    for rec in out["records"]:
        checks = rec.get("checks") or {}
        if checks:
            lines.append(f"検算 {rec['case_id']}: 実数値 {json.dumps(checks.get('actual'), sort_keys=True)} 通常形態 {json.dumps(checks.get('actual_base_form'), sort_keys=True)}")
    for p in out.get("candidates_paths") or []:
        lines.append(f"候補: {p}")
    lines.append(f"保存: {out['saved_to'] or '(なし)'}")
    return lines


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="許可した URL だけを取得して記事バンクに入れる (巡回しない・並列しない・本文を保存しない)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--url", action="append", help="取得する URL (複数可)。方針で許可された URL だけが取得される")
    g.add_argument("--from-candidates", nargs="+", metavar="FILE", help="dry-run で保存した構造化した候補をバンクに入れる (再取得しない)")
    ap.add_argument("--policy", default=str(DEFAULT_POLICY_PATH))
    ap.add_argument("--state", default=str(DEFAULT_STATE_PATH))
    ap.add_argument("--access", default=str(DEFAULT_ACCESS_PATH), help="取得履歴 (dry-run を含む。本文なし)")
    ap.add_argument("--candidates-dir", default=str(DEFAULT_CANDIDATES_DIR), help="構造化した候補の保存先 (照合用。本文なし)")
    ap.add_argument("--bank-dir", default=str(DEFAULT_BANK_DIR))
    ap.add_argument("--base-version", help="この版の記録に足して新しい版を作る")
    ap.add_argument("--aliases", default=str(ALIASES_PATH))
    ap.add_argument("--site-ids", default=str(DEFAULT_SITE_IDS_PATH))
    ap.add_argument("--dry-run", action="store_true", help="取得と解析だけ行う (バンク・state・別名・観測は書かない。取得履歴と候補は書く)")
    ap.add_argument("--refetch", action="store_true", help="以前に取得した URL も取り直す (必要な更新時だけ)")
    ap.add_argument("--ja", action="store_true", help="記録の型と選出規則を日本語名で表示する (人の照合用。名前は表からの逆引き)")
    args = ap.parse_args(argv)
    if args.from_candidates:
        res = save_from_candidates(args.from_candidates, Path(args.bank_dir), args.base_version, Path(args.state))
        print(f"候補から保存: 記録 {res['n_records']} 件、入れなかった記録 {len(res['skipped'])} 件 {res['skipped'] or ''}")
        print(f"保存: {res['saved_to'] or '(なし)'}")
        return 0 if res["saved_to"] else 1
    policy = load_policy(Path(args.policy))
    out = run(args.url, policy, state_path=Path(args.state), bank_dir=Path(args.bank_dir), base_version=args.base_version,
              aliases_path=Path(args.aliases), site_ids_path=Path(args.site_ids), dry_run=args.dry_run, refetch=args.refetch,
              access_path=Path(args.access), candidates_dir=Path(args.candidates_dir))
    for ln in _summary_lines(out, ja=args.ja):
        print(ln)
    return 0 if any(p["action"] == "fetch" for p in out["plan"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
