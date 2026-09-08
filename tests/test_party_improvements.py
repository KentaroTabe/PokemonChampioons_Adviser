"""自パーティ改善案 (tools/party_improvements) の純粋部分のテスト。

    python -m tests.test_party_improvements
"""
from __future__ import annotations

from tools import party_improvements as PI


def test_parse_showdown_sets():
    text = ("Metagross @ metagrossite\nLevel: 50\nAbility: clearbody\nEVs: 2 HP / 32 Atk / 32 Spe\nAdamant Nature\n"
            "- bulletpunch\n- psychicfangs\n\nRotom-Wash @ choicescarf\nLevel: 50\nAbility: levitate\nModest Nature\n- hydropump\n")
    sets = PI.parse_showdown_sets(text)
    assert set(sets) == {"metagross", "rotomwash"}, sets
    m = sets["metagross"]
    assert m["item"] == "metagrossite" and m["ability"] == "clearbody" and m["nature"] == "adamant"
    assert m["evs"] == {"hp": 2, "atk": 32, "spe": 32} and m["moves"] == ["bulletpunch", "psychicfangs"]
    assert sets["rotomwash"]["evs"] == {} and sets["rotomwash"]["moves"] == ["hydropump"]
    print("test_parse_showdown_sets OK")


def test_pressure_and_difficulty():
    dec = [{"opp": "A", "best_kind": "move", "best_score": 120.0}, {"opp": "A", "best_kind": "switch", "best_score": 80.0},
           {"opp": "A", "best_kind": "move", "best_score": 30.0}, {"opp": "B", "best_kind": "move", "best_score": 150.0}]
    pm = PI.pressure_metrics(dec, low_score=60.0, min_n=2)
    assert pm["overall"]["n"] == 4 and pm["overall"]["pressure_share"] == 0.5
    assert "A" in pm["by_opponent"] and "B" not in pm["by_opponent"]          # B は決定数不足
    a = pm["by_opponent"]["A"]
    assert a["switch_share"] == round(1 / 3, 3) and a["low_share"] == round(1 / 3, 3) and a["pressure_share"] == round(2 / 3, 3)
    assert PI.difficulty_weight("loss", 0.5, loss_weight=1.0) == 2.5 and PI.difficulty_weight("win", None) == 1.0
    battles = [{"file": "x", "outcome": "loss", "opp_roster": ["a"], "decisions": dec},
               {"file": "y", "outcome": "win", "opp_roster": ["b"], "decisions": [dec[3]]},
               {"file": "z", "outcome": "win", "opp_roster": [], "decisions": []}]
    hard = PI.rank_hard_parties(battles, top=5)
    assert [h["file"] for h in hard] == ["x", "y"] and hard[0]["worst_opponent"] == "A"
    print("test_pressure_and_difficulty OK")


def test_team_concepts_psychic_terrain():
    members = {
        "armarouge": {"set": {"moves": ["armorcannon", "psychic", "psychicterrain", "destinybond"], "ability": "weakarmor"},
                      "base": {"hp": 85, "atk": 60, "def": 100, "spa": 125, "spd": 80, "spe": 75}},
        "raichu": {"set": {"moves": ["zapcannon", "focusblast", "grassknot", "nastyplot"], "ability": "lightningrod",
                           "item": "raichunitey"},
                   "base": {"hp": 60, "atk": 100, "def": 55, "spa": 160, "spd": 80, "spe": 130}},
        "garchomp": {"set": {"moves": ["earthquake", "stealthrock", "swordsdance", "scaleshot"], "ability": "roughskin"},
                     "base": {"hp": 108, "atk": 130, "def": 95, "spa": 80, "spd": 85, "spe": 102}},
    }
    tags = PI.team_concepts(members, ["move_opponent_psychicterrain"], move_info=None, name=lambda s: s.upper())
    by = {t["tag"]: t for t in tags}
    assert "psychic_terrain_support" in by, tags
    assert set(by["psychic_terrain_support"]["members"]) == {"armarouge", "raichu"}
    assert "ARMAROUGE" in by["psychic_terrain_support"]["evidence"] and "観測あり" in by["psychic_terrain_support"]["evidence"]
    assert "setup_sweep" in by and "hazard_stack" not in by        # 設置役は 1 体
    # 速くて脆い個体がいなければ地形だけでは概念にしない
    tags2 = PI.team_concepts({"armarouge": members["armarouge"], "garchomp": members["garchomp"]})
    assert all(t["tag"] != "psychic_terrain_support" for t in tags2), tags2
    print("test_team_concepts_psychic_terrain OK")


