"""記事のページ → 構築ごとの配列 (unit)、文字コード、規制名 (tools/team_build/article_units) のテスト
(docs/ARTICLE_BANK_DESIGN_1006.md §3.9)。HTTP はしない。

    python -m tests.test_article_units
"""
from __future__ import annotations

from tests.test_article_bank import SINGLE
from tests.test_article_parse import NAMED_FIVE, REP, SYNTHETIC
from tools.team_build import article_parse as P
from tools.team_build import article_units as U
from tools.team_build.articles_ingest import normalize_url

PAGE = "<html><head>{meta}<title>t</title></head><body><h2>使用ポケモン</h2><p>ペリッパー＠しめったいわ（ひかえめ）あめふらし ～①</p></body></html>"
TEXT = "ペリッパー＠しめったいわ（ひかえめ）あめふらし ～①"


def test_decode_html_order():
    """文字コード: HTTP ヘッダの charset → HTML の meta (charset / http-equiv) → 既定 UTF-8 (errors="replace")"""
    # meta charset (EUC-JP のバイト列を meta で読む。① は EUC-JP に無いので含めない)
    euc_page = PAGE.replace(" ～①", "").format(meta='<meta charset="EUC-JP">')
    euc = euc_page.encode("euc_jp")
    assert U.decode_html(euc, "text/html") == euc_page and U.decode_html(euc, None) == euc_page
    # http-equiv の Content-Type (Shift_JIS は cp932 で読むので ～ と ① も読める)
    sjis_page = PAGE.format(meta='<meta http-equiv="Content-Type" content="text/html; charset=Shift_JIS">')
    assert U.decode_html(sjis_page.encode("cp932")) == sjis_page
    # ヘッダが meta より優先 (meta が EUC-JP と言っても、ヘッダが UTF-8 なら UTF-8 で読む)
    utf_page = PAGE.format(meta='<meta charset="EUC-JP">')
    assert U.decode_html(utf_page.encode("utf-8"), "text/html; charset=UTF-8") == utf_page
    assert U.decode_html(euc, 'text/html; charset="euc-jp"') == euc_page                 # 引用符つきのヘッダ
    # ヘッダの charset が読めない名前なら meta へ、meta も無ければ UTF-8
    assert U.decode_html(euc, "text/html; charset=x-unknown-9") == euc_page
    plain = PAGE.format(meta="")
    assert U.decode_html(plain.encode("utf-8"), "text/html") == plain
    assert U.decode_html(b"\xef\xbb\xbf" + plain.encode("utf-8")) == plain                # 先頭の BOM は落とす
    # 宣言が無い EUC-JP は UTF-8 として読み、例外にしない (置換文字になる)
    broken = U.decode_html(PAGE.replace(" ～①", "").format(meta="").encode("euc_jp"))
    assert chr(0xFFFD) in broken and "ペリッパー" not in broken                           # U+FFFD (置換文字)
    # </head> より後 (本文中) の meta は見ない
    late = ("<html><head></head><body><meta charset='EUC-JP'>" + TEXT + "</body></html>").encode("utf-8")
    assert TEXT in U.decode_html(late)
    assert U.charset_from_content_type("text/html; charset=EUC-JP") == "EUC-JP" and U.charset_from_content_type(None) is None
    assert U.codec_name("Shift_JIS") == "cp932" and U.codec_name("windows-31j") == "cp932" and U.codec_name("bogus") is None
    print("test_decode_html_order OK")


def test_url_key_keeps_percent_encoding():
    """URL の既存のパーセント表現は変換しない。正規化は重複排除の鍵 (url_hash) にだけ使う"""
    u1 = "https://Yakkun.com/ch/search/%A5%DA%A5%EA/?utm_source=x#top"
    u2 = "https://yakkun.com/ch/search/%A5%DA%A5%EA"
    assert "%A5%DA%A5%EA" in normalize_url(u1)                                            # EUC-JP の %-表現をそのまま残す
    assert U.url_hash(u1) == U.url_hash(u2) and U.url_hash(u1) != U.url_hash("https://yakkun.com/ch/search/%A5%DA")
    print("test_url_key_keeps_percent_encoding OK")


