"""型の常識規則 (tools/team_build/set_lint) のテスト。技の情報は偽の表で渡す (図鑑に依存しない)。

    python -m tests.test_set_lint
"""
from __future__ import annotations

from tools.team_build import set_lint as L

MOVES = {
    "flamethrower": {"type": "Fire", "category": "special", "power": 90},
    "fireblast": {"type": "Fire", "category": "special", "power": 110},
    "earthquake": {"type": "Ground", "category": "physical", "power": 100},
    "closecombat": {"type": "Fighting", "category": "physical", "power": 120},
    "return": {"type": "Normal", "category": "physical", "power": 102},
    "hypervoice": {"type": "Normal", "category": "special", "power": 90, "flags": {"sound"}},
    "shadowball": {"type": "Ghost", "category": "special", "power": 80},
    "foulplay": {"type": "Dark", "category": "physical", "power": 95, "target_stat": True},
    "bodypress": {"type": "Fighting", "category": "physical", "power": 80, "other_stat": "def"},
    "seismictoss": {"type": "Fighting", "category": "physical", "power": 0, "fixed_damage": "level"},
    "nightshade": {"type": "Ghost", "category": "special", "power": 0, "fixed_damage": "level"},
    "counter": {"type": "Fighting", "category": "physical", "power": 0, "condition": "hit_by_physical"},
    "superfang": {"type": "Normal", "category": "physical", "power": 0},
    "fissure": {"type": "Ground", "category": "physical", "power": 0, "ohko": True},
    "gyroball": {"type": "Steel", "category": "physical", "power": 0, "variable_power": "speed_ratio"},
    "uturn": {"type": "Bug", "category": "physical", "power": 70},
    "voltswitch": {"type": "Electric", "category": "special", "power": 70},
    "fakeout": {"type": "Normal", "category": "physical", "power": 40},
    "rapidspin": {"type": "Normal", "category": "physical", "power": 50},
    "dragontail": {"type": "Dragon", "category": "physical", "power": 60},
    "circlethrow": {"type": "Fighting", "category": "physical", "power": 60},
    "nuzzle": {"type": "Electric", "category": "physical", "power": 20},
    "ironhead": {"type": "Steel", "category": "physical", "power": 80},
    "transform": {"type": "Normal", "category": "status", "power": 0},
    "trick": {"type": "Psychic", "category": "status", "power": 0},
    "stealthrock": {"type": "Rock", "category": "status", "power": 0},
    "acrobatics": {"type": "Flying", "category": "physical", "power": 55},
    "weatherball": {"type": "Normal", "category": "special", "power": 50},
    "terrainpulse": {"type": "Normal", "category": "special", "power": 50},
    "rest": {"type": "Psychic", "category": "status", "power": 0},
    "protect": {"type": "Normal", "category": "status", "power": 0},
    "swordsdance": {"type": "Normal", "category": "status", "power": 0},
    "sunnyday": {"type": "Fire", "category": "status", "power": 0},
    "raindance": {"type": "Water", "category": "status", "power": 0},
    "psychicterrain": {"type": "Psychic", "category": "status", "power": 0},
    "toxic": {"type": "Poison", "category": "status", "power": 0},
    "roost": {"type": "Flying", "category": "status", "power": 0},
    "substitute": {"type": "Normal", "category": "status", "power": 0},
    "willowisp": {"type": "Fire", "category": "status", "power": 0},
}
TYPES = {"charizard": ["Fire", "Flying"], "blissey": ["Normal"], "corviknight": ["Flying", "Steel"], "torkoal": ["Fire"],
         "pelipper": ["Water", "Flying"], "altaria": ["Dragon", "Fairy"], "greninja": ["Water", "Dark"], "hawlucha": ["Fighting", "Flying"],
         "garchomp": ["Dragon", "Ground"]}


class FakeInfo:
    def move(self, m):
        d = MOVES.get(m)
        if d is None:
            return None
        return dict({"variable_power": None, "fixed_damage": None, "ohko": False, "target_stat": False, "other_stat": None,
                     "condition": None, "flags": set()}, **d)

    def types_of(self, sid):
        return list(TYPES.get(sid, []))


INFO = FakeInfo()


