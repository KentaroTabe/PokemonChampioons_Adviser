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
    assert L.gate_rejects(C(), info=INFO, source="t") is not None and L.rejects_snapshot() == {"t:field_dup": 1}
    assert L.gate_rejects(C(), info=INFO, source="t", gate=False) is None and L.rejects_snapshot(reset=True) == {"t:field_dup": 2}
    assert L.rejects_snapshot() == {}
    C.moves = ["flamethrower", "earthquake", "protect", "toxic"]
    assert L.gate_rejects(C(), info=INFO) is None
    print("test_rows_report_and_gate OK")


def main() -> None:
    test_nature_move_rule()
    test_item_rule()
    test_field_dup_and_few_moves()
    test_stab_warning()
    test_fill_to_four()
    test_rows_report_and_gate()
    print("ALL OK")


if __name__ == "__main__":
    main()
