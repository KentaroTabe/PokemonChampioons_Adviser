"""型ライブラリ (sets) の純粋部分のテスト。DB を使う列挙は scratchpad の煙試験で確認する。

    python -m tests.test_team_build_sets
"""
from __future__ import annotations

from tools.team_build.sets import SetCandidate, resolve_item_clause, to_showdown_text


def test_item_clause_and_text():
    team = [SetCandidate("metagross", "clearbody", "metagrossite", "adamant", "2/32/0/0/0/32",
                         ["bulletpunch", "psychicfangs", "earthquake", "icepunch"]),
            SetCandidate("kingambit", "supremeoverlord", "blackglasses", "adamant", "32/32/0/0/0/2",
                         ["suckerpunch", "kowtowcleave", "ironhead", "swordsdance"]),
            SetCandidate("garchomp", "roughskin", "blackglasses", "jolly", "2/32/0/0/0/32",
                         ["earthquake", "stealthrock", "scaleshot", "swordsdance"])]
    fixed = resolve_item_clause(team, {"garchomp": ["blackglasses", "focussash", "lifeorb"]})
    assert [c.item for c in fixed] == ["metagrossite", "blackglasses", "focussash"], [c.item for c in fixed]
    assert fixed[2].notes == ["clause:blackglasses->focussash"]
    text = to_showdown_text(fixed)
    assert "garchomp @ focussash" in text and "EVs: 2 HP / 32 Atk / 32 Spe" in text
    assert "Jolly Nature" in text and "- stealthrock" in text and "Level: 50" in text
    assert text.count("\n\n") == 2
    print("test_item_clause_and_text OK")


def test_order_candidates_usage_prior():
    """使用率の事前分布: 珍しい持ち物は被覆の差だけでは代表型に勝てない (ドドゲザン いのちのたま 8.7% vs くろいメガネ 45.2%)"""
    from tools.team_build.sets import order_candidates
    rep = SetCandidate("kingambit", "supremeoverlord", "blackglasses", "adamant", "32/32/0/0/0/2", ["suckerpunch"],
                       "representative", 0.400)
    orb = SetCandidate("kingambit", "supremeoverlord", "lifeorb", "adamant", "32/32/0/0/0/2", ["suckerpunch"],
                       "alt:item", 0.461, usage_gap=0.365)
    sash = SetCandidate("kingambit", "supremeoverlord", "focussash", "adamant", "32/32/0/0/0/2", ["suckerpunch"],
                        "alt:item", 0.400, usage_gap=0.198)
    ordered = order_candidates([rep, orb, sash], usage_weight=0.3, rep_margin=0.05)
    assert ordered[0] is rep and abs(orb.adj - (0.461 - 0.3 * 0.365)) < 1e-9 and rep.adj == 0.400
    assert [c.item for c in ordered] == ["blackglasses", "lifeorb", "focussash"]
    # 重み 0 なら被覆だけ: 差 0.061 ≥ margin 0.05 で代替が先頭。margin を超えなければ代表型
    assert order_candidates([rep, orb, sash], usage_weight=0.0, rep_margin=0.05)[0] is orb
    assert order_candidates([rep, orb, sash], usage_weight=0.0, rep_margin=0.1)[0] is rep
    print("test_order_candidates_usage_prior OK")


