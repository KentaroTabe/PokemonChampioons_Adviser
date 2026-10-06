"""コンセプト規則 (tools/team_build/rules、learnsets、spec の rules) の純粋関数テスト。

    python -m tests.test_team_build_rules
"""
from __future__ import annotations

from pathlib import Path

from tools.team_build import rules as RU
from tools.team_build.candidates import SpeciesFeature
from tools.team_build.learnsets import can_learn, learnsets, parse_learnsets, resolve_species
from tools.team_build.sets import SetCandidate

RULE = RU.RULES["psychic_terrain_priority_ace"]
TH = dict(fast_share=0.75, fast_max_def=75, boost_share=0.8, boost_min_bulk=0.25, tr_share=0.3)


def _info(sid, spe=100, dfn=60, types=("Psychic",), ability="", item="", learn=False, speed=0.0, boost=None,
          mult=1.0, bulk=0.0, tr=False, offense=130, atk_moves=3, atk_types=2, cov=0.5):
    return RU.RuleInfo(sid, spe, dfn, tuple(types), ability, item, {"psychicterrain": learn},
                       speed_share=speed, boost_share=(speed if boost is None else boost), boost_mult=mult, bulk=bulk,
                       has_tr=tr, offense=offense, attack_moves=atk_moves, attack_types=atk_types, coverage_mean=cov)


def _infos():
    return {
        # 設置役
        "espathra": _info("espathra", 105, 60, ability="speedboost", item="focussash", learn=True, speed=0.6, boost=0.95,
                          mult=1.5, bulk=0.29),                                              # 設置役 + 自己加速型
        "delphox": _info("delphox", 134, 72, ("Fire", "Psychic"), "blaze", "delphoxite", learn=True, speed=0.8, bulk=0.44),  # 設置役 + 速攻型 (B72)
        "indeedee": _info("indeedee", 95, 55, ("Psychic", "Normal"), "psychicsurge", "leftovers", speed=0.5, bulk=0.3),
        # エース候補
        "raichu": _info("raichu", 130, 55, ("Electric",), "lightningrod", "raichunitey", speed=0.75, bulk=0.27),         # 速攻型
        "sneasler": _info("sneasler", 120, 60, ("Fighting", "Poison"), "unburden", "whiteherb", speed=0.7, boost=1.0,
                          mult=2.0, bulk=0.32),                                                                          # 自己加速型
        "gyarados": _info("gyarados", 81, 109, ("Water", "Dark"), "moldbreaker", "gyaradosite", speed=0.4, boost=0.95,
                          mult=1.5, bulk=0.6),                                                                           # 自己加速型 (りゅうのまい)
        "hydreigon": _info("hydreigon", 98, 90, ("Dark", "Dragon"), "levitate", "choicescarf", speed=0.95, bulk=0.45),   # ふゆう → 対象外
        "staraptor": _info("staraptor", 100, 70, ("Normal", "Flying"), "intimidate", "choicescarf", speed=0.7, bulk=0.46),  # ひこう → 対象外
        "greninja": _info("greninja", 142, 77, ("Water", "Dark"), "protean", "greninjite", speed=0.9, bulk=0.36),        # 速いが B77 → 速攻型でない
        "slowking": _info("slowking", 30, 80, ("Water", "Psychic"), "regenerator", "leftovers", speed=0.05, bulk=0.6, tr=True),  # TR 使い (遅い → TR 型エースでもある)
        "kingambit": _info("kingambit", 50, 120, ("Dark", "Steel"), "supremeoverlord", "blackglasses", speed=0.05, bulk=0.57),  # TR 型
        "airballoon_mon": _info("airballoon_mon", 120, 60, ("Fighting",), "unburden", "airballoon", speed=0.7, boost=1.0, mult=2.0, bulk=0.3),  # ふうせん
        # 火力・技範囲を満たさない (2026-09-10 指摘: 加速するだけの補助型ペロリーム、攻撃技 1 本のポットデス)
        "slurpuff": _info("slurpuff", 72, 86, ("Fairy",), "unburden", "sitrusberry", speed=0.3, boost=0.95, mult=2.0, bulk=0.38,
                          offense=85, atk_moves=1, atk_types=1, cov=0.2),
        "polteageist": _info("polteageist", 70, 65, ("Ghost",), "cursedbody", "whiteherb", speed=0.3, boost=0.95, mult=2.0,
                             bulk=0.5, offense=134, atk_moves=1, atk_types=1, cov=0.2),
        "narrow": _info("narrow", 130, 55, ("Electric",), "static", "lifeorb", speed=0.9, bulk=0.3, offense=120,
                        atk_moves=3, atk_types=1, cov=0.5),                                                                 # 技範囲 1 タイプ
        "weak": _info("weak", 130, 55, ("Electric",), "static", "lifeorb", speed=0.9, bulk=0.3, offense=80),               # 火力不足
    }


