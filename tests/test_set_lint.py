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
    "swordsdance": {"type": "Normal", "category": "status", "power": 0, "self_boosts": {"atk": 2}},
    "dragondance": {"type": "Dragon", "category": "status", "power": 0, "self_boosts": {"atk": 1, "spe": 1}},
    "shellsmash": {"type": "Normal", "category": "status", "power": 0,
                   "self_boosts": {"atk": 2, "spa": 2, "spe": 2, "def": -1, "spd": -1}},
    "aquajet": {"type": "Water", "category": "physical", "power": 40, "priority": 1},
    "extremespeed": {"type": "Normal", "category": "physical", "power": 80, "priority": 2},
    "suckerpunch": {"type": "Dark", "category": "physical", "power": 70, "priority": 1},
    "vacuumwave": {"type": "Fighting", "category": "special", "power": 40, "priority": 1},
    "moonblast": {"type": "Fairy", "category": "special", "power": 95},
    "flareblitz": {"type": "Fire", "category": "physical", "power": 120},
    "partingshot": {"type": "Dark", "category": "status", "power": 0},
    "switcheroo": {"type": "Dark", "category": "status", "power": 0},
    "thunderwave": {"type": "Electric", "category": "status", "power": 0},
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
                     "condition": None, "flags": set(), "priority": 0, "self_boosts": {}}, **d)

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