def lint(sid, ability, item, nature, moves):
    return L.lint_set(sid, ability, item, nature, moves, info=INFO)


def test_nature_move_rule():
    # 性格が下げる側の攻撃技 → 誤り。自分の能力を使わない技と除外の技 (交代技・除去・ねこだまし) は数えない
    assert "nature_move" in lint("charizard", "blaze", "lifeorb", "modest", ["flamethrower", "earthquake", "shadowball", "protect"])["errors"]
    assert lint("charizard", "blaze", "lifeorb", "modest", ["flamethrower", "shadowball", "uturn", "protect"])["errors"] == []
    r = lint("blissey", "naturalcure", "leftovers", "bold", ["seismictoss", "counter", "toxic", "protect"])
    assert "nature_move" not in r["errors"]
    assert lint("corviknight", "pressure", "leftovers", "bold", ["bodypress", "foulplay", "roost", "protect"])["errors"] == []
    assert lint("garchomp", "roughskin", "lifeorb", "timid", ["fissure", "superfang", "flamethrower", "protect"])["errors"] == []
    assert "nature_move" in lint("garchomp", "roughskin", "lifeorb", "timid", ["gyroball", "flamethrower", "protect", "toxic"])["errors"]
    assert "nature_move" in lint("charizard", "blaze", "lifeorb", "adamant", ["earthquake", "closecombat", "flamethrower", "protect"])["errors"]
    assert lint("charizard", "blaze", "lifeorb", "adamant", ["earthquake", "closecombat", "voltswitch", "fakeout"])["errors"] == []
    assert lint("charizard", "blaze", "lifeorb", "jolly", ["earthquake", "closecombat", "rapidspin", "protect"])["errors"] == []
    # 効果が目的の技 (ドラゴンテール / ともえなげ / ほっぺすりすり) は除く (10/5 の定義の漏れ: 登録チームのずぶとい + ドラゴンテール)
    assert lint("corviknight", "pressure", "leftovers", "bold", ["dragontail", "bodypress", "roost", "protect"])["errors"] == []
    assert lint("corviknight", "pressure", "leftovers", "bold", ["circlethrow", "nuzzle", "roost", "protect"])["errors"] == []
    assert "nature_move" in lint("corviknight", "pressure", "leftovers", "bold", ["ironhead", "bodypress", "roost", "protect"])["errors"]
    # 無補正や防御側を下げる性格は対象外
    assert lint("charizard", "blaze", "lifeorb", "naive", ["earthquake", "flamethrower", "protect", "toxic"])["errors"] == []
    assert lint("charizard", "blaze", "lifeorb", None, ["earthquake", "flamethrower", "protect", "toxic"])["errors"] == []
    print("test_nature_move_rule OK")


def test_item_rule():
    def item_errs(item, moves):
        return (lint("charizard", "blaze", item, "timid", moves)["detail"].get("item") or {}).get("problems", [])
    assert item_errs(None, ["flamethrower", "shadowball", "protect", "roost"]) == ["none"]
    assert item_errs("charcoal", ["shadowball", "hypervoice", "protect", "roost"]) == ["type_no_move"]
    assert item_errs("charcoal", ["flamethrower", "shadowball", "protect", "roost"]) == []
    assert item_errs("flameplate", ["shadowball", "hypervoice", "protect", "roost"]) == ["type_no_move"]
    assert item_errs("chestoberry", ["flamethrower", "shadowball", "protect", "roost"]) == ["chesto_no_rest"]
    assert item_errs("chestoberry", ["flamethrower", "shadowball", "rest", "roost"]) == []
    assert item_errs("choicespecs", ["flamethrower", "shadowball", "hypervoice", "protect"]) == ["choice_status"]
    assert item_errs("choicescarf", ["flamethrower", "shadowball", "hypervoice", "weatherball"]) == []
    assert item_errs("leftovers", ["acrobatics", "flamethrower", "roost", "protect"]) == ["acrobatics"]
    assert item_errs("focussash", ["acrobatics", "flamethrower", "roost", "protect"]) == []
    assert item_errs("sitrusberry", ["acrobatics", "flamethrower", "roost", "protect"]) == []
    assert item_errs("flyinggem", ["acrobatics", "flamethrower", "roost", "protect"]) == []
    # 持ち物なしは他の規則と重ならない (アクロバットは消費する持ち物の規則だけ)
    assert item_errs(None, ["acrobatics", "flamethrower", "roost", "protect"]) == ["none"]
    # こだわり + へんしん / トリック は誤りにしない (メタモンのスカーフ、こだわりトリック。10/5 の誤検出の補正)
    assert item_errs("choicescarf", ["transform"]) == []
    assert item_errs("choicescarf", ["trick", "flamethrower", "shadowball", "hypervoice"]) == []
    assert item_errs("choicescarf", ["protect", "flamethrower", "shadowball", "hypervoice"]) == ["choice_status"]
    print("test_item_rule OK")