def test_classify_setters_and_aces():
    infos = _infos()
    setters, aces, trs = RU.classify(infos, RULE, **TH)
    assert setters == {"espathra", "delphox", "indeedee"}
    assert aces == {"espathra": ["boost"], "delphox": ["fast"], "raichu": ["fast"], "sneasler": ["boost"],
                    "gyarados": ["boost"], "slowking": ["tr"], "kingambit": ["tr"]}, aces
    assert trs == {"slowking"}
    # 速攻型: 防御の閾値、加速型: 加速手段が要る (hydreigon は速いが浮いている、greninja は B77)
    assert "greninja" not in aces and "hydreigon" not in aces and "staraptor" not in aces and "airballoon_mon" not in aces
    assert RU.ace_types(infos["greninja"], RULE, fast_max_def=80) == ["fast"]                 # 閾値を緩めれば入る
    # 火力・技範囲: 加速するだけの補助型 (slurpuff)、攻撃技 1 本 (polteageist)、1 タイプ (narrow)、火力不足 (weak) は外れる
    assert all(s not in aces for s in ("slurpuff", "polteageist", "narrow", "weak"))
    assert RU.offensive(infos["sneasler"]) and not RU.offensive(infos["slurpuff"]) and not RU.offensive(infos["weak"])
    assert RU.ace_types(infos["polteageist"], RULE, min_attack_moves=1, min_attack_types=1, min_coverage=0.0) == ["boost", "tr"]
    assert RU.ace_types(infos["weak"], RULE, min_offense=80) == ["fast"]
    # 並びの判定: 設置役とエースは別個体。TR 型エースは TR 使いが同じ並びにいるとき
    assert RU.lineup_satisfies(("espathra", "raichu", "kingambit"), setters, aces, trs)
    assert not RU.lineup_satisfies(("espathra", "kingambit", "hydreigon"), setters, aces, trs)   # TR 使い無しの TR 型
    assert RU.lineup_satisfies(("espathra", "kingambit", "slowking"), setters, aces, trs)         # TR 使いあり
    assert not RU.lineup_satisfies(("delphox", "hydreigon"), setters, aces, trs)                   # 設置役 = エースの 1 体だけ
    assert RU.lineup_satisfies(("delphox", "espathra", "hydreigon"), setters, aces, trs)           # 互いに設置役/エース
    assert not RU.lineup_satisfies(("indeedee", "slowking"), setters, aces, trs)                   # TR 使い = TR 型エース自身
    ctx = RU.build_context(["psychic_terrain_priority_ace"], infos, **TH)
    assert ctx["per_rule"][0]["aces"] == aces and ctx["llm"][0]["tr_setters"] == ["slowking"]
    assert ctx["llm"][0]["ace_types"]["sneasler"] == ["boost"] and ctx["llm"][0]["label"] == RULE["label"]
    assert RU.satisfies(("espathra", "raichu"), ctx) and not RU.satisfies(("raichu",), ctx)
    # 既定の閾値 (config) でも同じ判定になる値域
    assert RU.classify(infos, RULE)[0] == setters
    print("test_classify_setters_and_aces OK")


def test_choose_pair_and_complementarity():
    infos = _infos()
    setters, aces, trs = RU.classify(infos, RULE, **TH)
    # 設置役はエースでない個体を優先、エースは速攻 > 加速 > TR の順
    assert RU.choose_pair(("delphox", "espathra", "sneasler", "raichu"), setters, aces, trs) == ("delphox", "raichu")
    assert RU.choose_pair(("espathra", "sneasler", "kingambit"), setters, aces, trs) == ("espathra", "sneasler")
    assert RU.choose_pair(("indeedee", "kingambit", "slowking"), setters, aces, trs) == ("indeedee", "kingambit")
    assert RU.choose_pair(("raichu", "kingambit"), setters, aces, trs) is None
    threats = ["t1", "t2", "t3", "t4", "t5"]

    def f(sid, cov, usage=1.0, mega=False):
        return SpeciesFeature(sid, dict(zip(threats, cov)), {}, ("Psychic",), mega, 0, usage, {})

    feats = {"s": f("s", [0.9, 0.1, 0.1, 0.1, 0.7]), "a": f("a", [0.1, 0.1, 0.1, 0.9, 0.2]),
             "x": f("x", [0.1, 0.9, 0.1, 0.1, 0.1]), "y": f("y", [0.1, 0.1, 0.5, 0.1, 0.1])}
    comp = RU.pair_complementarity(("s", "a", "x", "y"), "s", "a", feats, threats, threshold=0.4, cover=0.6)
    assert comp["shared_weak"] == ["t2", "t3"] and comp["covered_by"] == {"t2": "x"} and comp["uncovered"] == ["t3"]
    assert abs(comp["score"] - 0.5) < 1e-9
    assert comp["ace_checks"] == ["t1", "t2", "t3", "t5"] and abs(comp["setter_covers_ace_checks"] - 0.5) < 1e-9   # t1, t5
    none = RU.pair_complementarity(("s", "a"), "s", "a", {"s": f("s", [0.9] * 5), "a": f("a", [0.1] * 5)}, threats)
    assert none["shared_weak"] == [] and none["score"] == 1.0
    print("test_choose_pair_and_complementarity OK")


