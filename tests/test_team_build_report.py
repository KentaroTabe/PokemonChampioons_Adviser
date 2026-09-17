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


if __name__ == "__main__":
    test_report()
    test_report_finalists()
