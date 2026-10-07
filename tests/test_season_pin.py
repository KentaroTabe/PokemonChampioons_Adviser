"""季節 (規制) ごとの固定 (tools/team_build/season_pin) のテスト: 分割の seed・使用率スナップショット・実在の構築の区切りと、鮮度。

    python -m tests.test_season_pin
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

from tools.team_build import season_pin as SP


def test_resolve_pure():
    # 表に無い規制: run の seed、最新のスナップショット、今の時刻を登録して使う (fixed:new)
    pin, src, table = SP.resolve_pin("gen9championsbssregmb", 20260906, 57, 1_700_000_000.0, {}, fixed=True)
    assert pin == {"seed": 20260906, "pool_snapshot_id": 57, "roster_until": 1_700_000_000.0} and src == "fixed:new"
    assert table["gen9championsbssregmb"]["seed"] == 20260906 and table["gen9championsbssregmb"]["source"] == "first_run"
    # 表にある規制: 表の固定 (run の seed や最新のスナップショットが違っても)
    pin2, src2, table2 = SP.resolve_pin("gen9championsbssregmb", 777, 61, 1_800_000_000.0, table, fixed=True)
    assert pin2 == pin and src2 == "fixed:gen9championsbssregmb" and table2 == table
    # 別の規制は別の項
    pin3, src3, table3 = SP.resolve_pin("gen9championsbssregmc", 777, 61, 1_800_000_000.0, table2, fixed=True)
    assert pin3["seed"] == 777 and pin3["pool_snapshot_id"] == 61 and src3 == "fixed:new" and set(table3) == {"gen9championsbssregmb", "gen9championsbssregmc"}
    # 固定しない: run の seed と最新 (None)、表は触らない
    pin4, src4, table4 = SP.resolve_pin("gen9championsbssregmb", 777, 61, 1.0, table3, fixed=False)
    assert pin4 == {"seed": 777, "pool_snapshot_id": None, "roster_until": None} and src4 == "run" and table4 == table3
    # seed の上書き (--split-seed) は表を変えず由来に +arg
    pin5, src5, table5 = SP.resolve_pin("gen9championsbssregmb", 777, 61, 1.0, table3, fixed=True, override_seed=5)
    assert pin5["seed"] == 5 and pin5["pool_snapshot_id"] == 57 and src5 == "fixed:gen9championsbssregmb+arg" and table5 == table3
    # スナップショットが取れないときは None (最新を使う) で登録される
    pin6, _s6, _t6 = SP.resolve_pin("", 1, None, 2.0, {}, fixed=True)
    assert pin6["pool_snapshot_id"] is None and pin6["seed"] == 1
    print("test_resolve_pure OK")


def test_overlap_pure():
    a = [("garchomp", 1), ("primarina", 2), ("dragonite", 3)]
    assert SP.weighted_overlap(a, a) == 1.0
    assert SP.weighted_overlap(a, [("x", 1), ("y", 2), ("z", 3)]) == 0.0
    # 1 位が同じで残りが違う: 重みは 3/6, 2/6, 1/6 → 1 位の 0.5 だけ重なる
    assert SP.weighted_overlap(a, [("garchomp", 1), ("y", 2), ("z", 3)]) == 0.5
    # 順位が入れ替わると重みの小さい方
    assert SP.weighted_overlap(a, [("primarina", 1), ("garchomp", 2), ("dragonite", 3)]) == round(2 / 6 + 2 / 6 + 1 / 6, 4)
    assert SP.weighted_overlap([], a) is None and SP.rank_weights([]) == {}
    row = SP.freshness_row(57, 61, 0.75, 30, warn_below=0.8)
    assert row["warn"] is True and row["same"] is False and row["overlap"] == 0.75
    assert SP.freshness_row(57, 57, None, 30)["overlap"] == 1.0 and not SP.freshness_row(57, 57, None, 30)["warn"]
    assert SP.freshness_row(57, None, None, 30)["warn"] is False
    print("test_overlap_pure OK")


def test_file_roundtrip_and_repin():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "registry" / "season_pins.json"
        assert SP.load_table(path) == {}
        pin, src = SP.pin_for("regA", 11, path=path, fixed=True, latest_snapshot_id=57, now=1000.0)
        assert (pin["seed"], pin["pool_snapshot_id"], pin["roster_until"], src) == (11, 57, 1000.0, "fixed:new")
        saved = json.loads(path.read_text(encoding="utf-8"))
        assert saved["regA"]["pool_snapshot_id"] == 57 and saved["regA"]["created_at"]
        pin2, src2 = SP.pin_for("regA", 22, path=path, fixed=True, latest_snapshot_id=61, now=2000.0)
        assert pin2 == pin and src2 == "fixed:regA"
        # 固定し直し: 新しいスナップショット、区切りは今、前の固定は previous に残る
        entry = SP.repin("regA", snapshot_id=61, path=path, now=3000.0)
        assert entry["seed"] == 11 and entry["pool_snapshot_id"] == 61 and entry["roster_until"] == 3000.0 and entry["source"] == "repin"
        assert entry["previous"]["pool_snapshot_id"] == 57 and entry["previous"]["roster_until"] == 1000.0
        pin3, src3 = SP.pin_for("regA", 33, path=path, fixed=True, now=4000.0)
        assert pin3 == {"seed": 11, "pool_snapshot_id": 61, "roster_until": 3000.0} and src3 == "fixed:regA"
        # 壊れた表は空として扱う。固定しない設定は run の seed
        path.write_text("not json", encoding="utf-8")
        assert SP.load_table(path) == {}
        assert SP.pin_for("regA", 44, path=path, fixed=False)[1] == "run"
    print("test_file_roundtrip_and_repin OK")


def test_freshness_with_db():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE usage_snapshot (id INTEGER PRIMARY KEY, format TEXT, number_of_battles INTEGER)")
    conn.execute("CREATE TABLE pokemon_usage (snapshot_id INTEGER, pokemon_name TEXT, usage_percent REAL)")
    conn.executemany("INSERT INTO usage_snapshot VALUES (?, ?, ?)", [(1, "f", 100), (2, "f", 100)])
    rows = [(1, "garchomp", 50.0), (1, "primarina", 40.0), (1, "dragonite", 30.0), (1, "gengar", 20.0),
            (2, "garchomp", 55.0), (2, "primarina", 35.0), (2, "mimikyu", 33.0), (2, "gengar", 10.0)]
    conn.executemany("INSERT INTO pokemon_usage VALUES (?, ?, ?)", rows)
    assert SP.top_species(conn, 1, 3) == [("garchomp", 1), ("primarina", 2), ("dragonite", 3)]
    f = SP.freshness(1, 2, top_n=3, warn_below=0.9, conn=conn)
    # 上位 3: {garchomp 1, primarina 2, dragonite 3} vs {garchomp 1, primarina 2, mimikyu 3} → 3/6 + 2/6 = 0.8333
    assert f["overlap"] == round(5 / 6, 4) and f["warn"] is True and f["pinned"] == 1 and f["latest"] == 2
    assert SP.freshness(1, 1, top_n=3, conn=conn)["overlap"] == 1.0
    assert SP.freshness(None, 2, conn=conn)["note"] == "no_pin"
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "season_pins.json"
        SP.pin_for("regA", 1, path=path, fixed=True, latest_snapshot_id=1, now=1.0)
        res = SP.check(path=path, top_n=3, warn_below=0.9, conn=conn, latest_snapshot_id=2)
        assert res["any_warn"] is True and res["regulations"]["regA"]["overlap"] == round(5 / 6, 4) and res["regulations"]["regA"]["seed"] == 1
    print("test_freshness_with_db OK")


def _split_doc(seed: int = 7) -> dict:
    """families の純粋関数で作った分割 (opponents.build_split と同じ形の一部)。構築は 30、系統化の閾値 0.5"""
    from tools.team_build import families as F
    pool = [f"s{i}" for i in range(40)]
    teams = []
    for i in range(30):
        sp = frozenset(pool[(i * 3 + k) % 40] for k in range(6))
        teams.append(F.Team(team_id=f"t{i:02d}", species=sp, mega=None, rank=i + 1))
    fams = F.cluster_families(teams, min_jaccard=0.5)
    split = F.stratified_split(fams, seed=seed)
    search_fams = [f for f in fams if split["families"][f.family_id] == "search"]
    folds = F.cross_fit_folds(search_fams, k=3, seed=seed)
    return {"run_id": "r1", "seed": seed, "pool_source": "latest", "pool_snapshot": 58, "top_n": 30, "min_jaccard": 0.5,
            "tiers": {k: split[k] for k in F.TIERS}, "search_folds": folds, "sealed_id": F.sealed_id(split["holdout"]),
            "families": [{"family_id": f.family_id, "tier": split["families"][f.family_id], "teams": [t.team_id for t in f.teams]}
                         for f in fams],
            "teams": {t.team_id: {"rank": t.rank, "species": sorted(t.species), "mega": t.mega} for t in teams}}


def test_split_independence_pure():
    doc = _split_doc()
    r = SP.split_independence(doc)
    assert r["ok"] is True and r["sealed_ok"] is True, r
    assert r["team_multi_tier"] == 0 and r["family_cross_tier"] == 0 and r["folds"]["overlap"] == 0, r
    assert r["folds"]["missing_from_folds"] == 0 and r["folds"]["not_in_search"] == 0 and r["folds"]["family_cross_fold"] == 0, r
    assert sum(r["n_teams"].values()) == 30 and r["folds"]["n_folds"] == 3
    # 層の重なり: holdout の構築を selection にも入れる → 構築・系統の跨ぎ、封印 id はそのまま (holdout の列は変えない)
    leak = json.loads(json.dumps(doc))
    leak["tiers"]["selection"].append(leak["tiers"]["holdout"][0])
    r2 = SP.split_independence(leak)
    assert r2["ok"] is False and r2["team_multi_tier"] == 1 and r2["family_cross_tier"] >= 1 and r2["family_tier_mismatch"] >= 1, r2
    assert r2["identical_cross_tier"] >= 1, r2       # 同じ構築が 2 つの層 = 種族が完全に同じ組
    # fold の重なりと search 外
    leak2 = json.loads(json.dumps(doc))
    leak2["search_folds"][1].append(leak2["search_folds"][0][0])
    leak2["search_folds"][2].append(leak2["tiers"]["holdout"][0])
    r3 = SP.split_independence(leak2)
    assert r3["ok"] is False and r3["folds"]["overlap"] == 1 and r3["folds"]["not_in_search"] == 1, r3["folds"]
    assert r3["folds"]["family_cross_fold"] >= 1, r3["folds"]
    # 封印 id の食い違い
    leak3 = dict(doc, sealed_id="0" * 16)
    assert SP.split_independence(leak3)["sealed_ok"] is False
    # 似た組: メガ軸だけ違う同じ 6 体を別の層に置く → 別のメガ軸の似た組 (系統の定義の外。ok には含めない)
    sim = json.loads(json.dumps(doc))
    h0 = sim["tiers"]["holdout"][0]
    sim["teams"]["tx"] = dict(sim["teams"][h0], mega="s0")
    sim["tiers"]["search"].append("tx")
    sim["search_folds"][0].append("tx")
    sim["families"].append({"family_id": "FX", "tier": "search", "teams": ["tx"]})
    r4 = SP.split_independence(sim)
    assert r4["similar_cross_tier"]["other_mega"] >= 1 and r4["ok"] is True, r4
    print("test_split_independence_pure OK")


def test_similar_pair_details():
    """判断 10: 別メガ軸の似た組の 1 組ごとの集計 (構築 id・重み・型の共通性)。種・型の名前は出さない"""
    doc = _split_doc()
    h0 = doc["tiers"]["holdout"][0]
    doc["teams"]["tx"] = dict(doc["teams"][h0], mega="s0")
    doc["tiers"]["search"].append("tx")
    doc["search_folds"][0].append("tx")
    doc["families"].append({"family_id": "FX", "tier": "search", "teams": ["tx"]})
    sp = doc["teams"][h0]["species"]
    text_h = "\n\n".join(f"{s} @ Leftovers\nAbility: Pressure\n- Protect\n- Tackle\n- Growl\n- Ember" for s in sp)
    text_x = "\n\n".join(f"{s} @ {'Leftovers' if i < 2 else 'Life Orb'}\nAbility: Pressure\n- Protect\n- Tackle\n- Surf\n- Ember"
                         for i, s in enumerate(sp))
    doc["texts"] = {h0: text_h, "tx": text_x}
    pairs = SP.similar_pair_details(doc, scope="tier")
    p = next(x for x in pairs if {x["a"]["team_id"], x["b"]["team_id"]} == {h0, "tx"})
    assert p["jaccard"] == 1.0 and p["same_mega"] is False, p
    assert p["type"] == {"common_species": 6, "common_moves": 18, "moves_compared": 24, "same_item": 2, "same_ability": 6}, p["type"]
    side = p["a"] if p["a"]["team_id"] == "tx" else p["b"]
    n_search = len(doc["tiers"]["search"])
    assert side["group"] == "search" and side["family_id"] == "FX" and side["family_size"] == 1, side
    assert abs(side["measure_share"] - round(1 / n_search, 5)) < 1e-9 and side["family_share"] == side["measure_share"], side
    # 中身 (種の名前) は行に入らない
    assert not any(s in json.dumps(p) for s in sp), p
    # 同じメガ軸の組は既定で外す。fold をまたぐ組は fold の群で数える
    assert all(x["same_mega"] is False for x in pairs)
    assert all(x["a"]["group"].startswith("fold") for x in SP.similar_pair_details(doc, scope="fold"))
    txt = SP.format_similar_pairs(pairs, "層をまたぐ別メガ軸の似た組")
    assert "tx" in txt and f"{len(pairs)} 組" in txt, txt
    print("test_similar_pair_details OK")


def test_cross_run_and_selection():
    a, b = _split_doc(), _split_doc()
    b["run_id"] = "r2"
    res = SP.cross_run_consistency({"r1": a, "r2": b})
    assert res["ok"] is True and res["groups"][0]["runs"] == ["r1", "r2"] and res["groups"][0]["teams_conflict_tier"] == 0, res
    # 同じ固定なのに分割が違う (構築を別の層へ) → 食い違い
    c = json.loads(json.dumps(b))
    moved = c["tiers"]["holdout"].pop(0)
    c["tiers"]["selection"].append(moved)
    c["sealed_id"] = "x"
    res2 = SP.cross_run_consistency({"r1": a, "r3": c})
    assert res2["ok"] is False and res2["groups"][0]["teams_conflict_tier"] == 1 and len(res2["groups"][0]["sealed_ids"]) == 2, res2
    # 固定の別 (seed が違う) は別の群
    d = dict(_split_doc(seed=8), run_id="r4")
    assert len(SP.cross_run_consistency({"r1": a, "r4": d})["groups"]) == 2
    # 固定を使った run の選び方: 由来が fixed で seed が表と同じもの
    table = {"regA": {"seed": 7}}
    mans = {"r1": {"regulation": "regA", "season_pin": {"seed": 7, "source": "fixed:new"}},
            "r2": {"regulation": "regA", "season_pin": {"seed": 7, "source": "fixed:regA"}},
            "r3": {"regulation": "regA", "season_pin": {"seed": 9, "source": "fixed:regA+arg"}},
            "r4": {"regulation": "regA", "seed": 7},
            "r5": {"regulation": "regB", "season_pin": {"seed": 7, "source": "fixed:new"}}}
    assert SP.select_pinned_runs(mans, table) == {"r1": "regA", "r2": "regA"}
    print("test_cross_run_and_selection OK")


def test_check_splits_files():
    with tempfile.TemporaryDirectory() as d:
        runs = Path(d) / "runs"
        pins = Path(d) / "season_pins.json"
        pins.write_text(json.dumps({"regA": {"seed": 7}}), encoding="utf-8")
        for rid in ("r1", "r2"):
            (runs / rid).mkdir(parents=True)
            (runs / rid / "manifest.json").write_text(json.dumps({"regulation": "regA", "season_pin": {"seed": 7, "source": "fixed:regA"}}),
                                                      encoding="utf-8")
            (runs / rid / "opponent_families.json").write_text(json.dumps(dict(_split_doc(), run_id=rid)), encoding="utf-8")
        (runs / "old").mkdir()
        (runs / "old" / "manifest.json").write_text(json.dumps({"regulation": "regA", "seed": 1}), encoding="utf-8")
        (runs / "old" / "opponent_families.json").write_text(json.dumps(_split_doc(seed=1)), encoding="utf-8")
        res = SP.check_splits(runs, pins)
        assert res["ok"] is True and sorted(res["runs"]) == ["r1", "r2"], res
        txt = SP.format_check_splits(res)
        assert "重なり 0" in txt and "[r1]" in txt and "同じ固定の run ['r1', 'r2']" in txt, txt
        # 固定を使った run が無ければ ok にしない
        assert SP.check_splits(runs, Path(d) / "none.json")["ok"] is False
    print("test_check_splits_files OK")


def main() -> None:
    test_resolve_pure()
    test_overlap_pure()
    test_file_roundtrip_and_repin()
    test_freshness_with_db()
    test_split_independence_pure()
    test_similar_pair_details()
    test_cross_run_and_selection()
    test_check_splits_files()
    print("ALL OK")


if __name__ == "__main__":
    main()
