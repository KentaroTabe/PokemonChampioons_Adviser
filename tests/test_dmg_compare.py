"""ダメージ照合 (tools/dmg_compare) の純粋な部分のテスト: プロトコルの解析・状態の追跡・照合・確認表 (Showdown は使わない)。

    python -m tests.test_dmg_compare
"""
from __future__ import annotations

import random

from tools import dmg_compare as DC

TEAM_P1 = """Garchomp @ choicescarf
Level: 50
Ability: roughskin
EVs: 2 HP / 32 Atk / 32 Spe
Jolly Nature
- earthquake
- dragonclaw
- stoneedge
- swordsdance

Mimikyu @ lifeorb
Level: 50
Ability: disguise
EVs: 32 Atk / 32 Spe
Adamant Nature
- playrough
- shadowsneak
- swordsdance
- shadowclaw"""

TEAM_P2 = """Snorlax @ leftovers
Level: 50
Ability: thickfat
EVs: 32 HP / 32 Atk
Adamant Nature
- bodyslam
- curse
- rest
- earthquake

Garchomp @ garchompite
Level: 50
Ability: roughskin
EVs: 32 Atk / 32 Spe
Jolly Nature
- earthquake
- dragonclaw
- swordsdance
- protect"""

BUILD = {"ev": {"hp": 16, "atk": 252, "spe": 252}, "nature": {"spe": 1.1, "spa": 0.9}}


def _tracker():
    teams = {"p1": DC.parse_team_text(TEAM_P1), "p2": DC.parse_team_text(TEAM_P2)}
    builds = {"p1": {"garchomp": BUILD, "mimikyu": BUILD}, "p2": {"snorlax": BUILD, "garchomp": BUILD}}
    return DC.SimTracker(teams, builds)


def _feed(tr, text):
    for l in text.strip().splitlines():
        tr.feed_line(l.strip())


def test_team_and_protocol_parsing():
    sets = DC.parse_team_text(TEAM_P1)
    assert [s["species"] for s in sets] == ["Garchomp", "Mimikyu"] and sets[0]["item"] == "choicescarf"
    assert sets[0]["evs"] == {"hp": 2, "atk": 32, "spe": 32} and sets[0]["nature"] == "Jolly" and len(sets[0]["moves"]) == 4
    sim = DC.to_sim_team(sets)
    assert sim[0]["name"] == DC.nickname(0) == "M1" and sim[1]["name"] == "M2" and sim[0]["level"] == 50
    assert DC.parse_ident("p1a: M1") == ("p1", "m1") and DC.parse_ident("p2: M3") == ("p2", "m3")
    assert DC.parse_ident("p2") == ("p2", None) and DC.parse_ident("bogus") == (None, None)
    assert DC.parse_hp("131/131") == (131, 131, None) and DC.parse_hp("0 fnt") == (0, None, "fnt")
    assert DC.parse_hp("88/167 par") == (88, 167, "par")
    assert DC.species_of_details("Mimikyu-Busted, L50, M") == "mimikyubusted"
    lines = ["|move|p1a: M1|Earthquake|p2a: M1", "|split|p2", "|-damage|p2a: M1|100/245", "|-damage|p2a: M1|41/100", "|turn|2"]
    assert DC.omniscient_lines(lines) == ["|move|p1a: M1|Earthquake|p2a: M1", "|-damage|p2a: M1|100/245", "|turn|2"]
    print("test_team_and_protocol_parsing OK")


