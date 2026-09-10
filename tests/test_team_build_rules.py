"""コンセプト規則 (tools/team_build/rules、learnsets、spec の rules) の純粋関数テスト。

    python -m tests.test_team_build_rules
"""
from __future__ import annotations

from tools.team_build import rules as RU
from tools.team_build.candidates import SpeciesFeature
from tools.team_build.learnsets import can_learn, learnsets, parse_learnsets, resolve_species
from tools.team_build.sets import SetCandidate

RULE = RU.RULES["psychic_terrain_priority_ace"]


def _info(sid, spe, dfn, types=("Psychic",), ability="", item="", learn=False):
    return RU.RuleInfo(sid, spe, dfn, tuple(types), ability, item, {"psychicterrain": learn})


def test_classify_setters_and_aces():
    infos = {
        "espathra": _info("espathra", 105, 60, ability="speedboost", item="focussash", learn=True),
        "delphox": _info("delphox", 134, 72, ("Fire", "Psychic"), "blaze", "delphoxite", learn=True),
        "raichu": _info("raichu", 130, 55, ("Electric",), "lightningrod", "raichunitey"),
        "staraptor": _info("staraptor", 100, 70, ("Normal", "Flying"), "intimidate", "choicescarf"),   # 浮いている
        "rotom": _info("rotom", 86, 107, ("Electric", "Water"), "levitate", "leftovers"),
        "sneasler": _info("sneasler", 120, 60, ("Fighting", "Poison"), "unburden", "airballoon"),       # ふうせん
        "indeedee": _info("indeedee", 95, 55, ("Psychic", "Normal"), "psychicsurge", "leftovers"),
        "kingambit": _info("kingambit", 50, 120, ("Dark", "Steel"), "supremeoverlord", "blackglasses"),
    }
    setters, aces = RU.classify(infos, RULE, min_spe=100, max_def=70)
    assert setters == {"espathra", "delphox", "indeedee"}
    assert aces == {"espathra", "raichu"}          # delphox は防御 72、staraptor はひこう、sneasler はふうせん
    assert RU.lineup_satisfies(("espathra", "raichu", "kingambit"), setters, aces)
    assert not RU.lineup_satisfies(("espathra", "kingambit", "rotom"), setters, aces)      # 1 体が両方を兼ねるだけ
    assert RU.lineup_satisfies(("espathra", "delphox", "rotom"), setters, aces)             # delphox 設置 + espathra エース
    assert not RU.lineup_satisfies(("raichu", "kingambit"), setters, aces)
    ctx = RU.build_context(["psychic_terrain_priority_ace"], infos)
    assert ctx["per_rule"][0]["setters"] == RU.classify(infos, RULE)[0]
    assert ctx["llm"][0]["aces"] == sorted(RU.classify(infos, RULE)[1]) and ctx["llm"][0]["label"] == RULE["label"]
    assert RU.satisfies(("espathra", "raichu"), ctx) and not RU.satisfies(("raichu",), ctx)
    print("test_classify_setters_and_aces OK")