def test_field_dup_and_few_moves():
    assert lint("torkoal", "drought", "heatrock", "quiet", ["flamethrower", "sunnyday", "earthquake", "protect"])["errors"] == ["field_dup"]
    assert lint("torkoal", "drought", "heatrock", "quiet", ["flamethrower", "raindance", "earthquake", "protect"])["errors"] == []
    assert lint("torkoal", "whitesmoke", "heatrock", "quiet", ["flamethrower", "sunnyday", "earthquake", "protect"])["errors"] == []
    r = lint("torkoal", "whitesmoke", "heatrock", "quiet", ["flamethrower", "earthquake", "protect"])
    assert r["errors"] == ["few_moves"] and r["detail"]["few_moves"]["n"] == 3
    assert "few_moves" in lint("torkoal", "whitesmoke", "heatrock", "quiet", ["flamethrower", "flamethrower", "earthquake", "protect"])["errors"]
    assert L.has_errors(r) and not L.has_errors({"errors": [], "warnings": ["no_stab"]})
    print("test_field_dup_and_few_moves OK")


def test_few_moves_exemptions():
    # 覚える技が 4 つ無い種は「4 つ未満」を誤りにしない (メタモン)。learnset_size を持つ info でも同じ
    r = lint("ditto", "imposter", "choicescarf", "timid", ["transform"])
    assert r["errors"] == [] and r["warnings"] == ["no_stab"] if TYPES.get("ditto") else r["errors"] == []

    class Info2(FakeInfo):
        def learnset_size(self, sid):
            return 3 if sid == "tinymon" else 20
    assert L.lint_set("tinymon", None, "leftovers", "bold", ["protect", "toxic"], info=Info2())["errors"] == []
    assert "few_moves" in L.lint_set("charizard", None, "leftovers", "bold", ["protect", "toxic"], info=Info2())["errors"]
    print("test_few_moves_exemptions OK")


