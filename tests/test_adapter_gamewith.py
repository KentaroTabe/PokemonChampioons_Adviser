"""GameWith の変換層 (tools/team_build/adapters/gamewith) のテスト。素材は 2026-10-06 に確認したページ構造 (class 名と入れ子) を写した
合成 HTML (数値・文は架空。実ページの HTML は保存しない)。

    python -m tests.test_adapter_gamewith
"""
from __future__ import annotations

from tools.team_build import article_bank as B
from tools.team_build import article_parse as P
from tools.team_build.adapters import host_adapters
from tools.team_build.adapters import gamewith as G
from tools.team_build.article_units import units_for

GW = "https://gamewith.jp/pokemon-champions"


def _form(name, sid, item, ability, moves, actual, points, nature, active=False):
    cards = "".join(f"<div><img alt='t'><card class='is-tooltip-enabled'>{m}</card></div>" for m in moves)
    st = "".join(f"<span>{v}</span>" for v in actual)
    ev = "".join(f"<span>{v}</span>" for v in points)
    return (f"<div class='_form{' _active' if active else ''}'><div class='_header'><div class='_icon'><a href='{GW}/{sid}'><img alt='{name}'></a></div>"
            f"<div class='_name'><a href='{GW}/{sid}'>{name}</a><div class='_additional_info'><span class='_sum'>(600)</span></div></div>"
            f"<div class='_item'><div class='_item_icon'><img alt='{item}のアイコン'></div><card class='is-tooltip-enabled'>{item}</card></div></div>"
            f"<div class='_body'><div class='_body_left'><div class='_type_ability'><div class='_type'><img alt='みず'></div>"
            f"<div class='_ability'><card class='is-tooltip-enabled'>{ability}</card></div></div><div class='_moves'>{cards}</div></div>"
            f"<div class='_body_right'><div class='_params'><div class='_title'><span>Ｈ Ｐ</span></div><div class='_st'>{st}</div>"
            f"<div class='_evbar'><span></span></div><div class='_ev'>{ev}</div></div>"
            f"<div class='_nature'><div class='_nature_label'><span>性格</span></div><div class='_nature_value'><span>{nature}</span></div></div>"
            f"</div></div></div>")


def _li(forms, toggle=False):
    tg = ("<div class='wd-pkch-form-toggle'><button class='wd-pkch-form-toggle-btn'><img></button>"
          "<button class='wd-pkch-form-toggle-btn _active'><img></button></div>") if toggle else ""
    return f"<li class='_normal'><div class='_wrapper'>{tg}{''.join(forms)}</div></li>"


def _td(name, sid, mark=None):
    extra = f"<br>{mark}" if mark else ""
    return f"<td><a href='{GW}/{sid}'><img class='c-blank-img' alt='{name}'><noscript>&lt;img src='x'&gt;</noscript></a>{name}{extra}</td>"


# 1 構築目: メガ候補 1 体 (通常 / メガ の 2 form、既定はメガ) + 5 体。記事の例と同じ型の数値 (メガラグラージ / ペリッパー …)
TEAM1_MEMBERS = [
    _li([_form("ラグラージ", "100", "ラグラージナイト", "げきりゅう", ["ウェーブタックル", "じしん", "れいとうパンチ", "どくづき"],
               [177, 162, 120, 103, 110, 102], [2, 32, "-", "-", "-", 32], "ようき"),
         _form("メガラグラージ", "101", "ラグラージナイト", "すいすい", ["ウェーブタックル", "じしん", "れいとうパンチ", "どくづき"],
               [177, 202, 130, 103, 130, 134], [2, 32, "-", "-", "-", 32], "ようき", active=True)], toggle=True),
    _li([_form("ペリッパー", "102", "しめったいわ", "あめふらし", ["ぼうふう", "なみのり", "とんぼがえり", "おいかぜ"],
               [167, 63, 120, 158, 92, 88], [32, "-", "-", 29, 2, 3], "ひかえめ")]),
    _li([_form("ブリジュラス", "103", "ヨプのみ", "じきゅうりょく", ["あくのはどう", "エレクトロビーム", "ラスターカノン", "りゅうせいぐん"],
               [197, 112, 180, 156, 94, 105], [32, "-", 14, 11, 9, "-"], "ずぶとい")]),
    _li([_form("サーフゴー", "104", "ふうせん", "おうごんのからだ", ["わるだくみ", "10まんボルト", "ゴールドラッシュ", "シャドーボール"],
               [193, 72, 115, 184, 111, 124], [31, "-", "-", 15, "-", 20], "ひかえめ")]),
    _li([_form("バンギラス", "105", "オボンのみ", "すなおこし", ["じしん", "はたきおとす", "ストーンエッジ", "ちょうはつ"],
               [207, 158, 130, 103, 165, 81], [32, 4, "-", "-", 30, "-"], "しんちょう")]),
    _li([_form("アーマーガア", "106", "たべのこし", "ミラーアーマー", ["ボディプレス", "てっぺき", "はねやすめ", "とんぼがえり"],
               [205, 107, 172, 65, 107, 87], [32, "-", 32, "-", 2, "-"], "わんぱく")]),   # 実数値は再計算と一致する値 (検査が見る)
]
# 2 構築目: 技が 3 つの個体 (情報不足) を含む
TEAM2_MEMBERS = [_li([_form("ガブリアス", "200", "こだわりスカーフ", "さめはだ", ["げきりん", "じしん", "ストーンエッジ"],
                            [185, 182, 115, 90, 105, 169], [2, 32, "-", "-", "-", 32], "ようき")])]   # 実数値は再計算と一致する値