def test_item_clause_by_usage_and_prefer():
    """クローズの解決: prefer (規則のエース) > 代表型 > その種での使用率 > 並び順。usage_pct 無しは従来の並び順"""
    king = SetCandidate("kingambit", "supremeoverlord", "lifeorb", "adamant", "32/32/0/0/0/2", ["suckerpunch"], "alt:item")
    mimi = SetCandidate("mimikyu", "disguise", "lifeorb", "adamant", "1/32/1/0/0/32", ["playrough"], "representative")
    garc = SetCandidate("garchomp", "roughskin", "focussash", "jolly", "2/32/0/0/0/32", ["earthquake"], "representative")
    snea = SetCandidate("sneasler", "unburden", "focussash", "adamant", "2/32/0/0/0/32", ["closecombat"], "alt:item+rule")
    usage = {"kingambit": {"blackglasses": 45.2, "focussash": 25.4, "lifeorb": 8.7},
             "mimikyu": {"lifeorb": 81.0, "scopelens": 4.0},
             "garchomp": {"focussash": 41.4, "choicescarf": 19.4},
             "sneasler": {"whiteherb": 37.9, "focussash": 26.7}}
    item_usage = {"kingambit": ["blackglasses", "focussash", "lifeorb"], "mimikyu": ["lifeorb", "scopelens"],
                  "garchomp": ["focussash", "choicescarf", "sitrusberry"], "sneasler": ["whiteherb", "focussash"]}
    fixed = resolve_item_clause([king, mimi, garc, snea], item_usage, usage, prefer={"sneasler"})
    assert [c.item for c in fixed] == ["blackglasses", "lifeorb", "choicescarf", "focussash"], [c.item for c in fixed]
    assert fixed[0].notes == ["clause:lifeorb->blackglasses"] and fixed[2].notes == ["clause:focussash->choicescarf"]
    assert fixed[1] is mimi and fixed[3] is snea and king.item == "lifeorb"        # 残す側と元の型は不変
    # prefer の優先度: エース (2) は設置役 (1) の使用率 61.9% のタスキより優先して残す。設置役は次点の持ち物へ
    esp = SetCandidate("espathra", "speedboost", "focussash", "modest", "1/0/1/32/0/32", ["luminacrash"], "representative+rule")
    usage["espathra"] = {"focussash": 61.9, "sitrusberry": 19.0}
    item_usage["espathra"] = ["focussash", "sitrusberry", "leftovers"]
    fixed = resolve_item_clause([esp, snea], item_usage, usage, prefer={"espathra": 1, "sneasler": 2})
    assert [c.item for c in fixed] == ["sitrusberry", "focussash"] and fixed[0].notes == ["clause:focussash->sitrusberry"]
    # 使用率で決める: 代表型どうしなら使用率の高い方が残す
    a = SetCandidate("a", "x", "leftovers", "bold", "32/0/32/0/2/0", ["m"], "representative")
    b = SetCandidate("b", "x", "leftovers", "bold", "32/0/32/0/2/0", ["m"], "representative")
    fixed = resolve_item_clause([a, b], {"a": ["leftovers", "sitrusberry"], "b": ["leftovers", "rockyhelmet"]},
                                {"a": {"leftovers": 20.0}, "b": {"leftovers": 60.0}})
    assert [c.item for c in fixed] == ["sitrusberry", "leftovers"]
    # usage_pct 無し = 従来 (並び順で先が残す)
    old = resolve_item_clause([king, mimi], item_usage)
    assert [c.item for c in old] == ["lifeorb", "scopelens"]
    # 持ち物なし (メガ枠の解決で代替が無かったマフォクシー) には使用率順の未使用品を付ける (両方の経路)
    dlp = SetCandidate("delphox", "blaze", None, "timid", "2/0/0/32/0/32", ["flamethrower"], "representative",
                       notes=["single_mega:delphoxite->none"])
    usage["delphox"] = {"delphoxite": 99.0, "focussash": 0.2, "choicescarf": 0.1}
    item_usage["delphox"] = ["delphoxite", "focussash", "choicescarf", "sitrusberry"]
    for fixed in (resolve_item_clause([garc, dlp], item_usage, usage), resolve_item_clause([garc, dlp], item_usage)):
        assert [c.item for c in fixed] == ["focussash", "choicescarf"], [c.item for c in fixed]   # タスキは使用済み
        assert fixed[1].notes[-1] == "clause:None->choicescarf"
    print("test_item_clause_by_usage_and_prefer OK")


