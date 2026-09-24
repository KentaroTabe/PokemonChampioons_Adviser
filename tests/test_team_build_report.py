"""build_report のテンプレートとモック LLM のテスト。

    python -m tests.test_team_build_report
"""
import json
import tempfile
from pathlib import Path

from tools.team_build.llm.provider import MockProvider
from tools.team_build.report import facts_from_run, template_report, write_report


def test_report():
    with tempfile.TemporaryDirectory() as d:
        run = Path(d)
        (run / "evaluation").mkdir()
        (run / "s06_sets.json").write_text(json.dumps([{"candidate_id": "L00_C001", "members": ["a", "b"],
                                                        "sets": [{"species": "a", "item": "x", "nature": "adamant", "evs": "32/32/0/0/0/2", "moves": ["m1"]}]}]))
        (run / "s06_sets").mkdir()
        (run / "s06_sets" / "L00_C001.txt").write_text("a @ x\nLevel: 50\nAbility: abil1\nEVs: 32 HP / 32 Atk / 2 Spe\nAdamant Nature\n- m1\n")
        (run / "s04_concepts.json").write_text(json.dumps({"families": [{"family_id": "C001", "core_ids": ["a", "b"], "win_condition": "setup_sweep"}]}))
        (run / "evaluation" / "summary.json").write_text(json.dumps({"holdout": {"verdict": "PASS", "delta": 0.05, "ci": [0.02, 0.08], "n": 300},
                                                                     "ablation": {"team": {"mean": 0.03}}, "robustness_worst": 0.02}))
        facts = facts_from_run(run, "L00_C001")
        assert facts["concept"]["core_ids"] == ["a", "b"] and facts["holdout"]["verdict"] == "PASS"
        md = template_report(facts)
        assert facts["sets"][0]["ability"] == "abil1"                     # 特性は s06_sets/<id>.txt の本文から補う
        assert "PASS" in md and "| a | x | abil1 |" in md and "いじっぱり H32 A32 S2" in md      # 並びは日本語の表 (表に無い id はそのまま)
        good = json.dumps({"authoritative": {"ok": True}, "display": {"markdown": "# 記事\n本文"}})
        p = write_report(run, "L00_C001", MockProvider([good]))
        text = p.read_text(encoding="utf-8")
        assert text.startswith("# 記事") and "機械生成の数値表" in text
        assert "最終候補" not in md                                        # finalists が無ければ比較表も無い
    print("test_report OK")


def test_report_finalists():
    """複数の最終候補: 比較表と、他の候補の記事 (自分の holdout、書き先)"""
    with tempfile.TemporaryDirectory() as d:
        run = Path(d)
        (run / "evaluation").mkdir()
        (run / "s06_sets").mkdir()
        rows = []
        for cid, mem in (("L00_C001", ["a", "b"]), ("L05_C002", ["c", "d"])):
            rows.append({"candidate_id": cid, "members": mem, "sets": [{"species": mem[0], "item": "x", "nature": "adamant",
                                                                          "evs": "32/32/0/0/0/2", "moves": ["m1"]}]})
            (run / "s06_sets" / f"{cid}.txt").write_text(f"{mem[0]} @ x\nLevel: 50\nAbility: ab\nEVs: 32 HP\nAdamant Nature\n- m1\n")
        (run / "s06_sets.json").write_text(json.dumps(rows))
        (run / "s04_concepts.json").write_text(json.dumps({"families": [{"family_id": "C001", "core_ids": ["a"], "win_condition": "setup_sweep"},
                                                                         {"family_id": "C002", "core_ids": ["c"], "win_condition": "offense_trade"}]}))
        finalists = [{"rank": 1, "candidate_id": "L00_C001", "direction_ja": "積んで全抜き", "delta_s10": 0.07,
                      "holdout": {"verdict": "PASS", "delta": 0.05, "ci": [0.02, 0.08], "n": 300}},
                     {"rank": 2, "candidate_id": "L05_C002", "direction_ja": "対面で殴り勝つ", "delta_s10": 0.04,
                      "holdout": {"verdict": "INCONCLUSIVE", "delta": 0.02, "ci": [-0.01, 0.05], "n": 300}}]
        (run / "evaluation" / "summary.json").write_text(json.dumps({"holdout": finalists[0]["holdout"], "finalists": finalists,
                                                                     "ablation": {"team": {"mean": 0.03}}, "robustness_worst": 0.02}))
        top = facts_from_run(run, "L00_C001")
        assert top["finalist_rank"] == 1 and top["holdout"]["verdict"] == "PASS" and top["ablation"] is not None
        md = template_report(top)
        assert "最終候補" in md and "★ L00_C001" in md and "L05_C002" in md and "対面で殴り勝つ" in md and "INCONCLUSIVE +0.020" in md
        alt = facts_from_run(run, "L05_C002")
        assert alt["finalist_rank"] == 2 and alt["holdout"]["verdict"] == "INCONCLUSIVE" and alt["ablation"] is None
        out = run / "final" / "alternatives" / "L05_C002" / "build_report.md"
        p = write_report(run, "L05_C002", provider=None, out_path=out)
        assert p == out and "★ L05_C002" in out.read_text(encoding="utf-8")
    print("test_report_finalists OK")


