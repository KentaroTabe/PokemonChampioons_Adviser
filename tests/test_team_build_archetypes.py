"""構築の軸 (tools/team_build/archetypes) の純粋関数テスト。

    python -m tests.test_team_build_archetypes

- 表の整合 (役割の参照、勝ち筋の enum、分岐の構造)
- 役割の判定 (型が満たす / 覚える / 満たさない、特殊な勝ち筋は使用率を見ない)
- 環境適合 (脅威の技・特性・素早さの重み割合から)
- core の組み立て (役割の候補の組み合わせ、特殊な勝ち筋の合計上限、適合の下限)
- 並びの役割検査 (assign_roles / lineup_ok、兼任)
- 型への反映 (技の差し込み、持ち物、特性、こだわり系の差し替え)
DB・図鑑・learnset は使わない。
"""
from __future__ import annotations

from tools.team_build import archetypes as A
from tools.team_build.concepts import WIN_CONDITIONS
from tools.team_build.sets import SetCandidate


def _caps(sid, moves=(), ability="", item="", abilities=(), types=("Normal",), spe=80, learn=(), speed=0.5, boost=1.0,
          bulk=0.4, offense=120, atk_moves=3, atk_types=2, cov=0.5, coverage=None, usage=10.0, mega=False, grounded=True,
          has_setup=False, can_setup=False):
    return A.Caps(sid, tuple(moves), ability, item, tuple(abilities) or (ability,), tuple(types), spe,
                  {m: True for m in learn}, speed_share=speed, boost_share=speed, boost_mult=boost, bulk=bulk,
                  offense=offense, attack_moves=atk_moves, attack_types=atk_types, coverage_mean=cov,
                  coverage=coverage or {"t1": cov, "t2": cov}, usage=usage, mega=mega, grounded=grounded,
                  has_setup=has_setup, can_setup=can_setup)


def test_table_consistency():
    for axis_id, axis in A.ARCHETYPES.items():
        assert axis["win_condition"] in WIN_CONDITIONS, axis_id
        assert axis["switching"] in A.SWITCHING_JA, axis_id
        assert axis["branches"], axis_id
        for bid, br in axis["branches"].items():
            assert br["label"] and br["roles"], (axis_id, bid)
            for role, n in list(br["roles"]) + list(br.get("core_roles") or []):
                assert role in A.ROLE_SPECS and n >= 1, (axis_id, bid, role)
            if br.get("win_condition"):
                assert br["win_condition"] in WIN_CONDITIONS
    assert A.ARCHETYPES["special"]["special"] and len(A.ARCHETYPES) == 11
    assert "perishsong" in A.moves_needed() and "reflect" in A.moves_needed() and "trickroom" in A.moves_needed()
    assert A.label_ja("setup_sweep", "screens_dual").startswith("積み展開 / 2 枚壁") and A.label_ja("nope") == ""
    md = A.render_list_md()
    assert "特殊な勝ち筋 (特殊)" in md and "積み展開" in md
    print("test_table_consistency OK")


