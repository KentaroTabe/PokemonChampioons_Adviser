"""測定段 (S7〜S13) のオーケストレーション。run.py から呼ぶ。

S8a 全候補 (代理スコアで絞らない) を cheap adaptation (BUILD_SCREEN_ADAPT_BATTLES) してから Team × PickVariant で
    screening racing (variant = teampreview / generic / cheap、チームの実力 = variant の最善。参照も同じ variant の最善。
    SEARCH fold B、脱落は伸び代 margin 込み)
S7  生存チーム (最善 variant の Δ 順に max_candidates まで) の選出モデル適応 (SEARCH fold A、収束まで、checkpoint 保存)
    → checkpoint は独立 fold V の実測勝率で選ぶ (val_mse では選ばない)
S8b チーム × variant (teampreview / generic / fresh) × 参照の racing (fold B、別 seed)。チームごとに variant を測定で選ぶ
S9  介入実験 (ルール mutation [+ LLM 仮説]、任意)
S10 SELECTION で contenders を比較
S11 (任意、既定 off) 勝者の選出モデルを SEARCH + SELECTION で再学習。checkpoint 選択の検証 fold が学習に入るため
    既定では S7 の検証済み checkpoint をそのまま最終モデルにする
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

from champions_agent.config import (BUILD_ADAPT_MIN_BATTLES, BUILD_ADAPT_VALIDATE_MAX_CKPTS, BUILD_ADAPT_VALIDATE_N,
                                    BUILD_EQUIV_EPS, BUILD_FOLD_EVAL, BUILD_FOLD_VALIDATE, BUILD_PICK_VARIANTS,
                                    BUILD_RACE_DEFAULT_MAX, BUILD_RACE_STEPS, BUILD_SCREEN_ADAPT_BATTLES,
                                    BUILD_SCREEN_MARGIN, BUILD_SCREEN_MAX, BUILD_SCREEN_STEPS, BUILD_SCREEN_VARIANTS)
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
STOP_POINTS = ("s08a", "s08b")


def _log(run_dir: Path, msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with (run_dir / "run.log").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def pin_models() -> str:
    res = subprocess.run(["bash", "scripts/pin_models.sh"], capture_output=True, text=True, cwd=str(REPO))
    return res.stdout.strip().splitlines()[-1] if res.stdout.strip() else ""


def reference_arm(run_dir: Path, models_dir: str) -> R.Arm:
    """参照 = 現在の my_team (config/my_team.json)。選出方策は S8a で variant の最善を測って決める"""
    from tools.evaluate_team import build_myteam_text
    text = build_myteam_text()
    p = run_dir / "reference_team.txt"
    p.write_text(text, encoding="utf-8")
    return R.Arm("reference", p, None, models_dir)


def candidate_arms(run_dir: Path, models_dir: str, limit: Optional[int] = None, ids: Optional[list] = None) -> list:
    sets = json.loads((run_dir / "s06_sets.json").read_text(encoding="utf-8"))
    arms = []
    for r in sets:
        if not r.get("ok"):
            continue
        if ids is not None and r["candidate_id"] not in ids:
            continue
        arms.append(R.Arm(r["candidate_id"], run_dir / "s06_sets" / f"{r['candidate_id']}.txt", None, models_dir))
    return arms[:limit] if limit else arms


def _write_stage(run_dir: Path, name: str, obj) -> None:
    (run_dir / "evaluation").mkdir(parents=True, exist_ok=True)
    (run_dir / "evaluation" / f"{name}.json").write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n",
                                                         encoding="utf-8")


# ------------------------------------------------------------------ 純粋関数 (テスト対象)
def select_survivors(res8a: dict, max_candidates: Optional[int]) -> list:
    """(variant 無しの racing 結果向け) 脱落していない腕を参照との Δ の降順に並べ、max_candidates まで返す"""
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


def choose_variants(res: dict) -> dict:
    """チームごとに、脱落していない variant のうち参照との Δ が最大のものを選ぶ (Team × PickVariant の「最善」)。
    戻り値: {team_id: {"variant", "arm_id", "selection_model", "pick_policy", "delta", "se", "state", "n"}}"""
    chosen = {}
    for a in res.get("arms", []):
        if a.get("eliminated_at") is not None or a.get("state") == DEGRADED:
            continue
        r = a.get("result") or {}
        d = r.get("mean")
        if d is None:
            continue
        cid, v = split_variant(a["arm_id"])
        if cid not in chosen or d > chosen[cid]["delta"]:
            chosen[cid] = {"variant": v, "arm_id": a["arm_id"], "selection_model": a.get("selection_model"),
                           "pick_policy": a.get("pick_policy") or ("teampreview" if v == "teampreview" else "advisor"),
                           "delta": d, "se": r.get("se"), "state": a.get("state"), "n": a.get("n_done")}
    return chosen


def team_survivors(chosen: dict, max_candidates: Optional[int]) -> list:
    """choose_variants の結果を Δ の降順に並べ、max_candidates まで返す"""
    ids = sorted(chosen, key=lambda c: -chosen[c]["delta"])
    return ids[:max_candidates] if max_candidates else ids


def best_by_win_rate(rows: dict, order: tuple) -> Optional[str]:
    """{variant: win_rate} から最大のもの (同率は order の先頭寄り)"""
    best, best_wr = None, None
    for v in order:
        wr = rows.get(v)
        if wr is None:
            continue
        if best_wr is None or wr > best_wr:
            best, best_wr = v, wr
    return best


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


def _variant_arm(base: R.Arm, variant: str, models: dict, generic_path: Optional[str]) -> Optional[R.Arm]:
    """base (チーム) の選出方策 variant の腕。使えない variant は None"""
    if variant == "teampreview":
        return R.Arm(variant_arm_id(base.arm_id, variant), base.team_file, None, base.models_dir, pick_policy="teampreview")
    if variant == "generic":
        if not generic_path:
            return None
        return R.Arm(variant_arm_id(base.arm_id, variant), base.team_file, generic_path, base.models_dir, pick_policy="advisor")
    model = models.get(base.arm_id)
    if not model:
        return None
    return R.Arm(variant_arm_id(base.arm_id, variant), base.team_file, model, base.models_dir, pick_policy="advisor")


def run_measurement(run_dir: Path, seed: int, steps: tuple = BUILD_RACE_STEPS, max_battles: int = BUILD_RACE_DEFAULT_MAX,
                    adapt_min: int = BUILD_ADAPT_MIN_BATTLES, adapt_chunk: int = AD.CHUNK, adapt_max: int = AD.MAX_BATTLES,
                    stress_n: int = ST.STRESS_BATTLES, ablation_n: int = AB.ABLATION_BATTLES, parallel: int = R.PARALLEL,
                    repairs: int = 0, max_candidates: Optional[int] = None, registry: Optional[Registry] = None,
                    llm_provider=None, adapt_action: bool = False, action_steps: int = AD.ACTION_CHUNK_STEPS,
                    action_eval: int = AD.ACTION_EVAL_BATTLES, screen_adapt: int = BUILD_SCREEN_ADAPT_BATTLES,
                    screen_margin: float = BUILD_SCREEN_MARGIN, screen_steps: tuple = BUILD_SCREEN_STEPS,
                    screen_max: int = BUILD_SCREEN_MAX, screen_variants: tuple = BUILD_SCREEN_VARIANTS,
                    variants: tuple = BUILD_PICK_VARIANTS, candidate_ids: Optional[list] = None,
                    stop_after: Optional[str] = None, s08b_seed_offset: int = 1, s11: bool = False,
                    validate_n: int = BUILD_ADAPT_VALIDATE_N, validate_max: int = BUILD_ADAPT_VALIDATE_MAX_CKPTS) -> dict:
    from champions_agent.agent.selection_model import GENERAL_MODEL_PATH
    log = lambda m: _log(run_dir, m)
    split = run_dir / "opponent_families.json"
    doc = json.loads(split.read_text(encoding="utf-8"))
    n_folds = len(doc.get("search_folds") or [])
    if n_folds <= max(BUILD_FOLD_EVAL, BUILD_FOLD_VALIDATE):
        raise SystemExit(f"opponent_families.json の SEARCH fold が {n_folds} 個で足りない (評価 {BUILD_FOLD_EVAL} / "
                         f"検証 {BUILD_FOLD_VALIDATE})。探索段を新しい seed でやり直す")
    models_dir = pin_models()
    generic = str(GENERAL_MODEL_PATH) if Path(GENERAL_MODEL_PATH).exists() else None
    log(f"S7-13 measurement start: models_dir={models_dir} generic={'ok' if generic else 'none'}")
    ref = reference_arm(run_dir, models_dir)
    # 代理スコアでは絞らない (max_candidates は S7 で収束まで適応するチーム数)
    cands = candidate_arms(run_dir, models_dir, None, ids=candidate_ids)
    if not cands:
        raise SystemExit("合法な候補がありません (s06_sets.json)")
    summary = {"models_dir": models_dir, "reference": ref.to_dict(), "n_candidates": len(cands),
               "candidate_ids": [a.arm_id for a in cands],
               "protocol": {"screen_adapt": screen_adapt, "screen_margin": screen_margin, "screen_steps": list(screen_steps),
                            "screen_max": screen_max, "screen_variants": list(screen_variants),
                            "variants": list(variants), "max_candidates": max_candidates,
                            "fold_eval": BUILD_FOLD_EVAL, "fold_validate": BUILD_FOLD_VALIDATE,
                            "validate_n": validate_n, "validate_max_ckpts": validate_max,
                            "s08b_seed_offset": s08b_seed_offset, "s11": s11, "stop_after": stop_after}}
    eval_dir = run_dir / "evaluation"

    # S8a-1: 全候補 + 参照を同じ予算で cheap adaptation
    log(f"S8a cheap adaptation: {len(cands)} 候補 + 参照 × {screen_adapt} 戦")
    screen_models = _screen_adapt_all([ref] + cands, split, run_dir / "advisors_screen", seed, screen_adapt,
                                      adapt_chunk, parallel, log)
    _write_stage(run_dir, "s08a_screen_models", screen_models)

    # S8a-2: 参照の variant の最善 (同一相手列、screen_max 戦)
    ref_variants = [a for a in (_variant_arm(ref, v, screen_models, generic) for v in screen_variants) if a]
    R.measure_round(ref_variants, screen_max, 0, seed, split, "search", BUILD_FOLD_EVAL, eval_dir, "s08a_reference",
                    parallel=parallel)
    ref_wr = {split_variant(a.arm_id)[1]: (sum(a.outcomes) / len(a.outcomes) if a.outcomes else None) for a in ref_variants}
    ref_variant = best_by_win_rate(ref_wr, screen_variants) or "teampreview"
    ref_best = next(a for a in ref_variants if split_variant(a.arm_id)[1] == ref_variant)
    summary["reference_variant"] = {"variant": ref_variant, "win_rates": ref_wr, "selection_model": ref_best.selection_model,
                                    "pick_policy": ref_best.pick_policy}
    log(f"S8a reference variant: {ref_variant} " + " ".join(f"{k}={v}" for k, v in ref_wr.items()))

    def ref_arm(arm_id: str = "reference") -> R.Arm:
        return R.Arm(arm_id, ref.team_file, ref_best.selection_model, models_dir, pick_policy=ref_best.pick_policy)

    # S8a-3: screening racing (チーム × variant、脱落は margin 込み、短い段階)
    arms8a = [a for c in cands for a in (_variant_arm(c, v, screen_models, generic) for v in screen_variants) if a]
    res8a = R.race(arms8a, ref_arm(), split, "search", seed, eval_dir, stage="s08a_screen", fold=BUILD_FOLD_EVAL,
                   steps=screen_steps, max_battles=screen_max, eps=BUILD_EQUIV_EPS + screen_margin,
                   parallel=parallel, log=log)
    chosen8a = choose_variants(res8a)
    all_survivors = team_survivors(chosen8a, None)
    survivors = all_survivors[:max_candidates] if max_candidates else all_survivors
    log(f"S8a survivors: {len(all_survivors)}/{len(cands)} (脱落 {len(cands) - len(all_survivors)})、"
        f"S7 で適応する Δ 上位 {len(survivors)}: " +
        ", ".join(f"{c}={chosen8a[c]['variant']}({chosen8a[c]['delta']:+.3f})" for c in survivors))
    summary["s08a_variants"] = chosen8a
    summary["s08a_survivors"] = survivors
    summary["s08a_eliminated"] = [a.arm_id for a in cands if a.arm_id not in chosen8a]
    summary["s08a_capped"] = [c for c in all_survivors if c not in survivors]
    if stop_after == "s08a":
        summary["result"] = "stopped_after_s08a"
        _write_stage(run_dir, "summary", summary)
        return summary

    # S7: 生存チームの選出モデル適応 (fold A、収束まで、checkpoint 保存) → 独立 fold V の実測で checkpoint を選ぶ
    adapted = {}
    for a in cands:
        if a.arm_id not in survivors:
            continue
        r = AD.adapt_selection(a.arm_id, a.team_file, split, run_dir / "advisors", seed, min_battles=adapt_min,
                               chunk=adapt_chunk, max_battles=adapt_max, log=log, registry=registry,
                               keep_checkpoints=True)
        ckpts = AD.checkpoints_from_history(r.get("history"))
        if ckpts:
            sel = AD.select_checkpoint(a.arm_id, a.team_file, ckpts, split, seed + 8, models_dir,
                                       run_dir / "advisors" / a.arm_id / "validate", parallel=parallel, log=log,
                                       fold=BUILD_FOLD_VALIDATE, n=validate_n, max_ckpts=validate_max)
            r["validated"] = sel
            if sel.get("chosen"):
                r["model_last"] = r.get("model")
                r["model"] = sel["chosen"]
                if registry is not None and sel["chosen"] != r.get("model_last"):
                    try:
                        row = registry.register("selection_model", Path(sel["chosen"]),
                                                meta={"candidate_id": a.arm_id, "n_battles": sel.get("chosen_n"),
                                                      "validated": True, "fold": BUILD_FOLD_VALIDATE},
                                                run_id=run_dir.name, status="candidate")
                        r["artifact_id_validated"] = row["id"]
                    except Exception as e:
                        r["registry_error_validated"] = repr(e)
        adapted[a.arm_id] = r
    _write_stage(run_dir, "s07_adapt", adapted)

    # S8b: チーム × variant (teampreview / generic / fresh) × 参照 (variant の最善)。variant はチームごとに測定で選ぶ
    fresh_models = {cid: r.get("model") for cid, r in adapted.items()}
    arms8b = []
    for a in cands:
        if a.arm_id not in adapted:
            continue
        for v in variants:
            arm = _variant_arm(a, v, fresh_models, generic)
            if arm:
                arms8b.append(arm)
    res8b = R.race(arms8b, ref_arm(), split, "search", seed + s08b_seed_offset, eval_dir, stage="s08b_adapted",
                   fold=BUILD_FOLD_EVAL, steps=steps, max_battles=max_battles, parallel=parallel, log=log)
    chosen = choose_variants(res8b)
    contenders = team_survivors(chosen, None)
    summary["s08b_variants"] = chosen
    summary["s08b_contenders"] = contenders
    log("S8b contenders: " + ", ".join(f"{c}={chosen[c]['variant']}({chosen[c]['delta']:+.3f})" for c in contenders))
    if stop_after == "s08b":
        summary["result"] = "stopped_after_s08b"
        _write_stage(run_dir, "summary", summary)
        return summary

    # S9: 介入実験 (ルール mutation、任意)
    if repairs > 0 and contenders:
        log("S9: 介入実験は run.py の --repairs で有効化。この版はルール mutation の記録のみ (検証は次版)")
        best_id = max(contenders, key=lambda cid: chosen[cid]["delta"])
        recs = []
        bl = eval_dir / "battles" / f"s08b_adapted_{chosen[best_id]['arm_id']}.jsonl"
        if bl.exists():
            recs = [json.loads(l) for l in bl.read_text(encoding="utf-8").splitlines() if l.strip()]
        st = loss_stats(recs)
        _write_stage(run_dir, "s09_loss_stats", st)

    # S10: SELECTION で比較 (チームごとに選んだ variant で)
    team_of = {a.arm_id: a.team_file for a in cands}
    arms10 = [R.Arm(cid, team_of[cid], chosen[cid]["selection_model"], models_dir, pick_policy=chosen[cid]["pick_policy"])
              for cid in contenders]
    res10 = R.race(arms10, ref_arm(), split, "selection", seed + 2, eval_dir, stage="s10",
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

    # S11 (任意): 勝者の選出モデルを SEARCH + SELECTION で再学習 (variant が fresh のとき)。既定 off
    win_arm = next(a for a in cands if a.arm_id == winner)
    final_model = chosen[winner]["selection_model"]
    final_pick = chosen[winner]["pick_policy"]
    if s11 and chosen[winner]["variant"] == "fresh":
        r11 = AD.adapt_selection(f"{winner}_final", win_arm.team_file, split, run_dir / "advisors", seed + 3,
                                 min_battles=adapt_min, chunk=adapt_chunk, max_battles=adapt_max, log=log,
                                 registry=registry, tiers=(("search", None), ("selection", None)))
        final_model = r11.get("model") or final_model
        _write_stage(run_dir, "s11_final_adapt", r11)
    else:
        log(f"S11: 省略 (s11={s11}, variant={chosen[winner]['variant']}) → S7 の検証済みモデルを最終モデルにする")

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
    final_arm = R.Arm(winner, win_arm.team_file, final_model, final_models_dir, pick_policy=final_pick)
    hold = HO.final_holdout(final_arm, ref_arm(), split, doc["sealed_id"], run_dir, seed + 4,
                            candidate_key=f"{winner}:{Path(final_model or '').name}", steps=steps,
                            max_battles=max_battles, log=log, parallel=min(2, parallel))
    summary["holdout"] = hold
    rob = ST.run_stress(final_arm, ref_arm(), split, run_dir, seed + 5, n=stress_n, log=log, parallel=parallel)
    summary["robustness_worst"] = rob.get("worst_sensitivity_candidate")
    pop = ST.policy_population(run_dir / "advisors" / "population")
    # ablation の A1: adapter を採用したらそれ (action 効果 = adapter − 基底)、無ければ前世代のチェックポイント
    alt_dir = final_models_dir if final_models_dir != models_dir else pop.get("prev")
    abl = AB.ablation_grid(win_arm.team_file, ref.team_file, final_model, ref_best.selection_model, models_dir,
                           alt_dir, split, run_dir, seed + 6, n=ablation_n, log=log, parallel=parallel)
    summary["ablation"] = abl.get("effects")

    # S13: Package
    sets = json.loads((run_dir / "s06_sets.json").read_text(encoding="utf-8"))
    species = next((r["members"] for r in sets if r.get("candidate_id") == winner), [])
    summary["result"] = hold.get("verdict")
    _write_stage(run_dir, "summary", summary)
    pkg = build_package(run_dir, winner, win_arm.team_file, Path(final_model) if final_model else None, species,
                        registry=None, extra_manifest={"models_dir": final_models_dir, "base_models_dir": models_dir,
                                                       "seed": seed, "pick_variant": chosen[winner]["variant"],
                                                       "pick_policy": final_pick,
                                                       "reference_variant": ref_variant})
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