def test_enforce_max_megas():
    """メガ石は max_n 体まで (既定 config)。超えた分は keep → 被覆順に残し、他は石以外の代替へ。互換の enforce_single_mega は 1 体"""
    from tools.team_build.sets import enforce_max_megas, enforce_single_mega
    stones = ["metagrossite", "swampertite", "gengarite"]
    a = SetCandidate("metagross", "clearbody", "metagrossite", "adamant", "2/32/0/0/0/32", ["bulletpunch"], "representative", 0.6)
    b = SetCandidate("swampert", "damp", "swampertite", "adamant", "8/32/0/0/0/26", ["wavecrash"], "representative", 0.5)
    c = SetCandidate("gengar", "cursedbody", "gengarite", "timid", "2/0/0/32/0/32", ["shadowball"], "representative", 0.7)
    alt_b = SetCandidate("swampert", "torrent", "leftovers", "adamant", "32/32/0/0/0/2", ["wavecrash"], "alt:item", 0.4)
    alts = {"swampert": [b, alt_b], "metagross": [a], "gengar": [c]}
    assert enforce_max_megas([a, b, c], alts, max_n=3) == [a, b, c]
    team = enforce_max_megas([a, b, c], alts, max_n=2)
    assert [t.item for t in team] == ["metagrossite", "leftovers", "gengarite"]      # 被覆 0.5 の swampert が石を外す
    assert team[1].notes == ["mega_cap:swampertite->leftovers"] and b.item == "swampertite"
    team = enforce_max_megas([a, b, c], alts, keep="swampert", max_n=2)
    assert [t.item for t in team] == [None, "swampertite", "gengarite"]     # keep の swampert + 被覆 0.7 の gengar が残る
    assert team[0].notes == ["mega_cap:metagrossite->none"]                  # 石以外の代替が無ければ持ち物なし
    single = enforce_single_mega([a, b, c], alts)
    assert [t.item for t in single] == [None, "leftovers", "gengarite"]      # 互換: 最高被覆の gengar だけ
    assert sum(1 for t in single if t.item in stones) == 1
    print("test_enforce_max_megas OK")


def test_inject_and_required_moves():
    """技 + ポケモンの指定: 差し込み枠は 積み技でない変化技の末尾 → 変化技の末尾 → 末尾。4 本未満なら足す"""
    from tools.team_build.sets import apply_required_moves, finalize_candidates, inject_move
    cat = {"flamethrower": "special", "psychic": "special", "nastyplot": "status", "dazzlinggleam": "special",
           "protect": "status", "calmmind": "status", "luminacrash": "special", "psychicterrain": "status"}.get
    setup = {"nastyplot", "calmmind"}
    assert inject_move(["flamethrower", "psychic", "nastyplot", "dazzlinggleam"], "psychicterrain", cat, setup) == \
        (["flamethrower", "psychic", "psychicterrain", "dazzlinggleam"], "nastyplot")          # 変化技は積み技だけ → それ
    assert inject_move(["luminacrash", "protect", "calmmind", "dazzlinggleam"], "psychicterrain", cat, setup) == \
        (["luminacrash", "psychicterrain", "calmmind", "dazzlinggleam"], "protect")            # 積み技でない変化技を優先
    assert inject_move(["flamethrower", "psychic"], "psychicterrain", cat, setup) == (["flamethrower", "psychic", "psychicterrain"], None)
    assert inject_move(["flamethrower", "psychicterrain", "psychic", "dazzlinggleam"], "psychicterrain", cat, setup)[1] is None
    rep = SetCandidate("delphox", "blaze", "delphoxite", "timid", "2/0/0/32/0/32",
                       ["flamethrower", "psychic", "nastyplot", "dazzlinggleam"], "representative", 0.5)
    c = apply_required_moves(rep, ["psychicterrain"], cat, setup)
    assert c.moves == ["flamethrower", "psychic", "psychicterrain", "dazzlinggleam"] and c.source == "representative+req"
    assert c.notes == ["req:psychicterrain<-nastyplot"] and rep.moves[2] == "nastyplot" and c.score == 0.5
    assert apply_required_moves(rep, ["psychic"], cat, setup) is rep                          # 既にあれば何もしない
    # 全候補に差し込み、同じ型になったものはまとめる
    alt = SetCandidate("delphox", "blaze", "delphoxite", "timid", "2/0/0/32/0/32",
                       ["flamethrower", "psychic", "psychicterrain", "dazzlinggleam"], "alt:move", 0.4, usage_gap=0.3)
    out = finalize_candidates([rep, alt], ["psychicterrain"], cat, setup)
    assert [x.moves for x in out] == [c.moves] and out[0].source == "representative+req"
    assert finalize_candidates([rep, alt], None) == [rep, alt]
    print("test_inject_and_required_moves OK")


