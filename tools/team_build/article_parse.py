"""構築記事の決定的な解析 (docs/ARTICLE_BANK_DESIGN_1006.md §3)。本文を LLM に渡さない。

入力は「リンクつきの本文」(HTML を html_to_marked_text で直した Markdown 風の文字列。`[表示名](href)` がリンク)。
  1. 個体の定型部分: `種族名 @ 持ち物 (性格) 特性` の行を個体の見出しにし (辞書で種・持ち物・性格が解決できる行だけ)、
     配分 (能力ポイント / 252 表示 / 実数値を別に保持) と技一覧 (技名だけの最初の行から 4 技) を取る。
  2. 解説: 限定した規則 (W1 弱点 / W2 全体の弱点 / F1 有利 / P1 技の目的 / S1 抜き / S2 抜かれる / D1 耐え / R1 見れる / C1 助ける)
     だけで「誰についての、どんな関係か」を取る。対象は辞書で解決した種 id・タイプ・技 id だけ。文は残さない。
  3. 全体の節: 「相手に <条件> なら <味方名…>」だけを author_selection_rule にする。先発・残りの 3 体・確率は作らない。
出力は id と数値と列挙値だけの辞書。出典は source_ref (個体番号 + 文番号、例 "m2:s3")。
辞書は vision/data/jp_names.json (vision.normalize.normalize の正規化キーで厳密一致。あいまい一致はしない。リンクの番号で id を決めない)。
純粋関数 (ファイル・ネットワークに触れない。辞書と図鑑は読み取りだけ)。テストは tests/test_article_parse.py。
"""
from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from html.parser import HTMLParser
from typing import Optional

from champions_agent.config import (BUILD_ARTICLE_ACTUAL_LABEL, BUILD_ARTICLE_CONDITION_WORDS, BUILD_ARTICLE_EV252_LABEL,
                                    BUILD_ARTICLE_EV252_MAX, BUILD_ARTICLE_HEADING_MAX_CHARS, BUILD_ARTICLE_MAX_MEMBERS,
                                    BUILD_ARTICLE_MEMBER_SECTION_WORDS, BUILD_ARTICLE_MOVES_PER_SET, BUILD_ARTICLE_NAME_TOKEN_MAX_CHARS,
                                    BUILD_ARTICLE_NEGATION_SUFFIXES, BUILD_ARTICLE_PLAIN_NAME_MIN_CHARS,
                                    BUILD_ARTICLE_SELECTION_COMBINABLE_WORDS, BUILD_ARTICLE_SELECTION_REQUIRED_WORDS,
                                    BUILD_ARTICLE_STAT_WORDS, BUILD_ARTICLE_TARGET_MOD_WORDS, BUILD_ARTICLE_TEAM_SECTION_WORDS,
                                    BUILD_GEN_EV_POINT_CAP)
from vision.normalize import JP_NAMES_PATH, normalize

PARSER_VERSION = "article_parse/1"
STAT_ORDER = ("hp", "atk", "def", "spa", "spd", "spe")
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
BULLET_RE = re.compile(r"^\s*(?:[*\-・•]|\d+[.)])\s*")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！!？?])")
ENTITY_RE = re.compile(r"〔(\d+)〕")
ENTITY_GROUP = r"(?:〔\d+〕(?:や|、|と|・|,|,|\s)*)+"
CLAUSE_SEP = "、。！!？?（）()「」"
# 未確定項目の分類 (本文の代わりに残す列挙値。文は残さない)
UNRESOLVED_CATEGORIES = ("broad_matchup_claim", "move_purpose_non_species_target", "selection_else_branch_members_unspecified",
                         "selection_condition_unknown", "selection_members_unresolved", "unresolved_species", "unresolved_item",
                         "unresolved_nature", "unresolved_ability", "unresolved_move", "member_head_incomplete", "stat_line_unlabeled",
                         "extra_member_head", "durability_benchmark_ambiguous_modifiers")


