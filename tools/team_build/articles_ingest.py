"""記事バンクの前段 (docs/TEAM_BUILD_PLAN_1005.md §6 / Phase 4): 上位プレイヤーのブログ一覧 (CSV) → 取得対象の一覧 (manifest)。

本文の取得と LLM の抽出はしない (次の段)。ここでやるのは決定的な整形だけ:
  1. 確度 A / B の行を選ぶ (要追確認は --include-unconfirmed)
  2. 根拠記事 URL を分割 (空白区切り)、追跡クエリ (?app_launch 等) と末尾の / を落として重複を除く
  3. 題名から形式を分類 (ダブル / シングル / 不明。BUILD_ARTICLE_DOUBLE_WORDS)
  4. シーズンを正規化 (M-1 … M-6、MCS2026.05 …、マンスリーチャレンジ) し、規制 id に対応づける (BUILD_ARTICLE_SEASON_REGULATION、無ければ unknown)
  5. 順位 (「最終NNN位」の最小) を取る
  6. --check-robots なら host ごとに robots.txt を読み、取得して良い URL かを記録する (取得はしない)

    python -m tools.team_build.articles_ingest --csv logs/articles/blogs.csv [--include-unconfirmed] [--check-robots] [--out ...]
純粋関数 (parse / split_urls / classify_format / seasons_of / build_manifest) は tests/test_articles_ingest.py。
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
from collections import Counter
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, urlunparse

from champions_agent.config import BUILD_ARTICLE_DOUBLE_WORDS, BUILD_ARTICLE_SEASON_REGULATION

REPO = Path(__file__).resolve().parent.parent.parent
DEFAULT_CSV = REPO / "logs" / "articles" / "blogs.csv"
DEFAULT_OUT = REPO / "logs" / "articles" / "manifest.json"
USER_AGENT = "PokemonChampionsAdviser-articles/1.0"
COLUMNS = {"confidence": "確度", "round2": "第2ラウンド", "author": "ブログ/著者", "platform": "プラットフォーム", "blog_url": "ブログURL",
           "evidence": "根拠文言", "article_urls": "根拠記事URL", "title": "記事タイトル", "season": "シーズン", "note": "留保事項",
           "source": "出典担当"}


def parse_rows(text: str) -> list:
    """CSV の本文 → 行の辞書 (英語キー)。BOM は落とす"""
    rd = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    out = []
    for r in rd:
        row = {k: (r.get(v) or "").strip() for k, v in COLUMNS.items()}
        if any(row.values()):
            out.append(row)
    return out


def normalize_url(u: str) -> str:
    """追跡クエリと断片、末尾の / を落とす (重複排除用。note の ?app_launch=false 等)"""
    u = (u or "").strip()
    if not u:
        return ""
    p = urlparse(u)
    path = p.path.rstrip("/") or "/"
    return urlunparse((p.scheme.lower() or "https", p.netloc.lower(), path, "", "", ""))


def split_urls(s: str) -> list:
    """空白・改行・カンマで区切られた URL の列 → 正規化した一意の列 (順序保持)"""
    out: list = []
    for tok in re.split(r"[\s,]+", s or ""):
        if not tok.startswith("http"):
            continue
        n = normalize_url(tok)
        if n and n not in out:
            out.append(n)
    return out


def classify_format(title: str, words=BUILD_ARTICLE_DOUBLE_WORDS) -> str:
    """題名から形式: ダブルの語があれば double、題名が無ければ unknown、それ以外 single"""
    t = (title or "").lower()
    if not t:
        return "unknown"
    return "double" if any(w.lower() in t for w in words) else "single"


def seasons_of(s: str) -> list:
    """"M-2,M-3" / "M-3,マンスリーチャレンジ,MCS2026.06,MCS 2026.06" → ["M-2", "M-3"] / ["M-3", "MCS2026.06"] (正規化、重複除去)"""
    out: list = []
    for tok in re.split(r"[,、/]+", s or ""):
        tok = tok.strip()
        if not tok:
            continue
        m = re.fullmatch(r"M-(\d)", tok)
        if m:
            key = f"M-{m.group(1)}"
        else:
            m2 = re.fullmatch(r"MCS\s*(\d{4})\.(\d{2})", tok)
            if m2:
                key = f"MCS{m2.group(1)}.{m2.group(2)}"
            elif "マンスリー" in tok:
                key = "MCS"
            else:
                key = tok
        if key not in out:
            out.append(key)
    if "MCS" in out and any(k.startswith("MCS2") for k in out):
        out.remove("MCS")                     # 月が分かる MCS があれば総称は落とす
    return out


def regulation_of(seasons: list, table: dict = BUILD_ARTICLE_SEASON_REGULATION) -> dict:
    return {s: table.get(s, "unknown") for s in seasons}


def rank_of(text: str) -> Optional[int]:
    """「最終101位 / 最終286位」→ 101 (最小)"""
    nums = [int(x) for x in re.findall(r"(\d+)\s*位", text or "")]
    return min(nums) if nums else None


def build_manifest(rows: list, include_unconfirmed: bool = False) -> dict:
    """行 → manifest (純粋)。1 URL 1 entry。同じ URL が複数行にあれば最初の行"""
    sel = [r for r in rows if r["confidence"] in ("A", "B") or (include_unconfirmed and r["confidence"])]
    entries: list = []
    seen: set = set()
    n_rows_with_url = 0
    for r in sel:
        urls = split_urls(r["article_urls"])
        if urls:
            n_rows_with_url += 1
        seasons = seasons_of(r["season"])
        for u in urls:
            if u in seen:
                continue
            seen.add(u)
            entries.append({"url": u, "host": urlparse(u).netloc, "author": r["author"], "platform": r["platform"],
                            "confidence": r["confidence"], "title": r["title"], "format": classify_format(r["title"]),
                            "seasons": seasons, "regulation": regulation_of(seasons), "rank": rank_of(r["evidence"]),
                            "note": r["note"], "robots": "unchecked", "selected": classify_format(r["title"]) != "double"})
    fmt = Counter(e["format"] for e in entries)
    by_season: Counter = Counter()
    for e in entries:
        for s in e["seasons"] or ["(不明)"]:
            by_season[s] += 1
    ranks = sorted(e["rank"] for e in entries if e["rank"] is not None)
    return {"n_rows": len(rows), "n_selected_rows": len(sel), "n_rows_with_url": n_rows_with_url, "n_urls": len(entries),
            "n_double": fmt.get("double", 0), "n_single": fmt.get("single", 0), "n_unknown_format": fmt.get("unknown", 0),
            "n_to_fetch": sum(1 for e in entries if e["selected"]),
            "n_regulation_unknown": sum(1 for e in entries if e["selected"] and (not e["seasons"] or any(v == "unknown" for v in e["regulation"].values()))),
            "by_season": dict(sorted(by_season.items())), "by_platform": dict(Counter(e["platform"] or "(不明)" for e in entries)),
            "by_confidence": dict(Counter(e["confidence"] for e in entries)),
            "rank_median": (ranks[len(ranks) // 2] if ranks else None), "n_rank_le_100": sum(1 for x in ranks if x <= 100),
            "entries": entries}


def check_robots(entries: list, user_agent: str = USER_AGENT, timeout: float = 10.0, log=print) -> dict:
    """host ごとに robots.txt を読み、entry["robots"] = allowed / disallowed / unknown。取得はしない"""
    import urllib.robotparser as rp
    import urllib.request
    cache: dict = {}
    for e in entries:
        host = e["host"]
        if host not in cache:
            parser = rp.RobotFileParser()
            try:
                req = urllib.request.Request(f"https://{host}/robots.txt", headers={"User-Agent": user_agent})
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    body = resp.read().decode("utf-8", errors="replace")
                parser.parse(body.splitlines())
                cache[host] = parser
            except Exception as ex:
                log(f"robots {host}: {ex!r}")
                cache[host] = None
        parser = cache[host]
        if parser is None:
            e["robots"] = "unknown"
        else:
            e["robots"] = "allowed" if parser.can_fetch(user_agent, e["url"]) else "disallowed"
            if e["robots"] == "disallowed":
                e["selected"] = False
    return {h: ("ok" if p is not None else "unreadable") for h, p in cache.items()}


def main() -> None:
    ap = argparse.ArgumentParser(description="記事バンクの前段: ブログ一覧 → 取得対象の manifest")
    ap.add_argument("--csv", default=str(DEFAULT_CSV))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--include-unconfirmed", action="store_true", help="要追確認の行も含める (2 段目)")
    ap.add_argument("--check-robots", action="store_true", help="host ごとに robots.txt を読んで取得可否を記録する (本文は取得しない)")
    args = ap.parse_args()
    rows = parse_rows(Path(args.csv).read_text(encoding="utf-8"))
    man = build_manifest(rows, include_unconfirmed=args.include_unconfirmed)
    if args.check_robots:
        man["robots_hosts"] = check_robots(man["entries"])
        man["n_to_fetch"] = sum(1 for e in man["entries"] if e["selected"])
    man["source_csv"] = str(args.csv)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(man, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"行 {man['n_rows']} (選んだ行 {man['n_selected_rows']}、URL あり {man['n_rows_with_url']}) → 記事 URL {man['n_urls']} "
          f"(ダブル {man['n_double']} / シングル {man['n_single']} / 不明 {man['n_unknown_format']})、取得対象 {man['n_to_fetch']}、"
          f"規制が不明 {man['n_regulation_unknown']}、順位の中央値 {man['rank_median']} (100 位以内 {man['n_rank_le_100']})")
    print(f"シーズン別 {man['by_season']}")
    print(f"保存: {args.out}")


if __name__ == "__main__":
    main()