def test_repair_set():
    legal = lambda it: it != "lifeorb"      # noqa: E731  (ライフオーブが使えない規制の例)
    usage = ["flamethrower", "roost", "toxic", "willowisp", "earthquake"]
    # 性格: + を保って下げる側を変える。両方の攻撃を使うなら防御側 (ひかえめ + 物理と特殊 → おっとり)、
    # 片方しか使わないなら使わない側の攻撃 (ずぶとい + 物理だけ → わんぱく、ひかえめ + 物理だけ → いじっぱり)
    r = L.repair_set("charizard", "blaze", "lifeorb", "modest", ["flamethrower", "earthquake", "shadowball", "protect"], info=INFO, usage_moves=usage)
    assert r and r["nature"] == "mild" and r["repairs"] == ["nature_move"] and r["item"] == "lifeorb"
    r = L.repair_set("corviknight", "pressure", "leftovers", "bold", ["ironhead", "bodypress", "roost", "protect"], info=INFO)
    assert r and r["nature"] == "impish"
    r = L.repair_set("charizard", "blaze", "lifeorb", "modest", ["earthquake", "closecombat", "return", "protect"], info=INFO)
    assert r and r["nature"] == "adamant"
    assert L.nature_repair("jolly", ["flamethrower", "earthquake"], INFO.move) == "naive" and L.nature_repair("jolly", ["flamethrower"], INFO.move) == "timid"
    assert L.nature_repair("serious", ["flamethrower"], INFO.move) is None
    # 持ち物: なし → 予備 (合法なもの)。カゴのみ + ねむる無し / こだわり + 変化技 も予備に
    r = L.repair_set("charizard", "blaze", None, "timid", ["flamethrower", "shadowball", "roost", "protect"], info=INFO, legal_item=legal)
    assert r and r["item"] == "leftovers" and r["repairs"] == ["item:none"]
    r = L.repair_set("charizard", "blaze", "chestoberry", "timid", ["flamethrower", "shadowball", "roost", "protect"], info=INFO)
    assert r and r["item"] == "leftovers" and r["repairs"] == ["item:chesto_no_rest"]
    r = L.repair_set("charizard", "blaze", "choicespecs", "timid", ["flamethrower", "shadowball", "roost", "protect"], info=INFO)
    assert r and r["item"] == "leftovers" and r["repairs"] == ["item:choice_status"]
    r = L.repair_set("hawlucha", "unburden", "leftovers", "adamant", ["acrobatics", "closecombat", "swordsdance", "protect"], info=INFO)
    assert r and r["item"] == "focussash" and r["repairs"] == ["item:acrobatics"]
    # メガ石は外さない (判断 §9.6): アクロバットなら技の側を外して補充、他の持ち物の誤りは直さず止める (理由を数える)
    L.LINT_REPAIR_BLOCKED.clear()
    assert L.is_mega_stone("hawluchanite") and L.is_mega_stone("charizarditex") and not L.is_mega_stone("eviolite") and not L.is_mega_stone(None)
    r = L.repair_set("hawlucha", "unburden", "hawluchanite", "adamant", ["acrobatics", "closecombat", "swordsdance", "protect"], info=INFO,
                     usage_moves=["earthquake", "roost"])
    assert r and r["item"] == "hawluchanite" and "acrobatics" not in r["moves"] and r["moves"][3] == "earthquake"
    assert r["repairs"] == ["item:acrobatics->move", "few_moves"]
    # 技の側を外しても補充できなければ (必須技などの制約) 止めて理由を残す。石は外さない
    L.LINT_REPAIR_BLOCKED.clear()
    r = L.repair_set("hawlucha", "unburden", "hawluchanite", "adamant", ["acrobatics", "closecombat", "swordsdance", "protect"], info=INFO)
    assert r is None and L.LINT_REPAIR_BLOCKED == {"unfixed:few_moves": 1}
    assert L.repairs_snapshot()["blocked"] == {"unfixed:few_moves": 1}
    # 石 + 4 つ未満 → 補充だけ (石はそのまま)
    r = L.repair_set("charizard", "blaze", "charizarditex", "timid", ["flamethrower", "shadowball", "roost"], info=INFO, usage_moves=["protect"])
    assert r and r["item"] == "charizarditex" and r["moves"] == ["flamethrower", "shadowball", "roost", "protect"] and r["repairs"] == ["few_moves"]
    L.LINT_REPAIR_BLOCKED.clear()
    r = L.repair_set("charizard", "blaze", "charizarditex", "timid", ["protect", "roost", "toxic", "substitute"], info=INFO)
    assert r is None and L.LINT_REPAIR_BLOCKED == {}                       # 誤りが無い (警告だけ) なら None
    assert L.repairs_snapshot()["blocked"] == {}
    # 場の重複: 技を外し、使用率の技で補充。4 つ未満も補充
    r = L.repair_set("torkoal", "drought", "heatrock", "quiet", ["flamethrower", "sunnyday", "earthquake", "protect"], info=INFO, usage_moves=usage)
    assert r and "sunnyday" not in r["moves"] and len(r["moves"]) == 4 and r["repairs"] == ["field_dup", "few_moves"] and r["moves"][3] == "roost"
    r = L.repair_set("charizard", "blaze", "leftovers", "timid", ["flamethrower", "shadowball"], info=INFO, usage_moves=["roost", "toxic"])
    assert r and r["moves"] == ["flamethrower", "shadowball", "roost", "toxic"] and r["repairs"] == ["few_moves"]
    # 直せない: 補充する技が無い / 誤りが無いなら None
    assert L.repair_set("charizard", "blaze", "leftovers", "timid", ["flamethrower", "shadowball"], info=INFO) is None
    assert L.repair_set("charizard", "blaze", "leftovers", "timid", ["flamethrower", "shadowball", "roost", "protect"], info=INFO) is None
    # SetCandidate 相当の候補: source を保ち notes に lint_repair を残す
    from tools.team_build.sets import SetCandidate
    c = SetCandidate("charizard", "blaze", "chestoberry", "modest", "0/0/0/32/0/32", ["flamethrower", "earthquake", "shadowball", "protect"], "representative")
    f = L.repair_candidate(c, info=INFO)
    assert f.item == "leftovers" and f.nature == "mild" and f.source == "representative" and f.notes[-1] == "lint_repair:nature_move;item:chesto_no_rest"
    # 石を持つ型のこだわりの誤りは直さず止める (理由を数える)
    L.LINT_REPAIR_BLOCKED.clear()
    c2 = SetCandidate("charizard", "blaze", "charizarditex", "timid", "0/0/0/32/0/32", ["flamethrower", "shadowball", "protect", "roost"], "representative")
    c2.item = "charizarditex"
    assert L.repair_candidate(c2, info=INFO) is None                           # 誤り無し
    c3 = SetCandidate("torkoal", "drought", "torkoalite", "quiet", "0/0/0/32/0/32", ["flamethrower", "sunnyday", "earthquake"], "representative")
    f3 = L.repair_candidate(c3, info=INFO, usage_moves=["protect", "toxic"])
    assert f3 and f3.item == "torkoalite" and "sunnyday" not in f3.moves and len(f3.moves) == 4
    assert c.item == "chestoberry"                                                   # 元は変えない
    print("test_repair_set OK")