def test_role_score_now_can_none():
    spec = A.ROLE_SPECS["screens_dual"]
    now = _caps("grimmsnarl", moves=("reflect", "lightscreen", "spiritbreak", "partingshot"), learn=("reflect", "lightscreen"))
    can = _caps("meganium", moves=("gigadrain", "synthesis"), learn=("reflect", "lightscreen"))
    one = _caps("rotomwash", moves=("lightscreen",), learn=("lightscreen",))          # 2 本必要
    mega = _caps("metagross", moves=("reflect", "lightscreen"), learn=("reflect", "lightscreen"), mega=True)
    s_now, s_can = A.role_score(now, spec), A.role_score(can, spec)
    assert s_now is not None and s_can is not None and s_now > s_can, (s_now, s_can)
    assert A.role_score(one, spec) is None and A.role_score(mega, spec) is None
    # 積みエース: 積み技と火力の門
    ace = A.ROLE_SPECS["setup_ace"]
    assert A.role_score(_caps("x", has_setup=True), ace) is not None
    assert A.role_score(_caps("x", can_setup=True, offense=80), ace) is None       # 火力不足
    assert A.role_score(_caps("x"), ace) is None                                   # 積めない
    # TR エース: 先手率の上限。速攻役: 先手率かタスキ/スカーフ
    assert A.role_score(_caps("slow", speed=0.1), A.ROLE_SPECS["tr_ace"]) is not None
    assert A.role_score(_caps("fast", speed=0.9), A.ROLE_SPECS["tr_ace"]) is None
    assert A.role_score(_caps("fast", speed=0.9), A.ROLE_SPECS["fast_attacker"]) is not None
    assert A.role_score(_caps("scarf", speed=0.2, item="choicescarf"), A.ROLE_SPECS["fast_attacker"]) is not None
    assert A.role_score(_caps("mid", speed=0.2), A.ROLE_SPECS["fast_attacker"]) is None
    # 特性の条件: 型の特性が違っても持てる特性なら can
    sun = A.ROLE_SPECS["sun_setter"]
    assert A.role_score(_caps("torkoal", ability="drought"), sun) > A.role_score(_caps("torkoal2", ability="whitesmoke", abilities=("whitesmoke", "drought")), sun)
    assert A.role_score(_caps("nothing"), sun) is None
    # 特殊な勝ち筋は使用率を足さない
    ps = A.ROLE_SPECS["perish_singer"]
    lo = A.role_score(_caps("a", learn=("perishsong",), usage=0.1), ps, special=True)
    hi = A.role_score(_caps("b", learn=("perishsong",), usage=40.0), ps, special=True)
    assert lo == hi and lo is not None
    assert A.role_score(_caps("b", learn=("perishsong",), usage=40.0), ps, special=False) > lo
    # all_of: みちづれ役は みちづれ + (先手率かタスキ)
    db = A.ROLE_SPECS["destiny_bond"]
    assert A.role_score(_caps("g", learn=("destinybond",), speed=0.7), db) is not None
    assert A.role_score(_caps("g", learn=("destinybond",), speed=0.2, item="focussash"), db) is not None
    assert A.role_score(_caps("g", learn=("destinybond",), speed=0.2, mega=True), db) is None   # メガ石の型はタスキを持てない
    # 上位脅威への回答: 上位 5 の重みつき被覆
    ans = A.ROLE_SPECS["answer"]
    good = _caps("z", coverage={"t1": 0.9, "t2": 0.7, "t3": 0.1})
    assert A.role_score(good, ans, top_threats=["t1", "t2"], threat_weights={"t1": 2.0, "t2": 1.0}) is not None
    assert A.role_score(good, ans, top_threats=["t3"], threat_weights={"t3": 1.0}) is None
    print("test_role_score_now_can_none OK")


def _threats():
    return {
        "garchomp": {"moves": {"earthquake", "stealthrock", "dragontail"}, "ability": "roughskin", "types": ("Dragon",), "spe": 151, "weight": 59.0, "rock_mult": 1.0},
        "salamence": {"moves": {"doubleedge", "dragondance", "roost"}, "ability": "intimidate", "types": ("Dragon", "Flying"), "spe": 120, "weight": 59.0, "rock_mult": 2.0},
        "primarina": {"moves": {"moonblast", "encore", "aquajet"}, "ability": "torrent", "types": ("Water", "Fairy"), "spe": 60, "weight": 27.0, "rock_mult": 1.0},
        "hippowdon": {"moves": {"yawn", "stealthrock", "whirlwind"}, "ability": "sandstream", "types": ("Ground",), "spe": 47, "weight": 22.0, "rock_mult": 1.0},
        "gengar": {"moves": {"shadowball", "taunt"}, "ability": "cursedbody", "types": ("Ghost", "Poison"), "spe": 130, "weight": 16.0, "rock_mult": 1.0},
    }


