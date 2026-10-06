"""記事の決定的な解析 (tools/team_build/article_parse) と記録 (article_bank) の受け入れテスト (docs/ARTICLE_BANK_DESIGN_1006.md §9)。

素材はユーザーが添付した記事の例と同じ構造 (雨パ 3 体 + 天候対策 3 体、yakkun 風のリンク) で解説文を書き直した合成記事。
否定文 (「弱いわけではない」)、相手への言及 (性格の無い「相手の X @ 持ち物」と 「相手の X @ 持ち物 (性格) 特性」)、
技一覧の 2 行目 (変更前)、リンクの番号の矛盾 (n261m) を含む。
選出規則の schema 2 (2026-10-06) は SYNTHETIC_V2 (同じ 6 体 + 変換層の文の形 3 つと条件の語彙の文) と、代表 1 体 + 種名だけ分かる
個体 (REP + NAMED_FIVE) で見る。

    python -m tests.test_article_parse
"""
from __future__ import annotations

import json

from tools.team_build import article_bank as B
from tools.team_build import article_parse as P

L = "https://example.invalid"
SYNTHETIC = f"""# 【M-6】雨と天候対策の構築 (合成の試験記事)

はじめに
この記事は解析器の試験用の合成の文章です。

使用ポケモン

*
* [ペリッパー]({L}/zukan/n279)@[しめったいわ]({L}/item?item_s=137)([ひかえめ]({L}/nature#modest))[あめふらし]({L}/ability/2)
* HP:32 / 特攻:29 / 特防:2 / 素早:3
(252表示: HP:252 / 特攻:228 / 特防:12 / 素早:20)
* 実数値:167-63-120-158-92-88
* [ぼうふう]({L}/move/542)[なみのり]({L}/move/57)[とんぼがえり]({L}/move/369)[おいかぜ]({L}/move/366)

雨を降らせてから退く役です。Dは[メガガブリアスZ]({L}/zukan/n445z)のパワージェム耐え、Sは無振り[アーマーガア]({L}/zukan/n823)抜き。
相手の[ガブリアス]({L}/zukan/n445)@[こだわりスカーフ]({L}/item?item_s=1) には注意。
相手の[ガブリアス]({L}/zukan/n445)@[こだわりスカーフ]({L}/item?item_s=1)([ようき]({L}/nature#jolly))[さめはだ]({L}/ability/24) も見かけます。
[キラフロル]({L}/zukan/n967)に弱い。

*
* [メガラグラージ]({L}/zukan/n261m)@[ラグラージナイト]({L}/item?item_s=200)([ようき]({L}/nature#jolly))[すいすい]({L}/ability/33)
* HP:2 / 攻撃:32 / 素早:32
(252表示: HP:12 / 攻撃:252 / 素早:252)
* 実数値:177-202-130-103-130-134
* [ウェーブタックル]({L}/move/849)[じしん]({L}/move/89)[れいとうパンチ]({L}/move/8)[どくづき]({L}/move/398)
* [ウェーブタックル]({L}/move/849)[じしん]({L}/move/89)[じわれ]({L}/move/90)[どくづき]({L}/move/398)

上の行が現在の型で、下の行は変更前です。最速スカーフ[マスカーニャ]({L}/zukan/n908)に上を取られます。どくづきは[メガメガニウム]({L}/zukan/n154m)対策です。

*
* [ブリジュラス]({L}/zukan/n1018)@[ヨプのみ]({L}/item?item_s=45)([ずぶとい]({L}/nature#bold))[じきゅうりょく]({L}/ability/200)
* HP:32 / 防御:14 / 特攻:11 / 特防:9
(252表示: HP:252 / 防御:108 / 特攻:84 / 特防:68)
* 実数値:197-112-180-156-94-105
* [あくのはどう]({L}/move/399)[エレクトロビーム]({L}/move/920)[ラスターカノン]({L}/move/430)[りゅうせいぐん]({L}/move/434)

B特化玉[ガブリアス]({L}/zukan/n445)の地震耐え。大体物理アタッカーはカモです。[アシレーヌ]({L}/zukan/n730)に弱いわけではない。

*
* [メガムクホーク]({L}/zukan/n398m)@[ムクホークナイト]({L}/item?item_s=200)([ようき]({L}/nature#jolly))[あまのじゃく]({L}/ability/126)
* HP:2 / 攻撃:32 / 素早:32
(252表示: HP:12 / 攻撃:252 / 素早:252)
* 実数値:162-192-120-72-110-178
* [インファイト]({L}/move/370)[ブレイブバード]({L}/move/413)[ふきとばし]({L}/move/18)[はねやすめ]({L}/move/355)

[メガグソクムシャ]({L}/zukan/n768m)や[ゴリランダー]({L}/zukan/n812)に圧倒的有利です。[メガリザードンY]({L}/zukan/n6y)や[アシレーヌ]({L}/zukan/n730)、ゴーストタイプに弱いです。どんなポケモンも一発は耐えれます。ふきとばしは詰み技対策ですが[ブレイズキック]({L}/move/299)を入れた方がいいかもしれません。

*
* [サーフゴー]({L}/zukan/n1000)@[ふうせん]({L}/item?item_s=185)([ひかえめ]({L}/nature#modest))[おうごんのからだ]({L}/ability/283)
* HP:31 / 特攻:15 / 素早:20
(252表示: HP:244 / 特攻:116 / 素早:156)
* 実数値:193-72-115-184-111-124
* [わるだくみ]({L}/move/417)[10まんボルト]({L}/move/85)[ゴールドラッシュ]({L}/move/889)[シャドーボール]({L}/move/247)

通常の[カバルドン]({L}/zukan/n450)はカモれます。このパーティーで唯一[キラフロル]({L}/zukan/n967)を安定して見れます。
Sは最速[アシレーヌ]({L}/zukan/n730)や[ギルガルド]({L}/zukan/n681)抜きです。

*
* [バンギラス]({L}/zukan/n248)@[オボンのみ]({L}/item?item_s=14)([しんちょう]({L}/nature#careful))[すなおこし]({L}/ability/45)
* HP:32 / 攻撃:4 / 特防:30
(252表示: HP:252 / 攻撃:28 / 特防:236)
* 実数値:207-158-130-103-165-81
* [じしん]({L}/move/89)[はたきおとす]({L}/move/282)[ストーンエッジ]({L}/move/444)[ちょうはつ]({L}/move/269)

特に[メガリザードンY]({L}/zukan/n6y)に弱いメガムクホークとサーフゴーを助けます。[さいきのいのり]({L}/move/878)[メガジュペッタ]({L}/zukan/n354m)が苦手なので採用しました。
戦術と解説
相手に天候を操るポケモンがいるならメガムクホーク、サーフゴー、バンギラスの三体。それ以外は雨パで戦います。全体としてアシレーヌが重いので早めに処理します。選出は二つを混ぜても良いと思います。
"""