def test_rule_cores_and_context_cores():
    threats = ["t1", "t2", "t3", "t4"]

    def f(sid, cov, usage, mega=False, roles=None):
        return SpeciesFeature(sid, dict(zip(threats, cov)), roles or {}, ("Psychic",), mega, 0, usage, {})

    feats = {"espathra": f("espathra", [0.9, 0.1, 0.1, 0.1], 5.0, roles={"setup": 1.0}),
             "delphox": f("delphox", [0.1, 0.9, 0.1, 0.1], 3.0, mega=True),
             "raichu": f("raichu", [0.1, 0.1, 0.9, 0.1], 8.0, mega=True, roles={"setup": 1.0}),
             "volcarona": f("volcarona", [0.1, 0.1, 0.1, 0.9], 1.0, roles={"setup": 1.0})}
    cores = RU.rule_cores("psychic_terrain_priority_ace", {"espathra", "delphox"},
                          {"espathra", "raichu", "volcarona"}, feats, threats, max_cores=3)
    assert [c["core_ids"] for c in cores] == [["espathra", "raichu"], ["delphox", "raichu"], ["delphox", "espathra"]]
    assert cores[0]["mega_id"] == "raichu" and cores[2]["mega_id"] == "delphox"
    assert cores[0]["win_condition"] == "setup_sweep" and cores[0]["source"] == "rule:psychic_terrain_priority_ace"
    assert cores[0]["weak_to"][:2] == ["t2", "t4"]
    ctx = {"per_rule": [{"name": "psychic_terrain_priority_ace", "setters": {"espathra"}, "aces": {"raichu"}}]}
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
    team, sid, notes = RU.ensure_setter([rai, esp], {"espathra"}, {"raichu", "espathra"}, RULE,
                                        category_of=cat, setup_moves=setup)
    # 積み技でない変化技の末尾 (batonpass) を差し替える。元の型は変えない
    assert sid == "espathra" and team[1].moves == ["luminacrash", "protect", "psychicterrain", "calmmind"]
    assert team[1].source.endswith("+rule") and notes == ["rule:psychicterrain<-batonpass"] and team[0] is rai
    assert esp.moves[2] == "batonpass" and team[1].item == "focussash"
    # 設置役が 2 体いればエースでない方 (delphox) を設置役にする。変化技が積み技だけならそれを差し替える
    dlp = _set("delphox", "delphoxite", ["flamethrower", "psychic", "nastyplot", "dazzlinggleam"], "blaze")
    team, sid, _ = RU.ensure_setter([esp, dlp], {"espathra", "delphox"}, {"espathra"}, RULE,
                                    category_of=cat, setup_moves=setup)
    assert sid == "delphox" and team[0] is esp
    assert team[1].moves == ["flamethrower", "psychic", "psychicterrain", "dazzlinggleam"]
    # 既に技/特性で足りていればそのまま
    ind = _set("indeedee", "leftovers", ["expandingforce", "dazzlinggleam", "protect", "healingwish"], "psychicsurge")
    team, sid, notes = RU.ensure_setter([ind, rai], {"indeedee"}, {"raichu"}, RULE, category_of=cat)
    assert sid == "indeedee" and notes == [] and team[0] is ind
    has = _set("gardevoir", "leftovers", ["moonblast", "psychicterrain", "psychic", "calmmind"], "trace")
    assert RU.ensure_setter([has], {"gardevoir"}, set(), RULE)[1] == "gardevoir"
    assert RU.ensure_setter([rai], {"espathra"}, {"raichu"}, RULE) == ([rai], None, ["no_setter"])
    # こだわり持ち物は代替 (alt:item、こだわり/メガ石/チーム内重複でない) に替える。変化技が無ければ末尾に差し込む
    meo = _set("meowscarada", "choicescarf", ["flowertrick", "tripleaxel", "knockoff", "uturn"], "protean")
    alts = {"meowscarada": [meo,
                            _set("meowscarada", "focussash", meo.moves, "protean", source="alt:item"),
                            _set("meowscarada", "meowscaradite", meo.moves, "protean", source="alt:item"),
                            _set("meowscarada", "lifeorb", meo.moves, "protean", source="alt:item")]}
    team, sid, notes = RU.ensure_setter([esp, meo], {"meowscarada"}, {"espathra"}, RULE, alternatives=alts,
                                        category_of=cat, stones={"meowscaradite"})
    assert sid == "meowscarada" and team[1].item == "lifeorb" and "rule:item<-choicescarf" in notes
    assert team[1].moves == ["flowertrick", "tripleaxel", "knockoff", "psychicterrain"] and meo.item == "choicescarf"
    # 代替が無ければ持ち物はそのまま (注記だけ)
    _, _, notes = RU.ensure_setter([meo], {"meowscarada"}, set(), RULE, category_of=cat)
    assert "rule:choice_item_kept" in notes
    # apply_to_team は規則ごとの設置役を返す
    ctx = {"per_rule": [{"name": "psychic_terrain_priority_ace", "rule": RULE, "setters": {"espathra"},
                         "aces": {"raichu"}}]}
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
    usage = {"sneasler": {"whiteherb": 37.9, "focussash": min_pct + 10.0, "choicescarf": 6.2}}
    team, ace, notes = RU.ensure_ace_item([esp, snea], {"espathra", "sneasler"}, "espathra", RULE, usage)
    assert ace == "sneasler" and team[1].item == "focussash" and notes == ["rule:ace_item<-choicescarf"]
    assert snea.item == "choicescarf" and team[1].source == "alt:item+rule" and team[0] is esp
    # メガ石のエースは替えない / 既に優先品ならそのまま
    rai = _set("raichu", "raichunitey", ["zapcannon", "focusblast", "grassknot", "nastyplot"], "lightningrod")
    team, ace, notes = RU.ensure_ace_item([esp, rai], {"raichu"}, "espathra", RULE, usage, stones={"raichunitey"})
    assert ace == "raichu" and team[1].item == "raichunitey" and notes == []
    sash = _set("sneasler", "focussash", snea.moves, "unburden")
    assert RU.ensure_ace_item([sash], {"sneasler"}, None, RULE, usage) == ([sash], "sneasler", [])
    # 使用率が閾値未満なら据え置き (注記)
    low = {"sneasler": {"whiteherb": 90.0, "focussash": min_pct - 1.0}}
    _, _, notes = RU.ensure_ace_item([esp, snea], {"sneasler"}, "espathra", RULE, low)
    assert notes == ["rule:ace_item_kept"]
    # エース候補が設置役だけなら無し。複数なら優先品の使用率が高い方
    assert RU.ensure_ace_item([esp], {"espathra"}, "espathra", RULE, usage)[1] is None
    meo = _set("meowscarada", "choicescarf", ["flowertrick", "tripleaxel", "knockoff", "uturn"], "protean")
    usage2 = dict(usage, meowscarada={"choicescarf": 60.0, "focussash": min_pct + 30.0})
    assert RU.choose_ace([snea, meo], {"sneasler", "meowscarada"}, "espathra", RULE, usage2).species_id == "meowscarada"
    print("test_ensure_ace_item OK")


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
    print("test_spec_rules_parse_and_validate OK")


if __name__ == "__main__":
    test_classify_setters_and_aces()
    test_rule_cores_and_context_cores()
    test_ensure_setter_injects_move()
    test_ensure_ace_item()
    test_boost_multiplier()
    test_parse_learnsets_and_resolve()
    test_spec_rules_parse_and_validate()