def test_tracker_damage_order_and_events():
    tr = _tracker()
    _feed(tr, """
        |switch|p1a: M1|Garchomp, L50, M|185/185
        |switch|p2a: M1|Snorlax, L50, M|245/245
        |turn|1
    """)
    tr.set_choice("p1", {"kind": "move", "id": "earthquake"})
    tr.set_choice("p2", {"kind": "move", "id": "bodyslam"})
    _feed(tr, """
        |
        |move|p1a: M1|Earthquake|p2a: M1
        |-damage|p2a: M1|160/245
        |-damage|p1a: M1|170/185|[from] item: Life Orb
        |move|p2a: M1|Body Slam|p1a: M1
        |-crit|p1a: M1
        |-damage|p1a: M1|100/185
        |-damage|p2a: M1|137/245|[from] ability: Rough Skin|[of] p1a: M1
        |
        |-heal|p2a: M1|152/245|[from] item: Leftovers
        |-weather|Sandstorm|[from] ability: Sand Stream|[of] p1a: M1
        |-sidestart|p2: p2|move: Reflect
        |upkeep
        |turn|2
    """)
    d = tr.obs["damage"]
    assert len(d) == 2, d
    assert d[0]["move"] == "earthquake" and d[0]["damage"] == 85 and d[0]["hp_before"] == 245 and not d[0]["crit"], d[0]
    assert d[0]["attacker"]["species"] == "garchomp" and d[0]["defender"]["item"] == "leftovers" and d[0]["maxhp"] == 245
    assert d[1]["crit"] is True and d[1]["damage"] == 70, d[1]       # 反動 (Life Orb) と Rough Skin は技のダメージに入れない
    o = tr.obs["order"]
    assert len(o) == 1 and o[0]["first"] == "p1" and o[0]["choices"]["p2"]["id"] == "bodyslam", o
    acts = {(a["kind"], a["id"]) for a in tr.obs["activation"]}
    assert {("item", "lifeorb"), ("ability", "roughskin"), ("item", "leftovers"), ("ability", "sandstream")} <= acts, acts
    assert tr.field["weather"] == "sandstorm" and "reflect" in tr.side_cond["p2"] and tr.mons["p2"].get("p2") is None
    assert tr.n_moves == 2
    # 交代を選んだターンは行動順を記録しない、せんせいのツメは対象外の印
    tr.set_choice("p1", {"kind": "switch", "id": "mimikyu"})
    tr.set_choice("p2", {"kind": "move", "id": "bodyslam"})
    _feed(tr, """
        |switch|p1a: M2|Mimikyu, L50, M|131/131
        |move|p2a: M1|Body Slam|p1a: M2
        |-activate|p1a: M2|ability: Disguise
        |-damage|p1a: M2|131/131
        |detailschange|p1a: M2|Mimikyu-Busted, L50, M
        |-damage|p1a: M2|115/131|[from] pokemon: Mimikyu-Busted
        |turn|3
    """)
    assert len(tr.obs["order"]) == 1
    assert "ability: Disguise" in tr.obs["damage"][-1]["events"]
    tr.set_choice("p1", {"kind": "move", "id": "shadowsneak"})
    tr.set_choice("p2", {"kind": "move", "id": "bodyslam"})
    _feed(tr, """
        |-activate|p2a: M1|item: Quick Claw
        |move|p2a: M1|Body Slam|p1a: M2
        |-damage|p1a: M2|60/131
        |cant|p1a: M2|par
        |turn|4
    """)
    assert tr.obs["order"][-1]["flags"] == ["item: Quick Claw"] and tr.obs["incapacitation"][-1]["reason"] == "par"
    assert tr.para_cant == 1 and tr.para_attempts == 1
    print("test_tracker_damage_order_and_events OK")


