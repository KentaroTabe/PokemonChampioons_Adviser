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
    "expandingforce": {"type": "Psychic", "category": "Special", "power": 80, "accuracy": 100},
    "solarbeam": {"type": "Grass", "category": "Special", "power": 120, "accuracy": 100},
    "weatherball": {"type": "Normal", "category": "Special", "power": 50, "accuracy": 100},
    "sunnyday": {"type": "Fire", "category": "Status", "power": 0, "accuracy": 0},
}
ROLES = {"setup": ("nastyplot", "calmmind"), "protect": ("protect",), "status": ("willowisp",),
         "field": ("psychicterrain",), "pivot": ("uturn",), "priority": ("shadowsneak",), "hazard": ("stealthrock",),
         "heal": ("recover",), "screens": ("reflect",)}
DELPHOX = {"hp": 75, "atk": 69, "def": 72, "spa": 114, "spd": 100, "spe": 104}
PSY = {"terrain": "psychic", "weather": None}
SUN = {"terrain": None, "weather": "sun"}


def test_field_helpers():
    """自分で張れるフィールド/天候と、技固有の補正・タイプ変化・標準補正 (純粋)"""
    assert G.own_field("psychicsurge", []) == PSY
    assert G.own_field("drought", ["psychicterrain"]) == {"terrain": "psychic", "weather": "sun"}
    assert G.own_field(None, ["sunnyday", "raindance"]) == SUN     # 先に載っている技が優先
    assert G.own_field("blaze", ["protect"]) == G.NO_FIELD and G.field_key(PSY) == ("psychic", None)
    # 技固有の補正: 使用者/相手の接地条件、条件外は None、倍率 1.0 (ソーラービーム) は「条件下で使える」印
    assert G.field_move_boost("expandingforce", PSY) == 1.5
    assert G.field_move_boost("expandingforce", PSY, user_grounded=False) is None
    ele = {"terrain": "electric", "weather": None}
    assert G.field_move_boost("risingvoltage", ele) == 2.0 and G.field_move_boost("risingvoltage", ele, target_grounded=False) is None
    assert G.field_move_boost("terrainpulse", PSY) == 2.0 and G.field_move_boost("terrainpulse", G.NO_FIELD) is None
    assert G.field_move_boost("solarbeam", SUN) == 1.0 and G.field_move_boost("solarbeam", {"terrain": None, "weather": "rain"}) is None
    assert G.field_move_boost("psychic", PSY) is None
    # タイプ変化
    assert G.field_move_type("weatherball", "Normal", {"terrain": None, "weather": "rain"}) == "Water"
    assert G.field_move_type("weatherball", "Normal", {"terrain": "psychic", "weather": "rain"}) == "Water"
    assert G.field_move_type("terrainpulse", "Normal", PSY) == "Psychic"
    assert G.field_move_type("terrainpulse", "Normal", G.NO_FIELD) == "Normal" and G.field_move_type("psychic", "Psychic", PSY) == "Psychic"
    # 標準の補正 (フィールドは接地した使用者だけ、天候はタイプで増減)
    assert abs(G.standard_field_mult("Psychic", PSY) - 1.3) < 1e-9 and G.standard_field_mult("Psychic", PSY, grounded=False) == 1.0
    assert abs(G.standard_field_mult("Fire", SUN) - 1.5) < 1e-9 and abs(G.standard_field_mult("Water", SUN) - 0.5) < 1e-9
    assert G.standard_field_mult("Fire", PSY) == 1.0 and G.standard_field_mult("Fire", None) == 1.0
    # フィールド技の順: 自分のタイプを強化するものを先に (元の順は保つ)
    assert G.preferred_field_moves(["psychicterrain", "sunnyday", "trickroom"], ("Fire",)) == ["sunnyday", "psychicterrain", "trickroom"]
    assert G.preferred_field_moves(["psychicterrain", "sunnyday"], ("Normal",)) == ["psychicterrain", "sunnyday"]
    assert G.choose_wall_nature(("impish", "bold"), main_physical=False) == "bold"
    assert G.choose_wall_nature(("impish", "bold"), main_physical=True) == "impish" and G.choose_wall_nature(("calm",), False) == "calm"
    # 型ごとの攻撃技の分類で決め直す: 特殊技だけ → −Atk、物理技だけ → −SpA、混合 → 先頭 (最も効いた技) の分類を残す
    assert G.wall_nature_for_moves(("careful", "calm"), ["special", "special"], main_physical=True) == "calm"
    assert G.wall_nature_for_moves(("careful", "calm"), ["physical"], main_physical=False) == "careful"
    assert G.wall_nature_for_moves(("careful", "calm"), ["special", "physical"], main_physical=True) == "calm"
    assert G.wall_nature_for_moves(("careful", "calm"), ["physical", "special"], main_physical=False) == "careful"
    assert G.wall_nature_for_moves(("careful", "calm"), [], main_physical=True) == "careful"
    print("test_field_helpers OK")


