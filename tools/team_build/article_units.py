"""記事のページ → 構築ごとの配列 (unit)、文字コード、規制名 (docs/ARTICLE_BANK_DESIGN_1006.md §3.9)。HTTP はしない (取得はホストの許可待ち)。

unit = {"kind": "team" | "single_set", "marked": <リンクつきの本文>, "meta": {...}, "source": {...},
        "members_named_only": [{"species_id", "base_species_id", "mega_stone"}]}
- 変換層の出力は「区切り線つきの文章」ではなく構築ごとの配列。1 ページに複数の構築・単体の型があれば unit を分け、
  解析器は unit ごとに parse_article を呼ぶ (parse_units)。unit の本文はメモリ上だけで扱い、記録には入れない
- members_named_only: 型は載っていないが種名だけ分かる個体 (変換層がページの構造から渡す。既定は空)。解析器は本文から作らない。
  記録では members とは別の項目に置き、個体 id を付けない (選出紹介の和集合から 6 体を補完しない)
- ホスト固有の変換層は ADAPTERS (ホスト → 関数) に登録する。取得可否とページ構造を確認してから足す (今回は登録口だけ)。
  登録の無いホストは generic_units (ページ全体を 1 つの team unit にする)
- 文字コード (decode_html): HTTP ヘッダの charset → HTML の <meta charset> / <meta http-equiv="Content-Type"> → 既定 UTF-8
  (errors="replace")。URL の文字コード (EUC-JP の %-表現など) から本文の文字コードを決めない
- URL: 既存のパーセント表現は変換しない。正規化 (articles_ingest.normalize_url) は重複排除の鍵 (url_hash) にだけ使い、
  取得には元の URL を使う
- 規制 (regulation_from_text): 記事固有の記載 (題名・タグ) の「M-C」等 → 規制 id (BUILD_ARTICLE_REGULATION_NAMES)
純粋関数。テストは tests/test_article_units.py。
"""
from __future__ import annotations

import codecs
import hashlib
import re
import unicodedata
from typing import Callable, Optional

from champions_agent.config import (BUILD_ARTICLE_CHARSET_ALIASES, BUILD_ARTICLE_RECORD_KIND_MEMBERS, BUILD_ARTICLE_REGULATION_DASHES,
                                    BUILD_ARTICLE_REGULATION_NAMES)
from tools.team_build.article_parse import ArticleDictionary, canonical_host, html_to_marked_text, normalize_named_only, parse_article
from tools.team_build.articles_ingest import normalize_url

# ホスト (canonical_host の形) → 変換層 (html_text, source, meta) -> [unit]。取得可否とページ構造を確認してから登録する
ADAPTERS: dict = {}
_CHARSET_RE = re.compile(r"""charset\s*=\s*["']?\s*([A-Za-z0-9._:\-]+)""", re.I)
_META_TAG_RE = re.compile(rb"<meta\b[^>]*>", re.I)
_HEAD_END_RE = re.compile(rb"</head\s*>", re.I)
_DEFAULT_CODEC = "utf-8"


def make_unit(kind: str, marked: str, meta: Optional[dict] = None, source: Optional[dict] = None,
              members_named_only: Optional[list] = None) -> dict:
    """unit を作る (kind は BUILD_ARTICLE_RECORD_KIND_MEMBERS の種類だけ)。members_named_only は article_parse.normalize_named_only で
    形を検査する (種 id だけ。形が違えば ValueError)"""
    if kind not in BUILD_ARTICLE_RECORD_KIND_MEMBERS:
        raise ValueError(f"unit の kind が表に無い値 (許可: {', '.join(BUILD_ARTICLE_RECORD_KIND_MEMBERS)})")
    return {"kind": kind, "marked": marked or "", "meta": dict(meta or {}), "source": dict(source or {}),
            "members_named_only": normalize_named_only(members_named_only)}


def generic_units(html_text: str, source: Optional[dict] = None, meta: Optional[dict] = None) -> list:
    """ホスト固有の変換層が無いときの既定: ページ全体を html_to_marked_text で 1 つの team unit にする"""
    return [make_unit("team", html_to_marked_text(html_text), meta, source)]


def default_adapters() -> dict:
    """登録された変換層 (tools/team_build/adapters.host_adapters + このモジュールの ADAPTERS)。循環 import を避けて呼び出し時に読む"""
    from tools.team_build.adapters import host_adapters
    table = dict(host_adapters())
    table.update(ADAPTERS)
    return table


def units_for(host: Optional[str], html_text: str, source: Optional[dict] = None, meta: Optional[dict] = None,
              adapters: Optional[dict] = None) -> list:
    """ホストの変換層 (default_adapters。無ければ generic_units) でページ → unit の列。adapters はテスト用の差し替え"""
    table = default_adapters() if adapters is None else adapters
    adapter: Optional[Callable] = table.get(canonical_host(host) or "")
    units = adapter(html_text, source, meta) if adapter else generic_units(html_text, source, meta)
    for u in units:
        if u.get("kind") not in BUILD_ARTICLE_RECORD_KIND_MEMBERS:
            raise ValueError("変換層の unit の kind が表に無い値")
    return units


