"""固定部品との交差評価 (自動 ablation): 勝率変化を Team / Pick / Action に分解する。

  (T0,P0,A0) 参照チーム + teampreview     (T0,Pr,A0) 参照チーム + 手順で選んだ参照の選出方策 (teampreview なら省く)
  (T1,P0,A0) 候補チーム + teampreview     (T1,P1,A0) 候補チーム + 候補専用の選出モデル     (T1,P1,A1) + 別の行動方策
  Team           = WR(T1,P0,A0) − WR(T0,P0,A0)   チームそのものの差 (選出方策をチームに依らない teampreview に揃える)
  Pick           = WR(T1,P1,A0) − WR(T1,P0,A0)   候補専用の選出モデルの上げ幅
  Pick_reference = WR(T0,Pr,A0) − WR(T0,P0,A0)   参照の選出方策の上げ幅 (参照が fresh / cheap のとき)
  Action         = WR(T1,P1,A1) − WR(T1,P1,A0)   (A1 が指定されたときだけ)
  Total          = WR(T1,P1,A0) − WR(T0,Pr,A0)   holdout と同じ比較 (= Team + Pick − Pick_reference の恒等式)
同一相手列 (SELECTION 階層) で対応比較する。

2026-09-15: P0 を明示的に teampreview にした。以前の P0 は「参照の選出方策」で、参照が cheap のときは参照専用のモデルを
候補チームに使い (rule_0910)、teampreview のときは選出モデル無しの advisor = 分布内の参照だけ production モデルに落ちて
いた (rule_0913)。Interaction は線形の分解では恒等的に 0 になるので出さない。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from tools.team_build import racing as R
from tools.team_build.verdict import verdict4

ABLATION_BATTLES = 300
BASE_PICK = "teampreview"      # P0: チームに依らない選出方策


# ------------------------------------------------------------------ 純粋関数 (テスト対象)
def grid_arms(candidate_team: Path, reference_team: Path, cand_pick_model: Optional[str], ref_pick_model: Optional[str],
              models_dir: Optional[str], alt_models_dir: Optional[str], ref_pick_policy: Optional[str] = None) -> dict:
    """交差評価の腕。T0PrA0 は参照の選出方策が teampreview 以外のときだけ、T1P1A1 は alt_models_dir があるときだけ"""
    cand_policy = "advisor" if cand_pick_model else BASE_PICK
    arms = {
        "T0P0A0": R.Arm("T0P0A0", reference_team, None, models_dir, pick_policy=BASE_PICK),
        "T1P0A0": R.Arm("T1P0A0", candidate_team, None, models_dir, pick_policy=BASE_PICK),
        "T1P1A0": R.Arm("T1P1A0", candidate_team, cand_pick_model, models_dir, pick_policy=cand_policy),
    }
    if ref_pick_model or (ref_pick_policy and ref_pick_policy != BASE_PICK):
        arms["T0PrA0"] = R.Arm("T0PrA0", reference_team, ref_pick_model, models_dir, pick_policy=ref_pick_policy or "advisor")
    if alt_models_dir:
        arms["T1P1A1"] = R.Arm("T1P1A1", candidate_team, cand_pick_model, alt_models_dir, pick_policy=cand_policy)
    return arms


def effects_from_outcomes(outcomes: dict) -> dict:
    """{arm_id: [1/0, ...]} → {"win_rates", "effects" (team / pick / pick_reference? / action?), "total"}"""
    def wr(k):
        o = outcomes.get(k) or []
        return sum(o) / len(o) if o else None

    def d(k1, k0):
        return verdict4(outcomes.get(k1) or [], outcomes.get(k0) or []).to_dict()

    effects = {"team": d("T1P0A0", "T0P0A0"), "pick": d("T1P1A0", "T1P0A0")}
    if "T0PrA0" in outcomes:
        effects["pick_reference"] = d("T0PrA0", "T0P0A0")
    if "T1P1A1" in outcomes:
        effects["action"] = d("T1P1A1", "T1P1A0")
    total = d("T1P1A0", "T0PrA0" if "T0PrA0" in outcomes else "T0P0A0")
    return {"win_rates": {k: wr(k) for k in outcomes}, "effects": effects, "total": total}


# ------------------------------------------------------------------ 実行
def ablation_grid(candidate_team: Path, reference_team: Path, cand_pick_model: Optional[str],
                  ref_pick_model: Optional[str], models_dir: Optional[str], alt_models_dir: Optional[str],
                  split_file: Path, run_dir: Path, seed: int, n: int = ABLATION_BATTLES,
                  tier: str = "selection", log=print, parallel: int = 4, ref_pick_policy: Optional[str] = None) -> dict:
    out_dir = run_dir / "evaluation" / "ablation"
    out_dir.mkdir(parents=True, exist_ok=True)
    arms = grid_arms(candidate_team, reference_team, cand_pick_model, ref_pick_model, models_dir, alt_models_dir,
                     ref_pick_policy)
    t0 = time.time()
    R.measure_round(list(arms.values()), n, 0, seed, split_file, tier, None, out_dir, "ablation", parallel=parallel)
    res = effects_from_outcomes({k: [int(x) for x in a.outcomes] for k, a in arms.items()})
    out = {"n": n, "tier": tier, "seed": seed, "win_rates": res["win_rates"], "effects": res["effects"],
           "total": res["total"], "arms": {k: a.to_dict() for k, a in arms.items()},
           "elapsed_s": round(time.time() - t0, 1)}
    (run_dir / "evaluation" / "ablation.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n",
                                                          encoding="utf-8")
    eff = res["effects"]
    log("[ablation] " + " ".join(f"{k}={v.get('mean')}" for k, v in eff.items()) + f" total={res['total'].get('mean')}")
    return out