HTML = f"""<html><head><meta charset="utf-8"><title>t</title></head><body>
<nav><a href="/x">レギュレーションM-Bの情報</a></nav>
<div id="article-body" class="is-pc">
<h2>最強パーティランキング</h2>
<p>ポケモンチャンピオンズの最強パーティランキングです (合成の試験データ)。</p>
<p>レギュレーションM-Cは強力なメガシンカを一般枠で支える構築の評価が上がっています。</p>
<h2>メガラグラージ構築</h2>
<h3>メガラグラージ構築の詳細</h3>
<div class="gw_all_table"><table><tbody><tr><th>評価</th><th>チームID</th></tr><tr><td>S</td><td>E2E9 MW0B Q7</td></tr></tbody></table></div>
<div class="wd-pkch-party-toggle"><button class="wd-pkch-party-toggle-btn _active"><img></button></div>
<ol class="wd-pkch-pkmlist">{''.join(TEAM1_MEMBERS)}</ol>
<p>メガラグラージとペリッパーを軸にした雨パです。全体としてアシレーヌが重いので早めに処理します。</p>
<h3>選出紹介</h3>
<h4>基本選出</h4>
<div class="gw_all_table"><table><tbody><tr><th>基本選出</th></tr><tr>{_td('ペリッパー', '102', '(初手)')}{_td('メガラグラージ', '101')}{_td('ブリジュラス', '103', 'など')}</tr></tbody></table></div>
<div class="gw-info">※先発ポケモンは相手構築に応じて変更しましょう。</div>
<p>基本選出はペリッパー、メガラグラージ、ブリジュラスです。ペリッパーで雨を降らせてからメガラグラージで攻めます。</p>
<h4>天候対策選出</h4>
<div class="gw_all_table"><table><tbody><tr><th>天候対策選出</th></tr><tr>{_td('サーフゴー', '104', '(初手)')}{_td('バンギラス', '105')}{_td('アーマーガア', '106', 'など')}</tr></tbody></table></div>
<p>相手に天候を操るポケモンがいる場合はサーフゴーやバンギラスを選出します。</p>
<h2>ガブリアス構築</h2>
<h3>ガブリアス構築の詳細</h3>
<div class="gw_all_table"><table><tbody><tr><th>評価</th><th>チームID</th></tr><tr><td>A</td><td>ABCD1234</td></tr></tbody></table></div>
<ol class="wd-pkch-pkmlist">{''.join(TEAM2_MEMBERS)}</ol>
<p>ガブリアスを軸にした対面構築です。</p>
<h2>最強パーティランキングの著者情報</h2>
<p>著者の紹介。</p>
<h2>関連ページ</h2>
</div></body></html>"""


def test_units_from_synthetic_page():
    dic = P.default_dictionary()
    units = G.gamewith_units(HTML, source={"url": f"{GW}/555537"}, dic=dic)
    assert len(units) == 2                                                                  # 著者情報・関連ページは構築ではない
    u1, u2 = units
    assert u1["kind"] == "team" and u1["source"]["host"] == "gamewith.jp" and u1["source"]["publisher_kind"] == "editorial_site"
    assert u1["source"]["usage_evidence"] == "none" and u1["source"]["url"] == f"{GW}/555537"
    assert u1["meta"]["regulation"] == "gen9championsbssregmc" and u1["meta"]["regulation_basis"] == "article_text"   # nav の M-B は見ない
    assert u1["meta"]["team_code"] == "E2E9MW0BQ7" and u1["meta"]["axis_species_id"] == "swampertmega" and u1["meta"]["unit_index"] == 0
    assert u2["meta"]["team_code"] == "ABCD1234" and u2["meta"]["axis_species_id"] == "garchomp"
    assert u1["local"] == {"unresolved_names": [], "n_members": 6, "n_selection_lines": 5}
    lines = u1["marked"].splitlines()
    assert lines[0] == "使用ポケモン"
    assert lines[1] == f"* [メガラグラージ]({GW}/101)@ラグラージナイト(ようき)すいすい"                     # 既定 (_active) のメガ form
    assert lines[2] == "* HP:2 / 攻撃:32 / 素早:32" and lines[3] == "* 実数値:177-202-130-103-130-134"
    assert lines[4] == "* ウェーブタックル / じしん / れいとうパンチ / どくづき"
    assert "基本選出はペリッパー(初手)、メガラグラージ、ブリジュラスなど。" in lines
    assert "天候対策選出の選出例はサーフゴー(初手)、バンギラス、アーマーガアなど。" in lines
    assert "相手に天候を操るポケモンがいる場合はサーフゴーやバンギラスを選出します。" in lines
    assert "※先発ポケモンは相手構築に応じて変更しましょう。" in lines and lines.index("戦術と解説") > lines.index("使用ポケモン")
    assert "著者の紹介。" not in u1["marked"] and "レギュレーションM-B" not in u1["marked"]
    B.assert_no_prose(u1["meta"])
    B.assert_no_prose(u1["source"])
    print("test_units_from_synthetic_page OK")