# ------------------------------------------------------------------------------------------------------------------
# 辞書 (日本語名 → id。厳密一致だけ)
# ------------------------------------------------------------------------------------------------------------------
class ArticleDictionary:
    """jp_names.json の正規化キー → id。種は {"id", "num"}、タイプは英語名、他は id 文字列。あいまい一致はしない"""

    def __init__(self, raw: dict):
        self._tables: dict = {}
        for cat in ("species", "items", "moves", "abilities", "natures", "types"):
            table: dict = {}
            for ja, val in (raw.get(cat) or {}).items():
                table.setdefault(normalize(ja), val)
            self._tables[cat] = table
        self._species_ja = {}
        for ja, v in (raw.get("species") or {}).items():
            if isinstance(v, dict):
                self._species_ja.setdefault(v["id"], ja)
        self._move_ja = {}
        for ja, v in (raw.get("moves") or {}).items():
            self._move_ja.setdefault(v, ja)
        self._type_words = sorted((raw.get("types") or {}).items(), key=lambda kv: -len(kv[0]))
        # 素の文字列から種名を探すための一覧 (長い順。短すぎる名前は誤検出するので省く)
        self._species_words = sorted(((ja, v["id"]) for ja, v in (raw.get("species") or {}).items()
                                      if isinstance(v, dict) and len(ja) >= BUILD_ARTICLE_PLAIN_NAME_MIN_CHARS), key=lambda kv: -len(kv[0]))
        self.version = str(len(raw.get("species") or {})) + "/" + str(len(raw.get("moves") or {})) + "/" + str(len(raw.get("items") or {}))

    def lookup(self, category: str, text: str):
        key = normalize(text)
        return self._tables.get(category, {}).get(key) if key else None

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
def default_dictionary() -> ArticleDictionary:
    return ArticleDictionary(json.loads(JP_NAMES_PATH.read_text(encoding="utf-8")))


@lru_cache(maxsize=1)
def mega_table() -> dict:
    """メガ形態 id → (基本種 id, メガ石 id)、メガ石 id → メガ形態 id (図鑑 champions_dex の requiredItem が正)"""
    try:
        from tools.check_mega_items import DEX, mega_stones
        dex = json.loads(DEX.read_text(encoding="utf-8")).get("species", {})
    except Exception:
        return {"forms": {}, "stones": {}}
    forms, stones = {}, {}
    for sid, _name, _req, stone in mega_stones():
        base = re.sub(r"[^a-z0-9]", "", (dex.get(sid, {}).get("baseSpecies") or "").lower()) or None
        forms[sid] = (base, stone)
        if stone:
            stones[stone] = sid
    return {"forms": forms, "stones": stones}


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


# ------------------------------------------------------------------------------------------------------------------
# 節の見出しと個体の見出し
# ------------------------------------------------------------------------------------------------------------------
def _heading_text(line: str) -> Optional[str]:
    """見出しとみなせる行ならその文字列。`#` 始まり、または 箇条書きでもリンクでも文 (。で終わる) でもない短い行"""
    s = line.strip()
    if s.startswith("#"):
        return s.lstrip("#").strip()
    if BULLET_RE.match(s) or LINK_RE.search(s) or s.endswith(("。", "．", ".")):
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
    return {"species_id": form_id, "base_species_id": base_id, "mega_stone": stone, "item": item_id, "nature": nature_id,
            "ability": ability_id, "display": species_text, "warnings": warnings, "notes": notes, "unresolved": unresolved}


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