def test_priority_move_by_set():
    """先制技は型によって判別する (2026-10-05 ユーザー判断): 「1 ダメージを与えられれば良い先制技」は誤りにせず、
    「火力が必要な先制技」は誤りにする。一律に除くのでも一律に誤りにするのでもない"""
    def ok(*a):
        return "nature_move" not in lint(*a)["errors"]
    # 1 ダメージでよい: 特殊で戦う型 (下げる側は攻撃) の物理の先制技。使用率の上位に実際にある型
    assert ok("primarina", "torrent", "sitrusberry", "modest", ["moonblast", "hypervoice", "aquajet", "protect"])        # アシレーヌ
    assert ok("dragonite", "multiscale", "leftovers", "modest", ["roost", "extremespeed", "flamethrower", "shadowball"])  # カイリュー
    assert ok("houndoom", "flashfire", "focussash", "timid", ["shadowball", "flamethrower", "protect", "suckerpunch"])    # ヘルガー
    # 逆向き: 物理で戦う型 (下げる側は特攻) の特殊の先制技
    assert ok("lucario", "innerfocus", "lifeorb", "adamant", ["closecombat", "earthquake", "vacuumwave", "protect"])
    # 下げる側を使わないダメージ源 (ボディプレス) で戦う型の先制技も同じ
    assert ok("corviknight", "pressure", "leftovers", "bold", ["bodypress", "extremespeed", "roost", "protect"])
    # からをやぶる は両方の攻撃を上げるので、それだけでは火力が要るとしない (どちらで戦う型かはほかの技で決まる)
    assert ok("blastoise", "torrent", "whiteherb", "modest", ["shellsmash", "aquajet", "flamethrower", "shadowball"])
    # 火力が要る (誤り): 先制技でない物理技もある (ウインディ: ずぶとい + しんそく・フレアドライブ)
    r = lint("arcanine", "intimidate", "rockyhelmet", "bold", ["willowisp", "roost", "extremespeed", "flareblitz"])
    assert r["errors"] == ["nature_move"] and r["detail"]["nature_move"]["moves"] == ["extremespeed", "flareblitz"]
    # 火力が要る (誤り): 下げる側だけを上げる技を持つ / その攻撃を上げるこだわり系を持つ / 先制技が唯一の攻撃技
    assert not ok("dragonite", "multiscale", "leftovers", "modest", ["dragondance", "extremespeed", "flamethrower", "roost"])
    assert not ok("dragonite", "multiscale", "leftovers", "modest", ["swordsdance", "extremespeed", "flamethrower", "roost"])
    assert not ok("dragonite", "multiscale", "choiceband", "modest", ["extremespeed", "flamethrower", "shadowball", "hypervoice"])
    assert not ok("dragonite", "multiscale", "leftovers", "bold", ["extremespeed", "roost", "toxic", "protect"])
    # 判別の関数 (純粋)
    mv = INFO.move
    assert L.priority_needs_power("atk", "leftovers", ["moonblast", "aquajet", "protect", "roost"], mv) is False
    assert L.priority_needs_power("atk", "leftovers", ["swordsdance", "aquajet", "moonblast", "roost"], mv) is True
    assert L.priority_needs_power("atk", "leftovers", ["shellsmash", "aquajet", "moonblast", "roost"], mv) is False
    assert L.priority_needs_power("atk", "choiceband", ["moonblast", "aquajet", "shadowball", "hypervoice"], mv) is True
    assert L.priority_needs_power("atk", "choicespecs", ["moonblast", "aquajet", "shadowball", "hypervoice"], mv) is False
    assert L.priority_needs_power("atk", None, ["aquajet", "protect", "roost", "toxic"], mv) is True
    assert L.priority_needs_power("spa", "leftovers", ["closecombat", "vacuumwave", "protect", "roost"], mv) is False
    # 直す処理: 誤りでなくなった型は直さない (ひかえめ アシレーヌは ひかえめ のまま)。誤りのままの型は従来どおり直す
    assert L.repair_set("primarina", "torrent", "sitrusberry", "modest", ["moonblast", "hypervoice", "aquajet", "protect"], info=INFO) is None
    r = L.repair_set("arcanine", "intimidate", "rockyhelmet", "bold", ["willowisp", "roost", "extremespeed", "flareblitz"], info=INFO)
    assert r and r["nature"] == "impish"
    print("test_priority_move_by_set OK")


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
    # トリック / すりかえ を持つ型は、持ち物を押し付けたあとで変化技を使うので誤りにしない。すてゼリフ は縛られても交代するので
    # 誤りにしない (2026-10-05 ユーザー判断。パンプジン: トリック + おにび、アローラペルシアン: すりかえ + すてゼリフ・でんじは、
    # イキリンコ: こだわりスカーフ + すてゼリフ)
    assert item_errs("choicescarf", ["trick", "willowisp", "flamethrower", "shadowball"]) == []
    assert item_errs("choicescarf", ["switcheroo", "partingshot", "thunderwave", "flamethrower"]) == []
    assert item_errs("choicescarf", ["partingshot", "uturn", "flamethrower", "shadowball"]) == []
    assert item_errs("choicescarf", ["willowisp", "voltswitch", "flamethrower", "shadowball"]) == ["choice_status"]   # トリックが無ければ誤りのまま
    assert item_errs("choicescarf", ["partingshot", "willowisp", "flamethrower", "shadowball"]) == ["choice_status"]  # すてゼリフ 以外の変化技は数える
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
    # 実データの形: 使用率の一覧には代表型の技 (外したアクロバットを含む) がそのまま入っている。外した技は補充で入れ直さない
    # (2026-10-05: 入れ直して unfixed:item になり、ルチャブルの基本の型が石を持たない代替に替わっていた)
    L.LINT_REPAIR_BLOCKED.clear()
    r = L.repair_set("hawlucha", "unburden", "hawluchanite", "adamant", ["swordsdance", "acrobatics", "closecombat", "protect"], info=INFO,
                     usage_moves=["swordsdance", "acrobatics", "closecombat", "protect", "earthquake", "roost"])
    assert r and r["item"] == "hawluchanite" and r["moves"] == ["swordsdance", "closecombat", "protect", "earthquake"], r
    assert r["repairs"] == ["item:acrobatics->move", "few_moves"] and L.LINT_REPAIR_BLOCKED == {}
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