def test_tracker_formes_and_requests():
    tr = _tracker()
    req = {"side": {"pokemon": [
        {"ident": "p2: M2", "details": "Garchomp, L50, F", "condition": "183/183", "active": True,
         "stats": {"atk": 182, "def": 115, "spa": 90, "spd": 105, "spe": 169}, "ability": "roughskin", "item": "garchompite"}]}}
    tr.feed_request("p2", req)
    assert tr.obs["stats"][0]["species"] == "garchomp" and tr.obs["stats"][0]["truth"]["maxhp"] == 183
    _feed(tr, """
        |switch|p2a: M2|Garchomp, L50, F|183/183
        |-mega|p2a: M2|Garchomp|Garchompite
        |detailschange|p2a: M2|Garchomp-Mega, L50, F
    """)
    assert tr.mega_used["p2"] and tr.mons["p2"]["m2"]["species"] == "garchompmega"
    req2 = {"side": {"pokemon": [
        {"ident": "p2: M2", "details": "Garchomp-Mega, L50, F", "condition": "183/183", "active": True,
         "stats": {"atk": 222, "def": 135, "spa": 130, "spd": 115, "spe": 159}, "ability": "sandforce", "item": "garchompite"}]}}
    tr.feed_request("p2", req2)
    f = tr.obs["forme"]
    assert len(f) == 1 and f[0]["from"] == "garchomp" and f[0]["to"] == "garchompmega" and f[0]["kind"] == "mega", f
    assert f[0]["truth_after"]["stats"]["atk"] == 222 and f[0]["truth_after"]["ability"] == "sandforce"
    # 一時のフォルム (-formechange): request の details は元のままでも、姿は保つ
    tr2 = _tracker()
    _feed(tr2, """
        |switch|p1a: M2|Mimikyu, L50, M|131/131
        |-formechange|p1a: M2|Mimikyu-Busted|
    """)
    tr2.feed_request("p1", {"side": {"pokemon": [{"ident": "p1: M2", "details": "Mimikyu, L50, M", "condition": "131/131",
                                                  "active": True, "stats": {"atk": 156}, "ability": "disguise"}]}})
    assert tr2.mons["p1"]["m2"]["species"] == "mimikyubusted"
    assert tr2.obs["forme"][0]["truth_after"]["species"] == "mimikyubusted"
    # 正解の来なかった変化は finish で未確認として残る
    tr3 = _tracker()
    _feed(tr3, "|switch|p1a: M1|Garchomp, L50, M|185/185\n|detailschange|p1a: M1|Garchomp-Mega, L50, M")
    tr3.finish()
    assert tr3.obs["forme"][0]["truth_after"] is None
    print("test_tracker_formes_and_requests OK")


def test_mega_turn_ability_and_illusion():
    """メガシンカしたターンの技は、次の request の特性 (正解) で写しを直す。request が来ないまま終われば未確認。
    イリュージョン: プロトコルの名前 (化けた先) を request の場の個体に読み替える"""
    tr = _tracker()
    _feed(tr, """
        |switch|p1a: M1|Garchomp, L50, M|185/185
        |switch|p2a: M2|Garchomp, L50, F|183/183
        |turn|1
        |detailschange|p2a: M2|Garchomp-Mega, L50, F
        |-mega|p2a: M2|Garchomp|Garchompite
        |move|p2a: M2|Earthquake|p1a: M1
        |-damage|p1a: M1|60/185
        |upkeep
    """)
    o = tr.obs["damage"][0]
    assert o["attacker"]["ability"] == "roughskin" and o["attacker"]["ability_pending"] is True, o["attacker"]
    assert DC.judge_damage(o)["match"] is None and "特性が分からない" in DC.judge_damage(o)["reason"]
    tr.feed_request("p2", {"side": {"pokemon": [{"ident": "p2: M2", "details": "Garchomp-Mega, L50, F", "condition": "183/183",
                                                 "active": True, "stats": {"atk": 222}, "ability": "sandforce"}]}})
    assert o["attacker"]["ability"] == "sandforce" and o["attacker"]["ability_patched"] is True
    assert DC.judge_damage(o)["match"] is not None
    # イリュージョン
    tr2 = _tracker()
    _feed(tr2, "|switch|p2a: M2|Garchomp, L50, F|245/245\n|switch|p1a: M1|Garchomp, L50, M|185/185")
    tr2.feed_request("p2", {"side": {"pokemon": [
        {"ident": "p2: M1", "details": "Snorlax, L50, M", "condition": "245/245", "active": True, "stats": {"atk": 160}},
        {"ident": "p2: M2", "details": "Garchomp, L50, F", "condition": "183/183", "active": False, "stats": {"atk": 182}}]}})
    assert tr2.active["p2"] == "m1" and tr2.alias["p2"] == {"m2": "m1"}
    _feed(tr2, "|move|p1a: M1|Earthquake|p2a: M2\n|-damage|p2a: M2|200/245\n|upkeep")
    d = tr2.obs["damage"][0]
    assert d["defender"]["species"] == "snorlax" and d["damage"] == 45, d
    assert tr2.mons["p2"]["m2"]["hp"] == 183 and tr2.mons["p2"]["m1"]["hp"] == 200
    _feed(tr2, "|replace|p2a: M1|Snorlax, L50, M")
    assert tr2.alias["p2"] == {}
    print("test_mega_turn_ability_and_illusion OK")


