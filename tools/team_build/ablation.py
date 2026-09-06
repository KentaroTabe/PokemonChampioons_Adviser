"""固定部品との交差評価 (自動 ablation): 勝率変化を Team / Pick / Action / Interaction に分解する。

  (T0,P0,A0) 参照   (T1,P0,A0) 候補チーム + production 選出   (T1,P1,A0) + 候補専用選出   (T1,P1,A1) + 別の行動方策
  Team   = WR(T1,P0,A0) − WR(T0,P0,A0)
  Pick   = WR(T1,P1,A0) − WR(T1,P0,A0)
  Action = WR(T1,P1,A1) − WR(T1,P1,A0)   (A1 が指定されたときだけ)
同一相手列 (SELECTION 階層) で対応比較する。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from tools.team_build import racing as R
from tools.team_build.verdict import verdict4

ABLATION_BATTLES = 300


def ablation_grid(candidate_team: Path, reference_team: Path, cand_pick_model: Optional[str],
                  prod_pick_model: Optional[str], models_dir: Optional[str], alt_models_dir: Optional[str],
                  split_file: Path, run_dir: Path, seed: int, n: int = ABLATION_BATTLES,
                  tier: str = "selection", log=print, parallel: int = 4) -> dict:
    out_dir = run_dir / "evaluation" / "ablation"
    out_dir.mkdir(parents=True, exist_ok=True)
    arms = {
        "T0P0A0": R.Arm("T0P0A0", reference_team, prod_pick_model, models_dir),
        "T1P0A0": R.Arm("T1P0A0", candidate_team, prod_pick_model, models_dir),
        "T1P1A0": R.Arm("T1P1A0", candidate_team, cand_pick_model, models_dir),
    }
    if alt_models_dir:
        arms["T1P1A1"] = R.Arm("T1P1A1", candidate_team, cand_pick_model, alt_models_dir)
    t0 = time.time()
    R.measure_round(list(arms.values()), n, 0, seed, split_file, tier, None, out_dir, "ablation", parallel=parallel)

    def wr(k):
        a = arms[k]
        return sum(a.outcomes) / len(a.outcomes) if a.outcomes else None

    def d(k1, k0):
        return verdict4(arms[k1].outcomes, arms[k0].outcomes).to_dict()

    table = {"team": d("T1P0A0", "T0P0A0"), "pick": d("T1P1A0", "T1P0A0")}
    if "T1P1A1" in arms:
        table["action"] = d("T1P1A1", "T1P1A0")
    total = d("T1P1A0", "T0P0A0")
    inter = None
    if total.get("mean") is not None and table["team"].get("mean") is not None and table["pick"].get("mean") is not None:
        inter = round(total["mean"] - table["team"]["mean"] - table["pick"]["mean"], 4)
    out = {"n": n, "tier": tier, "seed": seed, "win_rates": {k: wr(k) for k in arms},
           "effects": table, "total": total, "interaction": inter, "elapsed_s": round(time.time() - t0, 1)}
    (run_dir / "evaluation" / "ablation.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n",
                                                          encoding="utf-8")
    log(f"[ablation] team={table['team'].get('mean')} pick={table['pick'].get('mean')} "
        f"action={table.get('action', {}).get('mean')} interaction={inter}")
    return out
