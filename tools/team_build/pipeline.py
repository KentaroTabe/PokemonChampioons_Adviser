"""測定段 (S7〜S13) のオーケストレーション。run.py から呼ぶ。

S8a 全候補 (代理スコアで絞らない) を cheap adaptation (BUILD_SCREEN_ADAPT_BATTLES) してから screening racing
    (参照も同じ予算で適応、SEARCH-B、脱落は伸び代 margin 込み)
S7  生存候補 (Δ 順に max_candidates まで) の選出モデル適応 (SEARCH-A、収束まで)
S8b 候補 × 選出方策 variant (fresh / generic) × 参照 (production 選出モデル) の racing (SEARCH-B、別 seed)。
    候補ごとに variant を測定で選ぶ
S9  介入実験 (ルール mutation [+ LLM 仮説]、任意)
S10 SELECTION で contenders を比較
S11 勝者の選出モデルを SEARCH + SELECTION で再学習 (variant が fresh のとき)
S12 封印 HOLDOUT + STRESS + ablation
S13 Final Build Package (registry に candidate)

2026-09-07: S8a を「teampreview で測って代理スコア上位だけ」から上記に変更 (docs/TEAM_BUILDING_IMPLEMENTATION.md §12)。
teampreview screening で落とした候補が適応後に参照と同等だった (pick_ablation)。
"""
from __future__ import annotations

import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from champions_agent.config import (BUILD_ADAPT_MIN_BATTLES, BUILD_EQUIV_EPS, BUILD_PICK_VARIANTS,
                                    BUILD_RACE_DEFAULT_MAX, BUILD_RACE_STEPS, BUILD_SCREEN_ADAPT_BATTLES,
                                    BUILD_SCREEN_MARGIN, BUILD_SCREEN_MAX, BUILD_SCREEN_STEPS)
from tools.team_build import ablation as AB
from tools.team_build import adapt as AD
from tools.team_build import holdout as HO
from tools.team_build import racing as R
from tools.team_build import stress as ST
from tools.team_build.loss_stats import loss_stats
from tools.team_build.package import build_package
from tools.team_build.registry import Registry
from tools.team_build.verdict import DEGRADED

REPO = Path(__file__).resolve().parent.parent.parent
VARIANT_SEP = "@"
SCREEN_ADAPT_PARALLEL = 4     # cheap adaptation の同時実行数 (収集は 1 プロセス 1 戦ずつ)