def test_stab_warning():
    # 自分のタイプの攻撃技が無い → 警告 (誤りではない)
    r = lint("blissey", "naturalcure", "leftovers", "bold", ["seismictoss", "toxic", "protect", "softboiled"])
    assert r["errors"] == [] and r["warnings"] == ["no_stab"]
    assert lint("blissey", "naturalcure", "leftovers", "bold", ["hypervoice", "toxic", "protect", "roost"])["warnings"] == []
    # ウェザーボール: 自分で張る天候のタイプが一致すれば一致 (特性でも技でも)。無ければノーマル
    assert lint("pelipper", "drizzle", "damprock", "modest", ["weatherball", "hypervoice", "roost", "protect"])["warnings"] == []
    assert lint("pelipper", "keeneye", "damprock", "modest", ["weatherball", "raindance", "roost", "protect"])["warnings"] == []
    assert lint("pelipper", "keeneye", "damprock", "modest", ["weatherball", "hypervoice", "roost", "protect"])["warnings"] == ["no_stab"]
    assert lint("torkoal", "drought", "heatrock", "quiet", ["weatherball", "earthquake", "protect", "toxic"])["warnings"] == []
    # -ate 特性、へんげんじざい、固定ダメージは一致に数えない
    assert lint("altaria", "pixilate", "leftovers", "modest", ["hypervoice", "roost", "protect", "toxic"])["warnings"] == []
    assert lint("altaria", "naturalcure", "leftovers", "modest", ["hypervoice", "roost", "protect", "toxic"])["warnings"] == ["no_stab"]
    assert lint("greninja", "protean", "lifeorb", "timid", ["shadowball", "hypervoice", "protect", "toxic"])["warnings"] == []
    assert lint("corviknight", "pressure", "leftovers", "impish", ["bodypress", "roost", "protect", "toxic"])["warnings"] == ["no_stab"]
    assert lint("hawlucha", "unburden", "focussash", "adamant", ["bodypress", "acrobatics", "swordsdance", "protect"])["warnings"] == []
    print("test_stab_warning OK")