def _snap(species, **kw):
    s = {"species": species, "base_species": species, "hp": 100, "maxhp": 100, "status": None, "boosts": {}, "item": None,
         "ability": None, "types": None, "build": {"ev": {"atk": 252, "spe": 252}, "nature": {}}, "screens": [], "tailwind": False}
    s.update(kw)
    return s


def _dmg_obs(damage, **kw):
    o = {"turn": 1, "move": "earthquake", "attacker": _snap("garchomp"), "defender": _snap("snorlax", hp=200, maxhp=200),
         "field": {}, "crit": False, "hits": 1, "damage": damage, "hp_before": 200, "hp_after": 200 - damage, "maxhp": 200,
         "ko": False, "events": [], "immune": False, "miss": False}
    o.update(kw)
    return o


def _fake_calc(lo, hi, hits=1.0):
    def calc(_a, _d, _m, _f):
        return {"min": lo, "max": hi, "hits": hits, "notes": []}
    return calc


def test_judge_damage():
    calc = _fake_calc(20.0, 25.0)            # 200 HP → 40〜50 HP、許容 1 + 0.2
    assert DC.judge_damage(_dmg_obs(45), calc=calc)["match"] is True
    assert DC.judge_damage(_dmg_obs(39), calc=calc)["match"] is True             # 許容の内
    r = DC.judge_damage(_dmg_obs(30), calc=calc)
    assert r["match"] is False and "小さい" in r["reason"] and r["outside_hp"] == 10.0, r
    assert "大きい" in DC.judge_damage(_dmg_obs(60), calc=calc)["reason"]
    # 倒した手は「助言側の最大が残り HP に届くか」
    assert DC.judge_damage(_dmg_obs(30, hp_before=30, hp_after=0, ko=True), calc=calc)["match"] is True
    assert DC.judge_damage(_dmg_obs(80, hp_before=80, hp_after=0, ko=True), calc=calc)["match"] is False
    # タスキで 1 残った手
    assert DC.judge_damage(_dmg_obs(199, hp_before=200, hp_after=1, events=["enditem:focussash"]), calc=_fake_calc(90, 120))["match"]
    # 急所・化けの皮は対象外 (未確認)
    assert DC.judge_damage(_dmg_obs(70, crit=True), calc=calc)["match"] is None
    assert DC.judge_damage(_dmg_obs(0, events=["ability: Disguise"]), calc=calc)["match"] is None
    # 無効の一致 / 不一致
    assert DC.judge_damage(_dmg_obs(0, immune=True), calc=_fake_calc(0, 0))["match"] is True
    assert DC.judge_damage(_dmg_obs(0, immune=True), calc=calc)["match"] is False
    # 連続技: 助言側の期待回数 (2.5) から 1 発ぶんにして、実際の回数 (4) を掛ける
    multi = _fake_calc(25.0, 30.0, hits=2.5)  # 1 発 10〜12% → 4 発 40〜48% → 80〜96 HP
    assert DC.judge_damage(_dmg_obs(90, hits=4), calc=multi)["match"] is True
    assert DC.judge_damage(_dmg_obs(60, hits=4), calc=multi)["match"] is False
    # 威力が上がる連続技 (トリプルアクセル: 助言側の hits = 1+2+3 = 6): 3 発当たれば幅そのまま、2 発なら (1+2)/6
    assert DC.hit_scale("tripleaxel", 6.0, 3) == 1.0 and DC.hit_scale("tripleaxel", 6.0, 2) == 0.5
    assert abs(DC.hit_scale("bulletseed", 3.1, 5) - 5 / 3.1) < 1e-9 and DC.hit_scale("earthquake", 1.0, 1) == 1.0
    # 計算が落ちる手は未確認
    def boom(*_a):
        raise ValueError("x")
    assert DC.judge_damage(_dmg_obs(45), calc=boom)["match"] is None
    # 本物の計算でも動く (助言側の図鑑で)
    assert DC.judge_damage(_dmg_obs(45))["calc_max"] is not None
    print("test_judge_damage OK")