def test_rule_cores_and_context_cores():
    threats = ["t1", "t2", "t3", "t4"]

    def f(sid, cov, usage, mega=False, roles=None):
        return SpeciesFeature(sid, dict(zip(threats, cov)), roles or {}, ("Psychic",), mega, 0, usage, {})

    feats = {"espathra": f("espathra", [0.9, 0.1, 0.1, 0.1], 5.0, roles={"setup": 1.0}),
             "delphox": f("delphox", [0.1, 0.9, 0.1, 0.1], 3.0, mega=True),
             "raichu": f("raichu", [0.1, 0.1, 0.9, 0.1], 8.0, mega=True, roles={"setup": 1.0}),
             "volcarona": f("volcarona", [0.1, 0.1, 0.1, 0.9], 1.0, roles={"setup": 1.0}),
             "kingambit": f("kingambit", [0.1, 0.1, 0.1, 0.5], 9.0, roles={"setup": 1.0}),
             "slowking": f("slowking", [0.3, 0.3, 0.3, 0.3], 2.0)}
    aces = {"espathra": ["boost"], "raichu": ["fast"], "volcarona": ["boost"], "kingambit": ["tr"]}
    cores = RU.rule_cores("psychic_terrain_priority_ace", {"espathra", "delphox"}, aces, feats, threats,
                          tr_setters={"slowking"}, max_cores=4)
    # 使用率の和の順: espathra+kingambit+slowking (16) > delphox+kingambit+slowking (14) > espathra+raichu (13) > delphox+raichu (11)
    assert [c["core_ids"] for c in cores] == [["espathra", "kingambit", "slowking"], ["delphox", "kingambit", "slowking"],
                                              ["espathra", "raichu"], ["delphox", "raichu"]], [c["core_ids"] for c in cores]
    assert cores[0]["mega_id"] is None and cores[1]["mega_id"] == "delphox" and cores[2]["mega_id"] == "raichu"
    assert cores[2]["win_condition"] == "setup_sweep" and cores[2]["source"] == "rule:psychic_terrain_priority_ace"
    assert cores[2]["weak_to"][:2] == ["t2", "t4"] and cores[0]["name"].endswith("espathra+kingambit+slowking")
    # TR 使いが居なければ TR 型だけのエースの軸は作らない
    no_tr = RU.rule_cores("psychic_terrain_priority_ace", {"espathra", "delphox"}, aces, feats, threats, max_cores=8)
    assert all("kingambit" not in c["core_ids"] for c in no_tr) and len(no_tr) == 5
    ctx = {"per_rule": [{"name": "psychic_terrain_priority_ace", "setters": {"espathra"}, "aces": {"raichu": ["fast"]},
                         "tr_setters": set()}]}
    assert [c["core_ids"] for c in RU.context_cores(ctx, feats, threats)] == [["espathra", "raichu"]]
    print("test_rule_cores_and_context_cores OK")


def _set(sid, item, moves, ability="", source="representative"):
    return SetCandidate(sid, ability, item, "modest", "2/0/0/32/0/32", list(moves), source)