def test_prune_moves_with_field():
    """自分で張れるフィールド/天候込みの採点でも上位を残す (ワイドフォース/ソーラービーム/ウェザーボール)"""
    plain = [a.move for a in G.prune_moves(set(MOVES), MOVES.get, DELPHOX, ("Fire", "Psychic"), ROLES)["attacks"]]
    # フィールド無し: ワイドフォース (120) はサイコキネシス (135) に負けて落ちる、ソーラービームは除外技。
    # ウェザーボールはノーマル特殊の唯一の技なので (弱くても) 残る
    assert "psychic" in plain and "expandingforce" not in plain and "solarbeam" not in plain and "weatherball" in plain
    # サイコフィールド: ワイドフォース 80×1.5(STAB)×1.3×1.5 = 234 > サイコキネシス 90×1.5×1.3 = 175.5 → 両方残る
    pool = G.prune_moves(set(MOVES), MOVES.get, DELPHOX, ("Fire", "Psychic"), ROLES, field=PSY)
    moves = [a.move for a in pool["attacks"]]
    assert "expandingforce" in moves and "psychic" in moves
    ef = next(a for a in pool["attacks"] if a.move == "expandingforce")
    assert abs(ef.score - 234.0) < 1e-9 and ef.type == "Psychic"
    # 接地していなければワイドフォースの補正もフィールドの補正も無い → 残らない
    air = [a.move for a in G.prune_moves(set(MOVES), MOVES.get, DELPHOX, ("Fire", "Psychic"), ROLES, field=PSY, grounded=False)["attacks"]]
    assert "expandingforce" not in air
    # 晴れ: 除外技のソーラービームが使える (くさ 1 本目)、だいもんじも残る。ウェザーボール (晴れならほのお
    # 50×1.5(STAB)×1.5×2 = 225 > だいもんじ 210.4) は既にプールにあり、実際のダメージは型ごとの表で天候込みに計算される
    sun = G.prune_moves(set(MOVES), MOVES.get, DELPHOX, ("Fire", "Psychic"), ROLES, field=SUN)
    sm = [a.move for a in sun["attacks"]]
    assert "solarbeam" in sm and "weatherball" in sm and "fireblast" in sm and "grassknot" in sm
    assert len(sun["attacks"]) <= 8
    # ウェザーボールがノーマル技として残らない (他にノーマル特殊技がある) 場合は、晴れの採点 (ほのお 225) で残る
    moves2 = dict(MOVES, hypervoice={"type": "Normal", "category": "Special", "power": 90, "accuracy": 100})
    sun2 = G.prune_moves(set(moves2), moves2.get, DELPHOX, ("Fire", "Psychic"), ROLES, field=SUN)
    wb = next(a for a in sun2["attacks"] if a.move == "weatherball")
    assert wb.type == "Fire" and abs(wb.score - 225.0) < 1e-9
    assert "weatherball" not in [a.move for a in G.prune_moves(set(moves2), moves2.get, DELPHOX, ("Fire", "Psychic"), ROLES)["attacks"]]
    print("test_prune_moves_with_field OK")