EXPECTED_SETS = {
    "m1": ("pelipper", "pelipper", None, "damprock", "modest", "drizzle", {"hp": 32, "spa": 29, "spd": 2, "spe": 3},
           ["hurricane", "surf", "uturn", "tailwind"]),
    "m2": ("swampertmega", "swampert", "swampertite", "swampertite", "jolly", "swiftswim", {"hp": 2, "atk": 32, "spe": 32},
           ["wavecrash", "earthquake", "icepunch", "poisonjab"]),
    "m3": ("archaludon", "archaludon", None, "chopleberry", "bold", "stamina", {"hp": 32, "def": 14, "spa": 11, "spd": 9},
           ["darkpulse", "electroshot", "flashcannon", "dracometeor"]),
    "m4": ("staraptormega", "staraptor", "staraptite", "staraptite", "jolly", "contrary", {"hp": 2, "atk": 32, "spe": 32},
           ["closecombat", "bravebird", "whirlwind", "roost"]),
    "m5": ("gholdengo", "gholdengo", None, "airballoon", "modest", "goodasgold", {"hp": 31, "spa": 15, "spe": 20},
           ["nastyplot", "thunderbolt", "makeitrain", "shadowball"]),
    "m6": ("tyranitar", "tyranitar", None, "sitrusberry", "careful", "sandstream", {"hp": 32, "atk": 4, "spd": 30},
           ["earthquake", "knockoff", "stoneedge", "taunt"]),
}


# 選出規則の schema 2 の受け入れ (2026-10-06 ユーザー判断)。SYNTHETIC の個体の節 (6 体) に全体の節の文を差し替えた合成記事。
# 先頭の 3 文は GameWith の変換層が出す文の形 (ページの文 1 つと、選出の表を直した文 2 つ) をそのまま入れたもの
MEMBERS_PART = "使用ポケモン\n" + SYNTHETIC.split("使用ポケモン\n", 1)[1].split("戦術と解説", 1)[0]
SELECTION_V2 = (
    "相手に天候を操るポケモンがいる場合はサーフゴーやバンギラスを選出します。",            # s1: 「場合」単独の接続の語
    "基本選出はペリッパー(初手)、メガラグラージ、ブリジュラスなど。",                     # s2: 基本選出 (default)、(初手)、など
    "天候対策選出の選出例はサーフゴー(初手)、バンギラス、アーマーガアなど。",              # s3: <見出し>の選出例 (見出しは捨てる)
    "相手構築にゴリランダーがいる場合はサーフゴーやメガボーマンダを選出します。",           # s4: species_present、型の無い種
    "相手に物理受けやドラゴンタイプが多い場合はサーフゴーやバンギラスを選出します。",       # s5: role + type_many (any_of、unknown)
    "相手にドラゴンタイプが3体以上ならメガムクホーク、サーフゴー、バンギラスを選出します。",  # s6: type_count
    "相手に氷技持ちが多い場合はバンギラスとサーフゴーを選出します。",                      # s7: move_type_present + 多い
    "基本選出はペリッパーとメガラグラージ + 残り1体は相手に合わせて選びます。",             # s8: 未指定の枠 1
    "初手ペリッパー、後発メガラグラージとブリジュラス。",                                   # s9: 初手 A、後発 B と C
    "基本選出はブリジュラス（初手）、ペリッパー、メガラグラージ。",                         # s10: 全角括弧の （初手）
    "相手にゴリランダーがいなければペリッパーとメガラグラージを選出します。",               # s11: 種の不在
    "基本選出はサーフゴー・バンギラス + 自由枠。",                                         # s12: 数の書かれていない自由枠
    "相手にドラゴンタイプが2体いる場合はサーフゴーを選出します。",                          # s13: 比較の語の無い数 → 条件にしない
    "基本選出: ペリッパー / メガラグラージ / ブリジュラス",                                 # s14: 「基本選出:」の行 (見出しにしない)
    "相手にトリルがいるならペリッパー、カイリュー。",                                       # s15: 述語の無い列挙の味方でない種は採らない
    "相手構築にゴリランダーとカイリューがいる場合はサーフゴーを選出します。",               # s16: 「と」→ all_of
    "基本選出はペリッパー、メガラグラージ、ブリジュラスですが、相手にゴリランダーがいる場合はサーフゴーを選出します。",  # s17: 1 文に 2 規則
)
SYNTHETIC_V2 = MEMBERS_PART + "戦術と解説\n" + "\n".join(SELECTION_V2) + "\n"
# 代表 1 体だけ型があり、全体の節に述語の無い列挙の選出規則がある合成記事 (種名だけ分かる個体は変換層が別に渡す)
REP = f"""使用ポケモン
* [メガラグラージ]({L}/zukan/n261m)@[ラグラージナイト]({L}/item?item_s=200)([ようき]({L}/nature#jolly))[すいすい]({L}/ability/33)
* HP:2 / 攻撃:32 / 素早:32
* 実数値:177-202-130-103-130-134
* [ウェーブタックル]({L}/move/849)[じしん]({L}/move/89)[れいとうパンチ]({L}/move/8)[どくづき]({L}/move/398)
戦術と解説
相手にゴリランダーがいるならサーフゴー、ボーマンダ、メガラグラージ。
"""
NAMED_FIVE = ({"species_id": "pelipper"}, {"species_id": "archaludon"}, {"species_id": "gholdengo"},
              {"species_id": "salamencemega", "base_species_id": "salamence", "mega_stone": "salamencite"}, {"species_id": "tyranitar"})


