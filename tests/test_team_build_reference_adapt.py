"""reference_adapt (参照チームの対照実験) の純粋部分: 腕の構成と要約。

    python -m tests.test_team_build_reference_adapt
"""
import tempfile
from pathlib import Path

from tools.team_build.reference_adapt import ARM_FULL, ARM_REF, control_arms, interpret, summarize


def test_control_arms():
    with tempfile.TemporaryDirectory() as d:
        run = Path(d)
        summary = {"models_dir": "/pins/base", "winner": "L24_C023",
                   "reference_variant": {"variant": "teampreview", "selection_model": None, "pick_policy": "teampreview"},
                   "s08b_variants": {"L24_C023": {"variant": "fresh", "selection_model": "/adv/L24/n8000.pt",
                                                  "pick_policy": "advisor"}}}
        by = {a.arm_id: a for a in control_arms(run, summary, {"models_dir": "/pins/final"}, "/adv/reference_full/n7000.pt")}
        assert set(by) == {ARM_REF, ARM_FULL, "L24_C023"}
        assert by[ARM_REF].pick_policy == "teampreview" and by[ARM_REF].selection_model is None
        assert by[ARM_FULL].selection_model.endswith("n7000.pt") and by[ARM_FULL].pick_policy == "advisor"
        assert by[ARM_FULL].team_file == by[ARM_REF].team_file == run / "reference_team.txt"
        w = by["L24_C023"]
        assert w.selection_model == "/adv/L24/n8000.pt" and w.models_dir == "/pins/final" and w.team_file.name == "L24_C023.txt"
        # Package に選出モデルがあればそれ (成果物と同じもの) を使い、manifest に models_dir が無ければ基底のピン
        (run / "final" / "advisor_policy").mkdir(parents=True)
        (run / "final" / "advisor_policy" / "selection_model.pt").write_bytes(b"x")
        w2 = {a.arm_id: a for a in control_arms(run, summary, {}, None)}["L24_C023"]
        assert w2.selection_model.endswith("final/advisor_policy/selection_model.pt") and w2.models_dir == "/pins/base"
    print("test_control_arms OK")


def test_summarize():
    base = [1, 0] * 100                      # 0.50
    full = [1, 1, 1, 0] * 50                 # 0.75
    win = [1, 1, 1, 0] * 50                  # 0.75 (= 参照+適応)
    res = summarize({ARM_REF: base, ARM_FULL: full, "L24_C023": win}, "L24_C023")
    assert res["win_rates"] == {ARM_REF: 0.5, ARM_FULL: 0.75, "L24_C023": 0.75}
    assert abs(res["adapt_effect"]["mean"] - 0.25) < 1e-9 and res["adapt_effect"]["state"] == "improved"
    assert abs(res["winner_vs_full"]["mean"]) < 1e-9 and abs(res["winner_vs_base"]["mean"] - 0.25) < 1e-9
    line = interpret(res)
    assert "参照の適応 +0.250" in line and "勝者−参照+適応 +0.000" in line and "勝者−参照 +0.250" in line
    empty = summarize({ARM_REF: [], ARM_FULL: [], "X": []}, "X")
    assert empty["win_rates"]["X"] is None and empty["adapt_effect"]["mean"] is None and "?" in interpret(empty)
    print("test_summarize OK")


if __name__ == "__main__":
    test_control_arms()
    test_summarize()
