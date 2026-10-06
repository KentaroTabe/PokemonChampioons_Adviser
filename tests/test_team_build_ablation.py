"""ablation (Team / Pick / Action の分解) の純粋部分: 腕の構成と勝敗列からの分解。

    python -m tests.test_team_build_ablation
"""
from pathlib import Path

from tools.team_build.ablation import BASE_PICK, effects_from_outcomes, grid_arms


def test_grid_arms():
    cand, ref = Path("/c.txt"), Path("/r.txt")
    # 参照が teampreview (選出モデル無し) → T0PrA0 は無い。P0 は両チームとも teampreview
    arms = grid_arms(cand, ref, "/cand.pt", None, "/pins", None, ref_pick_policy="teampreview")
    assert set(arms) == {"T0P0A0", "T1P0A0", "T1P1A0"}
    assert arms["T0P0A0"].pick_policy == BASE_PICK and arms["T0P0A0"].selection_model is None
    assert arms["T1P0A0"].pick_policy == BASE_PICK and arms["T1P0A0"].team_file == cand
    assert arms["T1P1A0"].pick_policy == "advisor" and arms["T1P1A0"].selection_model == "/cand.pt"
    # 参照が fresh / cheap (専用モデル) → T0PrA0 が加わる。alt_models_dir で T1P1A1
    arms = grid_arms(cand, ref, "/cand.pt", "/ref.pt", "/pins", "/pins_alt", ref_pick_policy="advisor")
    assert set(arms) == {"T0P0A0", "T1P0A0", "T1P1A0", "T0PrA0", "T1P1A1"}
    assert arms["T0PrA0"].team_file == ref and arms["T0PrA0"].selection_model == "/ref.pt" and arms["T0PrA0"].pick_policy == "advisor"
    assert arms["T1P1A1"].models_dir == "/pins_alt" and arms["T1P1A1"].selection_model == "/cand.pt"
    # 勝者が teampreview (専用モデル無し) → T1P1A0 も teampreview (pick 効果は 0 になる)
    arms = grid_arms(cand, ref, None, None, "/pins", None)
    assert arms["T1P1A0"].pick_policy == BASE_PICK and "T0PrA0" not in arms
    print("test_grid_arms OK")


def test_effects_from_outcomes():
    ref_tp = [1, 0, 1, 0] * 25          # 0.50
    ref_fr = [1, 1, 1, 0] * 25          # 0.75 (参照の適応で +0.25)
    cand_tp = [1, 0, 0, 0] * 25         # 0.25 (チームそのものは −0.25)
    cand_fr = [1, 1, 1, 0] * 25         # 0.75 (候補専用モデルで +0.50)
    res = effects_from_outcomes({"T0P0A0": ref_tp, "T0PrA0": ref_fr, "T1P0A0": cand_tp, "T1P1A0": cand_fr})
    e = res["effects"]
    assert abs(e["team"]["mean"] + 0.25) < 1e-9 and e["team"]["state"] == "degraded"
    assert abs(e["pick"]["mean"] - 0.5) < 1e-9 and e["pick"]["state"] == "improved"
    assert abs(e["pick_reference"]["mean"] - 0.25) < 1e-9 and "action" not in e
    assert abs(res["total"]["mean"]) < 1e-9                    # 恒等式: total = team + pick − pick_reference
    assert res["win_rates"] == {"T0P0A0": 0.5, "T0PrA0": 0.75, "T1P0A0": 0.25, "T1P1A0": 0.75}
    # 参照が teampreview のときは T0PrA0 が無く、total は T0P0A0 との差。A1 があれば action
    res2 = effects_from_outcomes({"T0P0A0": ref_tp, "T1P0A0": cand_tp, "T1P1A0": cand_fr, "T1P1A1": cand_fr})
    assert abs(res2["total"]["mean"] - 0.25) < 1e-9 and "pick_reference" not in res2["effects"]
    assert abs(res2["effects"]["action"]["mean"]) < 1e-9
    empty = effects_from_outcomes({"T0P0A0": [], "T1P0A0": [], "T1P1A0": []})
    assert empty["effects"]["team"]["mean"] is None and empty["win_rates"]["T0P0A0"] is None
    print("test_effects_from_outcomes OK")


def test_arms_from_json_and_failed():
    """終わった run の ablation.json から腕を作り直し、失敗した腕 (win_rate None / history に error) を見つける (純粋)"""
    from tools.team_build.ablation import arms_from_json, failed_arm_ids
    doc = {"n": 150, "seed": 7, "tier": "selection", "arms": {
        "T0P0A0": {"team_file": "/r.txt", "selection_model": None, "models_dir": "/pins", "extra_args": [],
                   "pick_policy": "teampreview", "win_rate": 0.61, "history": [{"offset": 0, "n": 150, "win_rate": 0.61}]},
        "T1P0A0": {"team_file": "/c.txt", "selection_model": None, "models_dir": "/pins", "extra_args": [],
                   "pick_policy": "teampreview", "win_rate": None, "history": [{"offset": 0, "n": 150, "error": "rc=124"}]},
        "T1P1A0": {"team_file": "/c.txt", "selection_model": "/cand.pt", "models_dir": "/pins", "extra_args": ["--x"],
                   "pick_policy": "advisor", "win_rate": 0.85, "history": [{"offset": 0, "n": 150, "win_rate": 0.85}]}}}
    arms = arms_from_json(doc)
    assert set(arms) == {"T0P0A0", "T1P0A0", "T1P1A0"} and arms["T1P1A0"].selection_model == "/cand.pt"
    assert arms["T1P1A0"].extra_args == ["--x"] and arms["T1P0A0"].pick_policy == "teampreview" and arms["T1P0A0"].outcomes == []
    assert str(arms["T0P0A0"].team_file) == "/r.txt"
    assert failed_arm_ids(doc) == ["T1P0A0"]
    print("test_arms_from_json_and_failed OK")


if __name__ == "__main__":
    test_grid_arms()
    test_effects_from_outcomes()
    test_arms_from_json_and_failed()