def test_judge_damage_passes_fainted_allies():
    """照合は攻撃側の味方のひんしの数を calc_damage の文脈 (ctx) に渡す (2026-10-07: そうりょうのつかさ・おはかまいり の手が
    幅の外になっていた)。0 のときは ctx を渡さない"""
    seen = []

    def calc(_a, _d, _m, _f, ctx=None):
        seen.append(ctx)
        return {"min": 20.0, "max": 25.0, "hits": 1.0, "notes": []}
    DC.judge_damage(_dmg_obs(45, attacker=_snap("kingambit", fainted_allies=2)), calc=calc)
    DC.judge_damage(_dmg_obs(45), calc=calc)
    assert seen == [{"fainted_allies": 2}, None], seen
    print("test_judge_damage_passes_fainted_allies OK")


def test_order_prediction():
    fast, slow = _snap("garchomp"), _snap("snorlax")
    assert DC.predict_first(fast, slow, "earthquake", "bodyslam", {})[0] == "p1"
    assert DC.predict_first(slow, fast, "bodyslam", "earthquake", {})[0] == "p2"
    pred, basis, _info = DC.predict_first(fast, slow, "earthquake", "suckerpunch", {})
    assert (pred, basis) == ("p2", "priority")
    assert DC.predict_first(fast, slow, "earthquake", "bodyslam", {"trick_room": True})[0] == "p2"
    assert DC.predict_first(fast, dict(fast), "earthquake", "earthquake", {})[0] is None      # 同速
    # おいかぜ (2 倍) は助言エンジンの素早さに入る: 同じ素早さでもおいかぜの側が先
    assert DC.predict_first(fast, dict(fast, tailwind=True), "earthquake", "earthquake", {})[0] == "p2"
    o = {"turn": 2, "choices": {"p1": {"id": "earthquake"}, "p2": {"id": "bodyslam"}}, "first": "p1", "snap": {"p1": fast, "p2": slow, "field": {}},
         "flags": []}
    assert DC.judge_order(o)["match"] is True
    assert DC.judge_order(dict(o, first="p2"))["match"] is False
    assert DC.judge_order(dict(o, flags=["item: Quick Claw"]))["match"] is None
    print("test_order_prediction OK")