def test_ensure_setter_injects_move():
    cat = {"luminacrash": "special", "protect": "status", "batonpass": "status", "calmmind": "status",
           "psychic": "special", "flamethrower": "special", "nastyplot": "status", "dazzlinggleam": "special",
           "zapcannon": "special", "focusblast": "special", "grassknot": "special", "psychicterrain": "status"}.get
    setup = {"calmmind", "nastyplot"}
    esp = _set("espathra", "focussash", ["luminacrash", "protect", "batonpass", "calmmind"], "speedboost")
    rai = _set("raichu", "raichunitey", ["zapcannon", "focusblast", "grassknot", "nastyplot"], "lightningrod")
    aces = {"raichu": ["fast"], "espathra": ["boost"]}
    team, sid, notes = RU.ensure_setter([rai, esp], {"espathra"}, aces, RULE, category_of=cat, setup_moves=setup)
    # 積み技でない変化技の末尾 (batonpass) を差し替える。元の型は変えない
    assert sid == "espathra" and team[1].moves == ["luminacrash", "protect", "psychicterrain", "calmmind"]
    assert team[1].source.endswith("+rule") and notes == ["rule:psychicterrain<-batonpass"] and team[0] is rai
    assert esp.moves[2] == "batonpass" and team[1].item == "focussash"
    # 設置役が 2 体いればエースでない方 (delphox) を設置役にする。変化技が積み技だけならそれを差し替える
    dlp = _set("delphox", "delphoxite", ["flamethrower", "psychic", "nastyplot", "dazzlinggleam"], "blaze")
    team, sid, _ = RU.ensure_setter([esp, dlp], {"espathra", "delphox"}, {"espathra": ["boost"]}, RULE,
                                    category_of=cat, setup_moves=setup)
    assert sid == "delphox" and team[0] is esp
    assert team[1].moves == ["flamethrower", "psychic", "psychicterrain", "dazzlinggleam"]
    # 既に技/特性で足りていればそのまま
    ind = _set("indeedee", "leftovers", ["expandingforce", "dazzlinggleam", "protect", "healingwish"], "psychicsurge")
    team, sid, notes = RU.ensure_setter([ind, rai], {"indeedee"}, {"raichu": ["fast"]}, RULE, category_of=cat)
    assert sid == "indeedee" and notes == [] and team[0] is ind
    has = _set("gardevoir", "leftovers", ["moonblast", "psychicterrain", "psychic", "calmmind"], "trace")
    assert RU.ensure_setter([has], {"gardevoir"}, {}, RULE)[1] == "gardevoir"
    assert RU.ensure_setter([rai], {"espathra"}, {"raichu": ["fast"]}, RULE) == ([rai], None, ["no_setter"])
    # こだわり持ち物は代替 (alt:item、こだわり/メガ石/チーム内重複でない) に替える。変化技が無ければ末尾に差し込む
    meo = _set("meowscarada", "choicescarf", ["flowertrick", "tripleaxel", "knockoff", "uturn"], "protean")
    alts = {"meowscarada": [meo,
                            _set("meowscarada", "focussash", meo.moves, "protean", source="alt:item"),
                            _set("meowscarada", "meowscaradite", meo.moves, "protean", source="alt:item"),
                            _set("meowscarada", "lifeorb", meo.moves, "protean", source="alt:item")]}
    team, sid, notes = RU.ensure_setter([esp, meo], {"meowscarada"}, {"espathra": ["boost"]}, RULE, alternatives=alts,
                                        category_of=cat, stones={"meowscaradite"})
    assert sid == "meowscarada" and team[1].item == "lifeorb" and "rule:item<-choicescarf" in notes
    assert team[1].moves == ["flowertrick", "tripleaxel", "knockoff", "psychicterrain"] and meo.item == "choicescarf"
    _, _, notes = RU.ensure_setter([meo], {"meowscarada"}, {}, RULE, category_of=cat)
    assert "rule:choice_item_kept" in notes
    # apply_to_team は規則ごとの設置役/エースと、クローズの優先度 (エース 2 > 設置役 1) を返す
    ctx = {"per_rule": [{"name": "psychic_terrain_priority_ace", "rule": RULE, "setters": {"espathra"},
                         "aces": {"raichu": ["fast"]}, "tr_setters": set()}]}
    team, roles, notes, prefer = RU.apply_to_team([rai, esp], ctx, category_of=cat, setup_moves=setup,
                                                  stones={"raichunitey"})
    assert roles == {"psychic_terrain_priority_ace": {"setter": "espathra", "ace": "raichu"}}
    assert "psychicterrain" in team[1].moves and team[0].item == "raichunitey"
    assert prefer == {"espathra": 1, "raichu": 2}
    print("test_ensure_setter_injects_move OK")