def _member_names(members: list, dic: ArticleDictionary) -> list:
    """[(名前, member_id)] を長い順に。見出しの表示名、使用形態と基本種の辞書名"""
    names = []
    for mem in members:
        cand = {mem["display"], dic.species_ja(mem["species_id"]) or "", dic.species_ja(mem["base_species_id"]) or ""}
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
    辞書の種名 (長い順、カタカナ語の境界で) ・その個体の採用技名。味方の種のリンク・味方名は member_id を持つ"""
    ents: list = []
    member_by_species = {}
    for mem in members:
        member_by_species.setdefault(mem["species_id"], mem["id"])
        member_by_species.setdefault(mem["base_species_id"], mem["id"])

    def add(kind, ident, member_id=None):
        ents.append({"kind": kind, "id": ident, "member_id": member_id})
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
            flat = _replace_word(flat, name, lambda sid=sid, mid=mid: add("species", sid, mid))
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
    """1 文 → (主張の列, 未確定の列, 解決できなかった技名の列)。subject は "m1" 等の個体 id か "team"。規則は docs §3.3 の表のとおり"""
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
                    unresolved_names.append(mv_text)
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


def extract_selection_rules(sentence: str, members: list, dic: ArticleDictionary, source_ref: str) -> tuple:
    """全体の節の 1 文 → (選出規則の列, 未確定の列)。「相手に <条件> なら <味方名…>」だけを規則にする。
    先発は「先発 / 初手」と味方名が同じ文にあるときだけ。それ以外の分岐 (味方名なし) は未確定"""
    flat, ents = entities_of(sentence, members, dic)
    rules, unresolved = [], []
    member_ids = [ents[i]["member_id"] for i in _group_ids(flat) if ents[i]["member_id"]]
    member_ids = list(dict.fromkeys(member_ids))
    if re.search(r"それ以外|他は|その他|以外は|残り", flat) and not member_ids:
        unresolved.append({"category": "selection_else_branch_members_unspecified", "source_ref": source_ref})
        return rules, unresolved
    conn = re.search(r"ならば|なら|の場合|であれば|なければ|ければ|のとき|の時|には", flat)
    if not conn or "相手" not in flat[:conn.start()]:
        return rules, unresolved
    cond_text, body = flat[:conn.end()], flat[conn.end():]       # 条件の節は接続の語まで含める (「いなければ」の否定を見るため)
    predicate = next((pred for pred, words in BUILD_ARTICLE_CONDITION_WORDS.items() if any(w in cond_text for w in words)), None)
    body_members = [ents[i]["member_id"] for i in _group_ids(body) if ents[i]["member_id"]]
    body_members = list(dict.fromkeys(body_members))
    if predicate is None:
        unresolved.append({"category": "selection_condition_unknown", "source_ref": source_ref})
        return rules, unresolved
    if not body_members:
        unresolved.append({"category": "selection_members_unresolved", "source_ref": source_ref})
        return rules, unresolved
    lead = None
    lm = re.search(r"〔(\d+)〕(?:を|が|から)?(?:先発|初手)|(?:先発|初手)(?:は|に|で)〔(\d+)〕", flat)
    if lm:
        e = ents[int(lm.group(1) or lm.group(2))]
        lead = e["member_id"] if e["member_id"] in body_members else None
    value = "absent" if re.search(r"(?:いない|いなければ|無い|なし)", cond_text) else "present"
    rec = "required" if any(w in flat for w in BUILD_ARTICLE_SELECTION_REQUIRED_WORDS) else "preferred"
    rules.append({"kind": "author_selection_rule", "condition": {"subject": "article_opponent", "predicate": predicate, "value": value},
                  "selected_members": body_members, "lead": lead, "recommendation": rec, "source_ref": source_ref})
    return rules, unresolved


# ------------------------------------------------------------------------------------------------------------------
# 記事全体
# ------------------------------------------------------------------------------------------------------------------
def _collect_unresolved_names(line: str, dic: ArticleDictionary) -> list:
    """技一覧らしい行 (リンクが 2 つ以上で、解決できたものが半分以上) の解決できなかった表示名 (辞書の補修用。短い語だけ)"""
    links = [t for t, h in tokenize_links(strip_bullet(line)) if h is not None]
    if len(links) < 2:
        return []
    bad = [t for t in links if not isinstance(dic.lookup("moves", t), str)]
    if len(bad) * 2 > len(links):
        return []
    return [t for t in bad if len(t) <= BUILD_ARTICLE_NAME_TOKEN_MAX_CHARS]


def parse_article(marked: str, dic: Optional[ArticleDictionary] = None) -> dict:
    """リンクつきの本文 → 構造化した結果 (本文を含まない)。
    {"members": [...], "claims": [...], "selection_rules": [...], "selection_combinable": bool|None, "unresolved": [...],
     "warnings": [...], "counts": {...}, "unresolved_names": [...] (ローカルの辞書補修用。記録と LLM の入力には入れない),
     "parser_version", "dictionary_version"}"""
    dic = dic or default_dictionary()
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
        kind = section_kind(ln)
        if kind:
            section = kind
            if kind == "team":
                cur = None
            continue
        head = parse_member_head(ln, dic) if section != "team" else None
        if head:
            if len(members) >= BUILD_ARTICLE_MAX_MEMBERS:
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
        bad_names = _collect_unresolved_names(ln, dic)
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
        rs, us2 = extract_selection_rules(s, members, dic, ref)
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
    return {"members": members, "claims": claims, "selection_rules": rules, "selection_combinable": combinable,
            "unresolved": unresolved, "warnings": warnings, "counts": counts, "unresolved_names": sorted(set(unresolved_names)),
            "parser_version": PARSER_VERSION, "dictionary_version": dic.version}