def test_branch_fit():
    th = _threats()
    f_setup, notes = A.branch_fit("setup_sweep", "screens_dual", th)
    counter = (59.0 + 27.0 + 22.0 + 16.0) / (59 + 59 + 27 + 22 + 16)   # ドラゴンテール / アンコール / ふきとばし / ちょうはつ
    assert abs(f_setup - round(1.0 - 0.8 * counter, 3)) < 1e-6 and notes and "%" in notes[0]
    f_tr, _ = A.branch_fit("trick_room", "tr_single", th)
    fast = (59.0 + 59.0 + 16.0) / 183.0
    taunt = 16.0 / 183.0
    prio = 27.0 / 183.0
    assert abs(f_tr - round(0.3 + 0.7 * fast - 0.5 * taunt - 0.3 * prio, 3)) < 1e-6
    f_sun, _ = A.branch_fit("weather", "sun", th)                      # カバルドンの砂が上書き
    assert abs(f_sun - round(1.0 - 0.8 * (22.0 / 183.0), 3)) < 1e-6
    f_sun2, _ = A.branch_fit("weather", "sun", th, type_mult=lambda a, ts: 2.0 if (a == "Fire" and "Grass" in ts) else 1.0)
    assert f_sun2 == f_sun                                              # くさタイプの脅威が無ければ弱点の項は 0
    f_haz, _ = A.branch_fit("hazard_stack", "rocks_phaze", th)
    assert abs(f_haz - round(0.4 + 0.6 * (59.0 / 183.0), 3)) < 1e-6
    f_perish, _ = A.branch_fit("special", "perish_trap", th)
    assert abs(f_perish - round(0.8 - 0.7 * (16.0 / 183.0 + 16.0 / 183.0), 3)) < 1e-6   # ゴースト + 挑発 (ゲンガー)
    assert A.branch_fit("anti_meta", "top_threats", th)[0] == 1.0
    assert A.branch_fit("cycle", "volt_turn", {})[0] == 0.5              # 情報なし
    for axis_id, axis in A.ARCHETYPES.items():                           # 全分岐が 0..1 を返す
        for bid in axis["branches"]:
            f, _n = A.branch_fit(axis_id, bid, th)
            assert 0.0 <= f <= 1.0, (axis_id, bid, f)
    print("test_branch_fit OK")


def _pool():
    return {
        "grimmsnarl": _caps("grimmsnarl", moves=("reflect", "lightscreen", "spiritbreak", "partingshot"), learn=("reflect", "lightscreen"),
                            offense=80, usage=1.0, cov=0.3, bulk=0.5),
        "meganium": _caps("meganium", moves=("gigadrain", "synthesis"), learn=("reflect", "lightscreen"), offense=80, cov=0.3, bulk=0.6),
        "blaziken": _caps("blaziken", moves=("swordsdance", "flareblitz", "closecombat", "thunderpunch"), has_setup=True, speed=0.6,
                          boost=1.5, cov=0.6, usage=4.0, mega=True),
        "mimikyu": _caps("mimikyu", moves=("swordsdance", "playrough", "shadowsneak", "shadowclaw"), has_setup=True, speed=0.5, cov=0.55, usage=21.0),
        "kingambit": _caps("kingambit", moves=("swordsdance", "suckerpunch", "kowtowcleave", "ironhead"), has_setup=True, speed=0.05,
                           cov=0.5, usage=10.0, bulk=0.57),
        "slowkinggalar": _caps("slowkinggalar", moves=("chillyreception", "flamethrower", "sludgebomb", "slackoff"), ability="regenerator",
                               learn=("trickroom",), speed=0.05, offense=110, cov=0.35, bulk=0.63, usage=2.0),
        "politoed": _caps("politoed", moves=("scald", "perishsong", "protect", "encore"), learn=("perishsong", "meanlook"), offense=90,
                          cov=0.2, usage=0.5, bulk=0.5),
    }