def test_incapacitation_forme_activation_legal():
    model = DC.incapacitation_model()
    assert set(DC.CANT_KINDS) <= set(model) and model["detail"]["healthy"] > 0, model
    o = {"turn": 3, "side": "p1", "species": "garchomp", "reason": "slp", "status": "slp"}
    assert DC.judge_incapacitation(o, {"slp": True})["match"] is True
    assert DC.judge_incapacitation(o, {"slp": False})["match"] is False
    # 形態遷移: 助言側の実数値と特性を正解に揃えれば一致、特性が違えば不一致、正解が無ければ未確認
    adv = DC.advisor_stats("garchompmega", BUILD)
    truth = {"stats": {k: adv[k] for k in DC.STAT_KEYS}, "maxhp": adv["hp"], "ability": "sandforce", "species": "garchompmega"}
    f = {"turn": 2, "side": "p2", "from": "garchomp", "to": "garchompmega", "kind": "mega", "item": "garchompite", "build": BUILD,
         "truth_after": truth}
    assert DC.judge_forme(f)["match"] is True, DC.judge_forme(f)
    assert DC.judge_forme(dict(f, truth_after=dict(truth, ability="roughskin")))["match"] is False
    bad = dict(truth, stats=dict(truth["stats"], atk=truth["stats"]["atk"] + 5))
    r = DC.judge_forme(dict(f, truth_after=bad))
    assert r["match"] is False and "atk" in r["stats_diff"], r
    assert DC.judge_forme(dict(f, truth_after=None))["match"] is None
    assert DC.judge_forme(dict(f, to="notaspecies"))["match"] is None
    st = DC.judge_stats({"side": "p1", "species": "garchomp", "build": BUILD,
                         "truth": {"stats": {k: v for k, v in DC.advisor_stats("garchomp", BUILD).items() if k != "hp"},
                                   "maxhp": DC.advisor_stats("garchomp", BUILD)["hp"]}})
    assert st["match"] is True
    # 発動: 助言側の表にあるか
    assert DC.judge_activation({"turn": 1, "kind": "item", "id": "lifeorb", "species": "x"})["match"] is True
    assert DC.judge_activation({"turn": 1, "kind": "ability", "id": "intimidate", "species": "x"})["match"] is True
    assert DC.judge_activation({"turn": 1, "kind": "item", "id": "zzznotanitem", "species": "x"})["match"] is False
    # 合法手
    lo = {"turn": 4, "best": {"kind": "move", "id": "earthquake"}, "legal_moves": ["earthquake"], "legal_switches": ["snorlax"]}
    assert DC.judge_legal(lo)["match"] is True
    assert DC.judge_legal(dict(lo, best={"kind": "move", "id": "firstimpression"}))["match"] is False
    assert DC.judge_legal(dict(lo, best={"kind": "switch", "id": "snorlax"}))["match"] is True
    assert DC.judge_legal(dict(lo, best=None, error="x"))["match"] is None
    print("test_incapacitation_forme_activation_legal OK")


def test_confirm_table_and_report():
    rows = {"damage": [{"match": True}, {"match": False}, {"match": None}], "order": [],
            "incapacitation": [{"match": False}], "forme": [{"match": True}],
            "activation": [{"kind": "item", "id": "leftovers", "match": False}] * 5 + [{"kind": "item", "id": "lifeorb", "match": True}],
            "legal": [{"match": True}, {"match": True}]}
    t = DC.confirm_table(rows)
    assert t["damage"] == {"match": 1, "checked": 2, "mismatch": 1, "unconfirmed": 1}
    assert t["order"] == {"match": 0, "checked": 0, "mismatch": 0, "unconfirmed": 0}
    assert t["activation"]["checked"] == 2 and t["activation"]["match"] == 1       # 種類ごと
    obs = {"damage": [_dmg_obs(45)], "order": [], "incapacitation": [], "forme": [], "activation": [], "stats": [], "legal": []}
    res = DC.judge_all(obs, model={"par": False, "slp": False, "frz": False, "flinch": False})
    txt = DC.format_report(res, n_moves=1, n_battles=1, para={"attempts": 0, "cant": 0})
    assert "対象別の確認表" in txt and "ダメージ" in txt and "観測なし" in txt, txt
    # 発動・行動不能の不一致は種類ごとの回数でまとめて出す
    rows2 = dict(rows, activation=[{"kind": "item", "id": "leftovers", "match": False, "note": "助言側の表に効果が無い"}] * 5,
                 incapacitation=[{"reason": "slp", "match": False, "note": "扱わない"}] * 3, damage=[], forme=[], legal=[])
    res2 = {"rows": rows2, "stats": [], "table": DC.confirm_table(rows2), "model": {}}
    txt2 = DC.format_report(res2, n_moves=1, n_battles=1)
    assert "item:leftovers 5 回" in txt2 and "slp 3 回" in txt2, txt2
    print("test_confirm_table_and_report OK")