def test_priority_dependence_and_proposals_with_dex():
    """実図鑑で: ふいうち持ちドドゲザンはサイコフィールド下 (先制技なし) で被覆が落ち、案の構造が正しい"""
    my = {"kingambit": {"item": "blackglasses", "ability": "supremeoverlord", "nature": "adamant",
                        "evs": {"hp": 32, "atk": 32, "spe": 2}, "moves": ["suckerpunch", "kowtowcleave", "ironhead", "swordsdance"]},
          "metagross": {"item": "metagrossite", "ability": "clearbody", "nature": "adamant",
                        "evs": {"hp": 2, "atk": 32, "spe": 32}, "moves": ["bulletpunch", "psychicfangs", "earthquake", "icepunch"]}}
    owned = dict(my)
    owned["dragonite"] = {"item": "heavydutyboots", "ability": "multiscale", "nature": "adamant",
                          "evs": {"hp": 2, "atk": 32, "spe": 32}, "moves": ["dragondance", "extremespeed", "earthquake", "icespinner"]}
    opp = {"armarouge": {"item": "focussash", "ability": "weakarmor", "nature": "modest", "evs": {"hp": 2, "spa": 32, "spe": 32},
                         "moves": ["armorcannon", "destinybond", "psychic", "psychicterrain"]},
           "raichu": {"item": "raichunitey", "ability": "lightningrod", "nature": "timid", "evs": {"hp": 2, "spa": 32, "spe": 32},
                      "moves": ["zapcannon", "focusblast", "grassknot", "nastyplot"]}}
    my_views, owned_views, opp_views = PI._views(my), PI._views(owned), PI._views(opp)
    assert set(opp_views) == {"armarouge", "raichu"} and opp_views["raichu"][0].species_id == "raichumegay"   # メガ後で評価
    pd = PI.priority_dependence(my_views, opp_views)
    k = pd["per_member"]["kingambit"]
    assert k["priority_moves"] == ["suckerpunch"] and k["drop"] >= 0.0
    assert 0.0 <= pd["team_coverage_no_priority"] <= pd["team_coverage"] <= 1.0
    pr = PI.counter_proposals(["kingambit", "metagross"], owned_views, opp_views, threat_order=["raichu", "armarouge"], top_threats=1)
    assert list(pr["per_threat"]) == ["raichu"]
    assert pr["per_threat"]["raichu"]["best_owned_outside"][0][1] == "dragonite"
    assert pr["swaps"] and all(s["in"] == "dragonite" for s in pr["swaps"]) and len(pr["swaps"]) == 1
    print("test_priority_dependence_and_proposals_with_dex OK")


def test_measure_command_and_measured_report():
    import json
    import tempfile
    from pathlib import Path
    cmd = PI.measure_command("improve_x", Path("w.json"), 3, "medium", 5, 42)
    s = " ".join(cmd)
    assert s.startswith("bash scripts/team_build_nohup.sh improve_x --stages all --profile medium")
    assert "--only-incumbent" in s and "--incumbent-neighbors-s5 3" in s and "--max-candidates 4" in s
    assert "--threat-weights-file w.json" in s and "--seed 42" in s
    # 測定済みの候補だけを載せ、型つきの本文を日本語で出す
    with tempfile.TemporaryDirectory() as d:
        run = Path(d) / "runs" / "improve_t"
        (run / "evaluation").mkdir(parents=True)
        (run / "s06_sets").mkdir()
        (run / "s06_sets.json").write_text(json.dumps([
            {"candidate_id": "L00_INC", "ok": True, "tag": "incumbent", "members": ["kingambit", "metagross"]},
            {"candidate_id": "L01_INC", "ok": True, "tag": "incumbent_mut", "members": ["dragonite", "metagross"]},
            {"candidate_id": "L02_INC", "ok": True, "tag": "incumbent_mut", "members": ["garchomp", "metagross"]}]),
            encoding="utf-8")
        (run / "s06_sets" / "L01_INC.txt").write_text(
            "Dragonite @ heavydutyboots\nLevel: 50\nAbility: multiscale\nEVs: 2 HP / 32 Atk / 32 Spe\nAdamant Nature\n- dragondance\n- extremespeed\n",
            encoding="utf-8")
        def arm(aid, mean, state, n=300):
            return {"arm_id": aid, "state": state, "eliminated_at": None, "n_done": n,
                    "result": {"mean": mean, "se": 0.03, "ci_low": mean - 0.06, "ci_high": mean + 0.06}}
        (run / "evaluation" / "s08a_screen.json").write_text(json.dumps({"arms": [
            arm("L00_INC@cheap", -0.02, "equivalent"), arm("L01_INC@teampreview", -0.10, "uncertain"),
            arm("L01_INC@cheap", +0.01, "uncertain")]}), encoding="utf-8")
        (run / "evaluation" / "s08b_adapted.json").write_text(json.dumps({"arms": [
            arm("L01_INC@fresh", +0.03, "uncertain"), arm("L01_INC@generic", -0.05, "uncertain")]}), encoding="utf-8")
        (run / "evaluation" / "summary.json").write_text(json.dumps({
            "reference_variant": {"variant": "teampreview"}, "winner": "L01_INC", "winner_variant": "fresh",
            "holdout": {"verdict": "PASS_EQUIVALENT", "delta": 0.01, "ci": [-0.03, 0.05], "n": 600}, "result": "PASS_EQUIVALENT"}),
            encoding="utf-8")
        orig = PI.REPO
        try:
            PI.REPO = Path(d)
            (Path(d) / "logs" / "build_search").mkdir(parents=True)
            (Path(d) / "logs" / "build_search" / "runs").symlink_to(Path(d) / "runs")
            md = PI.measured_report("improve_t")
        finally:
            PI.REPO = orig
    assert "L01_INC" in md and "L00_INC" in md and "L02_INC" not in md          # 未測定の L02 は載せない
    assert "S8b: variant=fresh Δ=+0.030" in md and "S8a: variant=cheap Δ=+0.010" in md
    assert "PASS_EQUIVALENT" in md and "カイリュー" in md and "りゅうのまい" in md and "いじっぱり" in md
    assert md.index("L01_INC") < md.index("L00_INC")                               # Δ の高い順
    print("test_measure_command_and_measured_report OK")


if __name__ == "__main__":
    test_parse_showdown_sets()
    test_pressure_and_difficulty()
    test_team_concepts_psychic_terrain()
    test_priority_dependence_and_proposals_with_dex()
    test_measure_command_and_measured_report()