def _claims(parsed, kind, subject=None):
    return [c for c in parsed["claims"] if c["kind"] == kind and (subject is None or c["subject"] == subject)]


def _rules_at(parsed, k):
    return [r for r in parsed["selection_rules"] if r["source_ref"] == f"team:s{k}"]


def _cond(predicate, value, evaluation, **extra):
    c = {"subject": "article_opponent", "predicate": predicate, "value": value, "evaluation": evaluation}
    c.update(extra)
    return c


def test_members_sets_and_moves():
    """§9-1〜3: 6 体・各 4 技、種 / 基本種 / 石 / 持ち物 / 性格 / 特性、配分の 3 種類を別に保持、説明文の技を採らない"""
    parsed = P.parse_article(SYNTHETIC)
    assert parsed["counts"]["members"] == 6 and parsed["warnings"] == []
    for m in parsed["members"]:
        sid, base, stone, item, nature, ability, points, moves = EXPECTED_SETS[m["id"]]
        assert (m["species_id"], m["base_species_id"], m["mega_stone"], m["item"], m["nature"], m["ability"]) == (sid, base, stone, item, nature, ability), m
        assert m["points"] == points and len(m["moves"]) == 4 and m["moves"] == moves, m
        assert m["ev252"] and m["actual"] and len(m["actual"]) == 6
        assert sum(m["points"].values()) == 66
        assert "display" not in m and m["warnings"] == []
    m1, m2 = parsed["members"][0], parsed["members"][1]
    assert m1["ev252"] == {"hp": 252, "spa": 228, "spd": 12, "spe": 20} and m1["actual"] == [167, 63, 120, 158, 92, 88]
    assert m2["alt_move_lines"] == 1 and "fissure" not in m2["moves"]                     # 変更前の 2 行目は採らない
    assert m2["notes"] == ["link_num_mismatch"] and parsed["members"][2]["notes"] == []     # リンクの番号 (n261m) は id を決めず注記だけ
    all_moves = {mv for m in parsed["members"] for mv in m["moves"]}
    assert "blazekick" not in all_moves and "revivalblessing" not in all_moves             # 変更候補と相手の技は採用技に混ざらない
    assert "garchomp" not in {m["species_id"] for m in parsed["members"]}                   # 相手への言及 (性格あり・なし) は 6 体に入らない
    print("test_members_sets_and_moves OK")


def test_claims_distinguish_team_and_member():
    """§9-4: 全体の弱点と個体の弱点、技の目的、役割、補完、速度調整。否定文と広い表現は主張にしない"""
    parsed = P.parse_article(SYNTHETIC)
    weak = {(c["subject"], c["object"]) for c in _claims(parsed, "weak_to")}
    assert ("m1", "glimmora") in weak and ("m4", "charizardmegay") in weak and ("m4", "primarina") in weak and ("m4", "Ghost") in weak
    assert ("team", "primarina") in weak                                                     # 「全体としてアシレーヌが重い」
    assert ("m3", "primarina") not in weak                                                   # 「弱いわけではない」は主張にしない
    assert ("m6", "charizardmegay") not in weak and ("m4", "charizardmegay") in weak and ("m5", "charizardmegay") in weak
    sup = _claims(parsed, "supports", "m6")
    assert len(sup) == 1 and sup[0]["object"] == "charizardmegay" and sup[0]["members"] == ["m4", "m5"]
    mp = _claims(parsed, "move_purpose", "m2")
    assert len(mp) == 1 and mp[0]["move"] == "poisonjab" and mp[0]["object"] == "meganiummega"
    hd = _claims(parsed, "handles", "m5")
    assert len(hd) == 1 and hd[0]["object"] == "glimmora" and hd[0]["exclusive"] is True
    sb = {(c["subject"], c["object"], tuple(c["conditions"])) for c in _claims(parsed, "speed_benchmark")}
    assert ("m1", "corviknight", ("no_investment",)) in sb and ("m5", "primarina", ("max_speed",)) in sb and ("m5", "aegislash", ("max_speed",)) in sb
    ob = _claims(parsed, "outsped_by", "m2")
    assert len(ob) == 1 and ob[0]["object"] == "meowscarada" and ob[0]["conditions"] == ["choice_scarf", "max_speed"]
    fav = {(c["subject"], c["object"]) for c in _claims(parsed, "favorable_vs")}
    assert ("m4", "golisopodmega") in fav and ("m4", "rillaboom") in fav and ("m5", "hippowdon") in fav
    assert not any(c["object_kind"] not in ("species", "type") for c in parsed["claims"])
    sv = {(c["subject"], c["object"], c["move"], c["mods_ambiguous"]) for c in _claims(parsed, "survives")}
    assert ("m1", "garchompmegaz", "powergem", False) in sv                                 # 「Dは」は自分の能力の指示なので曖昧ではない
    assert ("m3", "garchomp", None, True) in sv                                              # 「B特化玉」は区切りが曖昧、「地震」は辞書に無い
    cats = [u["category"] for u in parsed["unresolved"]]
    assert cats.count("broad_matchup_claim") == 2                                           # 「大体物理アタッカーはカモ」「どんなポケモンも…耐えれます」
    assert "durability_benchmark_ambiguous_modifiers" in cats and "unresolved_move" in cats
    assert cats.count("move_purpose_non_species_target") == 1                               # 「ふきとばしは詰み技対策」: 対象が種でないので主張にせず未確定
    assert not any(c["kind"] == "move_purpose" and c["subject"] == "m4" for c in parsed["claims"])
    # 解決できなかった名前は「地震」だけ (説明文の素の文字列なのでリンクのホスト・サイト固有 id は無い)
    assert parsed["unresolved_names"] == [{"category": "moves", "text": "地震", "host": None, "site_key": None}]
    assert all(c["basis"] == "author_explicit" for c in parsed["claims"])
    print("test_claims_distinguish_team_and_member OK")