def test_ensure_ace_item():
    """エースの持ち物: 優先品 (きあいのタスキ) をその種での使用率が閾値以上なら付ける。メガ石は替えない"""
    min_pct = float(RULE["ace_item_min_pct"])
    esp = _set("espathra", "focussash", ["luminacrash", "protect", "psychicterrain", "calmmind"], "speedboost")
    snea = _set("sneasler", "choicescarf", ["closecombat", "direclaw", "fakeout", "throatchop"], "unburden", source="alt:item")
    aces = {"espathra": ["boost"], "sneasler": ["boost"]}
    usage = {"sneasler": {"whiteherb": 37.9, "focussash": min_pct + 10.0, "choicescarf": 6.2}}
    # かるわざのエース: 発動させる消耗品 (しろいハーブ + インファイト) が優先。タスキは他に回せる (2026-09-11 ユーザー指摘)。
    # サイコシードは使用率が無いので飛ばす
    team, ace, notes = RU.ensure_ace_item([esp, snea], aces, "espathra", RULE, usage)
    assert ace == "sneasler" and team[1].item == "whiteherb" and notes == ["rule:ace_item<-choicescarf"]
    assert snea.item == "choicescarf" and team[1].source == "alt:item+rule" and team[0] is esp
    # メガ石のエースは替えない / 既に優先品ならそのまま
    rai = _set("raichu", "raichunitey", ["zapcannon", "focusblast", "grassknot", "nastyplot"], "lightningrod")
    team, ace, notes = RU.ensure_ace_item([esp, rai], {"raichu": ["fast"]}, "espathra", RULE, usage, stones={"raichunitey"})
    assert ace == "raichu" and team[1].item == "raichunitey" and notes == []
    # 代表型がタスキでも、より優先の発動品 (しろいハーブ 37.9%) が使えるならそちらへ (タスキは他に回せる)。
    # 既に最優先の使える持ち物ならそのまま
    sash = _set("sneasler", "focussash", snea.moves, "unburden")
    team, ace, notes = RU.ensure_ace_item([sash], {"sneasler": ["boost"]}, None, RULE, usage)
    assert team[0].item == "whiteherb" and notes == ["rule:ace_item<-focussash"] and sash.item == "focussash"
    herb = _set("sneasler", "whiteherb", snea.moves, "unburden")
    assert RU.ensure_ace_item([herb], {"sneasler": ["boost"]}, None, RULE, usage) == ([herb], "sneasler", [])
    # しろいハーブは自分の能力を下げる技が無いと発動しない → ノーマルジュエル (ノーマル攻撃技あり) → 無ければタスキ
    usage_gem = {"sneasler": {"whiteherb": 30.0, "normalgem": 15.0, "focussash": 30.0}}
    no_drop = _set("sneasler", "choicescarf", ["direclaw", "fakeout", "throatchop", "swordsdance"], "unburden")
    assert RU.ensure_ace_item([esp, no_drop], aces, "espathra", RULE, usage_gem)[0][1].item == "normalgem"
    no_normal = _set("sneasler", "choicescarf", ["direclaw", "throatchop", "swordsdance", "uturn"], "unburden")
    assert RU.ensure_ace_item([esp, no_normal], aces, "espathra", RULE, usage_gem)[0][1].item == "focussash"
    assert RU.unburden_trigger_ok("whiteherb", ["closecombat"], RULE["unburden_triggers"])
    assert not RU.unburden_trigger_ok("whiteherb", ["direclaw"], RULE["unburden_triggers"])
    assert RU.unburden_trigger_ok("normalgem", ["fakeout"], RULE["unburden_triggers"])
    assert RU.unburden_trigger_ok("focussash", [], RULE["unburden_triggers"]) and RU.unburden_trigger_ok("normalgem", [], {})
    # シード類は並びの場 (規則の前提) のフィールドが一致するときだけ
    psy = {"terrain": "psychic", "weather": None}
    assert RU.unburden_trigger_ok("psychicseed", [], RULE["unburden_triggers"], team_field=psy)
    assert not RU.unburden_trigger_ok("psychicseed", [], RULE["unburden_triggers"], team_field=None)
    assert not RU.unburden_trigger_ok("grassyseed", [], RULE["unburden_triggers"], team_field=psy)
    assert RU.ace_item_preference(RULE, "unburden")[0] == "psychicseed" and RU.ace_item_preference(RULE, "protean") == ("focussash",)
    # M-C の実データ (サイコシード 18.4% ≥ 閾値): 規則の場がサイコフィールドなので、タスキの代表型でもサイコシードへ
    usage_mc = {"sneasler": {"focussash": 25.6, "sitrusberry": 20.3, "psychicseed": 18.4, "whiteherb": 4.4}}
    team, ace, notes = RU.ensure_ace_item([esp, sash], aces, "espathra", RULE, usage_mc)
    assert team[1].item == "psychicseed" and notes == ["rule:ace_item<-focussash"]
    # 規則の場が無ければシードは使えず、しろいハーブ (4.4% < 閾値) も使えず、タスキのまま
    team, ace, notes = RU.ensure_ace_item([esp, sash], aces, "espathra", RULE, usage_mc, team_field={"terrain": None, "weather": None})
    assert team[1].item == "focussash" and notes == []
    # 特性に表が無ければ ace_items (タスキ)。使用率が閾値未満なら据え置き (注記)
    meo_scarf = _set("meowscarada", "choicescarf", ["flowertrick", "tripleaxel", "knockoff", "uturn"], "protean")
    low = {"meowscarada": {"choicescarf": 90.0, "focussash": min_pct - 1.0}}
    _, _, notes = RU.ensure_ace_item([esp, meo_scarf], {"meowscarada": ["fast"]}, "espathra", RULE, low)
    assert notes == ["rule:ace_item_kept"]
    ok_usage = {"meowscarada": {"choicescarf": 60.0, "focussash": min_pct + 5.0}}
    assert RU.ensure_ace_item([esp, meo_scarf], {"meowscarada": ["fast"]}, "espathra", RULE, ok_usage)[0][1].item == "focussash"
    # エース候補が設置役だけなら無し。複数なら優先品の使用率が高い方。TR 型のエースは TR 使いが並びにいるとき
    assert RU.ensure_ace_item([esp], {"espathra": ["boost"]}, "espathra", RULE, usage)[1] is None
    meo = _set("meowscarada", "choicescarf", ["flowertrick", "tripleaxel", "knockoff", "uturn"], "protean")
    usage2 = dict(usage, meowscarada={"choicescarf": 60.0, "focussash": min_pct + 30.0})
    assert RU.choose_ace([snea, meo], {"sneasler": ["boost"], "meowscarada": ["fast"]}, "espathra", RULE,
                         usage2).species_id == "meowscarada"
    king = _set("kingambit", "blackglasses", ["suckerpunch"], "supremeoverlord")
    slow = _set("slowking", "leftovers", ["trickroom"], "regenerator")
    assert RU.choose_ace([esp, king], {"kingambit": ["tr"]}, "espathra", RULE, usage, tr_setters={"slowking"}) is None
    assert RU.choose_ace([esp, king, slow], {"kingambit": ["tr"]}, "espathra", RULE, usage,
                         tr_setters={"slowking"}).species_id == "kingambit"
    print("test_ensure_ace_item OK")