def _log(run_dir: Path, msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with (run_dir / "run.log").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def pin_models() -> str:
    res = subprocess.run(["bash", "scripts/pin_models.sh"], capture_output=True, text=True, cwd=str(REPO))
    return res.stdout.strip().splitlines()[-1] if res.stdout.strip() else ""


def reference_arm(run_dir: Path, models_dir: str) -> R.Arm:
    """production の参照: 現在の my_team (config/my_team.json) + production 選出モデル
    (登録チームが production の分布外なら実助言と同じく teampreview に落ちる)"""
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


# ------------------------------------------------------------------ 純粋関数 (テスト対象)
def select_survivors(res8a: dict, max_candidates: Optional[int]) -> list:
    """screening の生存候補 (脱落していない = degraded でない) を参照との Δ の降順に並べ、max_candidates まで返す"""
    rows = []
    for a in res8a.get("arms", []):
        if a.get("eliminated_at") is not None or a.get("state") == DEGRADED:
            continue
        rows.append((a["arm_id"], (a.get("result") or {}).get("mean")))
    rows.sort(key=lambda r: -(r[1] if r[1] is not None else -1.0))
    ids = [r[0] for r in rows]
    return ids[:max_candidates] if max_candidates else ids


def variant_arm_id(candidate_id: str, variant: str) -> str:
    return f"{candidate_id}{VARIANT_SEP}{variant}"


def split_variant(arm_id: str) -> tuple:
    if VARIANT_SEP in arm_id:
        cid, v = arm_id.rsplit(VARIANT_SEP, 1)
        return cid, v
    return arm_id, "fresh"


def choose_variants(res8b: dict) -> dict:
    """候補ごとに、脱落していない variant のうち参照との Δ が最大のものを選ぶ。
    戻り値: {candidate_id: {"variant", "arm_id", "selection_model", "delta", "state"}}"""
    chosen = {}
    for a in res8b.get("arms", []):
        if a.get("eliminated_at") is not None or a.get("state") == DEGRADED:
            continue
        d = (a.get("result") or {}).get("mean")
        if d is None:
            continue
        cid, v = split_variant(a["arm_id"])
        if cid not in chosen or d > chosen[cid]["delta"]:
            chosen[cid] = {"variant": v, "arm_id": a["arm_id"], "selection_model": a.get("selection_model"),
                           "delta": d, "state": a.get("state")}
    return chosen


# ------------------------------------------------------------------ 実行
def _screen_adapt_all(arms: list, split: Path, out_dir: Path, seed: int, n_battles: int, chunk: int,
                      parallel: int, log) -> dict:
    """全 arm を同じ予算で cheap adaptation する (収集 n_battles 戦 → 1 回学習)。戻り値 {arm_id: model or None}"""
    def one(arm):
        r = AD.adapt_selection(f"{arm.arm_id}_screen", arm.team_file, split, out_dir, seed,
                               min_battles=n_battles, chunk=min(chunk, n_battles), max_battles=n_battles,
                               patience=10 ** 9, log=log, registry=None)
        return arm.arm_id, r.get("model"), r.get("stop_reason"), r.get("elapsed_s")

    out = {}
    with ThreadPoolExecutor(max_workers=max(1, min(parallel, SCREEN_ADAPT_PARALLEL))) as ex:
        for arm_id, model, stop, el in ex.map(one, arms):
            out[arm_id] = model
            log(f"[screen-adapt] {arm_id}: model={'ok' if model else 'none'} stop={stop} {el}s")
    return out


def run_measurement(run_dir: Path, seed: int, steps: tuple = BUILD_RACE_STEPS, max_battles: int = BUILD_RACE_DEFAULT_MAX,
                    adapt_min: int = BUILD_ADAPT_MIN_BATTLES, adapt_chunk: int = AD.CHUNK, adapt_max: int = AD.MAX_BATTLES,
                    stress_n: int = ST.STRESS_BATTLES, ablation_n: int = AB.ABLATION_BATTLES, parallel: int = R.PARALLEL,
                    repairs: int = 0, max_candidates: Optional[int] = None, registry: Optional[Registry] = None,
                    llm_provider=None, adapt_action: bool = False, action_steps: int = AD.ACTION_CHUNK_STEPS,
                    action_eval: int = AD.ACTION_EVAL_BATTLES, screen_adapt: int = BUILD_SCREEN_ADAPT_BATTLES,
                    screen_margin: float = BUILD_SCREEN_MARGIN, screen_steps: tuple = BUILD_SCREEN_STEPS,
                    screen_max: int = BUILD_SCREEN_MAX, variants: tuple = BUILD_PICK_VARIANTS) -> dict:
    from champions_agent.agent.selection_model import GENERAL_MODEL_PATH
    log = lambda m: _log(run_dir, m)
    split = run_dir / "opponent_families.json"
    doc = json.loads(split.read_text(encoding="utf-8"))
    models_dir = pin_models()
    log(f"S7-13 measurement start: models_dir={models_dir}")
    ref = reference_arm(run_dir, models_dir)
    # 代理スコアでは絞らない (max_candidates は S7 で収束まで適応する数)
    cands = candidate_arms(run_dir, models_dir, None)
    if not cands:
        raise SystemExit("合法な候補がありません (s06_sets.json)")
    summary = {"models_dir": models_dir, "reference": ref.to_dict(), "n_candidates": len(cands),
               "protocol": {"screen_adapt": screen_adapt, "screen_margin": screen_margin, "screen_steps": list(screen_steps),
                            "screen_max": screen_max, "variants": list(variants), "max_candidates": max_candidates}}

    # S8a-1: 全候補 + 参照を同じ予算で cheap adaptation
    log(f"S8a cheap adaptation: {len(cands)} 候補 + 参照 × {screen_adapt} 戦")
    screen_models = _screen_adapt_all([ref] + cands, split, run_dir / "advisors_screen", seed, screen_adapt,
                                      adapt_chunk, parallel, log)
    _write_stage(run_dir, "s08a_screen_models", screen_models)

    # S8a-2: screening racing (脱落は margin 込み、短い段階)
    arms8a = [R.Arm(a.arm_id, a.team_file, screen_models.get(a.arm_id), models_dir) for a in cands]
    ref8a = R.Arm("reference", ref.team_file, screen_models.get("reference"), models_dir)
    res8a = R.race(arms8a, ref8a, split, "search", seed, run_dir / "evaluation", stage="s08a_screen", fold=1,
                   steps=screen_steps, max_battles=screen_max, eps=BUILD_EQUIV_EPS + screen_margin,
                   parallel=parallel, log=log)
    all_survivors = select_survivors(res8a, None)
    survivors = all_survivors[:max_candidates] if max_candidates else all_survivors
    log(f"S8a survivors: {len(all_survivors)}/{len(cands)} (脱落 {len(cands) - len(all_survivors)})、"
        f"S7 で適応する Δ 上位 {len(survivors)}: {survivors}")
    summary["s08a_survivors"] = survivors
    summary["s08a_eliminated"] = [a["arm_id"] for a in res8a["arms"] if a["arm_id"] not in all_survivors]
    summary["s08a_capped"] = [c for c in all_survivors if c not in survivors]   # 生存したが適応枠に入らなかった候補

    # S7: 生存候補の選出モデル適応 (SEARCH-A、収束まで、チェックポイント保存)
    adapted = {}
    for a in cands:
        if a.arm_id not in survivors:
            continue
        r = AD.adapt_selection(a.arm_id, a.team_file, split, run_dir / "advisors", seed, min_battles=adapt_min,
                               chunk=adapt_chunk, max_battles=adapt_max, log=log, registry=registry,
                               keep_checkpoints=True)
        adapted[a.arm_id] = r
    _write_stage(run_dir, "s07_adapt", adapted)

    # S8b: 候補 × variant (fresh / generic) × 参照 (production)。variant は候補ごとに測定で選ぶ
    arms8b = []
    for a in cands:
        if a.arm_id not in adapted:
            continue
        fresh = adapted[a.arm_id].get("model")
        for v in variants:
            if v == "fresh":
                if not fresh:
                    continue
                model = fresh
            elif v == "generic":
                if not Path(GENERAL_MODEL_PATH).exists():
                    continue
                model = str(GENERAL_MODEL_PATH)
            else:
                continue
            arms8b.append(R.Arm(variant_arm_id(a.arm_id, v), a.team_file, model, models_dir))
    res8b = R.race(arms8b, R.Arm("reference", ref.team_file, ref.selection_model, models_dir), split, "search", seed + 1,
                   run_dir / "evaluation", stage="s08b_adapted", fold=1, steps=steps, max_battles=max_battles,
                   parallel=parallel, log=log)
    chosen = choose_variants(res8b)
    contenders = list(chosen)
    summary["s08b_variants"] = chosen
    summary["s08b_contenders"] = contenders
    log(f"S8b contenders: " + ", ".join(f"{c}={chosen[c]['variant']}({chosen[c]['delta']:+.3f})" for c in contenders))

    # S9: 介入実験 (ルール mutation、任意)
    if repairs > 0 and contenders:
        log("S9: 介入実験は run.py の --repairs で有効化。この版はルール mutation の記録のみ (検証は次版)")
        best_id = max(contenders, key=lambda cid: chosen[cid]["delta"])
        recs = []
        bl = run_dir / "evaluation" / "battles" / f"s08b_adapted_{chosen[best_id]['arm_id']}.jsonl"
        if bl.exists():
            recs = [json.loads(l) for l in bl.read_text(encoding="utf-8").splitlines() if l.strip()]
        st = loss_stats(recs)
        _write_stage(run_dir, "s09_loss_stats", st)

    # S10: SELECTION で比較 (候補ごとに選んだ variant で)
    team_of = {a.arm_id: a.team_file for a in cands}
    arms10 = [R.Arm(cid, team_of[cid], chosen[cid]["selection_model"], models_dir) for cid in contenders]
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
    summary["winner_variant"] = chosen[winner]["variant"]
    log(f"S10 winner: {winner} variant={chosen[winner]['variant']} (finalists {finalists})")

    # S11: 勝者の選出モデルを SEARCH + SELECTION で再学習 (variant が fresh のとき。generic なら汎用基底のまま)
    win_arm = next(a for a in cands if a.arm_id == winner)
    final_model = chosen[winner]["selection_model"]
    if chosen[winner]["variant"] == "fresh":
        r11 = AD.adapt_selection(f"{winner}_final", win_arm.team_file, split, run_dir / "advisors", seed + 3,
                                 min_battles=adapt_min, chunk=adapt_chunk, max_battles=adapt_max, log=log,
                                 registry=registry, tiers=(("search", None), ("selection", None)))
        final_model = r11.get("model") or final_model
        _write_stage(run_dir, "s11_final_adapt", r11)
    else:
        log(f"S11: 勝者の variant が {chosen[winner]['variant']} のため再学習は省略")

    # S11b: 行動方策 adapter (任意、full プロファイル既定): 勝者チーム固定で短く微調整し、基底との対応差で採否
    final_models_dir = models_dir
    if adapt_action:
        ra = AD.adapt_action(winner, win_arm.team_file, models_dir, run_dir / "advisors", split, seed + 7,
                             chunk_steps=action_steps, eval_battles=action_eval, log=log, registry=registry)
        final_models_dir = ra.get("models_dir") or models_dir
        summary["action_adapter"] = {k: ra.get(k) for k in ("use_adapted", "reason", "elapsed_s", "artifact_id")}
        _write_stage(run_dir, "s11b_action_adapt", ra)
        log(f"S11b action adapter: use_adapted={ra.get('use_adapted')} ({ra.get('reason')})")

    # S12: 封印 HOLDOUT + STRESS + ablation
    final_arm = R.Arm(winner, win_arm.team_file, final_model, final_models_dir)
    ref12 = R.Arm("reference", ref.team_file, ref.selection_model, models_dir)
    hold = HO.final_holdout(final_arm, ref12, split, doc["sealed_id"], run_dir, seed + 4,
                            candidate_key=f"{winner}:{Path(final_model or '').name}", steps=steps,
                            max_battles=max_battles, log=log, parallel=min(2, parallel))
    summary["holdout"] = hold
    rob = ST.run_stress(final_arm, ref12, split, run_dir, seed + 5, n=stress_n, log=log, parallel=parallel)
    summary["robustness_worst"] = rob.get("worst_sensitivity_candidate")
    pop = ST.policy_population(run_dir / "advisors" / "population")
    # ablation の A1: adapter を採用したらそれ (action 効果 = adapter − 基底)、無ければ前世代のチェックポイント
    alt_dir = final_models_dir if final_models_dir != models_dir else pop.get("prev")
    abl = AB.ablation_grid(win_arm.team_file, ref.team_file, final_model, ref.selection_model, models_dir,
                           alt_dir, split, run_dir, seed + 6, n=ablation_n, log=log, parallel=parallel)
    summary["ablation"] = abl.get("effects")

    # S13: Package
    sets = json.loads((run_dir / "s06_sets.json").read_text(encoding="utf-8"))
    species = next((r["members"] for r in sets if r.get("candidate_id") == winner), [])
    summary["result"] = hold.get("verdict")
    _write_stage(run_dir, "summary", summary)
    pkg = build_package(run_dir, winner, win_arm.team_file, Path(final_model) if final_model else None, species,
                        registry=None, extra_manifest={"models_dir": final_models_dir, "base_models_dir": models_dir,
                                                       "seed": seed, "pick_variant": chosen[winner]["variant"]})
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