def test_selection_rules_without_fabrication():
    """§9-5: 天候操作の条件つき 3 体は取れるが、雨側の 3 体・先発・選出確率は作らない"""
    parsed = P.parse_article(SYNTHETIC)
    rules = parsed["selection_rules"]
    assert len(rules) == 1
    r = rules[0]
    # schema 2 (2026-10-06) で条件に判定の可否 evaluation が加わった (天候操作は相手の型が確認できたときだけ判定できる)。他の項目は同じ
    assert r["condition"] == {"subject": "article_opponent", "predicate": "weather_control", "value": "present", "evaluation": "set_known_only"}
    assert r["selected_members"] == ["m4", "m5", "m6"] and r["lead"] is None and r["recommendation"] == "preferred"
    # schema 2 の項目: 明確な 3 体組 (例示・未指定の枠なし)、型の無い種は無い、先発の種も無い
    assert r["selected_species"] == [] and r["lead_species"] is None
    assert r["exact_trio"] is True and r["example"] is False and r["free_slots"] == 0
    assert not any(k in r for k in ("probability", "prob", "weight"))
    assert parsed["selection_combinable"] is True
    cats = [u["category"] for u in parsed["unresolved"]]
    assert "selection_else_branch_members_unspecified" in cats                               # 「それ以外は雨パ」は規則にしない
    print("test_selection_rules_without_fabrication OK")


def test_record_payload_no_prose():
    """§9-6: 記録と LLM の入力に本文・引用が混入しない。検査が通る。保存・読み出しは固定版"""
    import tempfile
    from pathlib import Path
    parsed = P.parse_article(SYNTHETIC)
    rec = B.build_record(parsed, source={"url_hash": "synthetic", "host": "example.invalid", "synthetic": True},
                         meta={"regulation": "gen9championsbssregmc"})
    assert rec["status"] == "ok" and "unresolved_names" not in rec and "site_id_observations" not in rec
    assert B.validate_record(rec) == []                                                     # ポイント合計 66、252 表示の対応、実数値の再計算、メガ石
    B.assert_no_prose(rec)
    payload = B.llm_payload(rec)
    text = json.dumps(payload, ensure_ascii=False)
    for word in ("雨を降らせて", "退く役", "気をつけて", "蹴散ら", "早めに処理", "example.invalid"):
        assert word not in text
    assert payload["members"][1]["mega_stone"] == "swampertite" and payload["unresolved"][0]["category"]
    try:
        B.assert_no_prose({"claims": [{"note": "雨を降らせる"}]})
        raise AssertionError("prose should be rejected")
    except ValueError as e:
        assert "雨" not in str(e) and "claims" in str(e)                                   # 違反の報告にも本文を含めない
    with tempfile.TemporaryDirectory() as d:
        out = B.save_bank([rec], Path(d), allow_synthetic=True)            # 素材は合成の記事なので許可を明示する
        version = out.name
        assert (out / "manifest.json").exists() and B.load_bank(version, Path(d), allow_synthetic=True)[0]["case_id"] == rec["case_id"]
        assert json.loads((out / "manifest.json").read_text(encoding="utf-8"))["n_cases"] == 1
    assert B.host_allowed({"a.example": {"fetch": "allow"}}, "a.example") and not B.host_allowed({"a.example": {"fetch": "unknown"}}, "a.example")
    assert not B.host_allowed({}, "b.example", "send_llm")
    print("test_record_payload_no_prose OK")