def test_offense_metrics_and_ace_set():
    """火力・技範囲は型ライブラリの代替も見る: ポットデスの代表型 (からをやぶる+バトンタッチ、攻撃技 1 本) は通らないが、
    アシストパワー入りの代替が通る → その型をエースの型として S6 で採用する"""
    info = {"shellsmash": ("Status", "Normal", 0), "batonpass": ("Status", "Normal", 0), "shadowball": ("Special", "Ghost", 80),
            "strengthsap": ("Status", "Grass", 0), "storedpower": ("Special", "Psychic", 20), "gigadrain": ("Special", "Grass", 75),
            "suckerpunch": ("Physical", "Dark", 70)}.get
    bs = {"atk": 65, "spa": 134}
    assert RU.offense_metrics(["shellsmash", "batonpass", "shadowball", "strengthsap"], bs, info) == (134, 1, 1)
    assert RU.offense_metrics(["shellsmash", "storedpower", "shadowball", "strengthsap"], bs, info) == (134, 2, 2)
    assert RU.offense_metrics(["suckerpunch", "shadowball"], bs, info) == (134, 2, 2)      # 物理も特殊もあれば大きい方
    assert RU.offense_metrics([], bs, info) == (0, 0, 0)
    rep = _set("polteageist", "whiteherb", ["shellsmash", "batonpass", "shadowball", "strengthsap"], "cursedbody")
    rep.score = 0.20
    alt_pass = SetCandidate("polteageist", "cursedbody", "whiteherb", "modest", "2/0/0/32/0/32",
                            ["shellsmash", "storedpower", "shadowball", "strengthsap"], "alt:move", 0.35, usage_gap=0.27)
    alt_low = SetCandidate("polteageist", "cursedbody", "whiteherb", "modest", "2/0/0/32/0/32",
                           ["shellsmash", "gigadrain", "shadowball", "strengthsap"], "alt:move", 0.25, usage_gap=0.67)
    th = dict(min_offense=100, min_attack_moves=2, min_attack_types=2, min_coverage=0.3)
    assert RU.pick_ace_set([rep, alt_low, alt_pass], bs, info, lambda c: c.score, **th) is alt_pass   # 被覆 0.25 は門を通らない
    assert RU.pick_ace_set([rep, alt_low], bs, info, lambda c: c.score, **th) is None
    good_rep = _set("sneasler", "whiteherb", ["suckerpunch", "shadowball"], "unburden")
    good_rep.score = 0.5
    assert RU.pick_ace_set([good_rep, alt_pass], bs, info, lambda c: c.score, **th) is good_rep       # 代表型が通ればそれ
    # S6: エースの型を差し替える (持ち物は今のまま)。同じ技構成なら何もしない。登録が無ければ何もしない
    cur = _set("polteageist", "focussash", rep.moves, "cursedbody")
    team, notes = RU.ensure_ace_set([cur], "polteageist", {"polteageist": alt_pass})
    assert team[0].moves == alt_pass.moves and team[0].item == "focussash" and team[0].source.endswith("+rule")
    assert notes == ["rule:ace_set<-shellsmash/batonpass/shadowball/strengthsap"] and cur.moves == rep.moves
    assert RU.ensure_ace_set([cur], "polteageist", {}) == ([cur], []) and RU.ensure_ace_set([cur], None, {"polteageist": alt_pass}) == ([cur], [])
    same = _set("polteageist", "focussash", alt_pass.moves, "cursedbody")
    assert RU.ensure_ace_set([same], "polteageist", {"polteageist": alt_pass}) == ([same], [])
    # apply_to_team: 設置役の技 → エースの型 → エースの持ち物
    esp = _set("espathra", "focussash", ["luminacrash", "protect", "psychicterrain", "calmmind"], "speedboost")
    ctx = {"per_rule": [{"name": "psychic_terrain_priority_ace", "rule": RULE, "setters": {"espathra"},
                         "aces": {"polteageist": ["boost"]}, "tr_setters": set()}],
           "ace_sets": {"polteageist": alt_pass}}
    usage = {"polteageist": {"whiteherb": 77.7, "focussash": 18.0}}
    team, roles, notes, prefer = RU.apply_to_team([esp, cur], ctx, usage_pct=usage)
    assert roles["psychic_terrain_priority_ace"] == {"setter": "espathra", "ace": "polteageist"}
    assert team[1].moves == alt_pass.moves and team[1].item == "focussash" and any(n.startswith("rule:ace_set") for n in notes)
    # 1 回積んだ後の能力ランク: 積み技の最大 (下降はそのまま) + 特性の加速 (1.5 → +1、2.0 → +2)
    setup = {"shellsmash": {"atk": 2, "spa": 2, "spe": 2, "def": -1, "spd": -1}, "calmmind": {"spa": 1, "spd": 1},
             "dragondance": {"atk": 1, "spe": 1}}
    assert RU.setup_stages(["shellsmash", "shadowball"], setup) == {"atk": 2, "spa": 2, "spe": 2, "def": -1, "spd": -1}
    assert RU.setup_stages(["calmmind", "luminacrash"], setup, ability_spe_mult=1.5) == {"spa": 1, "spd": 1, "spe": 1}
    assert RU.setup_stages(["closecombat"], setup, ability_spe_mult=2.0) == {"spe": 2}
    assert RU.setup_stages(["dragondance", "shellsmash"], setup) == {"atk": 2, "spa": 2, "spe": 2, "def": -1, "spd": -1}
    assert RU.setup_stages(["shadowball"], setup) == {}
    print("test_offense_metrics_and_ace_set OK")