def test_assemble_field_dependent_pick():
    """テンプレートにフィールド技が入るときだけ、その条件込みの表で攻撃技を選ぶ (pick_attacks は除外 = 補助技を受け取る)"""
    pool = G.prune_moves(set(MOVES), MOVES.get, DELPHOX, ("Fire", "Psychic"), ROLES, field=PSY)
    calls = []

    def pick(n, exclude):
        field = G.own_field("blaze", exclude)
        calls.append(field["terrain"])
        best = (["expandingforce", "flamethrower", "dazzlinggleam", "shadowball"] if field["terrain"] == "psychic"
                else ["psychic", "flamethrower", "dazzlinggleam", "shadowball"])
        return [m for m in best if m not in exclude][:n]

    templates = ({"name": "attack4", "attacks": 4, "utility": ()},
                 {"name": "attack3_field", "attacks": 3, "utility": ("field",)})
    sets = G.assemble_sets("delphox", pool, pick, "fast_special", "timid", "blaze", templates=templates)
    plain = next(s for s in sets if "attack4" in s.notes[0])
    field = next(s for s in sets if "attack3_field" in s.notes[0])
    assert "psychic" in plain.moves and "expandingforce" not in plain.moves
    assert field.moves == ["expandingforce", "flamethrower", "dazzlinggleam", "psychicterrain"]
    assert calls == [None, "psychic"]
    print("test_assemble_field_dependent_pick OK")


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
    sets = G.assemble_sets("delphox", pool, pick, "fast_special", "timid", "blaze", templates=templates, max_sets=6)
    names = [s.notes[0] for s in sets]
    assert names[0] == "gen:attack3_setup:fast_special" and all("gen:mega" not in n for s in sets for n in s.notes)
    assert all("attack3_priority" not in n for n in names)
    # メガ型はフォルムごとに別に組む: 持ち物は石で固定、印を付ける (2026-09-11 までは先頭の型の複製だった)
    mega = G.assemble_sets("delphox", pool, pick, "fast_special", "timid", "blaze", templates=templates,
                           fixed_item="delphoxite", extra_notes=("gen:mega:delphoxmega",), max_sets=2)
    assert len(mega) == 2 and all(s.item == "delphoxite" and "gen:mega:delphoxmega" in s.notes for s in mega)
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