def test_base_set_uses_repaired_set():
    """従来方式の基本の型 (sets.base_set): 代表型に誤りがあれば、直した型を基本の型にする (2026-10-05 ユーザー判断)。
    - メガ石 + アクロバット: 石を保持して技の側を直す。石を持たない代替には替えない (判断 §9.6)。門に使用率の技を渡していなかった
      ころは技の側の修理ができず、持ち物を替えた代替 (非メガ) が基本の型になっていた
    - 性格と技: 直した性格の型を返す (以前は直した数だけ数えて、元の誤りのある型を返していた)
    - 誤りが無い代表型はそのまま。直せない代表型は代替に替える
    使用率の表は実データと同じ形 (代表型の技が上位にそのまま入る) で作る"""
    import sqlite3
    from tools.team_build import sets as S
    from tools.team_build.sets import SetCandidate
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE meta_sets (snapshot_id, pokemon_name, ability_name, item_name, nature, evs, move1, move2, move3, move4)")
    conn.execute("CREATE TABLE move_usage (snapshot_id, pokemon_name, move_name, usage_percent)")
    conn.execute("CREATE TABLE item_usage (snapshot_id, pokemon_name, item_name, usage_percent)")
    sets = [(1, "hawlucha", "unburden", "hawluchanite", "adamant", ("swordsdance", "acrobatics", "closecombat", "protect")),
            (1, "corviknight", "pressure", "leftovers", "bold", ("ironhead", "bodypress", "roost", "protect")),
            (1, "charizard", "blaze", "leftovers", "timid", ("flamethrower", "shadowball", "roost", "protect")),
            (2, "hawlucha", "unburden", "hawluchanite", "adamant", ("swordsdance", "acrobatics", "closecombat", "protect"))]
    for snap, sid, ab, it, nat, mv in sets:
        conn.execute("INSERT INTO meta_sets VALUES (?, ?, ?, ?, ?, '0/32/0/0/2/32', ?, ?, ?, ?)", (snap, sid, ab, it, nat, *mv))
        for i, m in enumerate(mv):
            conn.execute("INSERT INTO move_usage VALUES (?, ?, ?, ?)", (snap, sid, m, 60.0 - i))
    for m, p in (("earthquake", 29.9), ("roost", 27.9)):       # snapshot 1 のルチャブルだけ、補充に使える技がある
        conn.execute("INSERT INTO move_usage VALUES (1, 'hawlucha', ?, ?)", (m, p))
    alt = SetCandidate("hawlucha", "unburden", "sitrusberry", "adamant", "0/32/0/0/2/32", ["swordsdance", "acrobatics", "closecombat", "protect"],
                       "alt:item")
    old = (L.default_info, S.enumerate_sets, S.legal_item)
    L.default_info = lambda: INFO
    S.enumerate_sets = lambda *a, **k: [alt]            # 門で代表型が落ちたときの代替 (石を持たない型)
    S.legal_item = lambda it: True
    L.rejects_snapshot(reset=True)
    try:
        base = S.base_set(conn, 1, "hawlucha")
        nat_fixed = S.base_set(conn, 1, "corviknight")
        clean = S.base_set(conn, 1, "charizard")
        repaired = L.repairs_snapshot()["repaired"]
        blocked = L.repairs_snapshot()["blocked"]
        unfixable = S.base_set(conn, 2, "hawlucha")
        blocked2 = L.repairs_snapshot()["blocked"]
    finally:
        L.default_info, S.enumerate_sets, S.legal_item = old
        L.rejects_snapshot(reset=True)
    # メガ石は残り、アクロバットは使用率の次の技に替わる
    assert base is not None and base.item == "hawluchanite", (base.item if base else None, blocked)
    assert list(base.moves) == ["swordsdance", "closecombat", "protect", "earthquake"] and base.source == "representative", base.moves
    assert base.notes[-1] == "lint_repair:item:acrobatics->move;few_moves", base.notes
    # 性格を直した型が基本の型になる (ずぶとい + 物理技だけ → わんぱく)
    assert nat_fixed.nature == "impish" and list(nat_fixed.moves) == ["ironhead", "bodypress", "roost", "protect"]
    assert nat_fixed.notes[-1] == "lint_repair:nature_move"
    # 誤りが無ければ代表型のまま
    assert clean.nature == "timid" and clean.item == "leftovers" and not [n for n in clean.notes if str(n).startswith("lint_repair")]
    assert repaired == {"legacy": 2} and blocked == {}, (repaired, blocked)
    # 直せない (補充に使える技が無い) ときだけ代替に替える
    assert unfixable is alt and blocked2 == {"unfixed:few_moves": 1}, (unfixable, blocked2)
    print("test_base_set_uses_repaired_set OK")


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
    test_priority_move_by_set()
    test_item_rule()
    test_field_dup_and_few_moves()
    test_few_moves_exemptions()
    test_repair_set()
    test_base_set_uses_repaired_set()
    test_stab_warning()
    test_fill_to_four()
    test_rows_report_and_gate()
    print("ALL OK")


if __name__ == "__main__":
    main()