def test_boost_multiplier():
    from champions_agent.config import BUILD_SPEED_BOOST_ABILITIES, BUILD_SPEED_SETUP_MOVES
    from tools.team_build.features import boost_multiplier
    assert boost_multiplier("speedboost", "focussash", ["protect"]) == BUILD_SPEED_BOOST_ABILITIES["speedboost"]
    assert boost_multiplier("unburden", "whiteherb", []) == BUILD_SPEED_BOOST_ABILITIES["unburden"]
    assert boost_multiplier("unburden", "choicescarf", []) == 1.0             # 消費アイテムでなければ発動しない
    assert boost_multiplier("intimidate", "lifeorb", ["dragondance"]) == BUILD_SPEED_SETUP_MOVES["dragondance"]
    assert boost_multiplier(None, None, ["agility"]) == BUILD_SPEED_SETUP_MOVES["agility"]
    assert boost_multiplier("unburden", "sitrusberry", ["dragondance"]) == max(
        BUILD_SPEED_BOOST_ABILITIES["unburden"], BUILD_SPEED_SETUP_MOVES["dragondance"])
    assert boost_multiplier("blaze", "delphoxite", ["flamethrower"]) == 1.0
    print("test_boost_multiplier OK")


def test_parse_learnsets_and_resolve():
    text = ("export const Learnsets = {\n\tvenusaur: {\n\t\tlearnset: {\n\t\t\tacidspray: [\"9M\"],\n"
            "\t\t\tgrassyterrain: [\"9M\"],\n\t\t},\n\t},\n"
            "\traichu: {\n\t\tlearnset: {\n\t\t\tnastyplot: [\"9M\"],\n\t\t},\n"
            "\t\teventData: [\n\t\t\t{generation: 9, moves: [\"surf\"]},\n\t\t],\n\t},\n};\n")
    table = parse_learnsets(text)
    assert table == {"venusaur": {"acidspray", "grassyterrain"}, "raichu": {"nastyplot"}}
    assert resolve_species("raichumegay", table) == "raichu" and resolve_species("rotomwash", table) is None
    assert can_learn("raichumegay", "nastyplot", table) and not can_learn("raichu", "surf", table)
    real = learnsets()
    if real:
        # champions mod の実データ: クエスパトラはサイコフィールドを覚え、ドドゲザンは覚えない
        assert can_learn("espathra", "psychicterrain", real) and not can_learn("kingambit", "psychicterrain", real)
        assert can_learn("raichumegay", "nastyplot", real)
    else:
        print("  (champions mod の learnsets.ts が無いので実データの確認は省略)")
    print("test_parse_learnsets_and_resolve OK")