def test_regulation_from_text():
    """記事固有の記載 (題名・タグ) の規制名 → 規制 id。全角は NFKC、ダッシュ類は表で吸収。複数・無しは None"""
    mc, mb = "gen9championsbssregmc", "gen9championsbssregmb"
    assert U.regulation_from_text("【ポケモンチャンピオンズ】レギュレーションM-C 最終100位 雨パ") == mc
    assert U.regulation_from_text("M" + chr(0x2212) + "C 構築") == mc                    # U+2212 マイナス記号 (NFKC で - にならない)
    assert U.regulation_from_text("M" + chr(0x2013) + "C") == mc                         # U+2013 EN DASH
    assert U.regulation_from_text("ＭーＣ シーズン") == mc and U.regulation_from_text("Ｍ－Ｃ") == mc   # 全角・長音
    assert U.regulation_from_text("規制 M-C") == mc and U.regulation_from_text("M - C") == mc
    assert U.regulation_from_text("M-B の構築を M-B 用に調整") == mb                      # 同じ規制の重複は 1 つ
    assert U.regulation_from_text("M-B と M-C の両方で使った構築") is None                # 曖昧
    assert U.regulation_from_text("シーズン M-6 の構築") is None                          # シーズンは規制名ではない
    assert U.regulation_from_text("M-CS") is None and U.regulation_from_text("SM-C") is None   # 英数字の続く語の一部は拾わない
    assert U.regulation_from_text("") is None and U.regulation_from_text(None) is None
    print("test_regulation_from_text OK")


def test_units_and_adapters():
    """変換層の出力は構築ごとの配列。登録の無いホストは generic (ページ全体を 1 つの team unit)"""
    assert U.ADAPTERS == {}                                                               # ホスト別の変換層は取得の許可待ち (登録口だけ)
    html = "<html><body><nav>menu</nav><h2>使用ポケモン</h2><p>" + TEXT + "</p></body></html>"
    src, meta = {"host": "a.example", "url_hash": "u1"}, {"regulation": "unknown"}
    units = U.generic_units(html, src, meta)
    assert len(units) == 1 and units[0]["kind"] == "team" and units[0]["marked"] == P.html_to_marked_text(html)
    assert units[0]["source"] == src and units[0]["meta"] == meta and units[0]["source"] is not src
    assert U.units_for("a.example", html, src, meta) == units
    calls = []

    def adapter(html_text, source, meta_):
        calls.append(len(html_text))
        return [U.make_unit("single_set", SINGLE, meta_, source), U.make_unit("single_set", SINGLE, meta_, source)]
    two = U.units_for("WWW.Example.invalid", html, src, meta, adapters={"example.invalid": adapter})
    assert calls == [len(html)] and [u["kind"] for u in two] == ["single_set", "single_set"]
    assert U.units_for("other.example", html, src, meta, adapters={"example.invalid": adapter}) == units
    try:
        U.units_for("example.invalid", html, src, meta, adapters={"example.invalid": lambda h, s, m: [{"kind": "pair", "marked": ""}]})
        raise AssertionError("kind の検査が効いていない")
    except ValueError:
        pass
    try:
        U.make_unit("pair", "")
        raise AssertionError("kind の検査が効いていない")
    except ValueError:
        pass
    # 解析は unit ごと (個体の上限は種類ごと)
    pairs = U.parse_units([U.make_unit("team", SYNTHETIC), U.make_unit("single_set", SINGLE), U.make_unit("single_set", SYNTHETIC)])
    assert [p["counts"]["members"] for _u, p in pairs] == [6, 1, 1]
    assert pairs[1][1]["warnings"] == [] and "extra_member_head" in pairs[2][1]["warnings"]
    print("test_units_and_adapters OK")


def test_units_members_named_only():
    """unit の members_named_only (変換層が渡す種名だけの個体): 既定は空、形を検査して正規化、parse_units が解析器に渡す"""
    assert U.make_unit("team", SYNTHETIC)["members_named_only"] == []
    u = U.make_unit("team", REP, members_named_only=list(NAMED_FIVE))
    assert u["members_named_only"][0] == {"species_id": "pelipper", "base_species_id": "pelipper", "mega_stone": None}
    ((_u, parsed),) = U.parse_units([u])
    assert parsed["members_named_only"] == u["members_named_only"] and parsed["counts"]["members"] == 1
    assert [s["species_id"] for s in parsed["selection_rules"][0]["selected_species"]] == ["gholdengo", "salamencemega"]
    try:
        U.make_unit("team", REP, members_named_only=[{"display": "メガボーマンダ"}])
        raise AssertionError("species_id の無い要素を通した")
    except ValueError as e:
        assert "メガボーマンダ" not in str(e)                                                 # 例外の文言に表記を残さない
    print("test_units_members_named_only OK")


def main() -> None:
    test_decode_html_order()
    test_url_key_keeps_percent_encoding()
    test_regulation_from_text()
    test_units_and_adapters()
    test_units_members_named_only()
    print("ALL OK")


if __name__ == "__main__":
    main()