def test_report_lineage():
    """系統の軸が並びに居ない近傍 (arch_0924 の L06_C020) と持ち込みの並び: 軸・メガは実際の個体で書き、入替を明記する"""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        src = root / "arch_src"
        run = root / "arch_new"
        for r in (src, run):
            (r / "evaluation").mkdir(parents=True)
            (r / "s06_sets").mkdir()
        (src / "s04_concepts.json").write_text(json.dumps({"families": [{"family_id": "C029", "core_ids": ["dragonite", "kingambit"],
                                                                          "mega_id": "dragonite", "win_condition": "priority_cleanup"}]}))
        (run / "s04_concepts.json").write_text(json.dumps({"families": [{"family_id": "C020", "core_ids": ["dragonite", "hydreigon"],
                                                                          "mega_id": "dragonite", "win_condition": "cycle_pressure"}]}))
        rows = [{"candidate_id": "L06_C020", "members": ["charizard", "hydreigon", "metagross"], "tag": "concept",
                 "origin": {"kind": "mutation", "parent_concept": "C020", "swap": ["dragonite", "charizard"]},
                 "sets": [{"species": "charizard", "item": "charizarditey", "nature": "modest", "evs": "2/0/0/32/0/32", "moves": ["m1"]},
                          {"species": "hydreigon", "item": "choicescarf", "nature": "timid", "evs": "2/0/0/32/0/32", "moves": ["m1"]},
                          {"species": "metagross", "item": "metagrossite", "nature": "adamant", "evs": "2/32/0/0/0/32", "moves": ["m1"]}]},
                {"candidate_id": "L69_C029_from_arch_src", "members": ["dragonite", "kingambit", "slurpuff"], "tag": "imported",
                 "imported_from": {"run_id": "arch_src", "candidate_id": "L69_C029"},
                 "sets": [{"species": "dragonite", "item": "dragoninite", "nature": "adamant", "evs": "2/32/0/0/0/32", "moves": ["m1"]}]}]
        (run / "s06_sets.json").write_text(json.dumps(rows))
        for r in rows:
            (run / "s06_sets" / f"{r['candidate_id']}.txt").write_text("x @ y\nLevel: 50\nAbility: ab\nEVs: 32 HP\nAdamant Nature\n- m1\n")
        stones = {"charizarditey", "metagrossite", "dragoninite"}
        facts = facts_from_run(run, "L06_C020", stones=stones)
        c = facts["concept"]
        assert c["core_ids"] == ["hydreigon"] and c["core_missing"] == ["dragonite"] and c["concept_core_ids"] == ["dragonite", "hydreigon"]
        assert c["mega_id"] is None and c["mega_ids"] == ["charizard", "metagross"] and c["origin"]["swap"] == ["dragonite", "charizard"]
        assert facts["ja"]["species"]["dragonite"] if "ja" in facts else True
        md = template_report(facts)
        assert "はこの並びに含まれない (探索の近傍で" in md and "メガ石:" in md and "1 試合にメガシンカできるのは 1 体" in md, md
        assert "軸: サザンドラ (hydreigon)" in md or "軸: hydreigon" in md, md
        from tools.team_build.report import ja_facts
        names = ja_facts(facts)["species"]
        assert "dragonite" in names and "charizard" in names and "metagross" in names      # 入替前後と石持ちを対応表に
        # 持ち込み: 元 run の系統 (C029) で軸とメガを書く。系統の kingambit は居る、slurpuff は軸外
        imp = facts_from_run(run, "L69_C029_from_arch_src", stones=stones)
        assert imp["concept"]["family_id"] == "C029" and imp["concept"]["core_ids"] == ["dragonite", "kingambit"]
        assert imp["concept"]["core_missing"] == [] and imp["concept"]["mega_ids"] == ["dragonite"]
        assert "先制技で詰める" in imp["direction_ja"] and "メガ: " in imp["direction_ja"]
        md2 = template_report(imp)
        assert "含まれない" not in md2 and "メガ石:" in md2
    print("test_report_lineage OK")


if __name__ == "__main__":
    test_report()
    test_report_finalists()
    test_report_lineage()
