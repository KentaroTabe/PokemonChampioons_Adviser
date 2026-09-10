"""learnset からの型生成 (tools/team_build/gen_sets) の純粋関数テスト。

    python -m tests.test_team_build_gen_sets
"""
from __future__ import annotations

from tools.team_build import gen_sets as G

MOVES = {
    "flamethrower": {"type": "Fire", "category": "Special", "power": 90, "accuracy": 100},
    "fireblast": {"type": "Fire", "category": "Special", "power": 110, "accuracy": 85},
    "flareblitz": {"type": "Fire", "category": "Physical", "power": 120, "accuracy": 100},
    "psychic": {"type": "Psychic", "category": "Special", "power": 90, "accuracy": 100},
    "psyshock": {"type": "Psychic", "category": "Special", "power": 80, "accuracy": 100},
    "dazzlinggleam": {"type": "Fairy", "category": "Special", "power": 80, "accuracy": 100},
    "grassknot": {"type": "Grass", "category": "Special", "power": 60, "accuracy": 100},
    "shadowball": {"type": "Ghost", "category": "Special", "power": 80, "accuracy": 100},
    "hyperbeam": {"type": "Normal", "category": "Special", "power": 150, "accuracy": 90},
    "nastyplot": {"type": "Dark", "category": "Status", "power": 0, "accuracy": 0},
    "calmmind": {"type": "Psychic", "category": "Status", "power": 0, "accuracy": 0},
    "protect": {"type": "Normal", "category": "Status", "power": 0, "accuracy": 0},
    "willowisp": {"type": "Fire", "category": "Status", "power": 0, "accuracy": 85},
    "psychicterrain": {"type": "Psychic", "category": "Status", "power": 0, "accuracy": 0},
    "uturn": {"type": "Bug", "category": "Physical", "power": 70, "accuracy": 100},
}
ROLES = {"setup": ("nastyplot", "calmmind"), "protect": ("protect",), "status": ("willowisp",),
         "field": ("psychicterrain",), "pivot": ("uturn",), "priority": ("shadowsneak",), "hazard": ("stealthrock",),
         "heal": ("recover",), "screens": ("reflect",)}
DELPHOX = {"hp": 75, "atk": 69, "def": 72, "spa": 114, "spd": 100, "spe": 104}


def test_prune_moves():
    pool = G.prune_moves(set(MOVES), MOVES.get, DELPHOX, ("Fire", "Psychic"), ROLES, per_type=1, tolerance=0.85,
                         max_attacks=8)
    moves = [a.move for a in pool["attacks"]]
    # 特殊型 (特攻 114 ≫ 攻撃 69): 物理技 (フレアドライブ/とんぼがえり) は落ちる。タイプごとに 1 本、はかいこうせんは除外
    assert "flareblitz" not in moves and "uturn" not in moves and "hyperbeam" not in moves
    # 点 = 威力 × 命中 × STAB: だいもんじ 110×0.85×1.5 = 140.25 > かえんほうしゃ 90×1.0×1.5 = 135 → タイプごと 1 本なので だいもんじ だけ
    fire = [a for a in pool["attacks"] if a.type == "Fire"]
    assert len(fire) == 1 and fire[0].move == "fireblast" and abs(fire[0].score - 140.25) < 1e-9 and moves[0] == "fireblast"
    assert set(moves) >= {"psychic", "dazzlinggleam", "grassknot", "shadowball"}
    assert pool["utility"]["setup"] == ["nastyplot", "calmmind"] and "priority" not in pool["utility"]   # 覚えない役割は無い
    # 攻撃/特攻が近い種は両分類を残す (allow 2 per type: physical + special)
    both = G.prune_moves(set(MOVES), MOVES.get, {"atk": 100, "spa": 100}, ("Fire",), ROLES, per_type=1)
    bm = [a.move for a in both["attacks"]]
    assert "flareblitz" in bm and ("flamethrower" in bm or "fireblast" in bm)
    print("test_prune_moves OK")


def test_greedy_attacks_and_shares():
    table = {"flamethrower": {"t1": 0.9, "t2": 0.2, "t3": 0.3}, "psychic": {"t1": 0.3, "t2": 0.9, "t3": 0.2},
             "grassknot": {"t1": 0.1, "t2": 0.1, "t3": 0.95}, "shadowball": {"t1": 0.5, "t2": 0.5, "t3": 0.5}}
    # 1 本目は「脅威ごとの最大ダメージの和」が最大の技 (shadowball 1.5 > 1.4)、以降は増分が最大の技 (同点は表の順)
    assert G.greedy_attacks(table, 3) == ["shadowball", "grassknot", "flamethrower"]
    # 想定相手 t3 を重くすると t3 に効く grassknot が先、次は t2 を埋める psychic
    assert G.greedy_attacks(table, 2, {"t1": 1.0, "t2": 1.0, "t3": 10.0}) == ["grassknot", "psychic"]
    assert G.greedy_attacks(table, 2, preselected=["shadowball"])[0] == "shadowball"
    assert G.greedy_attacks({}, 2) == []
    assert abs(G.outsped_share(150, {"a": 100, "b": 200, "c": 149}) - 2 / 3) < 1e-9
    assert abs(G.outsped_share(150, {"a": 100, "b": 200}, {"a": 3.0, "b": 1.0}) - 0.75) < 1e-9
    # +Spe 性格で上を取れる相手が増えれば +Spe (jolly)、増えなければ +攻撃 (adamant)
    assert G.choose_nature(("jolly", "adamant"), 170, 155, {"a": 160, "b": 100}, gain_min=1.0) == "jolly"
    assert G.choose_nature(("jolly", "adamant"), 170, 155, {"a": 180, "b": 100}, gain_min=1.0) == "adamant"
    assert G.choose_nature(("modest",), 1, 0, {}) == "modest"
    print("test_greedy_attacks_and_shares OK")