def test_units_parse_to_records():
    """変換層の出力が解析器を通り、6 体・各 4 技の記録になる。技が 3 つの構築は incomplete。実数値の再計算と一致する"""
    dic = P.default_dictionary()
    units = G.gamewith_units(HTML, source={"url": f"{GW}/555537"}, dic=dic)
    parsed = P.parse_article(units[0]["marked"], dic, host="gamewith.jp")
    assert parsed["counts"]["members"] == 6 and all(len(m["moves"]) == 4 for m in parsed["members"])
    m1 = parsed["members"][0]
    assert (m1["species_id"], m1["base_species_id"], m1["mega_stone"], m1["ability"]) == ("swampertmega", "swampert", "swampertite", "swiftswim")
    assert m1["points"] == {"hp": 2, "atk": 32, "spe": 32} and m1["actual"] == [177, 202, 130, 103, 130, 134]
    rec = B.build_record(parsed, dict(units[0]["source"], synthetic=True), units[0]["meta"])
    assert rec["status"] == "ok" and B.validate_record(rec) == []                             # 6 体の実数値の再計算が一致
    weak = {(c["subject"], c["object"]) for c in rec["claims"] if c["kind"] == "weak_to"}
    assert ("team", "primarina") in weak
    rules = rec["selection_rules"]
    assert any(r["condition"] and r["condition"].get("predicate") == "weather_control" for r in rules)   # 文からの条件つき選出
    obs = {(o["category"], o["key"], o["id"]) for o in parsed["site_id_observations"]}
    assert ("species", "101", "swampertmega") in obs or obs == set()                        # サイト固有 id の表に gamewith があれば観測が出る
    parsed2 = P.parse_article(units[1]["marked"], dic, host="gamewith.jp")
    rec2 = B.build_record(parsed2, dict(units[1]["source"], synthetic=True), units[1]["meta"])
    assert rec2["status"] == "incomplete" and parsed2["members"][0]["warnings"] == ["moves_count"]
    print("test_units_parse_to_records OK")


def test_registry_and_edge_cases():
    adapters = host_adapters()
    assert set(adapters) == {"gamewith.jp"}
    units = units_for("www.gamewith.jp", HTML, source={"url": f"{GW}/555537"}, adapters=adapters)
    assert len(units) == 2 and units[0]["kind"] == "team"
    assert G.gamewith_units("<html><body><p>no article</p></body></html>") == []
    assert G.team_code([G.build_dom("<table><tr><th>チームID</th></tr><tr><td>日本語のID</td></tr></table>").find("table")]) is None
    cells = G.selection_cells(G.build_dom(f"<table><tr>{_td('ガブリアス', '1', '(初手)')}{_td('アシレーヌ', '2', 'など')}{_td('サーフゴー', '3')}</tr></table>").find("table"))
    assert cells == [("ガブリアス", True, False), ("アシレーヌ", False, True), ("サーフゴー", False, False)]
    assert G.selection_sentence("基本選出", cells) == "基本選出はガブリアス(初手)、アシレーヌなど、サーフゴー。"
    assert G.selection_sentence("メガボーマンダ選出", cells[:1]) == "メガボーマンダ選出の選出例はガブリアス(初手)。"
    assert G.selection_sentence("基本選出", []) is None
    # 辞書に無い種名は local に残る (記録には入れない)
    html_bad = HTML.replace("アーマーガア", "アーマーガアX")
    bad_units = G.gamewith_units(html_bad, dic=P.default_dictionary())
    assert bad_units[0]["local"]["unresolved_names"] == [{"category": "species", "text": "アーマーガアX", "host": "gamewith.jp", "site_key": "106"}]
    print("test_registry_and_edge_cases OK")


def main() -> None:
    test_units_from_synthetic_page()
    test_units_parse_to_records()
    test_registry_and_edge_cases()
    print("ALL OK")


if __name__ == "__main__":
    main()
