"""並びと型の同時探索 (tools/team_build/lineup_search) と配線の純粋関数 (joint_stage) のテスト。合成の候補・行列で閉じる。

    python -m tests.test_lineup_search
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from tools.team_build import joint_stage as J
from tools.team_build import lineup_search as L
from tools.team_build.sets import SetCandidate

# ---------------------------------------------------------------- 合成の世界
# 相手: 4 系統 (A〜D)、各 2 個体。自分の種: 候補ごとに被覆のベクトルを決める (行列は表引き)
OPP = {"A": ["a1", "a2"], "B": ["b1", "b2"], "C": ["c1", "c2"], "D": ["d1", "d2"]}
OPP_IDS = [o for lst in OPP.values() for o in lst]


def _pool(weights=None) -> L.OppPool:
    teams = {f"t{f}": [L.OppSet((o,), o, SimpleNamespace(ability=None), []) for o in lst] for f, lst in OPP.items()}
    fams = [(f, (weights or {}).get(f, 1.0), [f"t{f}"]) for f in OPP]
    return L.OppPool.build(teams, fams)


def _vec(a=0.0, b=0.0, c=0.0, d=0.0) -> dict:
    return {"a1": a, "a2": a, "b1": b, "b2": b, "c1": c, "c2": c, "d1": d, "d2": d}


# 種 → 役割 → [(型の名前, 持ち物, 石, 被覆, 自分の場)]
WORLD = {
    "core1": {"breaker": [("core1_x", "lifeorb", False, _vec(a=0.9), {})]},
    "core2": {"sweeper_setup": [("core2_x", "sitrusberry", False, _vec(b=0.9), {}),
                                ("core2_stone", "core2ite", True, _vec(b=0.95, a=0.3), {})]},
    "fillC": {"breaker": [("fillC_x", "choicescarf", False, _vec(c=0.9), {}),
                          ("fillC_y", "lifeorb", False, _vec(c=0.85), {})]},       # lifeorb は core1 と重複 → 候補にならない
    "fillD": {"hazard_lead": [("fillD_x", "focussash", False, _vec(d=0.8), {})]},
    "dup":   {"breaker": [("dup_x", "lifeorb", False, _vec(a=0.9, b=0.9, c=0.9, d=0.9), {})]},   # 持ち物が重複する最強候補
    "rain":  {"rain_setter": [("rain_x", "damprock", False, _vec(d=0.5), {"weather": "rain"})]},
    "sun":   {"sun_setter": [("sun_x", "heatrock", False, _vec(c=0.5), {"weather": "sun"})]},
    "weak":  {"breaker": [("weak_x", "leftovers", False, _vec(a=0.2, b=0.2, c=0.2, d=0.2), {})]},
    "stone2": {"breaker": [("stone2_s", "stone2ite", True, _vec(c=0.7, d=0.7), {})]},
    "ban":   {"breaker": [("ban_x", "choiceband", False, _vec(a=0.9, b=0.9, c=0.9, d=0.9), {})]},
}
COV = {name: cov for roles in WORLD.values() for lst in roles.values() for name, _i, _s, cov, _f in lst}


def _entry(sid, role, name, item, stone, own_field) -> L.SetEntry:
    cand = SetCandidate(sid, "ab", item, "jolly", "2/32/0/0/0/32", ["tackle"], f"role:{role}", 0.5, [f"name:{name}"])
    fld = {"terrain": None, "weather": None}
    fld.update(own_field or {})
    return L.SetEntry((name,), sid, role, cand, SimpleNamespace(ability="ab"), ["tackle"], item, stone, fld, dict(fld))


TUNED = {"tune": ("tune_t", "expertbelt", False, _vec(c=0.9, d=0.3), {})}     # 担当 (targets) を渡したときだけ出る型
COV["tune_t"] = TUNED["tune"][3]
WORLD["tune"] = {"breaker": [("tune_x", "expertbelt", False, _vec(c=0.5), {})]}
COV["tune_x"] = _vec(c=0.5)


def _candidates_fn(sid, role, used_items, mega_allowed, team_field, speed_plan, targets=None):
    """役割 id は軸の別名 (answer / setup_ace …) でも来る → 雛形名で世界を引き、型には要求された役割 id を付ける (本実装と同じ)。
    targets (担当) を渡すと担当向けの型 (TUNED) も返す"""
    try:
        tname = L.template_of(role)[0]
    except KeyError:
        tname = role
    world = WORLD.get(sid) or {}
    out = []
    rows = list(world.get(role) or world.get(tname) or [])
    if targets and sid in TUNED:
        rows.append(TUNED[sid])
    for name, item, stone, _cov, own in rows:
        if stone and not mega_allowed:
            continue
        out.append(_entry(sid, role, name, item, stone, own))
    return out


def _row_fn(entry, opp):
    return COV[entry.key[0]].get(opp.species_id, 0.0)


def _roles_of(sid):
    return list((WORLD.get(sid) or {}).keys())


def _search(pool=None):
    return L.LineupSearch(pool or _pool(), _candidates_fn, _row_fn, prefilter=None)


# ---------------------------------------------------------------- 評価
def test_team_eval():
    pool = _pool({"A": 2.0, "B": 1.0, "C": 1.0, "D": 1.0})
    fm, w = pool.family_matrix()
    assert fm.shape == (4, 8) and abs(float(fm[0].sum()) - 1.0) < 1e-6 and list(w) == [2.0, 1.0, 1.0, 1.0]
    rows = np.array([[COV["core1_x"][o] for o in OPP_IDS], [COV["core2_x"][o] for o in OPP_IDS]], dtype=np.float32)
    info = L.team_eval(rows, fm, w, hole_threshold=0.4, hole_weight=0.5)
    # 系統 A: core1 0.9 (最良) + core2 0.0 (次善) → 0.63、B: 0.63、C/D: 0 → 穴
    assert abs(float(info["best"][0]) - 0.63) < 1e-3 and abs(float(info["best"][2])) < 1e-6
    cov = (2 * 0.63 + 0.63) / 5.0
    hole = 0.5 * (0.4 + 0.4) / 5.0
    assert abs(info["coverage"] - cov) < 1e-3 and abs(info["hole"] - hole) < 1e-3 and abs(info["value"] - (cov - hole)) < 1e-3
    assert L.hole_families(info["best"], w, 0.4) == [2, 3]
    # 1 体だけなら 0.7 × 被覆、6 体なら 3 体の部分集合 20 通りの最良
    one = L.team_eval(rows[:1], fm, w)
    assert abs(float(one["best"][0]) - 0.63) < 1e-3
    six = np.array([[COV[n][o] for o in OPP_IDS] for n in ("core1_x", "core2_x", "fillC_x", "fillD_x", "weak_x", "sun_x")],
                   dtype=np.float32)
    info6 = L.team_eval(six, fm, w)
    assert info6["plans"].shape == (4, 3) and float(info6["best"][3]) > 0.5
    assert all(len(set(p)) == 3 for p in info6["plans"])
    print("test_team_eval OK")


def test_constraints_and_roles():
    e1 = _entry("core1", "breaker", "core1_x", "lifeorb", False, {})
    e2 = _entry("dup", "breaker", "dup_x", "lifeorb", False, {})
    e3 = _entry("core2", "sweeper_setup", "core2_stone", "core2ite", True, {})
    e4 = _entry("stone2", "breaker", "stone2_s", "stone2ite", True, {})
    assert L.constraints_ok([e1, e3], None, 1) == (True, "")
    assert L.constraints_ok([e1, e2], None, 1)[1] == "持ち物の重複"
    assert L.constraints_ok([e3, e4], None, 1)[1] == "メガ石の上限"
    assert L.constraints_ok([e1, e3], "core2", 1)[0] and not L.constraints_ok([e1, e4], "core2", 1)[0]
    # Species Clause: base_of が同じ種 (フォルム違い) は 2 体入れない
    base_of = lambda s: {"core1": "num:1", "dup": "num:1"}.get(s, s)      # noqa: E731
    e2b = _entry("dup", "breaker", "dup_y", "expertbelt", False, {})
    assert L.constraints_ok([e1, e2b], None, 1, base_of)[1] == "同じ種 (Species Clause)"
    assert L.constraints_ok([e1, e2b], None, 1)[0] and L.constraints_ok([e1, e3], None, 1, base_of)[0]
    assert L.mega_allowed_for("x", None, None, False) and not L.mega_allowed_for("x", None, None, True)
    assert L.mega_allowed_for("x", None, "x", False) and not L.mega_allowed_for("y", None, "x", False)
    assert L.mega_allowed_for("ace", "ace", "x", True) and not L.mega_allowed_for("x", "ace", "x", False)
    # 役割の一致: 別名 (軸の役割 id) と雛形、場つきの役割は場まで一致
    assert L.role_matches("setup_ace", "sweeper_setup") and L.role_matches("sweeper_setup", "setup_ace")
    assert L.role_matches("sun_setter", "sun_setter") and not L.role_matches("sun_setter", "rain_setter")
    assert L.role_matches("rocks_setter", "hazard_lead") and not L.role_matches("breaker", "wall")
    ents = [_entry("core2", "setup_ace", "core2_x", None, False, {}), e1]
    assert L.unmet_roles(ents, [("sweeper_setup", 2), ("rain_setter", 1)]) == ["sweeper_setup", "rain_setter"]
    assert abs(L.role_fulfillment(ents, [("sweeper_setup", 2), ("rain_setter", 1)]) - 1 / 3) < 1e-9
    assert L.role_fulfillment(ents, []) == 1.0
    # 始動源の衝突と並びの場
    r = _entry("rain", "rain_setter", "rain_x", "damprock", False, {"weather": "rain"})
    s = _entry("sun", "sun_setter", "sun_x", "heatrock", False, {"weather": "sun"})
    assert L.field_conflicts([r, s]) == 1 and L.field_conflicts([r, e1]) == 0
    assert L.team_field_from([e1, r])["weather"] == "rain" and L.team_field_from([e1])["weather"] is None
    # 構想の核と場と要求
    concept = {"core_ids": ["core1", "core2"], "roles": {"sun_abuser": ["core1"], "setup_ace": ["core2"]},
               "complement_requirements": [{"role": "hazard_lead", "n": 1}]}
    assert L.concept_core_specs(concept, favorites=("fav",), banned={"core2"}) == [("core1", "sun_abuser"), ("fav", None)]
    assert L.concept_field(concept)["weather"] == "sun" and L.concept_field({"roles": {"sand_setter": ["x"]}})["weather"] == "sandstorm"
    assert L.concept_field({}, {"terrain": "psychic", "weather": None})["terrain"] == "psychic"
    assert L.concept_field({"plan": {"field": {"weather": "sand", "terrain": "grassy"}}}) == {"terrain": "grassy", "weather": "sandstorm"}
    assert L.concept_requirements(concept, [("tr_setter", 1)]) == [("hazard_lead", 1), ("tr_setter", 1)]
    assert L.round_robin([1, 2, 3, 4], lambda i: "a" if i % 2 else "b", 3) == [1, 2, 3]
    print("test_constraints_and_roles OK")


# ---------------------------------------------------------------- 探索
def test_search_fills_holes_and_respects_constraints():
    s = _search()
    cfg = L.SearchConfig(core_beam=3, complement_beam=2, species_k=10, banned=frozenset({"ban"}))
    pool = ["core1", "core2", "fillC", "fillD", "dup", "rain", "sun", "weak", "stone2", "ban"]
    concept = {"family_id": "C001", "core_ids": ["core1", "core2"], "mega_id": "core2"}
    res = s.search(concept, cfg, pool, _roles_of, branch_roles=[("hazard_lead", 1)])
    assert res, "並びが出る"
    best = res[0]
    assert len(best.members) == 6 and len(set(best.members)) == 6
    assert "ban" not in best.members, "除外は候補にならない"
    items = [e.item for e in best.entries if e.item]
    assert len(items) == len(set(items)), "持ち物の一意性"
    assert sum(1 for e in best.entries if e.stone) <= 1, "メガ石は 1 つ"
    assert {"fillC", "fillD"} <= set(best.members), "穴 (C / D) を埋める種が入る"
    assert best.roles["fillD"] == "hazard_lead" and best.parts["roles"] == 1.0
    assert "dup" not in best.members, "持ち物 (いのちのたま) が核と重複する候補は入らない"
    # 補完の候補からも Species Clause で同じ種を外す (fillC と同じ図鑑番号の種は入らない)
    cfg_sc = L.SearchConfig(core_beam=3, complement_beam=2, species_k=10, banned=frozenset({"ban"}),
                            base_of=lambda s: {"fillC": "num:9", "fillD": "num:9"}.get(s, s))
    res_sc = s.search(concept, cfg_sc, pool, _roles_of)
    assert res_sc and not ({"fillC", "fillD"} <= set(res_sc[0].members))
    # 核が 2 体 → 3 体目を順に選び、核の型を選び直す。石持ちの core2 (b 0.95) が選ばれるのは mega_id の種だから
    assert any(e.species_id == "core2" and e.stone for e in best.entries)
    # 選出計画: 系統ごとに並びの 3 体、担当は計画から
    assert set(best.selection_plan) == set(OPP) and all(len(v) == 3 and set(v) <= set(best.members) for v in best.selection_plan.values())
    assert "fillC" in best.selection_plan["C"] and "C" in best.assignments["fillC"]
    assert best.fills.get("fillC") == ["C"] or best.fills.get("fillD") == ["D"]
    assert best.concept == "C001" and best.score > 0.5
    # 天候の始動源が衝突する並び (rain + sun) より衝突しない並びが上
    cfg2 = L.SearchConfig(core_beam=1, complement_beam=1, species_k=10)
    concept2 = {"family_id": "C002", "core_ids": ["rain", "sun", "core1"]}
    res2 = s.search(concept2, cfg2, pool, _roles_of)
    assert res2 and res2[0].parts["field_conflicts"] == 1
    print("test_search_fills_holes_and_respects_constraints OK")


def test_search_ace_and_core_role():
    """指定エースだけが石を持つ。核の役割は構想の roles から"""
    s = _search()
    cfg = L.SearchConfig(core_beam=2, complement_beam=2, species_k=10, ace="core2", favorites=("core2",))
    pool = ["core1", "core2", "fillC", "fillD", "stone2", "weak", "rain"]
    concept = {"family_id": "C003", "core_ids": ["core1"], "mega_id": "core2", "roles": {"answer": ["core1"], "setup_ace": ["core2"]}}
    res = s.search(concept, cfg, pool, _roles_of)
    assert res
    for r in res:
        stones = [e.species_id for e in r.entries if e.stone]
        assert stones == ["core2"], f"エースだけが石を持つ: {stones}"
        assert "stone2" not in r.members or not any(e.species_id == "stone2" and e.stone for e in r.entries)
        assert r.roles["core1"] == "answer" and r.roles["core2"] == "setup_ace"      # 構想の役割 id のまま (雛形は別名で解決)
    # 候補が作れない核 → 空
    assert s.search({"family_id": "X", "core_ids": ["nosuch"]}, cfg, pool, _roles_of) == []
    print("test_search_ace_and_core_role OK")


def test_incumbent_branch():
    s = _search()
    reg = [_entry("core1", "breaker", "core1_x", "lifeorb", False, {}),
           _entry("core2", "sweeper_setup", "core2_x", "sitrusberry", False, {}),
           _entry("fillC", "breaker", "fillC_x", "choicescarf", False, {}),
           _entry("weak", "breaker", "weak_x", "leftovers", False, {}),
           _entry("sun", "sun_setter", "sun_x", "heatrock", False, {"weather": "sun"}),
           _entry("rain", "rain_setter", "rain_x", "damprock", False, {"weather": "rain"})]
    cfg = L.SearchConfig(complement_beam=2, species_k=10, favorites=("core1",), banned=frozenset({"ban"}))
    pool = ["core1", "core2", "fillC", "fillD", "weak", "sun", "rain", "stone2", "ban"]
    inc, neigh = s.incumbent(reg, cfg, pool, _roles_of, n_neighbors=3)
    assert inc is not None and inc.tag == "incumbent" and inc.concept == "INC" and len(inc.members) == 6
    assert inc.parts["field_conflicts"] == 1
    assert 1 <= len(neigh) <= 3
    for n in neigh:
        assert n.tag == "incumbent_mut" and "core1" in n.members and "ban" not in n.members
        assert len(set(n.members) ^ set(inc.members)) == 2 and n.origin["kind"] == "mutation"
    # D の穴を埋める fillD が最良の近傍に入る
    assert any("fillD" in n.members for n in neigh)
    # 登録に除外種が居れば現行は無く、近傍はその枠の入替だけ
    cfg_b = L.SearchConfig(complement_beam=2, species_k=10, banned=frozenset({"weak"}))
    inc_b, neigh_b = s.incumbent(reg, cfg_b, pool, _roles_of, n_neighbors=3)
    assert inc_b is None and neigh_b and all("weak" not in n.members for n in neigh_b)
    assert s.incumbent(reg[:5], cfg, pool, _roles_of, 2) == (None, [])
    print("test_incumbent_branch OK")


def test_refine_targets():
    """仕上げ: 担当に合わせた型が点を上げるときだけ置き換える。指定の型 (locked) と石の持ち主は変えない"""
    s = _search()
    cfg = L.SearchConfig(species_k=10)
    ents = [_entry("core1", "breaker", "core1_x", "lifeorb", False, {}),
            _entry("core2", "sweeper_setup", "core2_stone", "core2ite", True, {}),
            _entry("tune", "breaker", "tune_x", "expertbelt", False, {}),
            _entry("fillD", "hazard_lead", "fillD_x", "focussash", False, {}),
            _entry("weak", "breaker", "weak_x", "leftovers", False, {}),
            _entry("sun", "sun_setter", "sun_x", "heatrock", False, {"weather": "sun"})]
    ents[0].locked = True
    combo = [s.lib.add(e) for e in ents]
    sc, _ = s.score_of(combo, [], cfg)
    res = s._finalize(sc, combo, {}, "X", [], cfg)
    calls = []

    def targets_of(r, sid):
        calls.append(sid)
        return ["c1", "c2"] if sid == "tune" else None
    new, changed = s.refine(res, cfg, [], targets_of)
    assert changed == ["tune"] and new.score > res.score, (changed, new.score, res.score)
    assert "core1" not in calls, "指定の型は作り直さない"
    assert [e.key[0] for e in new.entries][2] == "tune_t" and new.roles == res.roles
    assert any(e.species_id == "core2" and e.stone for e in new.entries)
    # 担当が無ければ元のまま
    same, changed2 = s.refine(res, cfg, [], lambda r, sid: None)
    assert changed2 == [] and same is res
    print("test_refine_targets OK")


# ---------------------------------------------------------------- 配線の純粋関数
def test_joint_stage_pure():
    cat = {"stealthrock": "status", "trickroom": "status", "recover": "status", "swordsdance": "status", "uturn": "physical",
           "willowisp": "status", "reflect": "status", "lightscreen": "status", "suckerpunch": "physical"}
    cat_of = lambda m: cat.get(m, "physical")   # noqa: E731
    setup_of = lambda m: m == "swordsdance"     # noqa: E731
    field_of = lambda ab, mv: {"terrain": "psychic" if ab == "psychicsurge" else None, "weather": "sandstorm" if ab == "sandstream" else None}  # noqa: E731

    def c(moves, item=None, nature="jolly", ability="ab"):
        return SetCandidate("x", ability, item, nature, None, moves)
    assert J.infer_role(c(["trickroom", "a", "b", "c"]), cat_of, setup_of, field_of) == "tr_setter"
    assert J.infer_role(c(["reflect", "lightscreen", "a", "b"]), cat_of, setup_of, field_of) == "screens_dual"
    assert J.infer_role(c(["stealthrock", "a", "b", "c"]), cat_of, setup_of, field_of) == "hazard_lead"
    assert J.infer_role(c(["a", "b", "c", "d"], ability="sandstream"), cat_of, setup_of, field_of) == "sand_setter"
    assert J.infer_role(c(["a", "b", "c", "d"], ability="psychicsurge"), cat_of, setup_of, field_of) == "psychic_setter"
    assert J.infer_role(c(["swordsdance", "a", "b", "c"]), cat_of, setup_of, field_of) == "sweeper_setup"
    assert J.infer_role(c(["uturn", "a", "b", "c"]), cat_of, setup_of, field_of) == "pivot"
    assert J.infer_role(c(["recover", "willowisp", "a", "b"]), cat_of, setup_of, field_of) == "wall"
    assert J.infer_role(c(["a", "b", "c", "d"], item="choicescarf"), cat_of, setup_of, field_of) == "cleaner"
    assert J.infer_role(c(["a", "b", "c", "d"], nature="brave"), cat_of, setup_of, field_of) == "tr_ace"
    assert J.infer_role(c(["a", "b", "c", "d"]), cat_of, setup_of, field_of) == "breaker"
    # 役割を満たせるか (雛形の必須の補助枠 / 始動源 / 火力 / 速さ)
    base_fast = {"hp": 80, "atk": 120, "def": 70, "spa": 60, "spd": 70, "spe": 110}
    base_wall = {"hp": 100, "atk": 60, "def": 110, "spa": 60, "spd": 90, "spe": 40}
    assert J.role_capable("hazard_lead", {"stealthrock", "a"}, [], base_wall, field_of)
    assert not J.role_capable("hazard_lead", {"a"}, [], base_wall, field_of)
    assert J.role_capable("sand_setter", {"a"}, ["sandstream"], base_wall, field_of)
    assert J.role_capable("sand_setter", {"sandstorm"}, ["ab"], base_wall, field_of)
    assert not J.role_capable("rain_setter", {"sandstorm"}, ["sandstream"], base_wall, field_of)
    assert J.role_capable("breaker", set(), [], base_fast, field_of) and not J.role_capable("breaker", set(), [], base_wall, field_of)
    # 役割の適性 (2026-10-04): 技だけの始動役・受け・設置除去・吹き飛ばしは耐久と攻撃種族値の上限を要求する。速い攻撃種は名目だけ満たさない
    assert not J.role_capable("rain_setter", {"raindance"}, ["ab"], base_fast, field_of), "ガブリアスの雨始動は作らない"
    assert J.role_capable("rain_setter", {"raindance"}, ["ab"], base_wall, field_of)
    assert J.role_capable("sand_setter", set(), ["sandstream"], base_fast, field_of), "特性の始動役は速くても可"
    thin = {"hp": 78, "atk": 84, "def": 78, "spa": 109, "spd": 85, "spe": 100}          # リザードン相当
    assert not J.role_capable("phazer", {"roar", "recover"}, [], thin, field_of) and not J.role_capable("hazard_removal", {"defog"}, [], thin, field_of)
    strong = {"hp": 110, "atk": 123, "def": 65, "spa": 100, "spd": 65, "spe": 65}        # エンブオー相当
    assert not J.role_capable("wall", {"recover"}, [], strong, field_of), "攻撃種族値が高い種は受けにしない"
    assert J.role_capable("wall", {"recover"}, [], base_wall, field_of)
    gliscor = {"hp": 75, "atk": 95, "def": 125, "spa": 45, "spd": 75, "spe": 95}
    assert J.role_capable("wall", {"roost"}, [], gliscor, field_of), "素早さ 95 ちょうどは壁の候補に入る"
    persian = {"hp": 65, "atk": 60, "def": 60, "spa": 75, "spd": 65, "spe": 115}
    assert not J.role_capable("wall", {"roost"}, [], persian, field_of)
    assert J.role_aptitude("sand_setter", set(), ["sandstream"], base_wall, field_of) > J.role_aptitude("sand_setter", {"sandstorm"}, ["ab"], base_wall, field_of)
    assert J.role_capable("hazard_lead", {"stealthrock"}, [], thin, field_of), "先発の設置役は耐久を要求しない"
    # 役割の逆引き: −Spe の性格だけでは tr_ace にしない (攻撃技 3 本以上で素早さ 0 のときだけ)。回復・状態技があれば wall
    assert J.infer_role(c(["recover", "a", "b", "c"], nature="relaxed"), cat_of, setup_of, field_of) == "wall"
    cr = SetCandidate("x", "ab", None, "relaxed", "32/0/32/0/2/0", ["a", "b", "c", "d"])
    assert J.infer_role(cr, cat_of, setup_of, field_of) == "tr_ace"
    cr2 = SetCandidate("x", "ab", None, "relaxed", "32/0/32/0/0/2", ["a", "b", "c", "d"])
    assert J.infer_role(cr2, cat_of, setup_of, field_of) == "breaker"
    # 並びの文脈: tr_setter が居なければ tr_ace は breaker
    ents_tr = [_entry("x", "tr_ace", "x1", None, False, {}), _entry("y", "breaker", "y1", "lifeorb", False, {})]
    assert [e.role for e in J.demote_tr_ace(ents_tr)] == ["breaker", "breaker"]
    ents_tr2 = ents_tr + [_entry("z", "tr_setter", "z1", "mentalherb", False, {})]
    assert [e.role for e in J.demote_tr_ace(ents_tr2)] == ["tr_ace", "breaker", "tr_setter"]
    # こだわり + 固定技の行: 無効にする相手が居る系統の相手だけ割り引く
    pool = _pool()
    vec = np.ones(len(pool.sets), dtype=np.float32)
    adj = J.lock_immune_adjust(vec, pool, {OPP_IDS.index("b1")}, discount=0.5)
    assert float(adj[OPP_IDS.index("b1")]) == 0.5 and float(adj[OPP_IDS.index("b2")]) == 0.5 and float(adj[OPP_IDS.index("a1")]) == 1.0
    assert np.allclose(J.lock_immune_adjust(vec, pool, set(), 0.5), vec)
    # 種の集中の上限
    from tools.team_build import candidates as C
    mk = lambda *m: C.Lineup(tuple(sorted(m)), "X", 1.0, {})      # noqa: E731
    chosen = [mk("k", "a", "b", "c", "d", "e"), mk("k", "a", "b", "c", "d", "f"), mk("k", "g", "h", "i", "j", "l"),
              mk("k", "m", "n", "o", "p", "q")]
    rest = [mk("r", "s", "t", "u", "v", "w"), mk("k", "x", "y", "z", "aa", "bb")]
    out = J.cap_species_share(chosen, rest, max_share=0.5, protected=())
    # 上限 2 (= 4 × 0.5): k 入りは 2 つまで、代わりに rest の r 入りが入る。足りない分は外した並びを戻す (目標数は守る) ので k は 3
    assert len(out) == 4 and any("r" in l.members for l in out)
    assert [l.members for l in out[:3]] == [chosen[0].members, chosen[1].members, rest[0].members]
    assert sum(1 for l in out if "k" in l.members) == 3
    rest2 = rest + [mk("r2", "s2", "t2", "u2", "v2", "w2")]
    out2 = J.cap_species_share(chosen, rest2, max_share=0.5, protected=())
    assert sum(1 for l in out2 if "k" in l.members) == 2 and len(out2) == 4
    out_p = J.cap_species_share(chosen, rest, max_share=0.5, protected={"k"})
    assert [l.members for l in out_p] == [l.members for l in chosen]
    assert J.role_capable("wall", {"recover"}, [], base_wall, field_of) and not J.role_capable("wall", {"recover"}, [], base_fast, field_of)
    assert J.role_capable("pivot", {"uturn"}, [], base_wall, field_of) and not J.role_capable("nosuch", set(), [], base_wall, field_of)
    # 役割の指定が無い種に試す役割
    assert J.default_roles(base_fast, {"swordsdance", "uturn"}) == ["sweeper_setup", "breaker", "pivot"]
    assert J.default_roles(base_wall, {"recover", "stealthrock"}) == ["wall", "hazard_lead"]
    assert J.default_roles({"atk": 50, "spa": 50, "spe": 50}, set()) == ["breaker"]
    # 規則の場 → 要求、軸の分岐 → 要求
    assert J.rule_roles({"terrain": "psychic", "weather": None}) == [("psychic_setter", 1), ("psychic_abuser", 1)]
    assert J.rule_roles({"terrain": None, "weather": "sandstorm"}) == [("sand_setter", 1), ("sand_abuser", 1)]
    assert J.rule_roles({}) == []
    arch = {"setup_sweep": {"branches": {"screens_dual": {"roles": [("screens_dual", 1), ("setup_ace", 2)]}}},
            "special": {"branches": {"ohko": {"roles": [("ohko_user", 1)]}}}}
    assert J.branch_roles_of({"archetype": "setup_sweep", "branch": "screens_dual", "special_branch": "ohko"}, arch) == \
        [("screens_dual", 1), ("setup_ace", 2), ("ohko_user", 1)]
    assert J.branch_roles_of({"name": "llm"}, arch) == []
    assert J.family_weight(2, {"a", "b"}, {"b": 1.0}, boost=2.0) == 6.0 and J.family_weight(1, {"a"}, None) == 1.0
    # 技の指定に積み技がある種は積み役として作る: 補助・受けの役割を求められても積みエースの雛形に (攻撃役の役割はそのまま)
    is_setup = lambda m: m == "swordsdance"     # noqa: E731
    assert J.role_for_required("pivot", ["swordsdance", "batonpass"], is_setup) == "sweeper_setup"
    assert J.role_for_required("wall", ["swordsdance"], is_setup) == "sweeper_setup"
    assert J.role_for_required("breaker", ["swordsdance", "batonpass"], is_setup) == "breaker"
    assert J.role_for_required("setup_ace", ["swordsdance"], is_setup) == "setup_ace"
    assert J.role_for_required("pivot", ["batonpass"], is_setup) == "pivot" and J.role_for_required("pivot", [], is_setup) == "pivot"
    assert J.role_for_required("nosuchrole", ["swordsdance"], is_setup) == "nosuchrole"
    # 自爆技の 1 回だけの費用 (§12): 利得が最大の相手 1 体にだけ足し、個体の他の相手への被覆の平均 × cost で割り引く
    adj = J.selfko_adjust([0.9, 0.8, 0.5], [0.2, 0.6, 0.5], cost=1.0)
    close = lambda a, b: abs(float(a) - float(b)) < 1e-5     # noqa: E731 (float32)
    assert close(adj[0], 0.2 + 0.7 * (1.0 - (0.2 + 0.6 + 0.5) / 3)) and close(adj[1], 0.6) and close(adj[2], 0.5)
    adj0 = J.selfko_adjust([0.9, 0.8], [0.2, 0.6], cost=0.0)
    assert close(adj0[0], 0.9) and close(adj0[1], 0.6)
    assert all(close(v, 0.3) for v in J.selfko_adjust([0.1, 0.1], [0.3, 0.3]))
    assert J.selfko_moves(["explosion", "tackle", "memento", "destinybond"]) == ["explosion", "memento"]     # みちづれは自爆ではない
    # 探索器の行の後処理: 自爆技を持つ型の行は技なしの行 + 1 体分の利得
    s = _search()
    s.row_post = lambda e, vec, pool: vec * 0.5
    i = s.lib.add(_entry("core1", "breaker", "core1_x", "lifeorb", False, {}))
    assert abs(float(s.rows_for([i])[0][0]) - 0.45) < 1e-6
    print("test_joint_stage_pure OK")


def test_composition_terms():
    """補助の価値・攻撃役の過多・同じ仕事の重複 (設計文書 §5.3)、3 体の役割構成の加点"""
    def ent(sid, role, moves, evs="2/32/0/0/0/32", nature="jolly", item=None):
        cand = SetCandidate(sid, "ab", item, nature, evs, list(moves), f"role:{role}", 0.5, [])
        return L.SetEntry((sid,), sid, role, cand, SimpleNamespace(ability="ab"), list(moves), item, False,
                          {"terrain": None, "weather": None}, {"terrain": None, "weather": None})
    atk = [ent(f"a{i}", "breaker", ["tackle"]) for i in range(6)]
    comp = L.composition_terms(atk, utility_bonus={"hazard": 0.03, "removal": 0.02, "priority": 0.02, "speed_control": 0.02},
                               max_attackers=4, attacker_penalty=0.03, dup_penalty=0.02)
    assert comp["attackers"] == 6 and abs(comp["excess"] - 0.06) < 1e-9 and comp["utility"] == 0.0 and comp["kinds"] == []
    assert abs(comp["dup"] - 0.02 * 5) < 1e-9 and abs(comp["value"] - (-0.06 - 0.10)) < 1e-9
    mixed = [ent("a", "breaker", ["tackle", "suckerpunch"]), ent("b", "sweeper_setup", ["swordsdance"], evs="2/32/0/0/0/32"),
             ent("c", "hazard_lead", ["stealthrock"], evs="32/0/32/0/2/0", nature="impish"),
             ent("d", "hazard_removal", ["defog"], evs="32/0/32/0/2/0", nature="impish"),
             ent("e", "speed_control", ["trickroom"], evs="32/0/32/0/2/0", nature="relaxed"),
             ent("f", "wall", ["recover"], evs="32/0/0/0/32/2", nature="calm")]
    comp2 = L.composition_terms(mixed, utility_bonus={"hazard": 0.03, "removal": 0.02, "priority": 0.02, "speed_control": 0.02},
                                max_attackers=4, attacker_penalty=0.03, dup_penalty=0.02)
    assert comp2["kinds"] == ["hazard", "priority", "removal", "speed_control"] and abs(comp2["utility"] - 0.09) < 1e-9
    assert comp2["attackers"] == 2 and comp2["excess"] == 0.0 and comp2["dup"] == 0.0 and abs(comp2["value"] - 0.09) < 1e-9
    assert L.role_class("breaker") == "offense" and L.role_class("setup_ace") == "offense" and L.role_class("wall") == "support"
    assert L.role_class("sun_setter") == "support" and L.role_class("sun_abuser") == "offense"
    assert L.speed_tier(mixed[0].cand) == "fast" and L.speed_tier(mixed[4].cand) == "tr" and L.speed_tier(mixed[2].cand) == "bulky"
    # 3 体の役割構成: 攻撃役と補助・受け役の両方が入る 3 体に加点 (系統の値へ)
    pool = _pool()
    fm, w = pool.family_matrix()
    rows = np.array([[0.5] * 8, [0.5] * 8, [0.5] * 8], dtype=np.float32)
    base = L.team_eval(rows, fm, w)
    same = L.team_eval(rows, fm, w, classes=["offense"] * 3, trio_bonus=0.03)
    mix = L.team_eval(rows, fm, w, classes=["offense", "offense", "support"], trio_bonus=0.03)
    assert abs(base["coverage"] - same["coverage"]) < 1e-6 and abs(mix["coverage"] - base["coverage"] - 0.03) < 1e-6
    # 探索の結果に構成の項と系統ごとの予測値が載る
    s = _search()
    cfg = L.SearchConfig(core_beam=2, complement_beam=2, species_k=10)
    res = s.search({"family_id": "C009", "core_ids": ["core1", "core2"], "mega_id": "core2"}, cfg, ["core1", "core2", "fillC", "fillD", "weak", "sun"], _roles_of)
    assert res and set(res[0].family_values) == set(OPP) and "utility" in res[0].parts and "attackers" in res[0].parts
    cfg_off = L.SearchConfig(core_beam=2, complement_beam=2, species_k=10, composition=False, trio_bonus=0.0)
    res_off = s.search({"family_id": "C009", "core_ids": ["core1", "core2"], "mega_id": "core2"}, cfg_off, ["core1", "core2", "fillC", "fillD", "weak", "sun"], _roles_of)
    assert res_off and res_off[0].parts["utility"] == 0.0
    print("test_composition_terms OK")


def main() -> None:
    test_team_eval()
    test_constraints_and_roles()
    test_search_fills_holes_and_respects_constraints()
    test_search_ace_and_core_role()
    test_incumbent_branch()
    test_refine_targets()
    test_joint_stage_pure()
    test_composition_terms()
    print("ALL OK")


if __name__ == "__main__":
    main()