def test_validate_catches_inconsistency():
    """検査: ポイント合計・252 表示の対応・実数値・メガ石・選出規則の個体・確率の混入を見つける"""
    parsed = P.parse_article(SYNTHETIC)
    rec = B.build_record(parsed, source={}, meta={})
    bad = json.loads(json.dumps(rec))
    bad["members"][0]["points"]["spa"] = 30                        # 合計 67、252 表示と不一致、実数値と不一致
    bad["members"][1]["item"] = "leftovers"                        # メガ形態なのに石でない
    bad["members"][2]["moves"] = bad["members"][2]["moves"][:3]
    bad["selection_rules"][0]["selected_members"].append("m9")
    bad["selection_rules"][0]["probability"] = 0.5
    probs = B.validate_record(bad)
    assert "m1:point_total:67" in probs and "m1:ev252_mismatch:spa" in probs and any(p.startswith("m1:actual_mismatch:spa") for p in probs)
    assert "m2:mega_item:leftovers" in probs and "m3:moves:3" in probs
    assert "rule0:members_outside_team" in probs and "rule0:probability_not_allowed" in probs
    assert B.expected_actual("pelipper", {"hp": 32, "spa": 29, "spd": 2, "spe": 3}, "modest") == [167, 63, 120, 158, 92, 88]
    print("test_validate_catches_inconsistency OK")


def test_line_level_rules():
    """行の分類: 個体の見出しの条件、配分の行の 3 種類とラベル無し、技一覧の行、節の見出し"""
    dic = P.default_dictionary()
    assert P.parse_member_head("* [ペリッパー](x)@[しめったいわ](y)([ひかえめ](z))[あめふらし](w)", dic)["species_id"] == "pelipper"
    assert P.parse_member_head("ペリッパー＠しめったいわ（ひかえめ）あめふらし", dic)["ability"] == "drizzle"           # 全角でもよい
    head = P.parse_member_head("ラグラージ@ラグラージナイト(ようき)", dic)
    assert head["species_id"] == "swampertmega" and head["base_species_id"] == "swampert" and "mega_form_from_stone" in head["notes"]
    assert head["ability"] is None and head["unresolved"] == ["member_head_incomplete"]
    assert P.parse_member_head("相手の[ガブリアス](x)@[こだわりスカーフ](y)([ようき](z))[さめはだ](w)", dic) is None   # 種名に余分な語
    assert P.parse_member_head("[ガブリアス](x)@[こだわりスカーフ](y)", dic) is None                                   # 性格が無い
    assert P.parse_member_head("ガブリアス@こだわりスカーフ(ようき)さめはだ はエースです。", dic) is None               # 文の中の形は特性が解決できず…
    assert P.parse_stat_line("* HP:32 / 特攻:29 / 特防:2 / 素早:3") == {"kind": "points", "values": {"hp": 32, "spa": 29, "spd": 2, "spe": 3}, "unlabeled": True}
    assert P.parse_stat_line("(252表示: HP:252 / 特攻:228)") == {"kind": "ev252", "values": {"hp": 252, "spa": 228}, "unlabeled": False}
    assert P.parse_stat_line("HP:252 / 攻撃:252 / 素早:4") == {"kind": "ev252", "values": {"hp": 252, "atk": 252, "spe": 4}, "unlabeled": True}
    assert P.parse_stat_line("* 実数値:167-63-120-158-92-88") == {"kind": "actual", "values": [167, 63, 120, 158, 92, 88], "unlabeled": False}
    assert P.parse_stat_line("HP:32 は多めに振りました") is None and P.parse_stat_line("初手に雨を降らせる役目です。") is None
    assert P.parse_moves_line("* [ぼうふう](a)[なみのり](b)[とんぼがえり](c)[おいかぜ](d)", dic) == ["hurricane", "surf", "uturn", "tailwind"]
    assert P.parse_moves_line("ぼうふう / なみのり / とんぼがえり / おいかぜ", dic) == ["hurricane", "surf", "uturn", "tailwind"]
    assert P.parse_moves_line("ぼうふうは強い", dic) is None and P.parse_moves_line("[ぼうふう](a)", dic) is None
    assert P.section_kind("使用ポケモン") == "member" and P.section_kind("## 戦術と解説") == "team" and P.section_kind("基本選出") == "team"
    assert P.section_kind("天候変えられそうな時は追い風しましょう。") is None and P.section_kind("- 条件付き選出。") is None
    assert P.section_kind("[選出](x)") is None
    print("test_line_level_rules OK")


def test_html_to_marked_text():
    html = ("<html><head><style>p{}</style><script>x()</script></head><body><nav>menu</nav><h2>使用ポケモン</h2>"
            "<ul><li><a href='/zukan/n279'>ペリッパー</a>@<a href='/item?item_s=137'>しめったいわ</a>(<a href='/n'>ひかえめ</a>)"
            "<a href='/a'>あめふらし</a></li><li>HP:32 / 特攻:29</li></ul><p>初手に<br>雨を降らせます。</p></body></html>")
    text = P.html_to_marked_text(html)
    lines = text.splitlines()
    assert lines[0] == "## 使用ポケモン" and "menu" not in text and "x()" not in text
    assert lines[1] == "* [ペリッパー](/zukan/n279)@[しめったいわ](/item?item_s=137)([ひかえめ](/n))[あめふらし](/a)"
    assert lines[2] == "* HP:32 / 特攻:29" and lines[3] == "初手に" and lines[4] == "雨を降らせます。"
    parsed = P.parse_article(text)
    assert parsed["counts"]["members"] == 1 and parsed["members"][0]["points"] == {"hp": 32, "spa": 29}
    assert parsed["members"][0]["warnings"] == ["no_moves_line"]
    print("test_html_to_marked_text OK")


