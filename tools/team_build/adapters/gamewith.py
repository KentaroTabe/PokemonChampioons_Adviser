"""GameWith (gamewith.jp) の「最強パーティランキング」ページ → 構築ごとの unit (docs/ARTICLE_BANK_DESIGN_1006.md §3.11)。

ページ構造 (2026-10-06 に許可した URL を 1 回閲覧して確認。HTML は保存していない):
  div#article-body
    (冒頭の段落: 「レギュレーションM-Cは…」のような記事固有の記述 → 規制の根拠 article_text)
    h2 "<軸>構築"                      … 次の h2 までが 1 構築 (10 構築。末尾の h2 「…の著者情報」「関連ページ」は対象外)
      h3 "<軸>構築の詳細"
      table (th 評価 / チームID、td にゲーム内のチーム ID)
      ol.wd-pkch-pkmlist[data-auto-generate] > li ×6 … 個体。**生の HTML では li の data-* 属性に全部入っている** (表示は JS が組み立てる。
        2026-10-06 の取得で確認): data-name (通常の形態の種名) / data-url (サイト固有 id) / data-item-name / data-ability / data-moveN-name (N=1..4) /
        data-nature / data-stat (通常の形態の実数値 HABCDS、カンマ区切り) / data-ev (能力ポイント、0 を含む) / data-init-form (form0 = 通常、
        form1 = メガ。既定の表示) / data-form1-name / data-form1-url / data-form1-ability (メガ後の形態)。
        描画後の DOM (ブラウザで見える形) では li > div._wrapper > div._form (通常 / メガ、_active が既定) > div._header (div._name > a、
        div._item > card) / div._body (div._ability > card、div._moves > div > card ×4、div._st > span ×6、div._ev > span ×6、div._nature_value)。
        変換層は data-* を先に読み、無ければ描画後の形を読む
      p (構築の説明)
      h3 "選出紹介" → h4 "基本選出" | "<X>選出" | 小話の見出し → table (td: img[alt=種名] 種名 <br> "(初手)" | "など") → div.gw-info → p (文)
出力: unit = {"kind": "team", "marked": 正規形 (使用ポケモン の節に 6 体の見出し行・配分の行・実数値の行・技一覧の行、戦術と解説 の節に
説明文・選出の文), "meta": {"regulation" (記事固有の記述から), "regulation_basis", "team_code" (ASCII), "axis_species_id"}, "source",
"local": {"unresolved_names": [...]} (辞書で解決できなかった種名 (ローカルの辞書補修用。記録に入れない))}。
- メガ形態は _active の form (ページの既定はメガ)。見出し行の特性はその form の特性 (記事の慣例と同じ: メガ後の特性)
- 選出の表は文に直して渡す: 基本選出 →「基本選出は A(初手)、Bなど、C。」、他の見出し →「<見出し>の選出例は A(初手)、B、Cなど。」
  (条件は同じ節の文 (ページの文) から規則抽出が読む。表の 3 体に条件を作らない)
- 編集部の推奨なので source.publisher_kind = editorial_site、usage_evidence = none を既定にする (呼び出し側が上書きできる)
- 本文は unit の中だけで扱い、記録には入れない。ページの HTML を保存しない
純粋関数。テストは tests/test_adapter_gamewith.py (同じ構造の合成 HTML)。
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Optional

from tools.team_build.article_units import make_unit, regulation_from_text

HOST = "gamewith.jp"
ARTICLE_BODY_ID = "article-body"
TEAM_HEADING_SUFFIX = "構築"
PARTY_LIST_CLASS = "wd-pkch-pkmlist"
FORM_CLASS = "_form"
ACTIVE_CLASS = "_active"
SELECTION_SECTION_WORD = "選出紹介"
BASIC_SELECTION_WORD = "基本選出"
LEAD_MARK = "初手"
EXAMPLE_MARK = "など"
EXCLUDED_TEAM_HEADINGS = ("著者情報", "関連ページ")
STAT_LABELS = ("HP", "攻撃", "防御", "特攻", "特防", "素早")       # 正規形の配分の行の能力名 (BUILD_ARTICLE_STAT_WORDS にある語)
DEFAULT_SOURCE = {"publisher_kind": "editorial_site", "usage_evidence": "none"}
_SITE_ID_RE = re.compile(r"/pokemon-champions/(\d+)")


# ------------------------------------------------------------------------------------------------------------------
# 最小の DOM (html.parser)
# ------------------------------------------------------------------------------------------------------------------
class Node:
    __slots__ = ("tag", "attrs", "children", "parent", "text")

    def __init__(self, tag: str, attrs: dict, parent: Optional["Node"]):
        self.tag, self.attrs, self.parent = tag, attrs, parent
        self.children: list = []
        self.text: Optional[str] = None                # テキストノードは tag = "#text"

    def classes(self) -> set:
        return set((self.attrs.get("class") or "").split())

    def iter(self):
        yield self
        for c in self.children:
            yield from c.iter()

    def find_all(self, tag: Optional[str] = None, cls: Optional[str] = None, attr: Optional[tuple] = None) -> list:
        out = []
        for n in self.iter():
            if n is self or n.tag == "#text":
                continue
            if tag and n.tag != tag:
                continue
            if cls and cls not in n.classes():
                continue
            if attr and n.attrs.get(attr[0]) != attr[1]:
                continue
            out.append(n)
        return out

    def find(self, tag: Optional[str] = None, cls: Optional[str] = None, attr: Optional[tuple] = None) -> Optional["Node"]:
        found = self.find_all(tag, cls, attr)
        return found[0] if found else None

    def texts(self) -> list:
        """子孫のテキストノード (空白だけは除く、前後の空白を落とす) を順に"""
        return [n.text.strip() for n in self.iter() if n.tag == "#text" and n.text and n.text.strip()]

    def text_content(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self.texts())).strip()


_VOID = {"img", "br", "hr", "meta", "link", "input", "source", "wbr"}


class _DomBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {}, None)
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: (v or "") for k, v in attrs}, self.cur)
        self.cur.children.append(node)
        if tag not in _VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(Node(tag, {k: (v or "") for k, v in attrs}, self.cur))

    def handle_endtag(self, tag):
        n = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:
            self.cur = n.parent

    def handle_data(self, data):
        if not data:
            return
        t = Node("#text", {}, self.cur)
        t.text = data
        self.cur.children.append(t)


def build_dom(html_text: str) -> Node:
    p = _DomBuilder()
    p.feed(html_text or "")
    p.close()
    return p.root


# ------------------------------------------------------------------------------------------------------------------
# ページ → 構築のブロック
# ------------------------------------------------------------------------------------------------------------------
def _article_body(root: Node) -> Node:
    body = root.find(attr=("id", ARTICLE_BODY_ID))
    return body if body is not None else root


def _is_team_heading(node: Node) -> bool:
    if node.tag != "h2":
        return False
    t = node.text_content()
    return t.endswith(TEAM_HEADING_SUFFIX) and not any(w in t for w in EXCLUDED_TEAM_HEADINGS)


def team_blocks(body: Node) -> tuple:
    """記事本体の直下の並び → (冒頭 (最初の構築の h2 まで) の要素の列, [(構築の見出しの文字列, その構築の要素の列)])。
    構築の見出し (h2 "X構築") から次の h2 までを 1 構築にする"""
    intro: list = []
    blocks: list = []
    cur: Optional[list] = None
    for node in body.children:
        if node.tag == "#text":
            continue
        if node.tag == "h2":
            if _is_team_heading(node):
                cur = []
                blocks.append((node.text_content(), cur))
            else:
                cur = None if blocks else intro        # 構築の前の h2 (「最強パーティランキング」) は冒頭に含める
                if cur is intro:
                    intro.append(node)
            continue
        if cur is not None:
            cur.append(node)
        elif not blocks:
            intro.append(node)
    return intro, blocks


def _site_id(href: Optional[str]) -> Optional[str]:
    m = _SITE_ID_RE.search(href or "")
    return m.group(1) if m else None


def parse_form(form: Node) -> Optional[dict]:
    """div._form → {"name", "href", "item", "ability", "moves", "actual", "points", "nature"} (文字列は表示名。id の解決は解析器が辞書で行う)"""
    name_a = None
    name_div = form.find("div", "_name")
    if name_div is not None:
        name_a = name_div.find("a")
    name = name_a.text_content() if name_a is not None else (name_div.text_content() if name_div is not None else "")
    if not name:
        return None
    item_div = form.find("div", "_item")
    item = ""
    if item_div is not None:
        cards = item_div.find_all("card")
        item = cards[0].text_content() if cards else ""
    ability_div = form.find("div", "_ability")
    ability = ability_div.text_content() if ability_div is not None else ""
    moves_div = form.find("div", "_moves")
    moves = [c.text_content() for c in moves_div.find_all("card")] if moves_div is not None else []
    st = form.find("div", "_st")
    ev = form.find("div", "_ev")
    actual = [s.text_content() for s in st.find_all("span")] if st is not None else []
    points = [s.text_content() for s in ev.find_all("span")] if ev is not None else []
    nature_div = form.find("div", "_nature_value")
    nature = nature_div.text_content() if nature_div is not None else ""
    return {"name": name, "href": name_a.attrs.get("href") if name_a is not None else None, "item": item, "ability": ability,
            "moves": moves, "actual": actual, "points": points, "nature": nature}


def parse_li_data(li: Node) -> Optional[dict]:
    """生の HTML の li (data-* 属性に個体の情報。表示は JS が組み立てる。2026-10-06 の取得で確認) → parse_form と同じ形。
    data-init-form = form1 ならメガ後の形態 (data-form1-name / -url / -ability) を使う。data-stat (実数値) は出さない: 表示用に
    JS が組み立てる値で、メガ後の形態の個体 (data-name がメガの名前のものも含む) では通常の形態の値になっていて再計算と合わない
    (10/6 の取得で 2 構築が矛盾になった)。能力ポイント (data-ev) と性格が正で、実数値はこちらで再計算できる。data-name が無ければ None"""
    a = li.attrs
    name = (a.get("data-name") or "").strip()
    if not name:
        return None
    init = (a.get("data-init-form") or "form0").strip()
    use_alt = init != "form0" and bool(a.get(f"data-{init}-name"))
    if use_alt:
        name = a.get(f"data-{init}-name", name).strip()
        url_id = a.get(f"data-{init}-url") or ""
        ability = (a.get(f"data-{init}-ability") or a.get("data-ability") or "").strip()
    else:
        url_id = a.get("data-url") or ""
        ability = (a.get("data-ability") or "").strip()
    moves = [(a.get(f"data-move{i}-name") or "").strip() for i in range(1, 5)]
    moves = [m for m in moves if m]
    points = [p.strip() for p in (a.get("data-ev") or "").split(",") if p.strip() != ""]
    href = f"https://{HOST}/pokemon-champions/{url_id}" if url_id.isdigit() else None
    return {"name": name, "href": href, "item": (a.get("data-item-name") or "").strip(), "ability": ability, "moves": moves,
            "actual": [], "points": points, "nature": (a.get("data-nature") or "").strip()}


def active_form(li: Node) -> Optional[Node]:
    """li の表示中の form (_active のトグルの順番と同じ位置の form。トグルが無ければ最初の form)"""
    forms = li.find_all("div", FORM_CLASS)
    if not forms:
        return None
    toggle = li.find("div", "wd-pkch-form-toggle")
    if toggle is not None:
        buttons = toggle.find_all("button")
        for i, b in enumerate(buttons):
            if ACTIVE_CLASS in b.classes() and i < len(forms):
                return forms[i]
    return forms[0]


def _int_or_zero(s: str) -> int:
    s = s.strip()
    return int(s) if s.isdigit() else 0


def member_lines(member: dict) -> list:
    """個体 → 正規形の行 (見出し行・配分の行・実数値の行・技一覧の行)。表示名はそのまま (id の解決は解析器)"""
    head_name = f"[{member['name']}]({member['href']})" if member.get("href") else member["name"]
    lines = [f"* {head_name}@{member['item']}({member['nature']}){member['ability']}"]
    pts = [_int_or_zero(p) for p in member["points"]]
    if len(pts) == len(STAT_LABELS):
        parts = [f"{label}:{v}" for label, v in zip(STAT_LABELS, pts) if v]
        if parts:
            lines.append("* " + " / ".join(parts))
    act = [_int_or_zero(a) for a in member["actual"]]
    if len(act) == len(STAT_LABELS) and all(act):
        lines.append("* 実数値:" + "-".join(str(a) for a in act))
    if member["moves"]:
        lines.append("* " + " / ".join(member["moves"]))
    return lines


def team_code(block: list) -> Optional[str]:
    """詳細の表 (th チームID) の td の値 (ASCII の英数字と空白だけ受け付ける。空白は落とす)"""
    for node in block:
        for table in ([node] if node.tag == "table" else node.find_all("table")):
            ths = [th.text_content() for th in table.find_all("th")]
            if "チームID" not in ths:
                continue
            tds = table.find_all("td")
            idx = ths.index("チームID")
            if idx < len(tds):
                code = re.sub(r"\s+", "", tds[idx].text_content())
                if code and re.fullmatch(r"[A-Za-z0-9]+", code):
                    return code
    return None


def selection_cells(table: Node) -> list:
    """選出の表 → [(種名, 初手か, 例示か)]。種名は img の alt か最初のテキスト、印は残りのテキスト"""
    out = []
    for td in table.find_all("td"):
        img = td.find("img")
        texts = [t for t in td.texts() if not t.startswith("<img")]       # noscript の中の生 HTML は除く
        name = (img.attrs.get("alt") if img is not None else None) or (texts[0] if texts else "")
        rest = " ".join(t for t in texts if t != name)
        if name:
            out.append((name, LEAD_MARK in rest, EXAMPLE_MARK in rest))
    return out


def selection_sentence(title: str, cells: list) -> Optional[str]:
    """選出の表 → 1 文。基本選出は「基本選出は A(初手)、Bなど、C。」、他は「<見出し>の選出例は A(初手)、B、Cなど。」"""
    if not cells:
        return None
    names = []
    for name, lead, example in cells:
        names.append(name + ("(初手)" if lead else "") + (EXAMPLE_MARK if example else ""))
    listed = "、".join(names)
    if BASIC_SELECTION_WORD in title:
        return f"{BASIC_SELECTION_WORD}は{listed}。"
    return f"{title}の選出例は{listed}。"


def _paragraph_lines(node: Node) -> list:
    """p / div.gw-info などの文の要素 → 行 (改行で分ける)。空は除く"""
    text = node.text_content()
    return [text] if text else []


def team_unit_text(block: list) -> tuple:
    """1 構築の要素の列 → (正規形の本文, 個体 (表示名の辞書) の列, 選出の文の数)"""
    members: list = []
    desc_lines: list = []
    selection_lines: list = []
    in_selection = False
    current_title = ""
    for node in block:
        if node.tag == "ol" and PARTY_LIST_CLASS in node.classes():
            for li in node.find_all("li"):
                if li.parent is not node:
                    continue
                m = parse_li_data(li)                        # 生の HTML (data-* 属性)。無ければ描画後の DOM (div._form)
                if m is None:
                    form = active_form(li)
                    m = parse_form(form) if form is not None else None
                if m:
                    members.append(m)
            continue
        if node.tag == "h3":
            in_selection = SELECTION_SECTION_WORD in node.text_content()
            current_title = ""
            continue
        if node.tag == "h4":
            current_title = node.text_content()
            continue
        if node.tag == "p" or (node.tag == "div" and "gw-info" in node.classes()):
            (selection_lines if in_selection else desc_lines).extend(_paragraph_lines(node))
            continue
        if in_selection and current_title and (node.tag == "table" or node.find("table") is not None):
            table = node if node.tag == "table" else node.find("table")
            sent = selection_sentence(current_title, selection_cells(table))
            if sent:
                selection_lines.append(sent)
    lines = ["使用ポケモン"]
    for m in members:
        lines.extend(member_lines(m))
    lines.append("戦術と解説")
    lines.extend(desc_lines)
    lines.extend(selection_lines)
    return "\n".join(lines), members, len(selection_lines)


def intro_text(intro: list) -> str:
    return "\n".join(ln for node in intro if node.tag in ("p", "h2", "h3", "div") for ln in _paragraph_lines(node))


def gamewith_units(html_text: str, source: Optional[dict] = None, meta: Optional[dict] = None, dic=None) -> list:
    """ページの HTML → 構築ごとの unit の列 (kind = team)。規制は冒頭の記事固有の記述から (無ければ meta のまま / unknown)。
    source は editorial_site / none を既定にして呼び出し側の値で上書き。辞書 (dic、無ければ default_dictionary) で解決できない
    種名・持ち物・性格・特性を unit["local"]["unresolved_names"] に残す (記録には入れない。別名の候補に回る)"""
    if dic is None:
        from tools.team_build.article_parse import default_dictionary
        dic = default_dictionary()
    root = build_dom(html_text)
    body = _article_body(root)
    intro, blocks = team_blocks(body)
    reg = regulation_from_text(intro_text(intro))
    base_meta = dict(meta or {})
    if reg:
        base_meta.setdefault("regulation", reg)
        base_meta.setdefault("regulation_basis", "article_text")
    src = dict(DEFAULT_SOURCE)
    src.update(source or {})
    src.setdefault("host", HOST)
    units = []
    for idx, (title, block) in enumerate(blocks):
        marked, members, n_sel = team_unit_text(block)
        if not members:
            continue
        m = dict(base_meta)
        m["unit_index"] = idx
        code = team_code(block)
        if code:
            m["team_code"] = code
        axis = title[: -len(TEAM_HEADING_SUFFIX)] if title.endswith(TEAM_HEADING_SUFFIX) else title
        if dic is not None:
            sid = dic.species_id(axis)
            if sid:
                m["axis_species_id"] = sid
        unit = make_unit("team", marked, m, src)
        if dic is not None:
            bad = [{"category": "species", "text": mem["name"], "host": HOST, "site_key": _site_id(mem.get("href"))}
                   for mem in members if not dic.species_id(mem["name"])]
            for cat, key in (("items", "item"), ("natures", "nature"), ("abilities", "ability")):     # 見出し行の他の項目も (別名の候補へ)
                for mem in members:
                    if mem.get(key) and not isinstance(dic.lookup(cat, mem[key]), str):
                        bad.append({"category": cat, "text": mem[key], "host": HOST, "site_key": None})
            unit["local"] = {"unresolved_names": bad, "n_members": len(members), "n_selection_lines": n_sel}
        else:
            unit["local"] = {"unresolved_names": [], "n_members": len(members), "n_selection_lines": n_sel}
        units.append(unit)
    return units