def test_spec_rules_parse_and_validate():
    from tools.team_build.spec import parse_form, validate_spec
    owned = ["a", "b", "c", "d", "e", "f"]
    spec = parse_form({"rules": "psychic_terrain_priority_ace", "banned": "scizor"}, owned=owned)
    assert spec.rules == ["psychic_terrain_priority_ace"] and spec.provenance["rules"] == "resolved"
    assert not [p for p in validate_spec(spec) if "rules" in p]
    bad = parse_form({"rules": ["nope", "psychic_terrain_priority_ace"]}, owned=owned)
    assert any("nope" in p for p in validate_spec(bad))
    assert parse_form({}, owned=owned).rules == []
    # 技 + ポケモンの指定 (日本語可) と指定の型
    from tools.team_build.spec import parse_required_moves
    got = parse_required_moves("マフォクシー:サイコフィールド, ポットデス:からをやぶる/アシストパワー")
    assert got == {"delphox": ["psychicterrain"], "polteageist": ["shellsmash", "storedpower"]}, got
    assert parse_required_moves({"espathra": ["luminacrash", "dazzlinggleam"]}) == {"espathra": ["luminacrash", "dazzlinggleam"]}
    assert parse_required_moves("delphox：psychicterrain") == {"delphox": ["psychicterrain"]}
    owned2 = ["delphox", "polteageist", "indeedee", "a", "b", "c"]
    spec2 = parse_form({"moves": "マフォクシー:サイコフィールド",
                        "sets": "Indeedee @ Psychic Seed\nAbility: Psychic Surge\n- Expanding Force\n- Protect\n"},
                       owned=owned2)
    assert spec2.required_moves == {"delphox": ["psychicterrain"]} and spec2.provenance["required_moves"] == "resolved"
    assert spec2.custom_sets["indeedee"]["moves"] == ["expandingforce", "protect"] and spec2.provenance["custom_sets"] == "resolved"
    assert not [p for p in validate_spec(spec2) if "指定" in p], validate_spec(spec2)
    # gengar は使える候補 (owned2) に無いので技指定が弾かれる (banned のファイルは読まない: 無いパスを渡す)
    bad2 = parse_form({"moves": "gengar:shadowball", "sets": "delphox @ delphoxite\n- notamove\n"}, owned=owned2,
                      banned_path=Path("/nonexistent/banned.txt"))
    probs = validate_spec(bad2)
    assert any("gengar" in p and "所持" in p for p in probs) and any("notamove" in p for p in probs), probs
    print("test_spec_rules_parse_and_validate OK")


def test_rule_field():
    """規則が前提とする場: 設置役の技 (サイコフィールド) から {"terrain": "psychic"}。規則が無ければ両方 None"""
    from tools.team_build import rules as RU
    assert RU.rule_field(["psychic_terrain_priority_ace"]) == {"terrain": "psychic", "weather": None}
    assert RU.rule_field([]) == {"terrain": None, "weather": None} and RU.rule_field(None) == {"terrain": None, "weather": None}
    print("test_rule_field OK")


if __name__ == "__main__":
    test_rule_field()
    test_classify_setters_and_aces()
    test_choose_pair_and_complementarity()
    test_rule_cores_and_context_cores()
    test_ensure_setter_injects_move()
    test_ensure_ace_item()
    test_offense_metrics_and_ace_set()
    test_boost_multiplier()
    test_parse_learnsets_and_resolve()
    test_spec_rules_parse_and_validate()