def test_negative_and_variant_articles():
    """1 本にだけ合う規則になっていないか: 個体の見出しが無い本文、7 体目、技一覧が無い個体、先発の明記"""
    none = P.parse_article("使用ポケモン\n今日は雨パの話です。\n戦術と解説\n相手に天候を操るポケモンがいるならメガムクホーク。")
    assert none["counts"]["members"] == 0 and none["selection_rules"] == []                 # 味方が解決できないので規則にしない
    assert [u["category"] for u in none["unresolved"]] == ["selection_members_unresolved"]
    rec = B.build_record(none, source={}, meta={})
    assert rec["status"] == "failed"
    seven = SYNTHETIC + "\n使用ポケモン\n* [カイリュー](x)@[ゴツゴツメット](y)([いじっぱり](z))[マルチスケイル](w)\n"
    p7 = P.parse_article(seven)
    assert p7["counts"]["members"] == 6 and "extra_member_head" in p7["warnings"]
    lead_text = SYNTHETIC.replace("相手に天候を操るポケモンがいるならメガムクホーク、サーフゴー、バンギラスの三体。",
                                  "相手に天候を操るポケモンがいるなら先発はバンギラスでメガムクホーク、サーフゴーを後ろに置き、必ずこの三体。")
    pl = P.parse_article(lead_text)
    r = pl["selection_rules"][0]
    assert r["lead"] == "m6" and sorted(r["selected_members"]) == ["m4", "m5", "m6"] and r["recommendation"] == "required"
    absent = P.parse_article("使用ポケモン\n" + SYNTHETIC.split("使用ポケモン\n", 1)[1].split("戦術と解説", 1)[0]
                             + "戦術と解説\n相手にトリックルームがいなければペリッパーとメガラグラージ。")
    ra = absent["selection_rules"][0]
    # schema 2 で evaluation が加わった (トリックルームは相手の型が確認できたときだけ判定できる)。述語・値・個体は同じ
    assert ra["condition"] == {"subject": "article_opponent", "predicate": "trick_room", "value": "absent", "evaluation": "set_known_only"}
    assert ra["selected_members"] == ["m1", "m2"]
    unknown = P.parse_article("使用ポケモン\n" + SYNTHETIC.split("使用ポケモン\n", 1)[1].split("戦術と解説", 1)[0]
                              + "戦術と解説\n相手に壁がいるならペリッパー。")
    assert unknown["selection_rules"] == [] and [u["category"] for u in unknown["unresolved"] if u["source_ref"].startswith("team")] == ["selection_condition_unknown"]
    print("test_negative_and_variant_articles OK")


def test_single_set_and_local_outputs():
    """max_members=1 (単体の型)、ローカル用の出力の形 (unresolved_names = 種別・表記・リンクのホスト・サイト固有 id、
    site_id_observations = 表示名が厳密一致で解決したリンクだけ)。記録には入れない"""
    from tools.team_build import article_aliases as A
    p1 = P.parse_article(SYNTHETIC, max_members=1)
    assert p1["counts"]["members"] == 1 and p1["members"][0]["species_id"] == "pelipper" and p1["members"][0]["moves"]
    assert p1["warnings"].count("extra_member_head") == 5                                  # 2 体目以降の見出しは採らない
    assert P.parse_article(SYNTHETIC)["site_id_observations"] == []                        # 表の無いホスト (example.invalid) は観測しない
    text = ("* [ペリッパー](/ch/zukan/n279)@[しめったいわ](/ch/item?item_s=137)([ひかえめ](/ch/nature))[あめふらし](/ch/zukan/search/?tokusei=2)\n"
            "* [ぼうふう](/ch/move?move=542)[なみのり](https://www.yakkun.com/ch/move?move=57)[蜻蛉返り](/ch/move?move=369)"
            "[おいかぜ](/ch/move?move=366)\n")
    p = P.parse_article(text, max_members=1, host="yakkun.com")
    obs = p["site_id_observations"]
    assert all(set(o) == {"host", "category", "key", "id"} for o in obs) and obs == sorted(obs, key=lambda o: (o["host"], o["category"], o["key"], o["id"]))
    for o in ({"host": "yakkun.com", "category": "species", "key": "279", "id": "pelipper"},
              {"host": "yakkun.com", "category": "items", "key": "137", "id": "damprock"},
              {"host": "yakkun.com", "category": "abilities", "key": "2", "id": "drizzle"},
              {"host": "yakkun.com", "category": "moves", "key": "57", "id": "surf"}):
        assert o in obs, o
    assert not any(o["key"] == "369" for o in obs)                                         # 解決できなかった表示名は観測にしない
    assert p["unresolved_names"] == [{"category": "moves", "text": "蜻蛉返り", "host": "yakkun.com", "site_key": "369"}]
    assert p["members"][0]["moves"] == [] and "unresolved_move" in [u["category"] for u in p["unresolved"]]
    # 相対リンクはページのホストで引く (host が無ければ、www つきの絶対 URL のリンクだけ観測する)
    p0 = P.parse_article(text, max_members=1)
    assert [(o["host"], o["key"]) for o in p0["site_id_observations"]] == [("yakkun.com", "57")]
    assert p0["unresolved_names"] == [{"category": "moves", "text": "蜻蛉返り", "host": None, "site_key": None}]
    # 別名で解決した表示名は観測にしない (観測は別名を含まない厳密一致だけ。別名から別名を確定させない)
    aliases = {"schema": A.SCHEMA, "entries": [A.make_entry("moves", "蜻蛉返り", "とんぼがえり", "uturn", "confirmed", "human", "2026-10-06")]}
    pa = P.parse_article(text, P.default_dictionary().with_aliases(aliases), max_members=1, host="yakkun.com")
    assert pa["members"][0]["moves"] == ["hurricane", "surf", "uturn", "tailwind"] and pa["unresolved_names"] == []
    assert not any(o["key"] == "369" for o in pa["site_id_observations"])
    rec = B.build_record(pa, source={"host": "yakkun.com"}, record_kind="single_set")
    assert rec["status"] == "ok" and "site_id_observations" not in rec and "unresolved_names" not in rec
    assert P.site_key("yakkun.com", "/ch/zukan/n445z") == ("species", "445z") and P.site_key(None, "https://yakkun.com/i?item_s=200") == ("items", "200")
    assert P.site_key("example.invalid", "/zukan/n279") is None and P.canonical_host("WWW.Yakkun.com:443") == "yakkun.com"
    print("test_single_set_and_local_outputs OK")


