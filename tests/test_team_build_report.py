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
        (run / "s04_concepts.json").write_text(json.dumps({"families": [{"family_id": "C001", "core_ids": ["a", "b"], "win_condition": "setup_sweep"}]}))
        (run / "evaluation" / "summary.json").write_text(json.dumps({"holdout": {"verdict": "PASS", "delta": 0.05, "ci": [0.02, 0.08], "n": 300},
                                                                     "ablation": {"team": {"mean": 0.03}}, "robustness_worst": 0.02}))
        facts = facts_from_run(run, "L00_C001")
        assert facts["concept"]["core_ids"] == ["a", "b"] and facts["holdout"]["verdict"] == "PASS"
        md = template_report(facts)
        assert "PASS" in md and "a @ x" in md
        good = json.dumps({"authoritative": {"ok": True}, "display": {"markdown": "# 記事\n本文"}})
        p = write_report(run, "L00_C001", MockProvider([good]))
        text = p.read_text(encoding="utf-8")
        assert text.startswith("# 記事") and "機械生成の数値表" in text
    print("test_report OK")


if __name__ == "__main__":
    test_report()