def parse_units(units: list, dic: Optional[ArticleDictionary] = None) -> list:
    """unit ごとに parse_article (個体の上限は種類ごと: team = 6、single_set = 1。種名だけ分かる個体は unit から渡す) → [(unit, parsed)]"""
    out = []
    for u in units:
        kind = u.get("kind")
        if kind not in BUILD_ARTICLE_RECORD_KIND_MEMBERS:
            raise ValueError("unit の kind が表に無い値")
        parsed = parse_article(u.get("marked") or "", dic, max_members=BUILD_ARTICLE_RECORD_KIND_MEMBERS[kind],
                               host=(u.get("source") or {}).get("host"), members_named_only=u.get("members_named_only"))
        out.append((u, parsed))
    return out


def url_hash(url: str) -> str:
    """重複排除の鍵: articles_ingest.normalize_url (追跡クエリ・断片・末尾の / を落とす) のハッシュ。
    正規化した URL は取得に使わない (取得は元の URL。既存のパーセント表現も変えない)"""
    return hashlib.sha256(normalize_url(url).encode("utf-8")).hexdigest()[:16]


def charset_from_content_type(content_type: Optional[str]) -> Optional[str]:
    """HTTP の Content-Type ヘッダの charset (無ければ None)"""
    m = _CHARSET_RE.search(content_type or "")
    return m.group(1) if m else None


def charset_from_meta(body: bytes) -> Optional[str]:
    """HTML の <meta charset="..."> / <meta http-equiv="Content-Type" content="...; charset=..."> (</head> より前の meta 要素だけ見る)"""
    data = body or b""
    end = _HEAD_END_RE.search(data)
    region = data[:end.start()] if end else data
    for m in _META_TAG_RE.finditer(region):
        cm = _CHARSET_RE.search(m.group(0).decode("ascii", errors="ignore"))
        if cm:
            return cm.group(1)
    return None


def codec_name(charset: Optional[str]) -> Optional[str]:
    """charset 名 → Python の codec 名 (BUILD_ARTICLE_CHARSET_ALIASES で Shift_JIS 等を読み替え)。不明なら None"""
    if not charset:
        return None
    name = charset.strip().strip("\"'").lower()
    name = BUILD_ARTICLE_CHARSET_ALIASES.get(name, name)
    try:
        return codecs.lookup(name).name
    except LookupError:
        return None


def decode_html(body: bytes, content_type: Optional[str] = None) -> str:
    """取得した本文のバイト列 → 文字列。文字コードは HTTP ヘッダの charset → HTML の meta (charset / http-equiv) → 既定 UTF-8 の順
    (どれも errors="replace" で読む。読めない名前は次の手段へ)。URL の文字コード (EUC-JP の %-表現など) から本文の文字コードを
    決めない。URL の既存のパーセント表現は変換しない (正規化は articles_ingest.normalize_url を重複排除の鍵にだけ使い、
    取得には元の URL を使う)"""
    data = body or b""
    codec = codec_name(charset_from_content_type(content_type)) or codec_name(charset_from_meta(data)) or _DEFAULT_CODEC
    # UTF-8 は先頭の BOM を落として読む (utf-8-sig は BOM が無ければ utf-8 と同じ)
    return data.decode("utf-8-sig" if codec == _DEFAULT_CODEC else codec, errors="replace")


def _regulation_patterns() -> list:
    """[(正規表現, 規制 id)]。「M-C」の区切りは - と NFKC で - にならないダッシュ類 (BUILD_ARTICLE_REGULATION_DASHES)、前後は英数字でないこと"""
    dash = "[" + re.escape(BUILD_ARTICLE_REGULATION_DASHES) + "]"
    out = []
    for name, reg in BUILD_ARTICLE_REGULATION_NAMES.items():
        n = unicodedata.normalize("NFKC", name)
        head, sep, tail = n.partition("-")
        body = rf"{re.escape(head)}\s*{dash}\s*{re.escape(tail)}" if sep else re.escape(n)
        out.append((re.compile(rf"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9])"), reg))
    return out


def regulation_from_text(text: Optional[str]) -> Optional[str]:
    """記事固有の記載 (題名・タグ) → 規制 id。「レギュレーションM-C」「M-C」「規制 M-C」「M−C」等を拾う (全角は NFKC で吸収)。
    違う規制が 2 つ以上あれば曖昧なので None、無ければ None。
    呼び出し側はサイト共通のメニュー・案内 (「M-C 情報」等、どの記事にも出る文字列) を渡さない (記事の規制の根拠にならない)"""
    t = unicodedata.normalize("NFKC", text or "")
    found = {reg for pat, reg in _regulation_patterns() if pat.search(t)}
    return next(iter(found)) if len(found) == 1 else None