def test_selection_schema_v2():
    """選出規則の schema 2 (2026-10-06 ユーザー判断): 無条件の基本選出 / 選出例、(初手)、例示、未指定の枠、条件の語彙と evaluation、
    型の無い種。変換層の文の形 3 つ (s1〜s3) はそのまま読めること"""
    parsed = P.parse_article(SYNTHETIC_V2)
    assert parsed["counts"]["members"] == 6 and parsed["members_named_only"] == []
    one = {k: _rules_at(parsed, k) for k in range(1, len(SELECTION_V2) + 1)}
    # s1 (ページの文): 「場合」単独で条件の節を閉じる。味方名が「を選出」の目的語
    (r,) = one[1]
    assert r["condition"] == _cond("weather_control", "present", "set_known_only")
    assert (r["selected_members"], r["selected_species"], r["lead"], r["recommendation"]) == (["m5", "m6"], [], None, "preferred")
    assert r["exact_trio"] is False and r["example"] is False and r["free_slots"] == 0
    # s2 (基本選出の表): 無条件 (default)、(初手) → lead、「など」→ example (3 体でも exact_trio にしない)
    (r,) = one[2]
    assert r["condition"] is None and r["recommendation"] == "default"
    assert r["selected_members"] == ["m1", "m2", "m3"] and r["lead"] == "m1" and r["lead_species"] is None
    assert r["example"] is True and r["exact_trio"] is False
    # s3 (他の見出しの表): 「<見出し>の選出例は」の見出しは捨てる (天候の語があっても条件にしない)、preferred。
    # アーマーガアは SYNTHETIC の 6 体にいない → 型の無い種として selected_species (個体 id を作らない)
    (r,) = one[3]
    assert r["condition"] is None and r["recommendation"] == "preferred" and r["lead"] == "m5"
    assert r["selected_members"] == ["m5", "m6"] and r["selected_species"] == [{"species_id": "corviknight", "base_species_id": "corviknight"}]
    assert r["example"] is True and r["exact_trio"] is False
    # s4: species_present (確定した種で判定できる)。型の無いメガボーマンダは基本種とフォルムを区別して保持
    (r,) = one[4]
    assert r["condition"] == _cond("species_present", {"species_id": "rillaboom", "base_species_id": "rillaboom", "present": True}, "species")
    assert r["selected_members"] == ["m5"] and r["selected_species"] == [{"species_id": "salamencemega", "base_species_id": "salamence"}]
    # s5: 物理受け (role) とドラゴンタイプが多い (type_many) の any_of。どちらも判定しない (unknown) が規則は保存する
    (r,) = one[5]
    assert r["condition"] == {"any_of": [_cond("role", "physical_wall", "unknown", quantity="many"),
                                         _cond("type_many", {"type": "Dragon"}, "unknown")], "evaluation": "unknown"}
    assert r["selected_members"] == ["m5", "m6"]
    # s6: type_count (判明している種の範囲で数える)。3 体の明確な組
    (r,) = one[6]
    assert r["condition"] == _cond("type_count", {"type": "Dragon", "op": ">=", "n": 3}, "species_count")
    assert r["selected_members"] == ["m4", "m5", "m6"] and r["exact_trio"] is True
    # s7: 氷技持ち (move_type_present: 型が確認できたときだけ) が「多い」→ 弱い方 (unknown)
    (r,) = one[7]
    assert r["condition"] == _cond("move_type_present", {"move_type": "Ice"}, "unknown", quantity="many")
    assert r["selected_members"] == ["m6", "m5"]
    # s8: A と B + 残り 1 体は相手に合わせて → free_slots 1 (「相手に合わせて」の「相手」は条件にしない)。初手の記載が無いので lead は null
    (r,) = one[8]
    assert r["condition"] is None and r["recommendation"] == "default" and r["selected_members"] == ["m1", "m2"]
    assert r["free_slots"] == 1 and r["exact_trio"] is False and r["lead"] is None and r["lead_species"] is None
    # s9: 初手 A、後発 B と C → default、lead は初手の A
    (r,) = one[9]
    assert (r["condition"], r["recommendation"], r["selected_members"], r["lead"], r["exact_trio"]) == (None, "default", ["m1", "m2", "m3"], "m1", True)
    # s10: 全角括弧の （初手）
    (r,) = one[10]
    assert r["lead"] == "m3" and r["selected_members"] == ["m3", "m1", "m2"] and r["exact_trio"] is True
    # s11: 種の不在 (present = False)
    (r,) = one[11]
    assert r["condition"] == _cond("species_present", {"species_id": "rillaboom", "base_species_id": "rillaboom", "present": False}, "species")
    assert r["selected_members"] == ["m1", "m2"]
    # s12: 数の書かれていない自由枠 → free_slots 0 (推測しない)、exact_trio にしない、未確定の分類を残す
    (r,) = one[12]
    assert r["free_slots"] == 0 and r["exact_trio"] is False and r["selected_members"] == ["m5", "m6"]
    team_unres = [(u["category"], u["source_ref"]) for u in parsed["unresolved"] if u["source_ref"].startswith("team")]
    assert ("selection_free_slot_count_unspecified", "team:s12") in team_unres
    # s13: 比較の語の無い数 (「2体いる」) は条件にしない → 規則なし、未確定
    assert one[13] == [] and ("selection_condition_unknown", "team:s13") in team_unres
    assert sorted(team_unres) == [("selection_condition_unknown", "team:s13"), ("selection_free_slot_count_unspecified", "team:s12")]
    # s14: 「基本選出:」の後に列挙が続く行は見出しではなく選出の文 (列挙の無い「基本選出」は従来どおり見出し)
    (r,) = one[14]
    assert r["selected_members"] == ["m1", "m2", "m3"] and r["exact_trio"] is True and r["lead"] is None
    assert P.section_kind("基本選出: ペリッパー / メガラグラージ / ブリジュラス") is None and P.section_kind("基本選出") == "team"
    # s15: 「を選出」も印も無い列挙の、味方でない種 (カイリュー) は採らない (相手への言及を混ぜない)
    (r,) = one[15]
    assert r["condition"] == _cond("trick_room", "present", "set_known_only") and r["selected_members"] == ["m1"] and r["selected_species"] == []
    # s16: 「A と B がいる」は all_of
    (r,) = one[16]
    assert r["condition"] == {"all_of": [_cond("species_present", {"species_id": "rillaboom", "base_species_id": "rillaboom", "present": True}, "species"),
                                         _cond("species_present", {"species_id": "dragonite", "base_species_id": "dragonite", "present": True}, "species")],
                              "evaluation": "species"}
    # s17: 基本選出の節と条件つきの節が 1 文にある → 2 つの規則 (条件の節の味方を基本選出に混ぜない)
    r_default, r_cond = one[17]
    assert r_default["condition"] is None and r_default["selected_members"] == ["m1", "m2", "m3"] and r_default["exact_trio"] is True
    assert r_cond["condition"]["predicate"] == "species_present" and r_cond["selected_members"] == ["m5"]
    # どの規則にも確率は無く、記録の門 (本文なし) を通る
    assert not any(k in r for r in parsed["selection_rules"] for k in ("probability", "prob", "weight"))
    rec = B.build_record(parsed, source={"synthetic": True}, meta={})
    B.assert_no_prose(rec)
    # s3 のアーマーガアは 6 体が全部分かっている構築の外の種 → 検査で矛盾 (species_outside_team)
    s3_idx = next(i for i, r in enumerate(rec["selection_rules"]) if r["source_ref"] == "team:s3")
    assert f"rule{s3_idx}:species_outside_team:corviknight" in B.validate_record(rec)
    # 全体の節の見出しが無く、個体の節の直後に「基本選出: …」の行が来ても、そこから全体の節として読む (最後の個体の説明文にしない)
    no_heading = P.parse_article(MEMBERS_PART + SELECTION_V2[13] + "\n" + SELECTION_V2[0] + "\n")
    assert [(r["source_ref"], r["recommendation"], r["selected_members"]) for r in no_heading["selection_rules"]] == [
        ("team:s1", "default", ["m1", "m2", "m3"]), ("team:s2", "preferred", ["m5", "m6"])]
    assert len(_claims(no_heading, "supports", "m6")) == 1 and no_heading["counts"]["team_sentences"] == 2
    print("test_selection_schema_v2 OK")