def test_parse_set_text_and_candidate_row():
    """指定の型 (Showdown 本文、EVs は能力ポイント) → SetCandidate (source custom)"""
    from tools.team_build.sets import candidate_from_row, parse_set_text
    text = ("Indeedee @ Psychic Seed\nLevel: 50\nAbility: Psychic Surge\nEVs: 32 HP / 32 SpA / 2 Spe\nModest Nature\n"
            "- Expanding Force\n- Dazzling Gleam\n- Protect\n- Healing Wish\n\n"
            "rillaboom @ choiceband\nAbility: grassysurge\nAdamant Nature\n- grassyglide\n- woodhammer\n- uturn\n- knockoff\n")
    sets = parse_set_text(text)
    ind = sets["indeedee"]
    assert ind.item == "psychicseed" and ind.ability == "psychicsurge" and ind.nature == "modest"
    assert ind.evs == "32/0/0/32/0/2" and ind.moves == ["expandingforce", "dazzlinggleam", "protect", "healingwish"]
    assert ind.source == "custom" and sets["rillaboom"].evs is None and sets["rillaboom"].item == "choiceband"
    row = {"item": ind.item, "ability": ind.ability, "nature": ind.nature, "evs": ind.evs, "moves": ind.moves}
    back = candidate_from_row("indeedee", row)
    assert back.key() == ind.key() and back.source == "custom"
    assert parse_set_text("") == {}
    print("test_parse_set_text_and_candidate_row OK")


def test_splice_registered_sets():
    from tools.team_build.sets import splice_registered_sets, team_blocks
    cand = ("metagross @ lifeorb\nLevel: 50\n- bulletpunch\n\nkingambit @ blackglasses\nLevel: 50\n- suckerpunch\n\n"
            "garchomp @ focussash\nLevel: 50\n- earthquake\n")
    reg = ("Metagross @ Metagrossite\nLevel: 50\nAbility: Clear Body\n- Bullet Punch\n\n"
           "Rotom-Wash @ Choice Scarf\nLevel: 50\n- Hydro Pump\n\nKingambit @ Black Glasses\nLevel: 50\n- Kowtow Cleave\n")
    assert [sid for sid, _ in team_blocks(reg)] == ["metagross", "rotomwash", "kingambit"]
    text, replaced = splice_registered_sets(cand, reg)
    assert replaced == ["metagross", "kingambit"], replaced
    assert "Metagross @ Metagrossite" in text and "Kowtow Cleave" in text     # 登録の型に差し替え
    assert "lifeorb" not in text and "garchomp @ focussash" in text          # 未登録の個体は候補の型のまま
    assert "Rotom-Wash" not in text                                           # 登録にいても候補にいない個体は足さない
    assert text.count("\n\n") == 2 and text.endswith("\n")
    print("test_splice_registered_sets OK")


def test_prefer_registered_keeps_registered_items():
    from tools.team_build.sets import prefer_registered, registered_items
    reg = "Rotomwash @ choicescarf\nLevel: 50\n- hydropump\n\nMetagross @ metagrossite\nLevel: 50\n- bulletpunch\n"
    items = registered_items(reg)
    assert items == {"rotomwash": "choicescarf", "metagross": "metagrossite"}, items
    team = [SetCandidate("hydreigon", "levitate", "choicescarf", "timid", "2/0/0/32/0/32", ["dracometeor"]),
            SetCandidate("rotomwash", "levitate", "leftovers", "modest", "32/0/0/32/0/2", ["hydropump"]),
            SetCandidate("metagross", "clearbody", "metagrossite", "adamant", "2/32/0/0/0/32", ["bulletpunch"])]
    ordered = prefer_registered(team, items)
    assert [c.species_id for c in ordered] == ["rotomwash", "metagross", "hydreigon"]
    assert ordered[0].item == "choicescarf"                                   # 登録の持ち物に合わせる
    fixed = resolve_item_clause(ordered, {"hydreigon": ["choicescarf", "choicespecs", "lifeorb"]})
    assert [c.item for c in fixed] == ["choicescarf", "metagrossite", "choicespecs"], [c.item for c in fixed]
    print("test_prefer_registered_keeps_registered_items OK")


if __name__ == "__main__":
    test_item_clause_and_text()
    test_order_candidates_usage_prior()
    test_item_clause_by_usage_and_prefer()
    test_enforce_max_megas()
    test_inject_and_required_moves()
    test_parse_set_text_and_candidate_row()
    test_splice_registered_sets()
    test_prefer_registered_keeps_registered_items()