def test_fill_to_four():
    learn = {"flamethrower", "fireblast", "shadowball", "protect", "toxic", "sunnyday", "roost", "substitute", "earthquake"}
    cat = lambda m: (MOVES.get(m) or {}).get("category")      # noqa: E731
    out = L.fill_to_four(["flamethrower", "shadowball"], ["fireblast", "earthquake"], ["protect", "substitute"], learn, move_of=INFO.move)
    assert out == ["flamethrower", "shadowball", "fireblast", "earthquake"]
    out = L.fill_to_four(["flamethrower", "shadowball", "roost"], [], ["protect", "substitute"], learn, move_of=INFO.move)
    assert out == ["flamethrower", "shadowball", "roost", "protect"]
    # こだわり系なら変化技は足さない → 攻撃技、それも無ければ learnset の攻撃技
    out = L.fill_to_four(["flamethrower", "shadowball", "fireblast"], [], ["protect"], learn, item="choicespecs", move_of=INFO.move)
    assert out == ["flamethrower", "shadowball", "fireblast", "earthquake"]
    # 特性で張る場と同じ場の技は足さない、除外も足さない、重複も足さない
    out = L.fill_to_four(["flamethrower", "shadowball", "roost"], [], ["sunnyday", "toxic"], learn, ability="drought", exclude={"protect"},
                         move_of=INFO.move)
    assert out == ["flamethrower", "shadowball", "roost", "toxic"]
    assert len(L.fill_to_four(["flamethrower"], [], [], {"flamethrower"}, move_of=INFO.move)) == 1       # 足せなければそのまま
    assert cat("protect") == "status"
    print("test_fill_to_four OK")


def test_rows_report_and_gate():
    rows = [{"candidate_id": "L00", "sets": [
        {"species": "charizard", "ability": "blaze", "item": "charcoal", "nature": "timid", "moves": ["flamethrower", "shadowball", "roost", "protect"], "source": "role:breaker"},
        {"species": "blissey", "ability": "naturalcure", "item": None, "nature": "bold", "moves": ["seismictoss", "toxic", "protect"], "source": "custom"},
        {"species": "torkoal", "ability": "drought", "item": "heatrock", "nature": "quiet", "moves": ["flamethrower", "sunnyday", "earthquake", "protect"], "source": "representative"}]}]
    rep = L.lint_rows(rows, info=INFO)
    assert rep["n_sets"] == 3 and rep["n_error_sets"] == 2 and rep["n_warning_sets"] == 1
    assert rep["generated"] == {"n_sets": 1, "n_error_sets": 0, "error_rate": 0.0}
    assert rep["by_code"] == {"few_moves": 1, "field_dup": 1, "item": 1, "no_stab": 1}
    assert rows[0]["lint"]["n_error_sets"] == 2 and rows[0]["lint"]["sets"]["blissey"]["errors"] == ["item", "few_moves"]
    assert rep["by_source"]["custom"]["errors"] == 1 and rep["by_source"]["role"]["n"] == 1

    class C:
        species_id, ability, item, nature, moves = "torkoal", "drought", "heatrock", "quiet", ["flamethrower", "sunnyday", "earthquake", "protect"]
    L.LINT_REJECTS.clear()
    L.LINT_REJECT_SPECIES.clear()
    assert L.gate_rejects(C(), info=INFO, source="t") is not None and L.rejects_snapshot() == {"t:field_dup": 1}
    assert L.rejects_species_top() == {"field_dup": {"torkoal": 1}}
    assert L.gate_rejects(C(), info=INFO, source="t", gate=False) is None and L.rejects_snapshot(reset=True) == {"t:field_dup": 2}
    assert L.rejects_snapshot() == {} and L.rejects_species_top() == {}
    C.item = "chestoberry"
    C.moves = ["flamethrower", "earthquake", "protect", "toxic"]
    assert L.gate_rejects(C(), info=INFO, source="t") is not None and L.rejects_species_top() == {"item:chesto_no_rest": {"torkoal": 1}}
    L.rejects_snapshot(reset=True)
    C.item = "heatrock"
    C.moves = ["flamethrower", "earthquake", "protect", "toxic"]
    assert L.gate_rejects(C(), info=INFO) is None
    print("test_rows_report_and_gate OK")


def main() -> None:
    test_nature_move_rule()
    test_item_rule()
    test_field_dup_and_few_moves()
    test_few_moves_exemptions()
    test_repair_set()
    test_stab_warning()
    test_fill_to_four()
    test_rows_report_and_gate()
    print("ALL OK")


if __name__ == "__main__":
    main()