def test_selection_named_only():
    """変換層が渡す「種名だけ分かる個体」: 結果に載せる (個体 id なし)、選出規則では味方として扱う (述語の無い列挙でも採る)。
    書かれた基本種名 (ボーマンダ) はその個体の形態 (メガボーマンダ) に対応させる"""
    plain = P.parse_article(REP)
    (r,) = plain["selection_rules"]
    assert plain["members_named_only"] == [] and r["selected_members"] == ["m1"] and r["selected_species"] == []
    named = P.parse_article(REP, members_named_only=list(NAMED_FIVE))
    assert named["members_named_only"][0] == {"species_id": "pelipper", "base_species_id": "pelipper", "mega_stone": None}
    assert named["members_named_only"][3] == {"species_id": "salamencemega", "base_species_id": "salamence", "mega_stone": "salamencite"}
    assert all("id" not in n for n in named["members_named_only"]) and named["counts"]["members"] == 1
    (r,) = named["selection_rules"]
    assert r["condition"]["predicate"] == "species_present" and r["selected_members"] == ["m1"]
    assert r["selected_species"] == [{"species_id": "gholdengo", "base_species_id": "gholdengo"},
                                     {"species_id": "salamencemega", "base_species_id": "salamence"}]
    assert r["exact_trio"] is True
    # 変換層の渡し方の検査: 知らない項目は捨てる、形が違えば ValueError
    assert P.normalize_named_only([{"species_id": "pelipper", "display": "ペリッパー"}]) == [
        {"species_id": "pelipper", "base_species_id": "pelipper", "mega_stone": None}]
    for bad in ([{"display": "x"}], ["pelipper"], [{"species_id": "pelipper", "mega_stone": 1}]):
        try:
            P.normalize_named_only(bad)
            raise AssertionError("形の違う members_named_only を通した")
        except ValueError:
            pass
    print("test_selection_named_only OK")


def main() -> None:
    test_members_sets_and_moves()
    test_claims_distinguish_team_and_member()
    test_selection_rules_without_fabrication()
    test_record_payload_no_prose()
    test_validate_catches_inconsistency()
    test_line_level_rules()
    test_html_to_marked_text()
    test_negative_and_variant_articles()
    test_single_set_and_local_outputs()
    test_selection_schema_v2()
    test_selection_named_only()
    print("ALL OK")


if __name__ == "__main__":
    main()