def test_build_context_and_cores():
    th = _threats()
    pool = _pool()
    threats = list(th)
    weights = {t: v["weight"] for t, v in th.items()}
    ctx = A.build_context(pool, th, threats, threat_weights=weights, coverage_fn=lambda core: 0.5, min_fit=0.0, k=2, special_max=1)
    cores = ctx["cores"]
    by_branch = {}
    for c in cores:
        by_branch.setdefault((c["archetype"], c["branch"]), []).append(c)
    # 2 枚壁: 壁役 (オーロンゲ / メガニウム) + 積みエース 2 (バシャーモ / ミミッキュ / ドドゲザン)
    dual = by_branch[("setup_sweep", "screens_dual")]
    assert dual and all(len(c["core_ids"]) == 3 for c in dual) and dual[0]["roles"]["screens_dual"] and dual[0]["mega_id"] in (None, "blaziken")
    assert dual[0]["archetype"] == "setup_sweep" and dual[0]["switching"] == "no_switch" and dual[0]["win_condition"] == "setup_sweep"
    assert dual[0]["source"] == "archetype:setup_sweep" and dual[0]["name"].startswith("arch:setup_sweep:screens_dual:")
    # TR: 使い = ガラルヤドキング (覚える)、エース = 遅い個体 (ドドゲザン / ヤドキング兼任)
    tr = by_branch.get(("trick_room", "tr_single"))
    assert tr and "slowkinggalar" in tr[0]["core_ids"] and "kingambit" in tr[0]["core_ids"]
    # 雨: 始動役がプールに無い → core 無し (見送りに記録)
    assert ("weather", "rain") not in by_branch
    assert any(s["axis"] == "weather" and s["branch"] == "rain" for s in ctx["skipped"])
    # 特殊: ほろびのうた (ニョロトノ、learnset ベース) は合計 1 まで。相方を足して 2 体以上
    sp = [c for c in cores if c["archetype"] == "special"]
    assert len(sp) == 1 and sp[0]["branch"] == "perish_trap" and "politoed" in sp[0]["core_ids"] and len(sp[0]["core_ids"]) >= 2
    # 適合の下限で分岐を見送る
    ctx2 = A.build_context(pool, th, threats, threat_weights=weights, min_fit=0.99, k=2)
    assert not [c for c in ctx2["cores"] if c["archetype"] != "anti_meta"] and any("環境適合" in s["reason"] for s in ctx2["skipped"])
    # LLM への一覧は軸ごとに 1 項目、特殊も 1 項目
    axes = A.llm_axes(ctx)
    assert len(axes) == len(A.ARCHETYPES) and any(a["special"] for a in axes)
    sw = next(a for a in axes if a["id"] == "setup_sweep")
    assert sw["switching_ja"] == "交代しない" and sw["branches"][0]["roles"][0]["candidates"]
    # JSON 往復
    back = A.context_from_json(A.context_to_json(ctx))
    assert back["fits"][("setup_sweep", "screens_dual")] == ctx["fits"][("setup_sweep", "screens_dual")]
    assert "適合" in A.render_context_md(back)
    print("test_build_context_and_cores OK")


def test_assign_roles_and_lineup_ok():
    q = {"screens_dual": {"grimmsnarl": 0.6, "meganium": 0.4}, "setup_ace": {"blaziken": 0.8, "mimikyu": 0.7, "grimmsnarl": 0.3}}
    reqs = [("screens_dual", 1), ("setup_ace", 2)]
    a = A.assign_roles(["grimmsnarl", "blaziken", "mimikyu", "x", "y", "z"], reqs, q)
    assert a == {"screens_dual": ["grimmsnarl"], "setup_ace": ["blaziken", "mimikyu"]}, a
    # 壁役が積みエースを兼ねると別個体が足りない → None。兼任可なら通る
    assert A.assign_roles(["grimmsnarl", "blaziken", "x"], reqs, q) is None
    assert A.assign_roles(["grimmsnarl", "blaziken", "x"], reqs, q, overlap_ok=True) is not None
    qb = {("setup_sweep", "screens_dual"): q}
    assert A.lineup_ok(["grimmsnarl", "blaziken", "mimikyu", "x", "y", "z"], "setup_sweep", "screens_dual", qb)
    assert not A.lineup_ok(["meganium", "blaziken", "x", "y", "z", "w"], "setup_sweep", "screens_dual", qb)
    assert A.lineup_ok(["x"], "setup_sweep", None, qb) and A.lineup_ok(["x"], "unknown", "b", qb)   # 分岐なしは制約なし
    print("test_assign_roles_and_lineup_ok OK")