def test_archetype_ability_and_assembly():
    assert G.choose_archetype(DELPHOX, fast_share=0.8, phys_pressure=1.0, spec_pressure=0.5, fast_min=0.6, wall_max=90) == "fast_special"
    assert G.choose_archetype({"atk": 130, "spa": 60}, 0.3, 1.0, 0.5, 0.6, 90) == "bulky_physical"
    assert G.choose_archetype({"atk": 60, "spa": 70}, 0.3, 0.4, 1.0, 0.6, 90) == "wall_special"
    assert G.choose_archetype({"atk": 60, "spa": 70}, 0.3, 1.2, 1.0, 0.6, 90) == "wall_physical"
    assert G.choose_ability(["innerfocus", "synchronize", "psychicsurge"], ("psychicsurge", "intimidate")) == "psychicsurge"
    assert G.choose_ability(["blaze"], ("psychicsurge",)) == "blaze" and G.choose_ability([], ("x",)) is None
    pool = G.prune_moves(set(MOVES), MOVES.get, DELPHOX, ("Fire", "Psychic"), ROLES)
    table = {"flamethrower": {"t1": 0.9, "t2": 0.2}, "psychic": {"t1": 0.3, "t2": 0.9}, "dazzlinggleam": {"t1": 0.4, "t2": 0.4},
             "grassknot": {"t1": 0.1, "t2": 0.1}, "shadowball": {"t1": 0.5, "t2": 0.5}}

    def pick(n, exclude):
        return G.greedy_attacks({m: r for m, r in table.items() if m not in exclude}, n)

    templates = ({"name": "attack3_setup", "attacks": 3, "utility": ("setup",)},
                 {"name": "attack3_priority", "attacks": 3, "utility": ("priority",)},     # 先制技を覚えない → 捨てる
                 {"name": "attack4", "attacks": 4, "utility": ()},
                 {"name": "attack3_field", "attacks": 3, "utility": ("field",)})
    sets = G.assemble_sets("delphox", pool, pick, "fast_special", "timid", "blaze", templates=templates,
                           mega_stone="delphoxite", max_sets=6)
    names = [s.notes[0] for s in sets]
    assert names[0] == "gen:attack3_setup:fast_special" and "gen:mega" in sets[1].notes and sets[1].item == "delphoxite"
    assert all("attack3_priority" not in n for n in names)
    setup_set = sets[0]
    # 貪欲: psychic (和 1.2) → flamethrower (t1 の増分 0.6) → 増分 0 の同点は表の順 (dazzlinggleam)
    assert setup_set.moves[:3] == ["psychic", "flamethrower", "dazzlinggleam"] and setup_set.moves[3] == "nastyplot"
    assert setup_set.item == "sitrusberry" and setup_set.evs == "2/0/0/32/0/32" and setup_set.source == "learnset"
    field_set = next(s for s in sets if "attack3_field" in s.notes[0])
    assert field_set.moves[3] == "psychicterrain" and field_set.item == "focussash"
    assert len(sets) <= 6 and len({tuple(sorted(s.moves)) + (s.item,) for s in sets}) == len(sets)
    # 壁型はテンプレートの順が攻撃 2 本優先
    wall = G.assemble_sets("x", pool, pick, "wall_special", "calm", None,
                           templates=({"name": "attack4", "attacks": 4, "utility": ()},
                                      {"name": "attack2_status", "attacks": 2, "utility": ("status", "protect")}))
    assert wall[0].notes[0].startswith("gen:attack2_status")
    # 使用率がある種の生成型の罰則: 代表型に無い技の使用率差の平均
    pct = {"flamethrower": 80.0, "psychic": 70.0, "nastyplot": 60.0, "dazzlinggleam": 50.0, "grassknot": 3.0}
    gap = G.generated_usage_gap(["flamethrower", "psychic", "grassknot", "shadowball"],
                                ["flamethrower", "psychic", "nastyplot", "dazzlinggleam"], pct)
    assert abs(gap - ((50 - 3) + (50 - 0)) / 2 / 100) < 1e-9
    assert G.generated_usage_gap(["flamethrower"], ["flamethrower", "psychic"], pct) == 0.0
    assert G.item_options("fast_special")[:2] == ["focussash", "lifeorb"] and "sitrusberry" in G.item_options("fast_special")
    # レギュレーションに無い持ち物は定型から落とす (全部落ちたら先頭だけ残す)
    arch, setup = G.legal_item_tables({"a": {"evs": "x", "natures": ("jolly",), "items": ("choiceband", "lifeorb")}},
                                      ("weaknesspolicy", "sitrusberry"), illegal={"choiceband", "weaknesspolicy"})
    assert arch["a"]["items"] == ("lifeorb",) and setup == ("sitrusberry",)
    arch2, _ = G.legal_item_tables({"a": {"evs": "x", "natures": ("jolly",), "items": ("choiceband",)}}, ("x",), illegal={"choiceband"})
    assert arch2["a"]["items"] == ("choiceband",)
    real = G.illegal_items()
    if real:
        assert "choiceband" in real and "assaultvest" in real and "leftovers" not in real and "focussash" not in real
    print("test_archetype_ability_and_assembly OK")


if __name__ == "__main__":
    test_prune_moves()
    test_greedy_attacks_and_shares()
    test_archetype_ability_and_assembly()
