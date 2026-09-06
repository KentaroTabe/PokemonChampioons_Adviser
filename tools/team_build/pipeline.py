"""測定段 (S7〜S13) のオーケストレーション。run.py から呼ぶ。

S8a スクリーニング racing (全候補、選出は相性順で公平に、SEARCH-B)
S7  生存候補の選出モデル適応 (SEARCH-A)
S8b 適応済み候補 × 参照 (production 選出モデル) の racing (SEARCH-B、別 seed)
S9  介入実験 (ルール mutation [+ LLM 仮説]、任意)
S10 SELECTION で contenders を比較
S11 勝者の選出モデルを SEARCH + SELECTION で再学習
S12 封印 HOLDOUT + STRESS + ablation
S13 Final Build Package (registry に candidate)
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Optional

from champions_agent.config import (BUILD_ADAPT_MIN_BATTLES, BUILD_RACE_DEFAULT_MAX, BUILD_RACE_STEPS)
from tools.team_build import ablation as AB
from tools.team_build import adapt as AD
from tools.team_build import holdout as HO
from tools.team_build import racing as R
from tools.team_build import stress as ST
from tools.team_build.loss_stats import loss_stats
from tools.team_build.package import build_package
from tools.team_build.registry import Registry

REPO = Path(__file__).resolve().parent.parent.parent


def _log(run_dir: Path, msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with (run_dir / "run.log").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def pin_models() -> str:
    res = subprocess.run(["bash", "scripts/pin_models.sh"], capture_output=True, text=True, cwd=str(REPO))
    return res.stdout.strip().splitlines()[-1] if res.stdout.strip() else ""


def reference_arm(run_dir: Path, models_dir: str) -> R.Arm:
    """production の参照: 現在の my_team (config/my_team.json) + production 選出モデル"""
    from tools.evaluate_team import build_myteam_text
    from champions_agent.agent.selection_model import MODEL_PATH
    text = build_myteam_text()
    p = run_dir / "reference_team.txt"
    p.write_text(text, encoding="utf-8")
    sel = str(MODEL_PATH) if Path(MODEL_PATH).exists() else None
    return R.Arm("reference", p, sel, models_dir)


def candidate_arms(run_dir: Path, models_dir: str, limit: Optional[int] = None) -> list:
    sets = json.loads((run_dir / "s06_sets.json").read_text(encoding="utf-8"))
    arms = []
    for r in sets:
        if not r.get("ok"):
            continue
        arms.append(R.Arm(r["candidate_id"], run_dir / "s06_sets" / f"{r['candidate_id']}.txt", None, models_dir))
    return arms[:limit] if limit else arms


def _write_stage(run_dir: Path, name: str, obj) -> None:
    (run_dir / "evaluation").mkdir(parents=True, exist_ok=True)
    (run_dir / "evaluation" / f"{name}.json").write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n",
                                                         encoding="utf-8")


def run_measurement(run_dir: Path, seed: int, steps: tuple = BUILD_RACE_STEPS, max_battles: int = BUILD_RACE_DEFAULT_MAX,
                    adapt_min: int = BUILD_ADAPT_MIN_BATTLES, adapt_chunk: int = AD.CHUNK, adapt_max: int = AD.MAX_BATTLES,
                    stress_n: int = ST.STRESS_BATTLES, ablation_n: int = AB.ABLATION_BATTLES, parallel: int = R.PARALLEL,
                    repairs: int = 0, max_candidates: Optional[int] = None, registry: Optional[Registry] = None,
                    llm_provider=None) -> dict:
    log = lambda m: _log(run_dir, m)
    split = run_dir / "opponent_families.json"
    doc = json.loads(split.read_text(encoding="utf-8"))
    models_dir = pin_models()
    log(f"S7-13 measurement start: models_dir={models_dir}")
    ref = reference_arm(run_dir, models_dir)
    cands = candidate_arms(run_dir, models_dir, max_candidates)
    if not cands:
        raise SystemExit("合法な候補がありません (s06_sets.json)")
    summary = {"models_dir": models_dir, "reference": ref.to_dict(), "n_candidates": len(cands)}

    # S8a: スクリーニング (選出は全員 teampreview で公平に)
    res8a = R.race(cands, R.Arm("reference", ref.team_file, None, models_dir), split, "search", seed, run_dir / "evaluation",
                   stage="s08a_screen", fold=1, steps=steps, max_battles=max_battles, parallel=parallel, log=log,
                   pick_policy="teampreview")
    survivors = R.contenders(res8a)
    log(f"S8a survivors: {survivors}")
    summary["s08a_survivors"] = survivors

    # S7: 生存候補の選出モデル適応 (SEARCH-A)
    adapted = {}
    for a in cands:
        if a.arm_id not in survivors:
            continue
        r = AD.adapt_selection(a.arm_id, a.team_file, split, run_dir / "advisors", seed, min_battles=adapt_min,
                               chunk=adapt_chunk, max_battles=adapt_max, log=log, registry=registry)
        adapted[a.arm_id] = r
    _write_stage(run_dir, "s07_adapt", adapted)

    # S8b: 適応済み候補 × 参照 (SEARCH-B、別 seed)
    arms8b = [R.Arm(a.arm_id, a.team_file, adapted[a.arm_id].get("model"), models_dir) for a in cands if a.arm_id in adapted]
    res8b = R.race(arms8b, R.Arm("reference", ref.team_file, ref.selection_model, models_dir), split, "search", seed + 1,
                   run_dir / "evaluation", stage="s08b_adapted", fold=1, steps=steps, max_battles=max_battles,
                   parallel=parallel, log=log)
    contenders = R.contenders(res8b)
    summary["s08b_contenders"] = contenders

    # S9: 介入実験 (ルール mutation、任意)
    if repairs > 0 and contenders:
        log("S9: 介入実験は run.py の --repairs で有効化。この版はルール mutation の記録のみ (検証は次版)")
        best_id = max(contenders, key=lambda cid: next(a["result"]["mean"] for a in res8b["arms"] if a["arm_id"] == cid) or -1)
        recs = []
        bl = run_dir / "evaluation" / "battles" / f"s08b_adapted_{best_id}.jsonl"
        if bl.exists():
            recs = [json.loads(l) for l in bl.read_text(encoding="utf-8").splitlines() if l.strip()]
        st = loss_stats(recs)
        _write_stage(run_dir, "s09_loss_stats", st)

    # S10: SELECTION で比較
    arms10 = [a for a in arms8b if a.arm_id in contenders]
    for a in arms10:
        a.outcomes, a.n_done, a.history, a.state, a.result, a.eliminated_at = [], 0, [], "uncertain", None, None
    ref10 = R.Arm("reference", ref.team_file, ref.selection_model, models_dir)
    res10 = R.race(arms10, ref10, split, "selection", seed + 2, run_dir / "evaluation", stage="s10",
                   steps=steps, max_battles=max_battles, parallel=parallel, log=log)
    finalists = R.contenders(res10)
    if not finalists:
        log("S10: contenders が残らなかった (全候補が参照に劣る)。run は失敗")
        summary["result"] = "no_contender"
        _write_stage(run_dir, "summary", summary)
        return summary
    winner = max(finalists, key=lambda cid: next((a["result"] or {}).get("mean") or -1 for a in res10["arms"] if a["arm_id"] == cid))
    summary["winner"] = winner
    log(f"S10 winner: {winner} (finalists {finalists})")

    # S11: 勝者の選出モデルを SEARCH + SELECTION で再学習
    win_arm = next(a for a in cands if a.arm_id == winner)
    r11 = AD.adapt_selection(f"{winner}_final", win_arm.team_file, split, run_dir / "advisors", seed + 3,
                             min_battles=adapt_min, chunk=adapt_chunk, max_battles=adapt_max, log=log,
                             registry=registry, tiers=(("search", None), ("selection", None)))
    final_model = r11.get("model") or adapted[winner].get("model")
    _write_stage(run_dir, "s11_final_adapt", r11)

    # S12: 封印 HOLDOUT + STRESS + ablation
    final_arm = R.Arm(winner, win_arm.team_file, final_model, models_dir)
    ref12 = R.Arm("reference", ref.team_file, ref.selection_model, models_dir)
    hold = HO.final_holdout(final_arm, ref12, split, doc["sealed_id"], run_dir, seed + 4,
                            candidate_key=f"{winner}:{Path(final_model or '').name}", steps=steps,
                            max_battles=max_battles, log=log, parallel=min(2, parallel))
    summary["holdout"] = hold
    rob = ST.run_stress(final_arm, ref12, split, run_dir, seed + 5, n=stress_n, log=log, parallel=parallel)
    summary["robustness_worst"] = rob.get("worst_sensitivity_candidate")
    pop = ST.policy_population(run_dir / "advisors" / "population")
    abl = AB.ablation_grid(win_arm.team_file, ref.team_file, final_model, ref.selection_model, models_dir,
                           pop.get("prev"), split, run_dir, seed + 6, n=ablation_n, log=log, parallel=parallel)
    summary["ablation"] = abl.get("effects")

    # S13: Package
    sets = json.loads((run_dir / "s06_sets.json").read_text(encoding="utf-8"))
    species = next((r["members"] for r in sets if r.get("candidate_id") == winner), [])
    summary["result"] = hold.get("verdict")
    _write_stage(run_dir, "summary", summary)
    pkg = build_package(run_dir, winner, win_arm.team_file, Path(final_model) if final_model else None, species,
                        registry=None, extra_manifest={"models_dir": models_dir, "seed": seed})
    # 記事 (表示専用): LLM があれば Sonnet、無ければテンプレート。registry 登録は記事を書いてから (Package の内容を固定)
    try:
        from tools.team_build.report import write_report
        write_report(run_dir, winner, provider=llm_provider)
    except Exception as e:
        log(f"S13 report error: {e!r}")
    if registry is not None:
        row = registry.register("package", run_dir / "final",
                                meta={"candidate_id": winner, "species": list(species), "holdout": hold},
                                run_id=run_dir.name, status="candidate")
        pkg["artifact_id"] = row["id"]
    summary["package"] = pkg
    _write_stage(run_dir, "summary", summary)
    log(f"S13 package: {pkg.get('artifact_id')} verdict={hold.get('verdict')}")
    return summary