def test_apply_to_team():
    learn = {"meganium": {"reflect", "lightscreen"}, "slowkinggalar": {"trickroom"}, "torkoal": set()}
    can_learn = lambda sid, m: m in learn.get(sid, set())
    cat = lambda m: "status" if m in ("reflect", "lightscreen", "synthesis", "trickroom", "leechseed", "toxic", "chillyreception") else "physical"
    team = [SetCandidate("meganium", "overgrow", "leftovers", "modest", "32/0/0/32/0/2", ["gigadrain", "earthpower", "synthesis", "leechseed"]),
            SetCandidate("blaziken", "speedboost", "blazikenite", "adamant", "2/32/0/0/0/32", ["swordsdance", "flareblitz", "closecombat", "protect"]),
            SetCandidate("mimikyu", "disguise", "lifeorb", "adamant", "2/32/0/0/0/32", ["swordsdance", "playrough", "shadowsneak", "shadowclaw"])]
    q = {"screens_dual": {"meganium": 0.4}, "setup_ace": {"blaziken": 0.8, "mimikyu": 0.7}}
    stones = frozenset({"blazikenite"})
    out, assign, notes = A.apply_to_team(team, "setup_sweep", "screens_dual", q, can_learn, cat, ("swordsdance",), stones,
                                         legal_item=lambda i: True)
    meg = out[0]
    assert assign["screens_dual"] == ["meganium"] and "reflect" in meg.moves and "lightscreen" in meg.moves and len(meg.moves) == 4
    assert "gigadrain" in meg.moves and "earthpower" in meg.moves           # 攻撃技は残し変化技 (やどりぎ/こうごうせい) を差し替え
    assert meg.item == "lightclay" and any(n.startswith("arch:screens_dual:reflect<-") for n in notes)
    assert out[1] is team[1] and out[2] is team[2]                          # エースの型は触らない (メガ石も残る)
    # 特性で満たす (天候始動): 型の特性を替え、技の枠は使わない
    tk = [SetCandidate("torkoal", "whitesmoke", "heatrock", "quiet", "32/0/0/32/2/0", ["overheat", "stealthrock", "yawn", "rapidspin"]),
          SetCandidate("victreebel", "chlorophyll", "lifeorb", "modest", "2/0/0/32/0/32", ["solarbeam", "sludgebomb", "weatherball", "sleeppowder"])]
    q2 = {"sun_setter": {"torkoal": 0.5}, "sun_abuser": {"victreebel": 0.6}}
    out2, a2, n2 = A.apply_to_team(tk, "weather", "sun", q2, can_learn, cat, (), stones, legal_item=lambda i: True,
                                   abilities_of=lambda s: {"drought", "whitesmoke"} if s == "torkoal" else set())
    assert out2[0].ability == "drought" and out2[0].moves == tk[0].moves and any("ability<-whitesmoke" in n for n in n2)
    assert a2 == {"sun_setter": ["torkoal"], "sun_abuser": ["victreebel"]}
    # こだわり系の型に変化技を差し込むときは代替の持ち物へ (無ければ注記)
    sk = [SetCandidate("slowkinggalar", "regenerator", "choicespecs", "modest", "32/0/0/32/0/2", ["flamethrower", "sludgebomb", "psychic", "chillyreception"]),
          SetCandidate("kingambit", "supremeoverlord", "blackglasses", "adamant", "32/32/0/0/0/2", ["kowtowcleave", "suckerpunch", "ironhead", "swordsdance"])]
    q3 = {"tr_setter": {"slowkinggalar": 0.5}, "tr_ace": {"kingambit": 0.6, "slowkinggalar": 0.4}}
    alt = {"slowkinggalar": [SetCandidate("slowkinggalar", "regenerator", "leftovers", "modest", "32/0/0/32/0/2", [], source="alt:item")]}
    out3, a3, n3 = A.apply_to_team(sk, "trick_room", "tr_single", q3, can_learn, cat, (), stones, legal_item=lambda i: True, alternatives=alt)
    assert "trickroom" in out3[0].moves and out3[0].item == "leftovers" and any("item<-choicespecs" in n for n in n3)
    # 役割を満たせない並びは注記だけ
    out4, a4, n4 = A.apply_to_team(sk, "setup_sweep", "screens_dual", {"screens_dual": {}, "setup_ace": {}}, can_learn, cat, (), stones)
    assert a4 is None and n4 == ["arch:unsatisfied"] and out4 == sk
    print("test_apply_to_team OK")


if __name__ == "__main__":
    test_table_consistency()
    test_role_score_now_can_none()
    test_branch_fit()
    test_build_context_and_cores()
    test_assign_roles_and_lineup_ok()
    test_apply_to_team()