def test_engine_state_legal_sets_and_choose():
    tr = _tracker()
    _feed(tr, "|switch|p1a: M1|Garchomp, L50, M|185/185\n|switch|p2a: M1|Snorlax, L50, M|245/245\n|turn|1")
    req = {"active": [{"moves": [{"move": "Earthquake", "id": "earthquake", "pp": 16, "maxpp": 16, "disabled": False},
                                 {"move": "Dragon Claw", "id": "dragonclaw", "pp": 0, "maxpp": 24, "disabled": False},
                                 {"move": "Stone Edge", "id": "stoneedge", "pp": 8, "maxpp": 8, "disabled": True},
                                 {"move": "Swords Dance", "id": "swordsdance", "pp": 32, "maxpp": 32, "disabled": False}]}],
           "side": {"pokemon": [
               {"ident": "p1: M1", "details": "Garchomp, L50, M", "condition": "185/185", "active": True,
                "stats": {"atk": 182}, "moves": ["earthquake", "dragonclaw", "stoneedge", "swordsdance"], "ability": "roughskin",
                "item": "choicescarf"},
               {"ident": "p1: M2", "details": "Mimikyu, L50, M", "condition": "131/131", "active": False,
                "stats": {"atk": 156}, "moves": ["playrough"], "ability": "disguise", "item": "lifeorb"}]}}
    tr.feed_request("p1", req)
    moves, switches = DC.legal_sets(tr, "p1", req)
    assert moves == {"earthquake", "swordsdance"} and switches == {"mimikyu"}, (moves, switches)
    trapped = dict(req, active=[dict(req["active"][0], trapped=True)])
    assert DC.legal_sets(tr, "p1", trapped)[1] == set()
    st = DC.engine_state(tr, "p1", req)
    me = st["player"]["party"][st["player"]["active_index"]]
    assert me["species_id"] == "garchomp" and [m["move_id"] for m in me["moves"]] == ["earthquake", "dragonclaw", "stoneedge", "swordsdance"]
    assert me["moves"][1]["pp"] == 0 and me["item_id"] == "choicescarf" and st["opponent"]["party"][0]["species_id"] == "snorlax"
    assert DC.engine_state(tr, "p1", {"side": {"pokemon": []}}) is None
    rng = random.Random(1)
    cmd, ch = DC._choose({"teamPreview": True, "maxChosenTeamSize": 3, "side": {"pokemon": [{}] * 6}}, rng, 0.0, 0.0)
    assert cmd.startswith("team ") and len(cmd) == 8 and ch is None
    cmd, ch = DC._choose(req, random.Random(2), 0.0, 0.0)
    assert cmd in ("move 1", "move 2", "move 4") and ch["kind"] == "move", (cmd, ch)
    cmd, ch = DC._choose(req, random.Random(2), 1.0, 0.0)
    assert cmd == "switch 2" and ch == {"kind": "switch", "id": "mimikyu"}
    cmd, ch = DC._choose(dict(req, forceSwitch=[True]), random.Random(2), 0.0, 0.0)
    assert cmd == "switch 2" and ch is None
    print("test_engine_state_legal_sets_and_choose OK")


def main() -> None:
    test_team_and_protocol_parsing()
    test_tracker_damage_order_and_events()
    test_tracker_formes_and_requests()
    test_mega_turn_ability_and_illusion()
    test_judge_damage()
    test_judge_damage_passes_fainted_allies()
    test_order_prediction()
    test_incapacitation_forme_activation_legal()
    test_confirm_table_and_report()
    test_engine_state_legal_sets_and_choose()
    print("ALL OK")


if __name__ == "__main__":
    main()
