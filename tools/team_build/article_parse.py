"""構築記事の決定的な解析 (docs/ARTICLE_BANK_DESIGN_1006.md §3)。本文を LLM に渡さない。

入力は「リンクつきの本文」(HTML を html_to_marked_text で直した Markdown 風の文字列。`[表示名](href)` がリンク)。
  1. 個体の定型部分: `種族名 @ 持ち物 (性格) 特性` の行を個体の見出しにし (辞書で種・持ち物・性格が解決できる行だけ)、
     配分 (能力ポイント / 252 表示 / 実数値を別に保持) と技一覧 (技名だけの最初の行から 4 技) を取る。
  2. 解説: 限定した規則 (W1 弱点 / W2 全体の弱点 / F1 有利 / P1 技の目的 / S1 抜き / S2 抜かれる / D1 耐え / R1 見れる / C1 助ける)
     だけで「誰についての、どんな関係か」を取る。対象は辞書で解決した種 id・タイプ・技 id だけ。文は残さない。
  3. 全体の節: 選出規則 (schema 2、docs §3.4): 条件つき (「相手に <条件> の場合は <味方名…> を選出」) と無条件 (「基本選出は A(初手)、B、C」
     「<見出し>の選出例は A、B、C など」「初手 A、後発 B と C」) を author_selection_rule にする。条件は語彙の表で述語に直し、判定の可否
     (evaluation) を付ける。先発は明記されたときだけ、残りの枠・確率は作らない。
  変換層 (ホスト別) が渡す「種名だけ分かる個体」(members_named_only) は受け取って結果に載せるだけ (parse_article 自身は作らない)。
出力は id と数値と列挙値だけの辞書。出典は source_ref (個体番号 + 文番号、例 "m2:s3")。
辞書は vision/data/jp_names.json (vision.normalize.normalize の正規化キーで厳密一致。あいまい一致はしない。リンクの番号で id を決めない)。
厳密一致の表に無い表記は、記事専用の別名辞書 (vision/data/article_aliases.json、tools/team_build/article_aliases) の confirmed だけで引く
(candidate は使わない)。
ローカル用の出力 (記録・LLM の入力には入れない): unresolved_names (解決できなかった名前 1 語ずつ) と site_id_observations
(リンクの表示名が厳密一致で解決できたときの (ホスト, 種別, サイト固有 id) → id の観測。別名の自動確定の根拠に使う)。
純粋関数 (ファイル・ネットワークに触れない。辞書と図鑑は読み取りだけ)。テストは tests/test_article_parse.py。
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from functools import lru_cache
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from champions_agent.config import (BSS_PICK_COUNT, BUILD_ARTICLE_ABSENCE_WORDS, BUILD_ARTICLE_ACTUAL_LABEL, BUILD_ARTICLE_BACK_WORDS,
                                    BUILD_ARTICLE_CONDITION_AND_WORDS, BUILD_ARTICLE_CONDITION_CONNECTORS, BUILD_ARTICLE_CONDITION_EVALUATION,
                                    BUILD_ARTICLE_CONDITION_EVALUATION_ORDER, BUILD_ARTICLE_CONDITION_MANY_EVALUATION,
                                    BUILD_ARTICLE_CONDITION_OR_WORDS, BUILD_ARTICLE_CONDITION_SUBJECT_WORDS, BUILD_ARTICLE_CONDITION_WORDS,
                                    BUILD_ARTICLE_COUNT_COMPARATORS, BUILD_ARTICLE_COUNTER_WORDS, BUILD_ARTICLE_EV252_LABEL,
                                    BUILD_ARTICLE_EV252_MAX, BUILD_ARTICLE_EXAMPLE_WORDS, BUILD_ARTICLE_FREE_SLOT_COUNTERS,
                                    BUILD_ARTICLE_FREE_SLOT_NAMES, BUILD_ARTICLE_FREE_SLOT_WORDS, BUILD_ARTICLE_HEADING_MAX_CHARS,
                                    BUILD_ARTICLE_LEAD_WORDS, BUILD_ARTICLE_MANY_WORDS, BUILD_ARTICLE_MAX_MEMBERS,
                                    BUILD_ARTICLE_MEMBER_SECTION_WORDS, BUILD_ARTICLE_MOVE_TYPE_WORDS, BUILD_ARTICLE_MOVES_PER_SET,
                                    BUILD_ARTICLE_NAME_TOKEN_MAX_CHARS, BUILD_ARTICLE_NEGATION_SUFFIXES, BUILD_ARTICLE_NUMERAL_WORDS,
                                    BUILD_ARTICLE_PLAIN_NAME_MIN_CHARS, BUILD_ARTICLE_PRESENCE_ATTACHED_WORDS, BUILD_ARTICLE_PRESENCE_WORDS,
                                    BUILD_ARTICLE_ROLE_WORDS, BUILD_ARTICLE_SELECTION_COMBINABLE_WORDS, BUILD_ARTICLE_SELECTION_ELSE_WORDS,
                                    BUILD_ARTICLE_SELECTION_LIST_SEPARATORS, BUILD_ARTICLE_SELECTION_MARKERS,
                                    BUILD_ARTICLE_SELECTION_PREDICATE_EXCLUDES, BUILD_ARTICLE_SELECTION_PREDICATES,
                                    BUILD_ARTICLE_SELECTION_REQUIRED_WORDS, BUILD_ARTICLE_SITE_ID_PATTERNS, BUILD_ARTICLE_STAT_WORDS,
                                    BUILD_ARTICLE_TARGET_MOD_WORDS, BUILD_ARTICLE_TEAM_SECTION_WORDS, BUILD_ARTICLE_TYPE_KANJI_WORDS,
                                    BUILD_GEN_EV_POINT_CAP)
from vision.normalize import JP_NAMES_PATH, normalize

# 2: unresolved_names を {"category", "text", "host", "site_key"} に、site_id_observations と max_members (single_set) を追加 (2026-10-06)
# 3: 選出規則の schema 2 (無条件の基本選出・選出例、selected_species / lead_species / exact_trio / example / free_slots、条件の語彙と
#    evaluation、any_of / all_of)、members_named_only の受け取り、「場合」単独の接続の語 (2026-10-06 ユーザー判断)
PARSER_VERSION = "article_parse/3"
ARTICLE_ALIASES_PATH = Path(JP_NAMES_PATH).parent / "article_aliases.json"   # 記事専用の別名辞書 (OCR 用の jp_names.json とは別)
DICT_CATEGORIES = ("species", "items", "moves", "abilities", "natures", "types")
STAT_ORDER = ("hp", "atk", "def", "spa", "spd", "spe")
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
BULLET_RE = re.compile(r"^\s*(?:[*\-・•]|\d+[.)])\s*")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！!？?])")
ENTITY_RE = re.compile(r"〔(\d+)〕")
ENTITY_GROUP = r"(?:〔\d+〕(?:や|、|と|・|,|,|\s)*)+"
CLAUSE_SEP = "、。！!？?（）()「」"
# 未確定項目の分類 (本文の代わりに残す列挙値。文は残さない)
UNRESOLVED_CATEGORIES = ("broad_matchup_claim", "move_purpose_non_species_target", "selection_else_branch_members_unspecified",
                         "selection_condition_unknown", "selection_members_unresolved", "selection_free_slot_count_unspecified",
                         "unresolved_species", "unresolved_item", "unresolved_nature", "unresolved_ability", "unresolved_move",
                         "member_head_incomplete", "stat_line_unlabeled", "extra_member_head", "durability_benchmark_ambiguous_modifiers")


def _alt(words) -> str:
    """語の列 → 正規表現の選択 (長い語から照合する)"""
    return "|".join(re.escape(w) for w in sorted(set(words), key=len, reverse=True))


# ------------------------------------------------------------------------------------------------------------------
# 辞書 (日本語名 → id。厳密一致だけ)
# ------------------------------------------------------------------------------------------------------------------
def value_id(value) -> Optional[str]:
    """辞書の値 → id (種は {"id", "num"} の id、他は文字列そのもの)"""
    if isinstance(value, dict):
        return value.get("id")
    return value if isinstance(value, str) else None


class ArticleDictionary:
    """jp_names.json の正規化キー → id。種は {"id", "num"}、タイプは英語名、他は id 文字列。あいまい一致はしない。
    aliases (記事専用の別名辞書のデータ、tools/team_build/article_aliases.load_aliases の形) を渡すと、厳密一致の表に無い表記を
    confirmed の別名だけで引く (candidate / rejected は使わない)。素の文字列から種名・技名を探す一覧には別名を入れない"""

    def __init__(self, raw: dict, aliases: Optional[dict] = None):
        self._raw = raw
        self._tables: dict = {}
        self._names: dict = {}            # 種別 → id → 日本語名 (最初の表記。別名の canonical に使う)
        for cat in DICT_CATEGORIES:
            table: dict = {}
            names: dict = {}
            for ja, val in (raw.get(cat) or {}).items():
                table.setdefault(normalize(ja), val)
                vid = value_id(val)
                if vid:
                    names.setdefault(vid, ja)
            self._tables[cat] = table
            self._names[cat] = names
        # 名前表に無いフォルム名 (ヒートロトム / フラエッテ(えいえんのはな) / ヒスイ…) は、図鑑の全種について組み立てた日本語名
        # (advisor.infer.species_ja_name。tools/team_build/spec._form_name_index と同じ考え) を第 2 の厳密一致の表にする
        forms = constructed_form_names(set(self._names["species"]))
        for ja, sid, num in forms:
            self._tables["species"].setdefault(normalize(ja), {"id": sid, "num": num})
            self._names["species"].setdefault(sid, ja)
        self._species_ja = dict(self._names["species"])
        self._move_ja = dict(self._names["moves"])
        self._type_words = sorted((raw.get("types") or {}).items(), key=lambda kv: -len(kv[0]))
        # 素の文字列から種名を探すための一覧 (長い順。短すぎる名前は誤検出するので省く)。組み立てたフォルム名も含める
        species_entries = [(ja, v["id"]) for ja, v in (raw.get("species") or {}).items() if isinstance(v, dict)] + [(ja, sid) for ja, sid, _n in forms]
        self._species_words = sorted({(ja, sid) for ja, sid in species_entries if len(ja) >= BUILD_ARTICLE_PLAIN_NAME_MIN_CHARS},
                                     key=lambda kv: -len(kv[0]))
        if aliases:
            from tools.team_build.article_aliases import confirmed_map     # 循環 import を避けて呼び出し時に読む
            self._aliases = confirmed_map(aliases)
        else:
            self._aliases = {}
        self.alias_count = sum(len(t) for t in self._aliases.values())
        base = str(len(raw.get("species") or {})) + "/" + str(len(raw.get("moves") or {})) + "/" + str(len(raw.get("items") or {}))
        alias_hash = hashlib.sha256(json.dumps(self._aliases, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
        self.version = f"{base}/aliases:{self.alias_count}" + (f":{alias_hash}" if self.alias_count else "")

    def with_aliases(self, aliases: Optional[dict]) -> "ArticleDictionary":
        """同じ辞書の表で、別名だけ差し替えた辞書 (別名を確定した後の再解析に使う)"""
        return ArticleDictionary(self._raw, aliases)

    def lookup_exact(self, category: str, text: str):
        """厳密一致の表だけで引く (別名を含まない。往復一致の検査とサイト固有 id の観測に使う)"""
        key = normalize(text)
        return self._tables.get(category, {}).get(key) if key else None

    def lookup(self, category: str, text: str):
        """厳密一致の表 → 無ければ confirmed の別名。値の形は表と同じ (種は {"id", "num"})"""
        v = self.lookup_exact(category, text)
        if v is not None:
            return v
        key = normalize(text)
        aid = self._aliases.get(category, {}).get(key) if key else None
        if aid is None:
            return None
        return {"id": aid, "num": self.species_num(aid)} if category == "species" else aid

    def name_of(self, category: str, ident: str) -> Optional[str]:
        """id → 辞書の日本語名 (最初の表記)"""
        return self._names.get(category, {}).get(ident)

    def species_id(self, text: str) -> Optional[str]:
        v = self.lookup("species", text)
        return v["id"] if isinstance(v, dict) else None

    def species_num(self, species_id: str) -> Optional[int]:
        for v in self._tables["species"].values():
            if isinstance(v, dict) and v.get("id") == species_id:
                return v.get("num")
        return None

    def species_ja(self, species_id: str) -> Optional[str]:
        return self._species_ja.get(species_id)

    def move_ja(self, move_id: str) -> Optional[str]:
        return self._move_ja.get(move_id)

    def type_words(self) -> list:
        """[(日本語, 英語)] を長い順に (「ゴースト」→ "Ghost")"""
        return list(self._type_words)

    def species_words(self) -> list:
        """[(日本語, id)] を長い順に (素の文字列から種名を探す用)"""
        return self._species_words


@lru_cache(maxsize=1)
def _constructed_form_names_all() -> tuple:
    """図鑑の全種の (組み立てた日本語名, id, 図鑑番号)。advisor.infer.species_ja_name が id をそのまま返す種は除く。図鑑が無ければ空"""
    try:
        from advisor.dex import get_dex
        from advisor.infer import species_ja_name
        dex = get_dex()
        out = []
        for sid in dex.species_ids():
            ja = species_ja_name(sid)
            if ja and ja != sid:
                out.append((ja, sid, (dex.species(sid) or {}).get("num")))
        return tuple(out)
    except Exception:
        return ()


def constructed_form_names(known_ids: set) -> list:
    """名前表に無い種 (known_ids に id が無い) の組み立てた日本語名だけ → [(日本語名, id, 図鑑番号)] (決定的な並び)"""
    return [(ja, sid, num) for ja, sid, num in _constructed_form_names_all() if sid not in known_ids]


@lru_cache(maxsize=1)
def default_dictionary() -> ArticleDictionary:
    """jp_names.json + 記事専用の別名辞書 (ファイルがあれば。使うのは confirmed だけ)"""
    aliases = None
    if ARTICLE_ALIASES_PATH.exists():
        from tools.team_build.article_aliases import load_aliases
        aliases = load_aliases(ARTICLE_ALIASES_PATH)
    return ArticleDictionary(json.loads(JP_NAMES_PATH.read_text(encoding="utf-8")), aliases)


@lru_cache(maxsize=1)
def mega_table() -> dict:
    """メガ形態 id → (基本種 id, メガ石 id)、メガ石 id → メガ形態 id (図鑑 champions_dex の requiredItem が正)、
    メガ形態 id → メガ後の特性 id の列 (abilities。記事の特性がメガ前のものか見分けるため)"""
    try:
        from tools.check_mega_items import DEX, mega_stones
        dex = json.loads(DEX.read_text(encoding="utf-8")).get("species", {})
    except Exception:
        return {"forms": {}, "stones": {}, "abilities": {}}
    forms, stones, abilities = {}, {}, {}
    for sid, _name, _req, stone in mega_stones():
        base = re.sub(r"[^a-z0-9]", "", (dex.get(sid, {}).get("baseSpecies") or "").lower()) or None
        forms[sid] = (base, stone)
        if stone:
            stones[stone] = sid
        abilities[sid] = [re.sub(r"[^a-z0-9]", "", str(a).lower()) for a in (dex.get(sid, {}).get("abilities") or {}).values() if a]
    return {"forms": forms, "stones": stones, "abilities": abilities}


# ------------------------------------------------------------------------------------------------------------------
# HTML → リンクつきの本文
# ------------------------------------------------------------------------------------------------------------------
class _MarkedTextParser(HTMLParser):
    BLOCK_TAGS = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "br", "tr", "section", "article", "blockquote", "table"}
    SKIP_TAGS = {"script", "style", "nav", "header", "footer", "aside", "noscript", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list = []
        self._skip = 0
        self._href: Optional[str] = None
        self._link_text: list = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP_TAGS:
            self._skip += 1
            return
        if self._skip:
            return
        if tag == "a":
            self._href = dict(attrs).get("href") or ""
            self._link_text = []
        elif tag in self.BLOCK_TAGS:
            self.parts.append("\n")
            if tag == "li":
                self.parts.append("* ")
            elif tag[0] == "h" and tag[1:].isdigit():
                self.parts.append("#" * int(tag[1:]) + " ")

    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag == "a" and self._href is not None:
            text = "".join(self._link_text).strip()
            self.parts.append(f"[{text}]({self._href})" if text else "")
            self._href, self._link_text = None, []
        elif tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip:
            return
        if self._href is not None:
            self._link_text.append(data)
        else:
            self.parts.append(data)


def html_to_marked_text(html: str) -> str:
    """HTML → リンクつきの本文 (<a> は [表示名](href)、段落・箇条書き・見出しは行、script/style/nav は落とす)"""
    p = _MarkedTextParser()
    p.feed(html or "")
    p.close()
    text = "".join(p.parts)
    lines = [re.sub(r"[ \t　]+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


# ------------------------------------------------------------------------------------------------------------------
# 行の字句: リンクと素の文字列
# ------------------------------------------------------------------------------------------------------------------
def tokenize_links(line: str) -> list:
    """行 → [(text, href or None)]。リンクの表示名と、その間の素の文字列 (空は除く)"""
    out, pos = [], 0
    for m in LINK_RE.finditer(line):
        if m.start() > pos:
            seg = line[pos:m.start()]
            if seg.strip():
                out.append((seg, None))
        out.append((m.group(1).strip(), m.group(2)))
        pos = m.end()
    if pos < len(line) and line[pos:].strip():
        out.append((line[pos:], None))
    return out


def flatten(line: str) -> str:
    """リンクを表示名に置き換えた素の文字列"""
    return LINK_RE.sub(lambda m: m.group(1), line)


def strip_bullet(line: str) -> str:
    return BULLET_RE.sub("", line).strip()


def _link_species_num(href: Optional[str]) -> Optional[int]:
    """リンクに種族番号があれば返す (矛盾の警告に使うだけ。id は決めない)。yakkun の zukan/n260m → 260"""
    if not href:
        return None
    m = re.search(r"/zukan/n(\d+)", href)
    return int(m.group(1)) if m else None


def canonical_host(host: Optional[str]) -> Optional[str]:
    """ホスト名の比較用の形 (小文字、ポートと先頭の www. を落とす)。空なら None"""
    h = (host or "").strip().lower()
    if not h:
        return None
    h = h.rsplit("@", 1)[-1].split(":", 1)[0]
    if h.startswith("www."):
        h = h[len("www."):]
    return h or None


def link_host(host: Optional[str], href: Optional[str]) -> Optional[str]:
    """リンクの属するホスト (比較用の形)。href が絶対 URL ならそのホスト、相対ならページのホスト host"""
    if not href:
        return None
    try:
        netloc = urlparse(href).netloc
    except ValueError:
        return None
    return canonical_host(netloc) if netloc else canonical_host(host)


def site_key(host: Optional[str], href: Optional[str]) -> Optional[tuple]:
    """リンクのサイト固有 id → (種別, key)。リンクのホストに表 (BUILD_ARTICLE_SITE_ID_PATTERNS) が無い・どの形にも合わなければ None。
    host はページのホスト (相対リンクの解決にだけ使う)。id はこの key で決めない (表示名の厳密一致との対応を観測するだけ)"""
    pats = BUILD_ARTICLE_SITE_ID_PATTERNS.get(link_host(host, href) or "")
    if not pats:
        return None
    for cat, pat in pats.items():
        m = re.search(pat, href)
        if m:
            return cat, m.group(1)
    return None


def _name_item(category: str, text: str, href: Optional[str], host: Optional[str]) -> dict:
    """解決できなかった名前 1 語 (ローカルの辞書補修用)。リンクがあれば、そのホストと同じ種別のサイト固有 id を添える"""
    lh = link_host(host, href) if href else None
    sk = site_key(host, href) if href else None
    return {"category": category, "text": text, "host": lh, "site_key": sk[1] if sk and sk[0] == category else None}


def site_id_observations(lines: list, dic: "ArticleDictionary", host: Optional[str] = None) -> list:
    """全部の行のリンクのうち、表示名がサイト固有 id と同じ種別で厳密一致 (別名を含まない) に解決できたもの →
    [{"host", "category", "key", "id"}] (重複なし、並びは決定的)。別名の自動確定 (site_id_verified) の根拠"""
    seen: set = set()
    for ln in lines:
        for text, href in tokenize_links(ln):
            if href is None:
                continue
            sk = site_key(host, href)
            if not sk:
                continue
            ident = value_id(dic.lookup_exact(sk[0], text))
            if ident:
                seen.add((link_host(host, href), sk[0], sk[1], ident))
    return [{"host": h, "category": c, "key": k, "id": i} for h, c, k, i in sorted(seen)]


# ------------------------------------------------------------------------------------------------------------------
# 節の見出しと個体の見出し
# ------------------------------------------------------------------------------------------------------------------
_MARKER_LINE_RE = re.compile(rf"^(?:{_alt(BUILD_ARTICLE_SELECTION_MARKERS)})\s*(?:は|:|：|→)\s*\S")


def _heading_text(line: str) -> Optional[str]:
    """見出しとみなせる行ならその文字列。`#` 始まり、または 箇条書きでもリンクでも文 (。で終わる) でもない短い行。
    「基本選出: A / B / C」のように選出の印の後に列挙が続く行は見出しにしない (選出規則の文として読む)"""
    s = line.strip()
    if s.startswith("#"):
        return s.lstrip("#").strip()
    if BULLET_RE.match(s) or LINK_RE.search(s) or s.endswith(("。", "．", ".")) or _MARKER_LINE_RE.match(s):
        return None
    t = re.sub(r"[【】\[\]■□●○◆◇▼▽★☆:：]", "", s).strip()
    return t if 0 < len(t) <= BUILD_ARTICLE_HEADING_MAX_CHARS else None


def section_kind(line: str) -> Optional[str]:
    """行が節の見出しなら "member" (個体の節) / "team" (全体の節)、見出しでなければ None。全体の語を先に見る"""
    t = _heading_text(line)
    if not t:
        return None
    if any(w in t for w in BUILD_ARTICLE_TEAM_SECTION_WORDS):
        return "team"
    if any(w in t for w in BUILD_ARTICLE_MEMBER_SECTION_WORDS):
        return "member"
    return None


HEAD_RE = re.compile(r"^(?P<species>[^@＠]+?)\s*[@＠]\s*(?P<item>[^（(]+?)\s*[（(]\s*(?P<nature>[^）)]+?)\s*[）)]\s*(?P<ability>.*)$")


def parse_member_head(line: str, dic: ArticleDictionary) -> Optional[dict]:
    """`種族名 @ 持ち物 (性格) 特性` の行 → 個体の見出し。種・持ち物・性格が辞書で解決できなければ None (個体の見出しではない)。
    特性が無い・解決できないときは warnings に残して採る"""
    s = strip_bullet(line)
    flat = flatten(s)
    if "。" in flat:
        return None                                   # 文 (説明) は個体の見出しではない
    m = HEAD_RE.match(flat)
    if not m:
        return None
    species_text, item_text, nature_text = m.group("species").strip(), m.group("item").strip(), m.group("nature").strip()
    species_id = dic.species_id(species_text)
    item_id = dic.lookup("items", item_text)
    nature_id = dic.lookup("natures", nature_text)
    if not (species_id and isinstance(item_id, str) and isinstance(nature_id, str)):
        return None
    warnings, notes, unresolved = [], [], []
    ability_id = None
    ability_text = m.group("ability").strip()
    if ability_text:
        for piece in re.split(r"[\s/／、,]+", ability_text):
            v = dic.lookup("abilities", piece)
            if isinstance(v, str):
                ability_id = v
                break
    if ability_id is None:
        unresolved.append("member_head_incomplete" if not ability_text else "unresolved_ability")
    # リンクの種族番号と辞書の num の矛盾は注記に残す (id はリンクで決めない。yakkun の番号は新しい種で全国図鑑番号とずれる)
    for text, href in tokenize_links(s):
        num = _link_species_num(href)
        if num is not None and normalize(text) == normalize(species_text):
            dnum = dic.species_num(species_id)
            if dnum is not None and dnum != num:
                notes.append("link_num_mismatch")
    base_id, form_id, stone = species_id, species_id, None
    mega = mega_table()
    if species_id in mega["forms"]:
        base_id, stone_req = mega["forms"][species_id]
        base_id = base_id or species_id
        if stone_req and item_id != stone_req:
            warnings.append("mega_form_item_mismatch")
        stone = item_id if item_id in mega["stones"] else None
    elif item_id in mega["stones"]:
        stone = item_id
        form_id = mega["stones"][item_id]
        if mega["forms"].get(form_id, (None,))[0] != species_id:
            warnings.append("mega_stone_species_mismatch")
            form_id = species_id
        else:
            notes.append("mega_form_from_stone")
    # メガ形態の特性: 記事がメガ前の特性 (げきりゅう / もうか) を書くことがある (GameWith はトグルの無い個体でそう書く。10/6 の取得で確認)。
    # 図鑑のメガ後の特性が 1 つに決まるなら、記事の特性はメガ前として pre_mega_ability に残し、ability はメガ後の特性にする
    pre_mega_ability = None
    mega_abilities = mega["abilities"].get(form_id) or []
    if form_id in mega["forms"] and ability_id and ability_id not in mega_abilities and len(mega_abilities) == 1:
        pre_mega_ability, ability_id = ability_id, mega_abilities[0]
        notes.append("ability_pre_mega")
    return {"species_id": form_id, "base_species_id": base_id, "mega_stone": stone, "item": item_id, "nature": nature_id,
            "ability": ability_id, "pre_mega_ability": pre_mega_ability, "display": species_text, "warnings": warnings, "notes": notes,
            "unresolved": unresolved}


# ------------------------------------------------------------------------------------------------------------------
# 配分の行・技一覧の行
# ------------------------------------------------------------------------------------------------------------------
def _stat_word_pattern() -> str:
    words = sorted({w for ws in BUILD_ARTICLE_STAT_WORDS.values() for w in ws}, key=len, reverse=True)
    return "|".join(re.escape(w) for w in words)


_STAT_PAIR_RE = re.compile(rf"(?P<name>{_stat_word_pattern()})\s*[:：]\s*(?P<val>\d+)")
_ACTUAL_RE = re.compile(r"(\d+)\s*[-−ー–]\s*(\d+)\s*[-−ー–]\s*(\d+)\s*[-−ー–]\s*(\d+)\s*[-−ー–]\s*(\d+)\s*[-−ー–]\s*(\d+)")


def _stat_key(word: str) -> Optional[str]:
    for key, ws in BUILD_ARTICLE_STAT_WORDS.items():
        if word in ws:
            return key
    return None


def parse_stat_line(line: str) -> Optional[dict]:
    """配分の行 → {"kind": "points" | "ev252" | "actual", "values": {...} | [6], "unlabeled": bool}。配分の行でなければ None。
    3 種類は別に保持し、換算しない。ラベルの無い行は値の範囲で種類を決めて unlabeled を付ける"""
    s = unicodedata.normalize("NFKC", flatten(strip_bullet(line)))
    if not s or LINK_RE.search(line):
        return None
    if BUILD_ARTICLE_ACTUAL_LABEL in s:
        m = _ACTUAL_RE.search(s)
        if not m:
            return None
        return {"kind": "actual", "values": [int(x) for x in m.groups()], "unlabeled": False}
    pairs = [(m.group("name"), int(m.group("val"))) for m in _STAT_PAIR_RE.finditer(s)]
    if not pairs:
        return None
    residue = _STAT_PAIR_RE.sub("", s).replace(BUILD_ARTICLE_EV252_LABEL, "")
    residue = re.sub(r"[\s/／:：()（）、,・]", "", residue)
    if residue:
        return None                                   # 配分以外の文字が残る行は説明文
    values: dict = {}
    for name, val in pairs:
        key = _stat_key(name)
        if key:
            values[key] = val
    if not values:
        return None
    if BUILD_ARTICLE_EV252_LABEL in s:
        return {"kind": "ev252", "values": values, "unlabeled": False}
    if all(v <= BUILD_GEN_EV_POINT_CAP for v in values.values()):
        return {"kind": "points", "values": values, "unlabeled": True}
    if all(v <= BUILD_ARTICLE_EV252_MAX for v in values.values()):
        return {"kind": "ev252", "values": values, "unlabeled": True}
    return None


def parse_moves_line(line: str, dic: ArticleDictionary) -> Optional[list]:
    """技名だけで構成された行 → 技 id の列。1 つでも技に解決できない語があれば None (説明文)。
    リンクの表示名と、素の文字列を / ・ 、 , 空白で分けたものを語にする"""
    s = strip_bullet(line)
    if not s:
        return None
    pieces: list = []
    for text, href in tokenize_links(s):
        if href is not None:
            pieces.append(text)
        else:
            pieces.extend(p for p in re.split(r"[\s/／、,，・]+", text) if p)
    if len(pieces) < 2:
        return None
    ids = []
    for p in pieces:
        v = dic.lookup("moves", p)
        if not isinstance(v, str):
            return None
        ids.append(v)
    return ids


# ------------------------------------------------------------------------------------------------------------------
# 文と実体 (リンク・味方名・タイプ) → 規則
# ------------------------------------------------------------------------------------------------------------------
def split_sentences(lines: list) -> list:
    """説明文の行 → 文の列 (。！？ で区切る。行の区切りも文の区切り)。リンクはそのまま残す"""
    out = []
    for ln in lines:
        for s in SENTENCE_SPLIT_RE.split(strip_bullet(ln)):
            s = s.strip()
            if s:
                out.append(s)
    return out


_NAME_QUALIFIER_RE = re.compile(r"[（(][^）)]*[）)]$")


def _member_names(members: list, dic: ArticleDictionary) -> list:
    """[(名前, member_id)] を長い順に。見出しの表示名、使用形態と基本種の辞書名。注記つきの名前 (イエッサン(オス) / フラエッテ(えいえんのはな))
    は注記を外した短い名前でも味方として読む (筆者は自分の個体を短い名前で呼ぶ。10/6 の GameWith の実ページで「イエッサン」が別の種
    (indeedee) に解決して矛盾になった)"""
    names = []
    for mem in members:
        cand = {mem["display"], dic.species_ja(mem["species_id"]) or "", dic.species_ja(mem["base_species_id"]) or ""}
        cand |= {_NAME_QUALIFIER_RE.sub("", n) for n in list(cand)}
        for n in cand:
            if n:
                names.append((n, mem["id"]))
    return sorted(names, key=lambda x: -len(x[0]))


_KATAKANA = r"ァ-ヶー"


def _replace_word(flat: str, word: str, make_token) -> str:
    """素の文字列の中の word を実体に置き換える (カタカナ語の一部にはしない: 前後がカタカナなら置き換えない)。
    置き換えは出現ごとに別の実体にする"""
    pat = re.compile(rf"(?<![{_KATAKANA}]){re.escape(word)}(?![{_KATAKANA}])")
    return pat.sub(lambda _m: make_token(), flat)


def entities_of(sentence: str, members: list, dic: ArticleDictionary, own_moves: Optional[list] = None) -> tuple:
    """文 → (実体を 〔i〕 に置き換えた文, 実体の列)。実体 = リンク (種・技・タイプ・持ち物) + 素の文字列の 「X タイプ」・味方名・
    辞書の種名 (長い順、カタカナ語の境界で) ・その個体の採用技名。味方の種のリンク・味方名は member_id を持つ。
    実体は {"kind", "id", "member_id", "written"}。written は書かれた表記が辞書で指す種 id (味方名で置き換えた実体の id は味方の
    使用形態なので、相手についての条件 (「相手にボーマンダがいる」) は written で読む。種以外は id と同じ)"""
    ents: list = []
    member_by_species = {}
    for mem in members:
        member_by_species.setdefault(mem["species_id"], mem["id"])
        member_by_species.setdefault(mem["base_species_id"], mem["id"])

    def add(kind, ident, member_id=None, written=None):
        ents.append({"kind": kind, "id": ident, "member_id": member_id, "written": ident if written is None else written})
        return f"〔{len(ents) - 1}〕"

    def link_repl(m):
        text = m.group(1).strip()
        sid = dic.species_id(text)
        if sid:
            return add("species", sid, member_by_species.get(sid))
        mv = dic.lookup("moves", text)
        if isinstance(mv, str):
            return add("move", mv)
        it = dic.lookup("items", text)
        if isinstance(it, str):
            return add("item", it)
        ty = dic.lookup("types", text)
        if isinstance(ty, str):
            return add("type", ty)
        return "〔?〕"                                  # 解決できないリンクは実体にしない

    flat = LINK_RE.sub(link_repl, sentence)
    for ja, en in dic.type_words():                   # 「ゴーストタイプ」は種名 (ゴース) より先に
        if (ja + "タイプ") in flat:
            flat = _replace_word(flat, ja + "タイプ", lambda en=en: add("type", en))
    for name, mid in _member_names(members, dic):
        if name in flat:
            sid = next(mem["species_id"] for mem in members if mem["id"] == mid)
            written = dic.species_id(name) or sid
            flat = _replace_word(flat, name, lambda sid=sid, mid=mid, written=written: add("species", sid, mid, written))
    for ja, sid in dic.species_words():
        if ja in flat:
            flat = _replace_word(flat, ja, lambda sid=sid: add("species", sid, member_by_species.get(sid)))
    for mv in own_moves or []:
        ja = dic.move_ja(mv)
        if ja and ja in flat:
            flat = _replace_word(flat, ja, lambda mv=mv: add("move", mv))
    return flat, ents


def _group_ids(flat_group: str) -> list:
    return [int(x) for x in ENTITY_RE.findall(flat_group)]


def _prefix_clause(flat: str, pos: int) -> str:
    """位置 pos の直前の、同じ節の素の文字列 (実体・区切りまで)"""
    start = pos
    while start > 0 and flat[start - 1] not in CLAUSE_SEP and flat[start - 1] != "〕":
        start -= 1
    return flat[start:pos]


_OWN_STAT_RE = re.compile(rf"^(?:{_stat_word_pattern()})(?:は|が|を|も|[:：])?")
_PARTICLES_RE = re.compile(r"[はがのをにもでと、\s]")


def _strip_own_stat(prefix: str) -> str:
    """節の先頭の自分の能力の指示 (「Dは」「Sは」) を落とす (相手の修飾ではない)"""
    return _OWN_STAT_RE.sub("", prefix.strip(), count=1)


def _mods_of(prefix: str) -> tuple:
    """対象の直前の修飾語 → (条件の列, 表に無い修飾が残ったか)"""
    rest, conds = _strip_own_stat(prefix), []
    for cond, words in BUILD_ARTICLE_TARGET_MOD_WORDS.items():
        for w in words:
            if w in rest:
                conds.append(cond)
                rest = rest.replace(w, "")
    rest = _PARTICLES_RE.sub("", rest)
    return sorted(set(conds)), bool(rest)


def _negated(flat: str, end: int) -> bool:
    tail = flat[end:]
    return any(tail.startswith(suf) for suf in BUILD_ARTICLE_NEGATION_SUFFIXES)


def _claim(kind, subject, obj, obj_kind, source_ref, **extra) -> dict:
    c = {"kind": kind, "subject": subject, "object": obj, "object_kind": obj_kind, "conditions": [], "basis": "author_explicit",
         "source_ref": source_ref}
    c.update(extra)
    return c


def extract_claims(sentence: str, subject: str, members: list, dic: ArticleDictionary, source_ref: str) -> tuple:
    """1 文 → (主張の列, 未確定の列, 解決できなかった名前の列)。subject は "m1" 等の個体 id か "team"。規則は docs §3.3 の表のとおり。
    名前は {"category", "text", "host", "site_key"} (説明文の素の文字列なので host / site_key は None)"""
    own_moves = next((m["moves"] for m in members if m["id"] == subject), [])
    flat, ents = entities_of(sentence, members, dic, own_moves)
    claims, unresolved, unresolved_names = [], [], []

    def ent(i):
        return ents[i]

    def targets(group):
        return [ent(i) for i in _group_ids(group) if ent(i)["kind"] in ("species", "type")]

    # W1 / C1: X に弱い [味方…を助け]
    for m in re.finditer(rf"({ENTITY_GROUP})に弱い", flat):
        if _negated(flat, m.end()):
            continue
        objs = targets(m.group(1))
        after = re.match(rf"({ENTITY_GROUP})", flat[m.end():])
        explicit = [ent(i) for i in _group_ids(after.group(1))] if after else []
        explicit_members = [e["member_id"] for e in explicit if e["member_id"]]
        subjects = explicit_members or [subject]
        for o in objs:
            for s in subjects:
                claims.append(_claim("weak_to", s, o["id"], o["kind"], source_ref))
        if explicit_members and after and re.match(r"を助け", flat[m.end() + after.end():]):
            for o in objs:
                claims.append(_claim("supports", subject, o["id"], o["kind"], source_ref, members=explicit_members))
    # W2: 全体として X が重い / X が重い
    for m in re.finditer(rf"({ENTITY_GROUP})(?:が|は)重い", flat):
        if _negated(flat, m.end()):
            continue
        for o in targets(m.group(1)):
            claims.append(_claim("weak_to", "team" if "全体" in flat or subject == "team" else subject, o["id"], o["kind"], source_ref))
    # F1: X に (圧倒的) 有利 / X はカモ
    for m in re.finditer(rf"({ENTITY_GROUP})(?:に(?:圧倒的に?)?有利|(?:は|を)カモ)", flat):
        if _negated(flat, m.end()):
            continue
        objs = targets(m.group(1))
        conds, unparsed = _mods_of(_prefix_clause(flat, m.start()))
        for o in objs:
            claims.append(_claim("favorable_vs", subject, o["id"], o["kind"], source_ref, conditions=conds, mods_unparsed=unparsed))
    if re.search(r"有利|カモ|耐えれ|耐えられ", flat) and not re.search(rf"{ENTITY_GROUP}(?:に(?:圧倒的に?)?有利|(?:は|を)カモ)|〔\d+〕の[^〔〕]*耐え", flat):
        unresolved.append({"category": "broad_matchup_claim", "source_ref": source_ref})
    # P1: 技 X は Y 対策
    for m in re.finditer(r"〔(\d+)〕(?:は|が)([^〔〕。]*?)(〔\d+〕)?(?:の)?対策", flat):
        mv = ent(int(m.group(1)))
        if mv["kind"] != "move":
            continue
        tgt = ent(_group_ids(m.group(3))[0]) if m.group(3) else None
        if tgt and tgt["kind"] in ("species", "type") and not m.group(2).strip():
            claims.append(_claim("move_purpose", subject, tgt["id"], tgt["kind"], source_ref, move=mv["id"]))
        else:
            unresolved.append({"category": "move_purpose_non_species_target", "source_ref": source_ref})
    # S1: (修飾) X 抜き
    for m in re.finditer(rf"({ENTITY_GROUP})抜き", flat):
        conds, unparsed = _mods_of(_prefix_clause(flat, m.start()))
        for o in targets(m.group(1)):
            claims.append(_claim("speed_benchmark", subject, o["id"], o["kind"], source_ref, conditions=conds, mods_unparsed=unparsed))
    # S2: (修飾) X に上を取られ
    for m in re.finditer(rf"({ENTITY_GROUP})に上を取られ", flat):
        conds, unparsed = _mods_of(_prefix_clause(flat, m.start()))
        for o in targets(m.group(1)):
            claims.append(_claim("outsped_by", subject, o["id"], o["kind"], source_ref, conditions=conds, mods_unparsed=unparsed))
    # D1: X の 技 Y 耐え (X の直前の修飾は区切りが曖昧なので採らない)
    for m in re.finditer(r"〔(\d+)〕の(〔\d+〕|[^〔〕、。]+?)耐え", flat):
        atk = ent(int(m.group(1)))
        if atk["kind"] != "species":
            continue
        mv_text = m.group(2)
        if ENTITY_RE.fullmatch(mv_text):
            e = ent(_group_ids(mv_text)[0])
            move_id = e["id"] if e["kind"] == "move" else None
        else:
            v = dic.lookup("moves", mv_text)
            move_id = v if isinstance(v, str) else None
            if move_id is None:
                unresolved.append({"category": "unresolved_move", "source_ref": source_ref})
                if len(mv_text) <= BUILD_ARTICLE_NAME_TOKEN_MAX_CHARS:
                    unresolved_names.append(_name_item("moves", mv_text, None, None))
        prefix = _strip_own_stat(_prefix_clause(flat, m.start()))
        ambiguous = bool(_PARTICLES_RE.sub("", prefix))
        if ambiguous:
            unresolved.append({"category": "durability_benchmark_ambiguous_modifiers", "source_ref": source_ref})
        claims.append(_claim("survives", subject, atk["id"], "species", source_ref, move=move_id, mods_ambiguous=ambiguous))
    # R1: (唯一) X を (安定して) 見れる
    for m in re.finditer(rf"({ENTITY_GROUP})を(?:安定して|安定的に)?(?:見れ|見られ|受けられ|受けれ|相手にでき|対応でき)", flat):
        if _negated(flat, m.end()):
            continue
        for o in targets(m.group(1)):
            claims.append(_claim("handles", subject, o["id"], o["kind"], source_ref, exclusive=("唯一" in flat)))
    return claims, unresolved, unresolved_names


# ------------------------------------------------------------------------------------------------------------------
# 選出規則 (schema 2。docs/ARTICLE_BANK_DESIGN_1006.md §3.4)
# ------------------------------------------------------------------------------------------------------------------
def species_ref(species_id: str) -> dict:
    """種 id → {"species_id" (使用形態), "base_species_id"}。基本種はメガ形態の表 (mega_table。図鑑の requiredItem が正) だけで決める
    (parse_member_head と同じ。個体 id は作らない)"""
    base = (mega_table()["forms"].get(species_id) or (None, None))[0]
    return {"species_id": species_id, "base_species_id": base or species_id}


def normalize_named_only(entries) -> list:
    """変換層が渡す「種名だけ分かる個体」→ [{"species_id", "base_species_id", "mega_stone"}] (個体 id は付けない。他の項目は捨てる)。
    base_species_id が無ければ species_ref (メガ形態の表) で補い、mega_stone が無ければ None (未知)。形が違えば ValueError"""
    out = []
    for e in entries or []:
        if not isinstance(e, dict) or not isinstance(e.get("species_id"), str) or not e["species_id"]:
            raise ValueError("members_named_only の要素は species_id (文字列) を持つ dict にする")
        base = e.get("base_species_id") or species_ref(e["species_id"])["base_species_id"]
        stone = e.get("mega_stone")
        if not isinstance(base, str) or (stone is not None and not isinstance(stone, str)):
            raise ValueError("members_named_only の base_species_id / mega_stone は文字列 (mega_stone は None も可) にする")
        out.append({"species_id": e["species_id"], "base_species_id": base, "mega_stone": stone})
    return out


_CONNECTOR_RE = re.compile(_alt(BUILD_ARTICLE_CONDITION_CONNECTORS))
_OPPONENT_RE = re.compile(_alt(BUILD_ARTICLE_CONDITION_SUBJECT_WORDS))
_FREE_WORD_RE = re.compile(_alt(BUILD_ARTICLE_FREE_SLOT_WORDS))
_MARKER_RE = re.compile(rf"(?P<marker>{_alt(BUILD_ARTICLE_SELECTION_MARKERS)})\s*(?:は|:|：|→)?\s*")
_LEAD_BACK_RE = re.compile(rf"(?:{_alt(BUILD_ARTICLE_LEAD_WORDS + BUILD_ARTICLE_BACK_WORDS)})\s*(?:は|に|で|:|：)?\s*")
_LEAD_AFTER_RE = re.compile(rf"(?:{_alt(BUILD_ARTICLE_LEAD_WORDS)})\s*(?:は|に|で|:|：)?\s*〔(\d+)〕")
_LEAD_BEFORE_RE = re.compile(rf"〔(\d+)〕(?:を|が|から)?(?:{_alt(BUILD_ARTICLE_LEAD_WORDS)})")
_LEAD_MARK_RE = re.compile(rf"\s*[（(]\s*(?:{_alt(BUILD_ARTICLE_LEAD_WORDS)})\s*[）)]")
_LIST_SEP_RE = re.compile(rf"(?:{_alt(BUILD_ARTICLE_SELECTION_LIST_SEPARATORS)}|\s)+")
# 例示の語。漢字 1 文字の語 (「等」) は直後が漢字なら例示にしない (「等倍」)
_EXAMPLE_RE = re.compile("|".join(re.escape(w) + (r"(?![一-龥])" if re.fullmatch(r"[一-龥]", w) else "")
                                  for w in sorted(BUILD_ARTICLE_EXAMPLE_WORDS, key=len, reverse=True)))
_SEL_PRED_RE = re.compile(rf"\s*(?:{_alt(BUILD_ARTICLE_SELECTION_PREDICATES)})(?!{_alt(BUILD_ARTICLE_SELECTION_PREDICATE_EXCLUDES)})")
_NUM_PAT = rf"(?:[0-9０-９]+|{_alt(BUILD_ARTICLE_NUMERAL_WORDS)})"
_FREE_COUNT_RE = re.compile(rf"(?P<n>{_NUM_PAT})\s*(?:{_alt(BUILD_ARTICLE_FREE_SLOT_COUNTERS)})\s*(?:は|を|も)?\s*"
                            rf"(?:{_alt(BUILD_ARTICLE_FREE_SLOT_WORDS)})")
_FREE_NAME_COUNT_RE = re.compile(rf"(?:{_alt(BUILD_ARTICLE_FREE_SLOT_NAMES)})\s*(?:[×xX✕＊*]\s*)?(?P<n>{_NUM_PAT})")
_POLARITY_RE = re.compile(_alt(BUILD_ARTICLE_ABSENCE_WORDS + BUILD_ARTICLE_PRESENCE_WORDS))
_ABSENCE = frozenset(BUILD_ARTICLE_ABSENCE_WORDS)
_SPECIES_COND_RE = re.compile(rf"(?P<group>{ENTITY_GROUP})(?:(?:が|は|も)(?P<verb>{_alt(BUILD_ARTICLE_ABSENCE_WORDS + BUILD_ARTICLE_PRESENCE_WORDS)})"
                              rf"|(?P<attached>{_alt(BUILD_ARTICLE_PRESENCE_ATTACHED_WORDS)}))")
_MANY_RE = re.compile(_alt(BUILD_ARTICLE_MANY_WORDS))
_PARTICLES_ONLY_RE = re.compile(r"(?:が|は|も|の|ポケモン|\s)*")
_CLAUSE_BREAKS = "、，,"
_EVAL_RANK = {e: i for i, e in enumerate(BUILD_ARTICLE_CONDITION_EVALUATION_ORDER)}
_WEAKEST = BUILD_ARTICLE_CONDITION_EVALUATION_ORDER[-1]
_MANY_EXEMPT = ("type_many", "type_count", "species_present")   # 「多い」を付けない述語 (述語自体が多さ・数・在否を表す)


def weakest_evaluation(evaluations) -> str:
    """判定の可否の列 → 最も弱いもの (BUILD_ARTICLE_CONDITION_EVALUATION_ORDER の後ろほど弱い。表に無い値は最も弱いものとして扱う)"""
    ranks = [_EVAL_RANK.get(e, len(_EVAL_RANK) - 1) for e in evaluations]
    return BUILD_ARTICLE_CONDITION_EVALUATION_ORDER[max(ranks)] if ranks else _WEAKEST


def _condition(predicate: str, value) -> dict:
    return {"subject": "article_opponent", "predicate": predicate, "value": value,
            "evaluation": BUILD_ARTICLE_CONDITION_EVALUATION.get(predicate, _WEAKEST)}


def _type_items(dic: ArticleDictionary) -> tuple:
    """タイプの表記 → 英語名の組 (辞書のひらがな・カタカナ + 漢字の略記 BUILD_ARTICLE_TYPE_KANJI_WORDS)"""
    return tuple(dic.type_words()) + tuple(sorted(BUILD_ARTICLE_TYPE_KANJI_WORDS.items()))


@lru_cache(maxsize=4)
def _type_patterns(type_items: tuple) -> dict:
    """タイプの言及を読む正規表現 (move_type_present / type_count / type_many)。タイプ = 実体 〔i〕 (「Xタイプ」・タイプのリンク) か
    素の語 (カタカナ語・漢字の略記。前が同じ字種なら語の一部とみて読まない。ひらがなの語は技の語が続くときだけ)"""
    lex = dict(type_items)
    kata = [w for w in lex if re.fullmatch(r"[ァ-ヶー]+", w)]
    hira = [w for w in lex if re.fullmatch(r"[ぁ-ん]+", w)]
    kanji = [w for w in lex if w not in kata and w not in hira]

    def type_alt(bare_hiragana: bool) -> str:
        alts = [r"〔(?P<ent>\d+)〕"]
        if kata:
            alts.append(rf"(?<![ァ-ヶー])(?P<kata>{_alt(kata)})(?:タイプ)?")
        if kanji:
            alts.append(rf"(?<![一-龥])(?P<kanji>{_alt(kanji)})(?:タイプ)?")
        if bare_hiragana and hira:
            alts.append(rf"(?P<hira>{_alt(hira)})(?:タイプ)?")
        return "(?:" + "|".join(alts) + ")"
    mod = r"(?:の(?:ポケモン)?)?"
    return {"lex": lex,
            "move_type_present": re.compile(type_alt(True) + rf"(?:の)?(?:{_alt(BUILD_ARTICLE_MOVE_TYPE_WORDS)})"),
            "type_count": re.compile(type_alt(False) + mod + rf"(?:が|は|を|も)?\s*(?P<num>{_NUM_PAT})\s*"
                                     rf"(?:{_alt(BUILD_ARTICLE_COUNTER_WORDS)})\s*(?P<cmp>{_alt(BUILD_ARTICLE_COUNT_COMPARATORS)})"),
            "type_many": re.compile(type_alt(False) + mod + rf"(?:が|は|も)?\s*(?:{_alt(BUILD_ARTICLE_MANY_WORDS)})")}


def _type_of(m, ents: list, lex: dict) -> Optional[str]:
    """タイプの言及の一致 → 英語のタイプ名 (実体がタイプでなければ None)"""
    g = m.groupdict()
    if g.get("ent") is not None:
        e = ents[int(g["ent"])]
        return e["id"] if e["kind"] == "type" else None
    for key in ("kata", "kanji", "hira"):
        if g.get(key):
            return lex.get(g[key])
    return None


def _num_value(text: str) -> int:
    t = unicodedata.normalize("NFKC", text)
    return int(t) if t.isdigit() else BUILD_ARTICLE_NUMERAL_WORDS[text]


def _absent_after(text: str, pos: int) -> bool:
    """pos より後で最初に現れた在否の語が不在の語か (どちらも無ければ False = 在る)"""
    m = _POLARITY_RE.search(text, pos)
    return bool(m) and m.group(0) in _ABSENCE


def _strip_or_words(joint: str) -> str:
    for w in sorted(BUILD_ARTICLE_CONDITION_OR_WORDS, key=len, reverse=True):
        joint = joint.replace(w, "")
    return joint


def _joint_is_and(joint: str) -> bool:
    """2 つの条件の間の文字列が「かつ」か (2 文字以上の AND の語はそのまま見る。「と」は OR の語 (「とか」等) を除いてから見る)"""
    if any(w in joint for w in BUILD_ARTICLE_CONDITION_AND_WORDS if len(w) > 1):
        return True
    rest = _strip_or_words(joint)
    return any(w in rest for w in BUILD_ARTICLE_CONDITION_AND_WORDS if len(w) == 1)


def _apply_many(cond_text: str, found: list) -> None:
    """「多い」を、その直前の条件と、そこから OR の語 (「や / 、」) だけで繋がる前の条件に付ける (quantity = many、判定は
    BUILD_ARTICLE_CONDITION_MANY_EVALUATION との弱い方)。found は始まりの順。type_many は述語自体が「多い」なので変えない"""
    for mm in _MANY_RE.finditer(cond_text):
        p = mm.start()
        idx = max((k for k, (s, _e, _c) in enumerate(found) if s <= p), default=None)
        if idx is None:
            continue
        s, e, _c = found[idx]
        if not (s <= p < e) and not _PARTICLES_ONLY_RE.fullmatch(cond_text[e:p]):
            continue                                   # 「多い」が直前の条件に付いていない
        k = idx
        while True:
            c = found[k][2]
            if c["predicate"] not in _MANY_EXEMPT:
                c["quantity"] = "many"
                c["evaluation"] = weakest_evaluation([c["evaluation"], BUILD_ARTICLE_CONDITION_MANY_EVALUATION])
            if k == 0 or _strip_or_words(cond_text[found[k - 1][1]:found[k][0]]).strip():
                break
            k -= 1


def parse_selection_condition(cond_text: str, ents: list, dic: ArticleDictionary) -> Optional[dict]:
    """条件の節 (「相手 … 場合」。接続の語まで。実体は 〔i〕) → 条件 (dict) / None (語彙の表に無い条件)。
    条件 = {"subject": "article_opponent", "predicate", "value", "evaluation" (BUILD_ARTICLE_CONDITION_EVALUATION)(, "quantity": "many")}。
    1 文の複数の条件は {"any_of" | "all_of": [条件…], "evaluation": 要素の最も弱いもの} (間に「と / かつ / 〜て、」があれば all_of)。
    - species_present: 「(相手構築に) <種> がいる / いない / 入り」→ value {"species_id", "base_species_id", "present"} (書かれた形態のまま。
      味方と同じ種名でも味方の形態に読み替えない)
    - type_count: 「<タイプ>(タイプ)が 3 体以上」→ value {"type", "op", "n"} (比較の語の無い数は条件にしない)
    - type_many: 「<タイプ>(タイプ)が多い」→ value {"type"} (「多い」の基準の数を付け足さない)
    - role: 「物理受け / 特殊受け / 受けポケモン」→ value = BUILD_ARTICLE_ROLE_WORDS の鍵 (種だけで断定しない)
    - move_type_present: 「<タイプ>技持ち」→ value {"move_type"} (覚えられるだけでは成立しない)
    - 既存の語の表 (BUILD_ARTICLE_CONDITION_WORDS: weather_control / trick_room / setup / stall / sand / rain) → value "present" / "absent"
    「多い」は直前の条件 (と「や」で繋がる前の条件) に quantity = many を付ける。在否は条件の語の後で最初に現れた語で決め、不在を値で
    表せない述語 (role / type_count / type_many / move_type_present) の不在は条件にしない。認識できない部分は条件に入れない"""
    pats = _type_patterns(_type_items(dic))
    found: list = []                                   # [始まり, 終わり, 条件] (重ならない)

    def overlaps(s: int, e: int) -> bool:
        return any(not (e <= a or s >= b) for a, b, _c in found)

    def add(s: int, e: int, predicate: str, value) -> None:
        if overlaps(s, e) or any(c["predicate"] == predicate and c["value"] == value for _a, _b, c in found):
            return
        found.append([s, e, _condition(predicate, value)])

    for m in pats["move_type_present"].finditer(cond_text):
        ty = _type_of(m, ents, pats["lex"])
        if ty and not _absent_after(cond_text, m.end()):
            add(m.start(), m.end(), "move_type_present", {"move_type": ty})
    for m in pats["type_count"].finditer(cond_text):
        ty = _type_of(m, ents, pats["lex"])
        if ty and not _absent_after(cond_text, m.end()):
            add(m.start(), m.end(), "type_count",
                {"type": ty, "op": BUILD_ARTICLE_COUNT_COMPARATORS[m.group("cmp")], "n": _num_value(m.group("num"))})
    for m in pats["type_many"].finditer(cond_text):
        ty = _type_of(m, ents, pats["lex"])
        if ty and not _absent_after(cond_text, m.end()):
            add(m.start(), m.end(), "type_many", {"type": ty})
    for m in _SPECIES_COND_RE.finditer(cond_text):
        absent = bool(m.group("verb")) and m.group("verb") in _ABSENCE
        g0 = m.start("group")
        for t in ENTITY_RE.finditer(m.group("group")):
            e = ents[int(t.group(1))]
            if e["kind"] == "species":
                add(g0 + t.start(), g0 + t.end(), "species_present", dict(species_ref(e["written"]), present=not absent))
    for key, words in BUILD_ARTICLE_ROLE_WORDS.items():
        for m in re.finditer(_alt(words), cond_text):
            if not _absent_after(cond_text, m.end()):
                add(m.start(), m.end(), "role", key)
    for predicate, words in BUILD_ARTICLE_CONDITION_WORDS.items():
        spans = [(m.start(), m.end()) for m in re.finditer(_alt(words), cond_text)]
        move_ids = {value_id(dic.lookup("moves", w)) for w in words} - {None}     # 技のリンク (「[トリックルーム](…)」) も同じ述語
        spans += [(t.start(), t.end()) for t in ENTITY_RE.finditer(cond_text)
                  if ents[int(t.group(1))]["kind"] == "move" and ents[int(t.group(1))]["id"] in move_ids]
        for s, e in sorted(spans):
            if not overlaps(s, e):
                add(s, e, predicate, "absent" if _absent_after(cond_text, e) else "present")
                break
    if not found:
        return None
    found.sort(key=lambda x: x[0])
    _apply_many(cond_text, found)
    conds = [c for _s, _e, c in found]
    if len(conds) == 1:
        return conds[0]
    joints = [cond_text[found[k][1]:found[k + 1][0]] for k in range(len(found) - 1)]
    op = "all_of" if any(_joint_is_and(j) for j in joints) else "any_of"
    return {op: conds, "evaluation": weakest_evaluation([c["evaluation"] for c in conds])}


def _scan_list(flat: str, pos: int, limit: int, ents: list) -> Optional[dict]:
    """flat[pos:limit] の先頭からの選出の列挙 (種の実体・区切り・(初手) の印・例示の語) → {"start", "end", "ents", "leads", "example"}。
    種の実体で始まらなければ None。種以外の実体・他の文字で終わる"""
    i = end = pos
    idxs, leads, example = [], [], False
    while i < limit:
        m = ENTITY_RE.match(flat, i)
        if m and m.end() <= limit:
            k = int(m.group(1))
            if ents[k]["kind"] != "species":
                break
            idxs.append(k)
            i = end = m.end()
            continue
        if not idxs:
            break
        m = _LEAD_MARK_RE.match(flat, i)
        if m and m.end() <= limit:
            leads.append(idxs[-1])                     # 名前の直後の (初手) / （初手）
            i = end = m.end()
            continue
        m = _EXAMPLE_RE.match(flat, i)
        if m and m.end() <= limit:
            example = True
            i = end = m.end()
            continue
        m = _LIST_SEP_RE.match(flat, i)
        if m and m.end() <= limit:
            i = m.end()
            continue
        break
    return {"start": pos, "end": end, "ents": idxs, "leads": leads, "example": example} if idxs else None


def _lists_in(flat: str, a: int, b: int, ents: list) -> list:
    """区間 [a, b) の選出の列挙の候補 (種の実体から始まる最長の列挙) の列"""
    out, covered = [], a
    for t in ENTITY_RE.finditer(flat, a, b):
        if t.start() < covered or ents[int(t.group(1))]["kind"] != "species":
            continue
        lst = _scan_list(flat, t.start(), b, ents)
        if lst:
            out.append(lst)
            covered = lst["end"]
    return out


def _list_starts(flat: str, a: int, b: int) -> set:
    """選出の印 (基本選出 / 選出例) と先発・後発の語の直後 (助詞・区切りを除く) の位置"""
    out = set()
    for rx in (_MARKER_RE, _LEAD_BACK_RE):
        for m in rx.finditer(flat, a, b):
            sep = _LIST_SEP_RE.match(flat, m.end())
            out.add(sep.end() if sep else m.end())
    return out


def _named_ref(entity: dict, named: list) -> Optional[dict]:
    """種の実体が種名だけ分かる個体 (使用形態か基本種が一致) なら、その個体の {"species_id", "base_species_id"}"""
    for n in named:
        if entity["id"] in (n["species_id"], n["base_species_id"]):
            return {"species_id": n["species_id"], "base_species_id": n["base_species_id"]}
    return None


def _candidates(flat: str, a: int, b: int, ents: list, named: list, anywhere: bool) -> tuple:
    """区間 [a, b) の選出の候補 → ([(実体の番号, "member" | "species", 個体 id | 種の参照)], 列挙の列)。
    味方の個体 (型あり) と種名だけ分かる個体は anywhere なら区間のどこでも、そうでなければ選出の列挙の中だけ。
    それ以外の辞書の種は選出の列挙 (印・先発の語の直後か、選出の述語の直前) の中だけ (相手への言及を候補に混ぜない)"""
    lists = _lists_in(flat, a, b, ents)
    starts = _list_starts(flat, a, b)
    selected = {k for lst in lists if lst["start"] in starts or _SEL_PRED_RE.match(flat, lst["end"]) for k in lst["ents"]}
    out = []
    for t in ENTITY_RE.finditer(flat, a, b):
        k = int(t.group(1))
        e = ents[k]
        if e["kind"] != "species":
            continue
        ours = anywhere or k in selected
        if e["member_id"]:
            if ours:
                out.append((k, "member", e["member_id"]))
            continue
        ref = _named_ref(e, named)
        if ref is not None:
            if ours:
                out.append((k, "species", ref))
        elif k in selected:
            out.append((k, "species", species_ref(e["written"])))
    return out, lists


def _free_slots(text: str) -> tuple:
    """未指定の枠 → (数, 言及があるか, 数の書かれていない自由枠があるか)。数は書かれたものだけ (推測しない)"""
    n, spans = 0, []
    for rx in (_FREE_COUNT_RE, _FREE_NAME_COUNT_RE):
        for m in rx.finditer(text):
            if all(m.end() <= s or m.start() >= e for s, e in spans):
                spans.append((m.start(), m.end()))
                n += _num_value(m.group("n"))
    named_slot = any(w in text for w in BUILD_ARTICLE_FREE_SLOT_NAMES)
    return n, bool(spans) or named_slot, named_slot and not spans


def _make_rule(condition: Optional[dict], flat: str, a: int, b: int, cands: list, lists: list, recommendation: str,
               source_ref: str) -> tuple:
    """候補と区間 → (選出規則, 未確定の列)。先発は (初手) の印・「初手は X」「X を先発」が候補の中で 1 つに決まるときだけ"""
    members, species, key_of, seen = [], [], {}, set()
    for k, kind, v in cands:
        key = (kind, v if kind == "member" else v["species_id"])
        key_of[k] = key
        if key not in seen:
            seen.add(key)
            (members if kind == "member" else species).append(v)
    lead_ks = [k for lst in lists for k in lst["leads"]]
    lead_ks += [int(m.group(1)) for rx in (_LEAD_AFTER_RE, _LEAD_BEFORE_RE) for m in rx.finditer(flat, a, b)]
    lead_keys = {key_of[k] for k in lead_ks if k in key_of}
    lead = lead_species = None
    if len(lead_keys) == 1:
        kind, ident = next(iter(lead_keys))
        if kind == "member":
            lead = ident
        else:
            lead_species = ident
    example = any(lst["example"] for lst in lists if set(lst["ents"]) & set(key_of))
    free, free_mentioned, free_unspecified = _free_slots(flat[a:b])
    exact = len(members) + len(species) == BSS_PICK_COUNT and not example and free == 0 and not free_mentioned
    rule = {"kind": "author_selection_rule", "condition": condition, "selected_members": members, "selected_species": species,
            "lead": lead, "lead_species": lead_species, "recommendation": recommendation, "exact_trio": exact, "example": example,
            "free_slots": free, "source_ref": source_ref}
    unresolved = [{"category": "selection_free_slot_count_unspecified", "source_ref": source_ref}] if free_unspecified else []
    return rule, unresolved


def _mask_free_words(flat: str) -> str:
    """未指定の枠の語 (「相手に合わせて」等) を同じ長さの埋め字にした文字列 (その中の「相手」を条件の主体にしない。位置は flat と同じ)"""
    return _FREE_WORD_RE.sub(lambda m: "\0" * len(m.group(0)), flat)


def _selection_units(flat: str, ents: list, masked: str) -> list:
    """1 文 → [(種類, 始まり, 終わり)]。種類 = "marker" (列挙が続く選出の印から) / "heading" (印の直前の「<見出し>の」: 捨てる) / "plain"。
    区切りは 選出の印の位置と、接続の語で閉じる「相手」の節の始まり (直前の読点の後)"""
    bounds, markers = {0}, set()
    for m in _MARKER_RE.finditer(flat):
        sep = _LIST_SEP_RE.match(flat, m.end())
        p = sep.end() if sep else m.end()
        if _scan_list(flat, p, len(flat), ents) or _LEAD_BACK_RE.match(flat, p):
            bounds.add(m.start())
            markers.add(m.start())
    opps = [m.start() for m in _OPPONENT_RE.finditer(masked)]
    conns = [m.start() for m in _CONNECTOR_RE.finditer(flat)]
    for k, p in enumerate(opps):
        nxt = opps[k + 1] if k + 1 < len(opps) else len(flat)
        if any(p < c < nxt for c in conns):
            bounds.add(max(flat.rfind(ch, 0, p) for ch in _CLAUSE_BREAKS) + 1)
    order = sorted(bounds)
    units = []
    for k, s in enumerate(order):
        e = order[k + 1] if k + 1 < len(order) else len(flat)
        if s >= e:
            continue
        if s in markers:
            kind = "marker"
        elif e in markers and flat[s:e].rstrip().endswith("の"):
            kind = "heading"
        else:
            kind = "plain"
        units.append((kind, s, e))
    return units


def _marker_rules(flat: str, a: int, b: int, ents: list, named: list, masked: str, dic: ArticleDictionary, source_ref: str) -> tuple:
    """選出の印 (基本選出 / 選出例) からの無条件の規則。候補は印・先発・後発の語の直後の列挙の中だけ"""
    m = _MARKER_RE.match(flat, a)
    recommendation = BUILD_ARTICLE_SELECTION_MARKERS[m.group("marker")]
    cands, lists = _candidates(flat, a, b, ents, named, anywhere=False)
    if not cands:
        return [], [{"category": "selection_members_unresolved", "source_ref": source_ref}]
    rule, unresolved = _make_rule(None, flat, a, b, cands, lists, recommendation, source_ref)
    return [rule], unresolved


def _plain_rules(flat: str, a: int, b: int, ents: list, named: list, masked: str, dic: ArticleDictionary, source_ref: str) -> tuple:
    """印の無い区間: それ以外の分岐 (味方名なし) → 未確定 / 「相手 … 接続の語」の条件つきの規則 / 「初手 A、後発 B と C」の基本選出"""
    unit = flat[a:b]

    def miss(category: str) -> tuple:
        return [], [{"category": category, "source_ref": source_ref}]

    if any(w in unit for w in BUILD_ARTICLE_SELECTION_ELSE_WORDS) and not _candidates(flat, a, b, ents, named, anywhere=True)[0]:
        return miss("selection_else_branch_members_unspecified")
    conns = [c for c in _CONNECTOR_RE.finditer(flat, a, b) if _OPPONENT_RE.search(masked, a, c.start())]
    if conns:
        cond = conn = None
        for conn in conns:                             # 条件の節は接続の語まで (「いなければ」の否定を見る)。読めた最初の接続の語で閉じる
            cond = parse_selection_condition(flat[a:conn.end()], ents, dic)
            if cond is not None:
                break
        if cond is None:
            return miss("selection_condition_unknown")
        cands, lists = _candidates(flat, conn.end(), b, ents, named, anywhere=True)
        if not cands:
            return miss("selection_members_unresolved")
        rec = "required" if any(w in unit for w in BUILD_ARTICLE_SELECTION_REQUIRED_WORDS) else "preferred"
        rule, unresolved = _make_rule(cond, flat, conn.end(), b, cands, lists, rec, source_ref)
        return [rule], unresolved
    if any(w in unit for w in BUILD_ARTICLE_LEAD_WORDS) and any(w in unit for w in BUILD_ARTICLE_BACK_WORDS):
        cands, lists = _candidates(flat, a, b, ents, named, anywhere=False)
        if cands:
            rule, unresolved = _make_rule(None, flat, a, b, cands, lists, "default", source_ref)
            return [rule], unresolved
    return [], []


def extract_selection_rules(sentence: str, members: list, dic: ArticleDictionary, source_ref: str,
                            members_named_only: Optional[list] = None) -> tuple:
    """全体の節の 1 文 → (選出規則の列, 未確定の列)。規則の形 (schema 2、docs/ARTICLE_BANK_DESIGN_1006.md §3.4):
    {"kind": "author_selection_rule", "condition": None | 条件 (parse_selection_condition), "selected_members": [個体 id],
     "selected_species": [{"species_id", "base_species_id"}], "lead": 個体 id | None, "lead_species": 種 id | None,
     "recommendation": "preferred" | "required" | "default", "exact_trio": bool, "example": bool, "free_slots": int, "source_ref"}
    - 条件つき: 「相手」の後の接続の語 (BUILD_ARTICLE_CONDITION_CONNECTORS。「場合」単独を含む) までを条件の節にする。語彙の表に無い
      条件は規則にせず未確定 (selection_condition_unknown)。evaluation が unknown の規則も保存する (条件付きの上乗せに使わないだけ)。
      recommendation は必須の語があれば required、無ければ preferred
    - 無条件: 「基本選出は A(初手)、B、C」→ default、「<見出し>の選出例は A、B、C など」→ preferred (見出しは捨てて条件にしない。同じ節の
      別の文の条件つき規則と結びつけない)、「初手 A、後発 B と C」→ default。default は無条件の推奨 (必須・選出確率 100% ではない)
    - 候補: 味方の個体 (型あり) → selected_members、それ以外の種 → selected_species (基本種とフォルムを区別し、個体 id を作らない)。
      種名だけ分かる個体 (members_named_only。変換層が渡す) は味方として扱う。それ以外の辞書の種は選出の列挙 (印・先発の語の直後か
      「を選出」の直前) の中だけ (相手への言及を混ぜない)
    - 先発は (初手) / （初手） の印・「初手は X」「X を先発」が 1 つに決まるときだけ (記載順から推測しない)。example = 例示の語
      (BUILD_ARTICLE_EXAMPLE_WORDS) が列挙に付く、free_slots = 書かれた未指定の枠の数 (書かれていなければ 0)、exact_trio = 候補が
      ちょうど BSS_PICK_COUNT 体で例示・未指定の枠が無い。選出確率は作らない。それ以外の分岐 (味方名なし) は未確定
    予測で使うときの注意: 相手の選出を予測するとき、記事の「相手に X がいる」は**自分側の構築**についての条件 (記事の筆者側 = 今回の相手、
    記事の「相手」= 今回の自分)。判定は自分の 6 体 (と型) で行う (取り違えると筆者の構築の側で条件を見てしまう。条件を見る側が自分なので
    set_known_only も自分の型で判定できる)。初版で組の予測分布に直接使うのは exact_trio かつ条件が評価できる (evaluation が unknown
    でない) 規則だけ。基本選出 (default) は必須・確率 100% にしない。species_present の不在 (present = False) は、未確定の枠に X が
    残り得るなら成立と断定しない"""
    flat, ents = entities_of(sentence, members, dic)
    named = normalize_named_only(members_named_only)
    masked = _mask_free_words(flat)
    rules, unresolved = [], []
    for kind, a, b in _selection_units(flat, ents, masked):
        if kind == "heading":
            continue
        fn = _marker_rules if kind == "marker" else _plain_rules
        rs, us = fn(flat, a, b, ents, named, masked, dic, source_ref)
        rules.extend(rs)
        unresolved.extend(us)
    return rules, unresolved


# ------------------------------------------------------------------------------------------------------------------
# 記事全体
# ------------------------------------------------------------------------------------------------------------------
def _collect_unresolved_names(line: str, dic: ArticleDictionary, host: Optional[str] = None) -> list:
    """技一覧らしい行 (リンクが 2 つ以上で、解決できたものが半分以上) の解決できなかった表示名 (辞書の補修用。短い語だけ) →
    [{"category": "moves", "text", "host", "site_key"}]"""
    links = [(t, h) for t, h in tokenize_links(strip_bullet(line)) if h is not None]
    if len(links) < 2:
        return []
    bad = [(t, h) for t, h in links if not isinstance(dic.lookup("moves", t), str)]
    if len(bad) * 2 > len(links):
        return []
    return [_name_item("moves", t, h, host) for t, h in bad if len(t) <= BUILD_ARTICLE_NAME_TOKEN_MAX_CHARS]


def _unique_names(items: list) -> list:
    """名前の列の重複を除き、(種別, 表記, ホスト, key) の順に並べる (決定的)"""
    seen = {}
    for it in items:
        seen.setdefault((it["category"], it["text"], it["host"] or "", it["site_key"] or ""), it)
    return [seen[k] for k in sorted(seen)]


def parse_article(marked: str, dic: Optional[ArticleDictionary] = None, max_members: int = BUILD_ARTICLE_MAX_MEMBERS,
                  host: Optional[str] = None, members_named_only: Optional[list] = None) -> dict:
    """リンクつきの本文 → 構造化した結果 (本文を含まない)。
    max_members: 個体の上限 (構築 = BUILD_ARTICLE_MAX_MEMBERS、単体の型 = 1。超えた個体の見出しは警告にして採らない)。
    host: ページのホスト (相対リンクのサイト固有 id を引くためだけに使う)。
    members_named_only: 変換層が渡す「種名だけ分かる個体」([{"species_id", "base_species_id", "mega_stone"}]。既定は空)。
    受け取って結果に載せ、選出規則の候補 (味方) に使うだけで、本文から作らない (個体 id も付けない)。
    {"members": [...], "members_named_only": [...], "claims": [...], "selection_rules": [...], "selection_combinable": bool|None,
     "unresolved": [...], "warnings": [...], "counts": {...}, "parser_version", "dictionary_version",
     ローカル用 (記録と LLM の入力には入れない):
     "unresolved_names": [{"category", "text", "host", "site_key"}] (辞書の補修用。名前 1 語だけ),
     "site_id_observations": [{"host", "category", "key", "id"}] (リンクの表示名が厳密一致で解決できたときのサイト固有 id の観測)}"""
    dic = dic or default_dictionary()
    named = normalize_named_only(members_named_only)
    lines = [ln for ln in (marked or "").splitlines() if ln.strip()]
    members: list = []
    blocks: dict = {}                 # member_id → 説明文の行
    team_lines: list = []
    warnings: list = []
    unresolved: list = []
    unresolved_names: list = []
    section = None                    # None / "member" / "team"
    cur: Optional[dict] = None
    for ln in lines:
        if _MARKER_LINE_RE.match(strip_bullet(ln)):
            section, cur = "team", None              # 「基本選出: A / B / C」: 全体の節に入り (見出しと同じ)、選出の文として読む
            team_lines.append(ln)
            continue
        kind = section_kind(ln)
        if kind:
            section = kind
            if kind == "team":
                cur = None
            continue
        head = parse_member_head(ln, dic) if section != "team" else None
        if head:
            if len(members) >= max_members:
                warnings.append("extra_member_head")
                unresolved.append({"category": "extra_member_head", "source_ref": f"m{len(members) + 1}"})
                cur = None
                continue
            mid = f"m{len(members) + 1}"
            head.update({"id": mid, "points": None, "ev252": None, "actual": None, "moves": [], "alt_move_lines": 0})
            for u in head.pop("unresolved"):
                unresolved.append({"category": u, "source_ref": mid})
            members.append(head)
            blocks[mid] = []
            cur = head
            section = section or "member"
            continue
        if section == "team" or (cur is None and section is None):
            if section == "team":
                team_lines.append(ln)
            continue
        if cur is None:
            continue
        stat = parse_stat_line(ln)
        if stat:
            key = stat["kind"]
            if cur[key] is None:
                cur[key] = stat["values"]
                if stat["unlabeled"] and key == "ev252":
                    cur["notes"].append("stat_line_unlabeled")     # ラベル無しで 252 表示と判定した (能力ポイントのラベル無しは通常の書き方)
            else:
                cur["warnings"].append(f"duplicate_{key}_line")
            continue
        moves = parse_moves_line(ln, dic)
        if moves:
            if not cur["moves"]:
                cur["moves"] = moves
                if len(moves) != BUILD_ARTICLE_MOVES_PER_SET:
                    cur["warnings"].append("moves_count")
            else:
                cur["alt_move_lines"] += 1
            continue
        bad_names = _collect_unresolved_names(ln, dic, host)
        if bad_names:
            unresolved_names.extend(bad_names)
            if not cur["moves"]:
                unresolved.append({"category": "unresolved_move", "source_ref": cur["id"]})
                continue
        blocks[cur["id"]].append(ln)
    claims: list = []
    for mem in members:
        for k, s in enumerate(split_sentences(blocks[mem["id"]]), start=1):
            cs, us, names = extract_claims(s, mem["id"], members, dic, f"{mem['id']}:s{k}")
            claims.extend(cs)
            unresolved.extend(us)
            unresolved_names.extend(names)
    rules: list = []
    combinable = None
    for k, s in enumerate(split_sentences(team_lines), start=1):
        ref = f"team:s{k}"
        cs, us, names = extract_claims(s, "team", members, dic, ref)
        claims.extend(cs)
        unresolved.extend(us)
        unresolved_names.extend(names)
        rs, us2 = extract_selection_rules(s, members, dic, ref, named)
        rules.extend(rs)
        unresolved.extend(us2)
        if any(w in flatten(s) for w in BUILD_ARTICLE_SELECTION_COMBINABLE_WORDS):
            combinable = True
    for mem in members:
        mem.pop("display", None)
        if not mem["moves"]:
            mem["warnings"].append("no_moves_line")
    counts = {"members": len(members), "claims": len(claims), "selection_rules": len(rules), "unresolved": len(unresolved),
              "team_sentences": len(split_sentences(team_lines))}
    return {"members": members, "members_named_only": named, "claims": claims, "selection_rules": rules, "selection_combinable": combinable,
            "unresolved": unresolved, "warnings": warnings, "counts": counts, "unresolved_names": _unique_names(unresolved_names),
            "site_id_observations": site_id_observations(lines, dic, host),
            "parser_version": PARSER_VERSION, "dictionary_version": dic.version}
