"""STRESS (頑健性、S12): 助言方策の揺らぎと分布外の相手で最終候補を測る。順位決定には使わない。

variants:
  action_noise 5% / 10%  (2 位の手を選ぶ確率)     pick_noise 5% / 10% (選出を乱択)
  policy_population: prev / best のチェックポイント (遵守モデルの 3 条件は 2026-10-05 に廃止: 操縦はアドバイザーが行う)
  ood: 外部取り込み構築 (STRESS 用に別生成)
感度 = WR(current 条件) − WR(variant)。候補と参照を同じ variant で測って対応差も出す。
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Optional

from champions_agent.config import BUILD_STRESS_ACTION_NOISE
from tools.team_build import racing as R
from tools.team_build.verdict import verdict4

REPO = Path(__file__).resolve().parent.parent.parent
CKPT_DIR = REPO / "champions_agent" / "train" / "checkpoints"
STRESS_BATTLES = 300      # variant ごとの戦数 (config 化候補)


def policy_population(out_dir: Path, styles: tuple = ("balance", "offense", "cycle", "stall")) -> dict:
    """prev / best のチェックポイントから、rl_bridge が読める形 (battle_policy_<style>_ema.zip) の
    models dir を作る。戻り値 {"prev": dir, "best": dir} (存在するものだけ)"""
    out = {}
    for tag, suffix in (("prev", ".zip.prev"), ("best", "_best.zip")):
        d = out_dir / f"policy_{tag}"
        made = 0
        for st in styles:
            src = CKPT_DIR / (f"battle_policy_{st}{suffix}")
            if src.exists():
                d.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, d / f"battle_policy_{st}_ema.zip")
                made += 1
        if made:
            out[tag] = str(d)
    return out


def variants(models_dir_current: Optional[str], population: dict) -> list:
    v = [{"name": "current", "extra": [], "models_dir": models_dir_current}]
    for p in BUILD_STRESS_ACTION_NOISE:
        v.append({"name": f"action_noise_{int(p * 100)}", "extra": ["--action-noise", str(p)], "models_dir": models_dir_current})
    for p in BUILD_STRESS_ACTION_NOISE:
        v.append({"name": f"pick_noise_{int(p * 100)}", "extra": ["--pick-noise", str(p)], "models_dir": models_dir_current})
    for tag, d in population.items():
        v.append({"name": f"policy_{tag}", "extra": [], "models_dir": d})
    return v


def run_stress(candidate: R.Arm, reference: R.Arm, split_file: Path, run_dir: Path, seed: int,
               n: int = STRESS_BATTLES, tier: str = "selection", log=print, parallel: int = 4) -> dict:
    out_dir = run_dir / "evaluation" / "stress"
    out_dir.mkdir(parents=True, exist_ok=True)
    pop = policy_population(run_dir / "advisors" / "population")
    rows = []
    base_wr = {}
    t0 = time.time()
    for var in variants(candidate.models_dir, pop):
        arms = []
        for base, tag in ((candidate, "cand"), (reference, "ref")):
            arms.append(R.Arm(f"{tag}_{var['name']}", base.team_file, base.selection_model,
                              var["models_dir"], list(var["extra"])))
        R.measure_round(arms, n, 0, seed, split_file, tier, None, out_dir, "stress", parallel=parallel)
        cand, ref = arms
        wr_c = sum(cand.outcomes) / len(cand.outcomes) if cand.outcomes else None
        wr_r = sum(ref.outcomes) / len(ref.outcomes) if ref.outcomes else None
        v = verdict4(cand.outcomes, ref.outcomes).to_dict()
        if var["name"] == "current":
            base_wr = {"cand": wr_c, "ref": wr_r}
        rows.append({"variant": var["name"], "wr_candidate": wr_c, "wr_reference": wr_r,
                     "sensitivity_candidate": (base_wr.get("cand") - wr_c) if (base_wr.get("cand") is not None and wr_c is not None) else None,
                     "sensitivity_reference": (base_wr.get("ref") - wr_r) if (base_wr.get("ref") is not None and wr_r is not None) else None,
                     "paired": v})
        log(f"[stress] {var['name']:16s} cand={wr_c} ref={wr_r} Δ={v.get('mean')} ({v.get('state')})")
    worst = max((r["sensitivity_candidate"] or 0.0) for r in rows) if rows else 0.0
    out = {"n_per_variant": n, "tier": tier, "seed": seed, "rows": rows,
           "worst_sensitivity_candidate": round(worst, 4), "elapsed_s": round(time.time() - t0, 1),
           "note": "順位決定には使わない。感度が大きい候補は特定方策の癖に依存している疑い"}
    (run_dir / "evaluation" / "robustness.json").write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n",
                                                            encoding="utf-8")
    return out