def test_ev_tuning_helpers():
    """能力ポイントの微調整 (純粋): 素早さの候補、配分の列挙、採点、選択"""
    # 素早さ候補: speed_of(p) = 100 + p (単調)、脅威 110/115/200 → 上を取る最小ポイント 11, 16 (200 は届かない) + 0 と上限
    assert G.speed_point_options(lambda p: 100 + p, {"a": 110, "b": 115, "c": 200}, cap=32) == [0, 11, 16, 32]
    cands = G.enumerate_spreads(66, 32, [0, 32], "spa", step=8)
    assert all(sum(c.values()) == 66 and max(c.values()) <= 32 and c["atk"] == 0 for c in cands)
    assert {"hp": 2, "atk": 0, "def": 0, "spa": 32, "spd": 0, "spe": 32} in cands
    assert {"hp": 32, "atk": 0, "def": 32, "spa": 0, "spd": 2, "spe": 0} in cands      # 端数はどの耐久にも置ける
    assert len(cands) == len({G.spread_string(c) for c in cands})
    assert G.parse_spread("2/0/0/32/0/32") == {"hp": 2, "atk": 0, "def": 0, "spa": 32, "spd": 0, "spe": 32}
    assert G.parse_spread("bad") == {} and G.spread_string({"hp": 2, "spa": 32, "spe": 32}) == "2/0/0/32/0/32"
    # 採点: 上を取る / 耐える (1 発・2 発・余裕) / 倒す (1 発・2 発・割合)
    params = {"outspeed": 1.0, "survive": 1.0, "survive_2hit": 0.5, "survive_margin": 0.25,
              "ko": 1.0, "ko_2hko": 0.5, "ko_margin": 0.25}
    stats = {"hp": 150, "atk": 100, "def": 100, "spa": 100, "spd": 100, "spe": 120}
    incoming = {"t": {"def": (0.6, 100, 150), "spd": (0.0, 100, 150)}}   # 基準と同じ実数値 → 被ダメ 0.6
    outgoing = {"t": [("spa", 0.7, 100)]}
    sc = G.score_spread(stats, ["t"], {"t": 2.0}, {"t": 110}, incoming, outgoing, params)
    # outspeed 1 + survive 1 + 2 発 0 + 余裕 0.25×0.4 + ko 0 + 2 発 0.5 + 割合 0.25×0.7 = 2.775 → 重み 2 倍
    assert abs(sc - 2 * 2.775) < 1e-9
    stats2 = dict(stats, **{"def": 200})                                  # 防御を倍 → 被ダメ 0.3 → 2 発耐え (+0.5)
    sc2 = G.score_spread(stats2, ["t"], {"t": 2.0}, {"t": 110}, incoming, outgoing, params)
    assert abs(sc2 - 2 * (1 + 1 + 0.5 + 0.25 * 0.7 + 0.5 + 0.25 * 0.7)) < 1e-9
    stats3 = dict(stats, **{"spa": 150})                                  # 与ダメ 1.05 → 1 発 (+1)
    sc3 = G.score_spread(stats3, ["t"], {"t": 1.0}, {"t": 110}, incoming, outgoing, params)
    assert abs(sc3 - (1 + 1 + 0.25 * 0.4 + 1 + 0.5 + 0.25)) < 1e-9
    # トリックルームは下を取る (被ダメ無しなら耐える側は満点 1 + 0.5 + 0.25)。通常なら S50 < 110 で上を取れない
    assert abs(G.score_spread(dict(stats, spe=50), ["t"], {}, {"t": 110}, {}, {}, params, trick_room=True) - 2.75) < 1e-9
    assert abs(G.score_spread(dict(stats, spe=50), ["t"], {}, {"t": 110}, {}, {}, params) - 1.75) < 1e-9
    # tune_spread: 脅威が無ければ (全て同点) 定型、あれば候補から最大 (上を取る最小の 11 ポイント + 残りは耐久へ)
    def stats_of(pts):
        return {"hp": 100 + pts["hp"], "atk": 100 + pts["atk"], "def": 100 + pts["def"], "spa": 100 + pts["spa"],
                "spd": 100 + pts["spd"], "spe": 100 + pts["spe"]}
    default = {"hp": 2, "atk": 0, "def": 0, "spa": 32, "spd": 0, "spe": 32}
    cands = G.enumerate_spreads(66, 32, [0, 11, 32], "spa")
    assert G.tune_spread(default, cands, stats_of, [], {}, {}, {}, {}, params) == default
    best = G.tune_spread(default, cands, stats_of, ["t"], {"t": 1.0}, {"t": 110}, incoming, {"t": [("spa", 0.4, 132)]}, params)
    # 与ダメ 0.4×(100+C)/132 は 2 発の線 (0.5) に届かない → 特攻より耐久 (被ダメの余裕) が有利、素早さは 11 で足りる
    assert best["spe"] == 11 and best["spa"] < 32 and sum(best.values()) == 66, best
    print("test_ev_tuning_helpers OK")


if __name__ == "__main__":
    test_prune_moves()
    test_greedy_attacks_and_shares()
    test_archetype_ability_and_assembly()
    test_field_helpers()
    test_prune_moves_with_field()
    test_assemble_field_dependent_pick()
    test_ev_tuning_helpers()
