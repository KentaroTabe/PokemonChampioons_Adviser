"""測定段 (S7〜S13) のオーケストレーション。run.py から呼ぶ。

S8a 全候補 (代理スコアで絞らない) を cheap adaptation (BUILD_SCREEN_ADAPT_BATTLES) してから Team × PickVariant で
    screening racing (variant = teampreview / generic / cheap、チームの実力 = variant の最善。参照も同じ variant の最善。
    SEARCH fold B、脱落は伸び代 margin 込み)
S7  生存チーム (最善 variant の Δ 順に max_candidates まで) の選出モデル適応 (SEARCH fold A、収束まで、checkpoint 保存)
    → checkpoint は独立 fold V の実測勝率で選ぶ (val_mse では選ばない)
S7b 参照 (登録チーム) にも S7 と同じ適応を与え (BUILD_REFERENCE_FULL_ADAPT)、fresh を参照の variant に加えて S8a-2 と同じ
    相手列で最善を選び直す。以降 (S8b / S10 / holdout / stress / ablation) の参照はそれ。
    2026-09-15: 候補だけ収束まで適応し参照は cheap 1000 戦だけ、という非対称を rule_0913 の対照実験で確認
    (参照 teampreview 0.757 / 参照+適応 0.830 / 勝者 0.788。docs/incidents/reports/2026-09-15-reference-adaptation-asymmetry.md)
S8b チーム × variant (teampreview / generic / fresh) × 参照の racing (fold B、別 seed)。チームごとに variant を測定で選ぶ
S9  測定からの戻り (repairs 周、既定 config BUILD_REPAIR_ROUNDS): S8a 後と S8b 後に上位の並びを探索 fold の記録で診断し、
    修理モード (tools/team_build/repair.py: 型だけの変種 B / 個体の入替 A、エースと核は固定、変更 ≤ BUILD_MAX_CHANGES) の変種を
    cheap adaptation + racing で測って生存したものを次の段の候補に加える。LLM の仮説は使わない (docs/TEAM_BUILD_REDESIGN_1002.md §14)
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
                                    BUILD_EQUIV_EPS, BUILD_FINALIST_HOLDOUT_ALL, BUILD_FINALIST_MAX_SHARED,
                                    BUILD_FINALISTS, BUILD_FOLD_EVAL, BUILD_FOLD_VALIDATE, BUILD_IDENTICAL_REFERENCE_SKIP,
                                    BUILD_PICK_VARIANTS, BUILD_PLAN_PRIOR, BUILD_RACE_DEFAULT_MAX, BUILD_RACE_STEPS, BUILD_REFERENCE_FULL_ADAPT,
                                    BUILD_REFERENCE_PRODUCTION_GAP, BUILD_REFERENCE_PRODUCTION_VARIANT,
                                    BUILD_REPAIR_EXPLORE_PARENTS, BUILD_REPAIR_FULL_ADAPT_ROUND2, BUILD_REPAIR_PARENTS,
                                    BUILD_REPAIR_POOL_VARIANTS, BUILD_REPAIR_ROUNDS, BUILD_REPRO_GATE,
                                    BUILD_SCREEN_ADAPT_BATTLES, BUILD_SCREEN_MARGIN, BUILD_SCREEN_MAX, BUILD_SCREEN_STEPS,
                                    BUILD_SCREEN_VARIANTS)
from tools.team_build import ablation as AB
from tools.team_build import adapt as AD
from tools.team_build import finalists as FN
from tools.team_build import holdout as HO
from tools.team_build import racing as R
from tools.team_build import stress as ST
from tools.team_build.package import build_package
from tools.team_build.registry import Registry
from tools.team_build.verdict import DEGRADED

REPO = Path(__file__).resolve().parent.parent.parent
VARIANT_SEP = "@"
SCREEN_ADAPT_PARALLEL = 4     # cheap adaptation の同時実行数 (収集は 1 プロセス 1 戦ずつ)
from tools.team_build import theme_check as TC  # noqa: E402

STOP_POINTS = ("s08a", "s08b")


def _log(run_dir: Path, msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with (run_dir / "run.log").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def pin_models() -> str:
    res = subprocess.run(["bash", "scripts/pin_models.sh"], capture_output=True, text=True, cwd=str(REPO))
    return res.stdout.strip().splitlines()[-1] if res.stdout.strip() else ""


def reference_arm(run_dir: Path, models_dir: str, resume: bool = False) -> R.Arm:
    """参照 = 現在の my_team (config/my_team.json)。選出方策は S8a で variant の最善を測って決める。
    resume では既存の reference_team.txt を使う (登録が変わっていても、段をまたいで参照が変わらないように。
    2026-09-09 chat_0907: 再開時に参照が別チーム (しかも登録不正) に置き換わり S8b の参照が全滅した)"""
    p = run_dir / "reference_team.txt"
    if not (resume and p.exists()):
        from tools.evaluate_team import build_myteam_text
        p.write_text(build_myteam_text(), encoding="utf-8")
    return R.Arm("reference", p, None, models_dir)


def candidate_arms(run_dir: Path, models_dir: str, limit: Optional[int] = None, ids: Optional[list] = None) -> list:
    sets = json.loads((run_dir / "s06_sets.json").read_text(encoding="utf-8"))
    arms = []
    for r in sets:
        if not r.get("ok"):
            continue
        if ids is not None and r["candidate_id"] not in ids:
            continue
        plan = run_dir / "s06_sets" / f"{r['candidate_id']}.plan.json"
        arms.append(R.Arm(r["candidate_id"], run_dir / "s06_sets" / f"{r['candidate_id']}.txt", None, models_dir,
                          plan_file=(str(plan) if plan.exists() else None)))
    return arms[:limit] if limit else arms


def _write_stage(run_dir: Path, name: str, obj) -> None:
    (run_dir / "evaluation").mkdir(parents=True, exist_ok=True)
    (run_dir / "evaluation" / f"{name}.json").write_text(json.dumps(obj, ensure_ascii=False, indent=1) + "\n",
                                                         encoding="utf-8")


def _records_of(eval_dir: Path, prefix: str, arm_ids: list) -> list:
    """対戦記録 (evaluation/battles/<prefix>_<arm_id>.jsonl) を腕の分だけ束ねて読む"""
    recs: list = []
    for aid in arm_ids:
        p = eval_dir / "battles" / f"{prefix}_{aid}.jsonl"
        if p.exists():
            recs.extend(json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip())
    return recs


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
                           "pick_policy": a.get("pick_policy") or (v if v in ("teampreview", "rule") else "advisor"),
                           "plan_file": a.get("plan_file"),
                           "delta": d, "se": r.get("se"), "state": a.get("state"), "n": a.get("n_done")}
    return chosen


def plan_ab_pairs(res: dict) -> dict:
    """plan prior の A/B (BUILD_PLAN_PRIOR = ab): 同じ並び・同じモデルの fresh と fresh_plan の対応差 {team_id: {"delta_fresh",
    "delta_fresh_plan", "diff"}} (純粋)。両方の腕があるチームだけ"""
    by: dict = {}
    for a in (res or {}).get("arms", []):
        cid, v = split_variant(a["arm_id"])
        d = (a.get("result") or {}).get("mean")
        if v in ("fresh", "fresh_plan") and d is not None:
            by.setdefault(cid, {})[v] = float(d)
    out = {}
    for cid, dd in by.items():
        if "fresh" in dd and "fresh_plan" in dd:
            out[cid] = {"delta_fresh": dd["fresh"], "delta_fresh_plan": dd["fresh_plan"], "diff": round(dd["fresh_plan"] - dd["fresh"], 4)}
    return out


def team_survivors(chosen: dict, max_candidates: Optional[int]) -> list:
    """choose_variants の結果を Δ の降順に並べ、max_candidates まで返す"""
    ids = sorted(chosen, key=lambda c: -chosen[c]["delta"])
    return ids[:max_candidates] if max_candidates else ids


def variants_for(cid: str, rows_by: dict, screen_variants, calibration_variants) -> tuple:
    """S8a で測る variant (純粋): 較正の標本 (tag calibration) は軽い適応の腕だけ、それ以外は screen_variants (判断 #4)"""
    tag = (rows_by.get(cid) or {}).get("tag") or ""
    return tuple(calibration_variants) if tag == "calibration" else tuple(screen_variants)


def should_run_stress(verdict: Optional[str], only_on_pass: bool) -> bool:
    """STRESS と ablation を回すか (純粋): only_on_pass なら holdout が PASS / PASS_EQUIVALENT のときだけ (判断 #1)"""
    return (not only_on_pass) or verdict in ("PASS", "PASS_EQUIVALENT")


def change_counts(rows: list, ids: list) -> dict:
    """修理の変種ごとの親との違い (純粋): {candidate_id: {"kind": "A"|"B", "n_changes": 変更の数}} (s06_sets.json の行の origin から。
    2026-10-05 判断 #6: 変種ごとの変更枠数を summary に残し、2 枠以上の変種が良いかを次の 2 run で見る)"""
    by = {r.get("candidate_id"): r for r in rows or []}
    out = {}
    for cid in ids or []:
        o = (by.get(cid) or {}).get("origin") or {}
        if o.get("kind") == "repair":
            out[cid] = {"kind": o.get("variant"), "n_changes": len(o.get("changes") or [])}
    return out


def exclude_tagged(ids: list, rows_by: dict, tags=("calibration",)) -> tuple:
    """並びの列から tag が tags の並び (較正の標本: S8a だけ測り、昇格・修理には使わない) を外す (純粋)。戻り値 (残り, 外した並び)"""
    out, dropped = [], []
    for cid in ids:
        if ((rows_by.get(cid) or {}).get("tag") or "") in tags:
            dropped.append(cid)
        else:
            out.append(cid)
    return out, dropped


def repro_gate_ok(s08b_delta: Optional[float], s10_delta: Optional[float]) -> bool:
    """再現性の門: 勝者は S8b (SEARCH) と S10 (SELECTION) の両分割で Δ ≥ 0 でなければ holdout に進めない。純粋"""
    return s08b_delta is not None and s10_delta is not None and s08b_delta >= 0.0 and s10_delta >= 0.0


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


def same_team(text_a: str, text_b: str) -> bool:
    """2 つのチーム本文が同じ 6 体・同じ型か (純粋: 種 id → 型のキーの集合で比べる。並び順・ニックネームは無視)"""
    from tools.team_build.sets import parse_set_text
    try:
        a = {sid: c.key() for sid, c in parse_set_text(text_a or "").items()}
        b = {sid: c.key() for sid, c in parse_set_text(text_b or "").items()}
    except Exception:
        return False
    return bool(a) and a == b


def split_identical(cands: list, reference_text: str, read_text=None) -> tuple:
    """候補のうち参照 (登録チーム) と同じ 6 体・同じ型のものを分ける (純粋)。戻り値 (残す候補, 同一の候補)。
    同じチームどうしの差は選出モデルの学習のばらつきだけなので腕にしない (BUILD_IDENTICAL_REFERENCE_SKIP)"""
    read_text = read_text or (lambda p: Path(p).read_text(encoding="utf-8"))
    keep, same = [], []
    for a in cands:
        try:
            txt = read_text(a.team_file)
        except Exception:
            txt = ""
        (same if same_team(txt, reference_text) else keep).append(a)
    return keep, same


def arms_of_team(res: dict, cid: str) -> list:
    """racing の結果からそのチームの全 variant の arm_id (対戦記録を束ねる用。純粋)"""
    return [a["arm_id"] for a in (res or {}).get("arms", []) if split_variant(a["arm_id"])[0] == cid]


def best_delta_by_team(res: dict) -> dict:
    """racing の結果 → {team_id: variant の最大 Δ} (脱落した腕も含む。純粋)"""
    out: dict = {}
    for a in (res or {}).get("arms", []):
        d = (a.get("result") or {}).get("mean")
        if d is None:
            continue
        cid = split_variant(a["arm_id"])[0]
        if cid not in out or d > out[cid]:
            out[cid] = d
    return out


def repair_parents(res: dict, survivors: list, rows_by: dict, identical: list = (), n_main: int = BUILD_REPAIR_PARENTS,
                   n_explore: int = BUILD_REPAIR_EXPLORE_PARENTS, pool_variants: bool = BUILD_REPAIR_POOL_VARIANTS,
                   chosen: Optional[dict] = None) -> list:
    """修理の親 [(candidate_id, [診断に使う腕の arm_id ...])] (純粋)。
    1. 参照と同一の候補 (identical): 参照の腕 ("reference") の記録で診断する (現行チームの修理)
    2. 生存した並びの Δ 上位 n_main
    3. 探索の並び (現行枝 incumbent / incumbent_mut と修理の変種を除く) の Δ 上位 n_explore。S8a で脱落していても診断する
       (2026-10-04: 探索の並びが全滅すると修理の対象が現行チームと近傍だけになっていた)
    pool_variants なら同じ並びの全 variant の腕を束ねる (診断の対戦数を増やす)。役割の無い行 (従来の S5) は親にしない"""
    def arms(cid: str) -> list:
        if pool_variants:
            a = arms_of_team(res, cid)
            if a:
                return a
        c = (chosen or {}).get(cid) or {}
        return [c["arm_id"]] if c.get("arm_id") else arms_of_team(res, cid)[:1]

    def has_roles(cid: str) -> bool:
        return bool((rows_by.get(cid) or {}).get("roles"))

    out: list = []
    seen: set = set()
    for cid in identical:
        if has_roles(cid) and cid not in seen:
            out.append((cid, ["reference"]))
            seen.add(cid)
    for cid in survivors:
        if len([c for c, _a in out if c not in identical]) >= n_main:
            break
        if cid in seen or not has_roles(cid):
            continue
        out.append((cid, arms(cid)))
        seen.add(cid)
    if n_explore > 0:
        deltas = best_delta_by_team(res)
        explore = [cid for cid in sorted(deltas, key=lambda c: -deltas[c])
                   if (rows_by.get(cid) or {}).get("tag") not in ("incumbent", "incumbent_mut", "repair", "calibration")
                   and cid not in seen and has_roles(cid)]
        for cid in explore[:n_explore]:
            out.append((cid, arms(cid)))
            seen.add(cid)
    return out


def reference_production_gap(win_rates: dict, threshold: float = BUILD_REFERENCE_PRODUCTION_GAP) -> Optional[dict]:
    """参照の variant の勝率 {variant: wr} から、本番 (production) の選出モデルが run 内で適応した fresh より
    threshold 以上弱ければその事実 (純粋)。弱くなければ None"""
    fresh, prod = win_rates.get("fresh"), win_rates.get("production")
    if fresh is None or prod is None:
        return None
    gap = float(fresh) - float(prod)
    if gap < threshold:
        return None
    return {"fresh": fresh, "production": prod, "gap": round(gap, 4), "threshold": threshold,
            "note": "本番の選出モデルが現行チームで弱い: 実際の助言の選出に影響する。fresh のモデルを registry に候補として登録した"}


# ------------------------------------------------------------------ 実行
def _repair_round(run_dir: Path, eval_dir: Path, round_no: int, parents: list, battles_prefix: str, split: Path, seed: int,
                  models_dir: str, generic: Optional[str], ref_arm, screen_adapt: int, adapt_chunk: int, parallel: int,
                  screen_variants: tuple, steps: tuple, max_battles: int, eps: float, log, resume: bool,
                  n_threats: int, plan_prior: str = BUILD_PLAN_PRIOR) -> tuple:
    """修理モードの 1 周: 診断 → 変種 (s06_sets に追加) → cheap adaptation → 参照との racing。
    戻り値 (変種の腕 [R.Arm], choose_variants の結果 {candidate_id: ...})。resume では s09_repair<n>.json の変種を再利用する"""
    from tools.team_build.repair import run_repair_round
    stage_json = eval_dir / f"s09_repair{round_no}.json"
    ids: list = []
    if resume and stage_json.exists():
        try:
            ids = list(json.loads(stage_json.read_text(encoding="utf-8")).get("ids") or [])
            log(f"S9 repair {round_no}: resume (s09_repair{round_no}.json の変種 {len(ids)} を再利用)")
        except Exception:
            ids = []
    if not ids:
        try:
            ids = run_repair_round(run_dir, parents, round_no, battles_prefix, log=log, n_threats=n_threats)["ids"]
        except Exception as e:      # 修理に失敗しても run は続ける (変種なし)
            log(f"S9 repair {round_no}: error {e!r} (変種なしで続ける)")
            return [], {}
    if not ids:
        log(f"S9 repair {round_no}: 変種なし")
        return [], {}
    arms = candidate_arms(run_dir, models_dir, ids=ids)
    # resume: 変種の cheap adaptation (s09_repair<n>_models.json) と racing (s09_repair<n>_race.json) も再利用する
    # (2026-10-03: 2 周目に修正を反映するため run を止めて再開した。1 周目の racing (約 2 時間) をやり直さない)
    models_json = eval_dir / f"s09_repair{round_no}_models.json"
    models = None
    if resume and models_json.exists():
        try:
            prev = json.loads(models_json.read_text(encoding="utf-8"))
            if all(a.arm_id in prev and prev[a.arm_id] and Path(prev[a.arm_id]).exists() for a in arms):
                models = prev
                log(f"S9 repair {round_no}: cheap adaptation は resume (s09_repair{round_no}_models.json を再利用)")
        except Exception:
            models = None
    if models is None:
        models = _screen_adapt_all(arms, split, run_dir / "advisors_screen", seed + 100 * round_no, screen_adapt, adapt_chunk,
                                   parallel, log, plan_prior=plan_prior)
        _write_stage(run_dir, f"s09_repair{round_no}_models", models)
    race_arms = [a for c in arms for a in (_variant_arm(c, v, models, generic, plan_prior=plan_prior) for v in screen_variants) if a]
    race_json = eval_dir / f"s09_repair{round_no}_race.json"
    res = None
    if resume and race_json.exists():
        try:
            prev = json.loads(race_json.read_text(encoding="utf-8"))
            if repair_race_reusable(prev, [a.arm_id for a in race_arms]):
                res = prev
                log(f"S9 repair {round_no}: racing は resume (s09_repair{round_no}_race.json を再利用)")
        except Exception:
            res = None
    if res is None:
        res = R.race(race_arms, ref_arm(), split, "search", seed + 100 * round_no, eval_dir, stage=f"s09_repair{round_no}_race",
                     fold=BUILD_FOLD_EVAL, steps=steps, max_battles=max_battles, eps=eps, parallel=parallel, log=log)
    return arms, choose_variants(res)


def repair_race_reusable(res: dict, arm_ids: list) -> bool:
    """保存済みの racing の結果が、今回の変種の腕を全部含み、測定が終わっている (各腕が確定か上限まで測った) か (純粋)"""
    arms = {a.get("arm_id"): a for a in (res or {}).get("arms") or []}
    if not arm_ids or any(aid not in arms for aid in arm_ids):
        return False
    max_n = int((res or {}).get("max_battles") or 0)
    for aid in arm_ids:
        a = arms[aid]
        done = a.get("eliminated_at") is not None or a.get("state") != "uncertain" or int(a.get("n_done") or 0) >= max_n
        if not done:
            return False
    return True


def _screen_adapt_all(arms: list, split: Path, out_dir: Path, seed: int, n_battles: int, chunk: int,
                      parallel: int, log, plan_prior: str = BUILD_PLAN_PRIOR) -> dict:
    """全 arm を同じ予算で cheap adaptation する (収集 n_battles 戦 → 1 回学習)。戻り値 {arm_id: model or None}。
    計画の事前は plan_prior が on のときだけ収集に使う"""
    def one(arm):
        # 選出計画があれば収集の探索枠で計画の選出を踏ませる (S7 の適応と同じ。2026-10-04: cheap adaptation に計画が渡っていなかった)
        r = AD.adapt_selection(f"{arm.arm_id}_screen", arm.team_file, split, out_dir, seed,
                               min_battles=n_battles, chunk=min(chunk, n_battles), max_battles=n_battles,
                               patience=10 ** 9, log=log, registry=None,
                               plan_file=(arm.plan_file if plan_prior == "on" else None))
        return arm.arm_id, r.get("model"), r.get("stop_reason"), r.get("elapsed_s")

    out = {}
    with ThreadPoolExecutor(max_workers=max(1, min(parallel, SCREEN_ADAPT_PARALLEL))) as ex:
        for arm_id, model, stop, el in ex.map(one, arms):
            out[arm_id] = model
            log(f"[screen-adapt] {arm_id}: model={'ok' if model else 'none'} stop={stop} {el}s")
    return out


def _variant_arm(base: R.Arm, variant: str, models: dict, generic_path: Optional[str],
                 production_path: Optional[str] = None, plan_prior: str = BUILD_PLAN_PRIOR) -> Optional[R.Arm]:
    """base (チーム) の選出方策 variant の腕。使えない variant は None。
    production = 配布版 (登録チームで微調整済み) の選出モデルを強制 (参照だけに使う。候補には無い)。
    plan_prior: off = 計画を使わない / on = モデルの腕 (cheap / fresh) に計画の事前を足す / ab = fresh_plan の腕だけが計画を使う
    (fresh と同じモデル + 計画。同一相手列での対応比較)"""
    if variant in ("teampreview", "rule"):
        # rule = 実戦の助言と同じ相性の規則 (モデル無し)。teampreview = 従来の簡易相性順 (指定時だけ)
        return R.Arm(variant_arm_id(base.arm_id, variant), base.team_file, None, base.models_dir, pick_policy=variant)
    if variant == "generic":
        if not generic_path:
            return None
        return R.Arm(variant_arm_id(base.arm_id, variant), base.team_file, generic_path, base.models_dir, pick_policy="advisor",
                     plan_file=(base.plan_file if plan_prior == "on" else None))
    if variant == "production":
        if not production_path:
            return None
        return R.Arm(variant_arm_id(base.arm_id, variant), base.team_file, production_path, base.models_dir,
                     pick_policy="advisor")
    model = models.get(base.arm_id)
    if not model:
        return None
    if variant == "fresh_plan":
        if plan_prior != "ab" or not base.plan_file:
            return None
        return R.Arm(variant_arm_id(base.arm_id, variant), base.team_file, model, base.models_dir, pick_policy="advisor",
                     plan_file=base.plan_file)
    return R.Arm(variant_arm_id(base.arm_id, variant), base.team_file, model, base.models_dir, pick_policy="advisor",
                 plan_file=(base.plan_file if plan_prior == "on" else None))


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
                    validate_n: int = BUILD_ADAPT_VALIDATE_N, validate_max: int = BUILD_ADAPT_VALIDATE_MAX_CKPTS,
                    resume: bool = False, reference_full_adapt: bool = BUILD_REFERENCE_FULL_ADAPT,
                    reference_production_variant: bool = BUILD_REFERENCE_PRODUCTION_VARIANT,
                    finalists_k: int = BUILD_FINALISTS, finalist_max_shared: int = BUILD_FINALIST_MAX_SHARED,
                    finalist_holdout_all: bool = BUILD_FINALIST_HOLDOUT_ALL, n_threats: int = 30,
                    plan_prior: str = BUILD_PLAN_PRIOR) -> dict:
    """resume: 途中で落ちた run の続き。evaluation/ の S8a 結果 (cheap モデル・参照 variant・racing) と
    advisors/<cid>/adapt_result.json (完了した適応) をそのまま使い、無いものだけ実行する
    reference_full_adapt: 参照にも S7 と同じ適応を与え、fresh を参照の variant に加える (S7b)
    reference_production_variant: 参照の variant に配布版 (本番) の選出モデルを加える
    finalists_k / finalist_max_shared / finalist_holdout_all: 方向性の違う最終候補を K 並び残し (共通メンバー ≤ max_shared)、
    それぞれに S11/S11b/封印 holdout を行う (2026-09-17)。STRESS と ablation は 1 位だけ
    plan_prior: off / on / ab (選出計画を選出モデルの初期値にするか。ab は S8b に fresh_plan の腕を足して対応比較する)"""
    from champions_agent.agent.selection_model import GENERAL_MODEL_PATH
    log = lambda m: _log(run_dir, m)

    def _load_json(p: Path):
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
    split = run_dir / "opponent_families.json"
    doc = json.loads(split.read_text(encoding="utf-8"))
    n_folds = len(doc.get("search_folds") or [])
    if n_folds <= max(BUILD_FOLD_EVAL, BUILD_FOLD_VALIDATE):
        raise SystemExit(f"opponent_families.json の SEARCH fold が {n_folds} 個で足りない (評価 {BUILD_FOLD_EVAL} / "
                         f"検証 {BUILD_FOLD_VALIDATE})。探索段を新しい seed でやり直す")
    models_dir = None
    if resume:
        # 途中再開では S8a と同じ RL ピンを使う (段をまたいで行動方策の世代が変わると対応比較が濁る)
        prev = _load_json(run_dir / "evaluation" / "s08a_screen.json") or {}
        prev_dir = (prev.get("reference") or {}).get("models_dir")
        if prev_dir and Path(prev_dir).exists():
            models_dir = prev_dir
    if not models_dir:
        models_dir = pin_models()
    generic = str(GENERAL_MODEL_PATH) if Path(GENERAL_MODEL_PATH).exists() else None
    log(f"S7-13 measurement start: models_dir={models_dir} generic={'ok' if generic else 'none'}"
        + (" (resume: S8a のピンを再利用)" if resume else ""))
    ref = reference_arm(run_dir, models_dir, resume=resume)
    # 代理スコアでは絞らない (max_candidates は S7 で収束まで適応するチーム数)
    cands = candidate_arms(run_dir, models_dir, None, ids=candidate_ids)
    identical: list = []
    if BUILD_IDENTICAL_REFERENCE_SKIP:
        cands, same = split_identical(cands, ref.team_file.read_text(encoding="utf-8"))
        identical = [a.arm_id for a in same]
        if identical:
            log(f"S7-13: 参照と同じ 6 体・同じ型の候補 {identical} は腕にしない (差は選出モデルの学習のばらつきだけ)。"
                f"修理の親としては参照の記録で診断する")
    if not cands:
        raise SystemExit("合法な候補がありません (s06_sets.json。参照と同一の候補は除く)")
    sets_rows_all = json.loads((run_dir / "s06_sets.json").read_text(encoding="utf-8"))
    rows_by_all = {r.get("candidate_id"): r for r in sets_rows_all}
    summary = {"models_dir": models_dir, "reference": ref.to_dict(), "n_candidates": len(cands),
               "candidate_ids": [a.arm_id for a in cands], "identical_to_reference": identical,
               "protocol": {"screen_adapt": screen_adapt, "screen_margin": screen_margin, "screen_steps": list(screen_steps),
                            "screen_max": screen_max, "screen_variants": list(screen_variants),
                            "variants": list(variants), "max_candidates": max_candidates,
                            "fold_eval": BUILD_FOLD_EVAL, "fold_validate": BUILD_FOLD_VALIDATE,
                            "validate_n": validate_n, "validate_max_ckpts": validate_max,
                            "s08b_seed_offset": s08b_seed_offset, "s11": s11, "stop_after": stop_after,
                            "reference_full_adapt": reference_full_adapt,
                            "reference_production_variant": reference_production_variant,
                            "finalists_k": finalists_k, "finalist_max_shared": finalist_max_shared,
                            "finalist_holdout_all": finalist_holdout_all, "plan_prior": plan_prior}}
    if plan_prior == "ab" and "fresh_plan" not in variants:
        variants = tuple(variants) + ("fresh_plan",)
    eval_dir = run_dir / "evaluation"

    # S8a-1: 全候補 + 参照を同じ予算で cheap adaptation
    screen_models = _load_json(eval_dir / "s08a_screen_models.json") if resume else None
    if screen_models and all(a.arm_id in screen_models for a in [ref] + cands):
        log(f"S8a cheap adaptation: resume (s08a_screen_models.json を再利用)")
    else:
        log(f"S8a cheap adaptation: {len(cands)} 候補 + 参照 × {screen_adapt} 戦")
        screen_models = _screen_adapt_all([ref] + cands, split, run_dir / "advisors_screen", seed, screen_adapt,
                                          adapt_chunk, parallel, log, plan_prior=plan_prior)
        _write_stage(run_dir, "s08a_screen_models", screen_models)

    # S8a-2: 参照の variant の最善 (同一相手列、screen_max 戦)
    # 参照の variant: screening の 3 つ + 配布版 (本番) の選出モデル (登録チームで微調整済み。rule_0913 の対照実験では
    # 本番 0.848 > 適応 0.830 > teampreview 0.757)。候補には本番モデルが無いので参照だけ
    production = None
    if reference_production_variant:
        try:
            from champions_agent.agent.selection_dispatch import deployed_model_path
            pth = deployed_model_path()
            production = str(pth) if Path(pth).exists() else None
        except Exception:
            production = None
    ref_order = tuple(screen_variants) + (("production",) if production else ())
    ref_variants = [a for a in (_variant_arm(ref, v, screen_models, generic, production) for v in ref_order) if a]
    ref_files = {a.arm_id: eval_dir / f"s08a_reference_{a.arm_id}_0_{screen_max}.json" for a in ref_variants}
    if resume and all(p.exists() for p in ref_files.values()):
        ref_wr = {split_variant(aid)[1]: (_load_json(p) or {}).get("win_rate") for aid, p in ref_files.items()}
        log("S8a reference variant: resume (測定済みの JSON を再利用)")
    else:
        R.measure_round(ref_variants, screen_max, 0, seed, split, "search", BUILD_FOLD_EVAL, eval_dir, "s08a_reference",
                        parallel=parallel)
        ref_wr = {split_variant(a.arm_id)[1]: (sum(a.outcomes) / len(a.outcomes) if a.outcomes else None)
                  for a in ref_variants}
    ref_variant = best_by_win_rate(ref_wr, ref_order) or ("rule" if "rule" in ref_order else "teampreview")
    ref_best = next(a for a in ref_variants if split_variant(a.arm_id)[1] == ref_variant)
    summary["reference_variant"] = {"variant": ref_variant, "win_rates": ref_wr, "selection_model": ref_best.selection_model,
                                    "pick_policy": ref_best.pick_policy, "production_model": production}
    log(f"S8a reference variant: {ref_variant} " + " ".join(f"{k}={v}" for k, v in ref_wr.items()))

    def ref_arm(arm_id: str = "reference") -> R.Arm:
        return R.Arm(arm_id, ref.team_file, ref_best.selection_model, models_dir, pick_policy=ref_best.pick_policy)

    # S8a-3: screening racing (チーム × variant、脱落は margin 込み、短い段階)
    res8a = _load_json(eval_dir / "s08a_screen.json") if resume else None
    if res8a and {split_variant(a["arm_id"])[0] for a in res8a.get("arms", [])} >= {c.arm_id for c in cands}:
        log("S8a screening: resume (s08a_screen.json を再利用)")
    else:
        from champions_agent.config import BUILD_CALIBRATION_VARIANTS
        arms8a = [a for c in cands
                  for a in (_variant_arm(c, v, screen_models, generic, plan_prior=plan_prior)
                            for v in variants_for(c.arm_id, rows_by_all, screen_variants, BUILD_CALIBRATION_VARIANTS)) if a]
        res8a = R.race(arms8a, ref_arm(), split, "search", seed, eval_dir, stage="s08a_screen", fold=BUILD_FOLD_EVAL,
                       steps=screen_steps, max_battles=screen_max, eps=BUILD_EQUIV_EPS + screen_margin,
                       parallel=parallel, log=log)
    chosen8a = choose_variants(res8a)
    all_survivors, _calib_measured = exclude_tagged(team_survivors(chosen8a, None), rows_by_all)
    survivors = all_survivors[:max_candidates] if max_candidates else all_survivors
    log(f"S8a survivors: {len(all_survivors)}/{len(cands)} (脱落 {len(cands) - len(all_survivors)})、"
        f"S7 で適応する Δ 上位 {len(survivors)}: " +
        ", ".join(f"{c}={chosen8a[c]['variant']}({chosen8a[c]['delta']:+.3f})" for c in survivors))
    summary["s08a_variants"] = chosen8a
    summary["s08a_survivors"] = survivors
    summary["s08a_eliminated"] = [a.arm_id for a in cands if a.arm_id not in chosen8a]
    summary["s08a_capped"] = [c for c in all_survivors if c not in survivors]
    calib_ids = [c for c, r in rows_by_all.items() if (r.get("tag") or "") == "calibration" and c in {a.arm_id for a in cands}]
    if calib_ids:
        # 較正の標本 (判断 #10): S8a の Δ を記録するだけ (生存・修理・昇格には使わない)。代理評価の較正 (s08a_calibration) には入る
        summary["calibration_sample"] = {c: (chosen8a.get(c) or {}).get("delta") for c in calib_ids}
        log(f"S8a calibration sample: 較正の標本 {len(calib_ids)} 並び (昇格・修理には使わない) Δ "
            + ", ".join(f"{c}={(chosen8a.get(c) or {}).get('delta')}" for c in calib_ids))
    # 代理評価 (S5 の点・系統ごとの予測) と実測 (S8a の Δ・系統ごとの勝率・選出計画の一致) の較正を記録する
    # (2026-10-04: 評価の点と実戦が逆、系統ごとの予測に識別力がない、計画が実戦で使われない — まず run ごとに見える形にする)
    try:
        from tools.team_build.review_run import family_calibration, surrogate_quality
        cal = {"surrogate": surrogate_quality(sets_rows_all, res8a), "teams": {}}
        for a in cands:
            recs = _records_of(eval_dir, "s08a_screen", arms_of_team(res8a, a.arm_id))
            cal["teams"][a.arm_id] = family_calibration((rows_by_all.get(a.arm_id) or {}).get("family_values") or {}, recs,
                                                        plan_file=a.plan_file)
        rhos = [t["spearman"] for t in cal["teams"].values() if t.get("spearman") is not None]
        cal["median_family_spearman"] = sorted(rhos)[len(rhos) // 2] if rhos else None
        _write_stage(run_dir, "s08a_calibration", cal)
        summary["calibration"] = {"surrogate_spearman": (cal["surrogate"] or {}).get("spearman"),
                                  "median_family_spearman": cal["median_family_spearman"],
                                  "plan_match3": {c: (t.get("plan") or {}).get("match3_rate") for c, t in cal["teams"].items()}}
        log(f"S8a calibration: 代理の点と Δ の順位相関 {summary['calibration']['surrogate_spearman']}、"
            f"系統ごとの予測と勝率の順位相関 (中央値) {cal['median_family_spearman']}、"
            f"計画と選出の 3 体一致 {summary['calibration']['plan_match3']}")
    except Exception as e:
        log(f"S8a calibration: error {e!r}")
    if stop_after == "s08a":
        summary["result"] = "stopped_after_s08a"
        _write_stage(run_dir, "summary", summary)
        return summary

    # S9 (1 周目): S8a の上位の並びを探索 fold の記録で診断し、修理モードの変種を cheap adaptation + screening で測って
    # 生存した変種を S7 以降の候補に加える (docs/TEAM_BUILD_REDESIGN_1002.md §14)。親は 参照と同一の候補 (参照の記録で診断) +
    # 生存の上位 + 探索の並びの Δ 上位 (脱落していても)。診断の記録は同じ並びの全 variant を束ねる
    repair_rounds = min(int(repairs or 0), BUILD_REPAIR_ROUNDS)
    if repair_rounds >= 1 and (survivors or identical):
        parents = repair_parents(res8a, survivors, rows_by_all, identical=identical, chosen=chosen8a)
        log(f"S9 repair 1: 親 {[(c, len(a)) for c, a in parents]}")
        new_arms, chosen_r = _repair_round(run_dir, eval_dir, 1, parents, "s08a_screen", split, seed, models_dir, generic,
                                           ref_arm, screen_adapt, adapt_chunk, parallel, screen_variants, screen_steps,
                                           screen_max, BUILD_EQUIV_EPS + screen_margin, log, resume, n_threats, plan_prior)
        if new_arms:
            cands = cands + new_arms
            chosen8a.update(chosen_r)
            added = team_survivors(chosen_r, None)
            survivors = survivors + [c for c in added if c not in survivors]
            summary["s08a_variants"] = chosen8a
            summary["s09_repair1"] = {"parents": [p[0] for p in parents], "variants": [a.arm_id for a in new_arms],
                                      "survivors": added,
                                      "changes": change_counts(json.loads((run_dir / "s06_sets.json").read_text(encoding="utf-8")),
                                                               [a.arm_id for a in new_arms])}
            log(f"S9 repair 1: 変種 {len(new_arms)} のうち生存 {len(added)} を S7 以降の候補に加える: "
                + ", ".join(f"{c}={chosen_r[c]['variant']}({chosen_r[c]['delta']:+.3f})" for c in added))

    # S7: 生存チームの選出モデル適応 (fold A、収束まで、checkpoint 保存) → 独立 fold V の実測で checkpoint を選ぶ。
    # 適応は数チームを並列 (収集は 1 プロセスずつ)、検証は チームごとに checkpoint を並列に測る。
    # S7b: 参照も同じ手順で適応する (候補だけ深く適応する非対称を無くす。advisors/reference/)
    to_adapt = [a for a in cands if a.arm_id in survivors]
    if reference_full_adapt:
        to_adapt.append(ref)

    prev_s07 = (_load_json(eval_dir / "s07_adapt.json") or {}) if resume else {}

    def _adapt_one(a):
        # 前回の s07_adapt.json (検証結果込み) → 無ければ adapt_result.json (適応のみ) を再利用
        prev = prev_s07.get(a.arm_id) if resume else None
        if not (prev and prev.get("model") and Path(prev["model"]).exists()):
            prev = _load_json(run_dir / "advisors" / a.arm_id / "adapt_result.json") if resume else None
        if prev and prev.get("model") and Path(prev["model"]).exists():
            prev["resumed"] = True
            log(f"[adapt:{a.arm_id}] resume (n={prev.get('n_battles')}"
                f"{', 検証済み' if (prev.get('validated') or {}).get('chosen') else ''})")
            return a.arm_id, prev
        try:
            return a.arm_id, AD.adapt_selection(a.arm_id, a.team_file, split, run_dir / "advisors", seed,
                                                min_battles=adapt_min, chunk=adapt_chunk, max_battles=adapt_max,
                                                log=log, registry=registry, keep_checkpoints=True,
                                                plan_file=(a.plan_file if plan_prior == "on" else None))
        except Exception as e:      # 1 チームの失敗で run 全体を落とさない (fresh variant 無しで S8b へ)
            log(f"[adapt:{a.arm_id}] failed: {e!r}")
            return a.arm_id, {"candidate_id": a.arm_id, "model": None, "history": [], "stop_reason": f"error:{e!r}"}

    def _full_adapt(arm_list: list) -> dict:
        """S7 の手順 (fold A で収束まで適応 → fold V の実測で checkpoint を選ぶ) を腕の列に適用する。
        2 周目の修理の変種にも同じ手順を与える (BUILD_REPAIR_FULL_ADAPT_ROUND2) ので関数にした"""
        out: dict = {}
        with ThreadPoolExecutor(max_workers=max(1, min(parallel, SCREEN_ADAPT_PARALLEL))) as ex:
            for cid, r in ex.map(_adapt_one, arm_list):
                out[cid] = r
        for a in arm_list:
            r = out[a.arm_id]
            ckpts = AD.checkpoints_from_history(r.get("history"))
            if (r.get("validated") or {}).get("chosen") and Path(r["validated"]["chosen"]).exists():
                log(f"[validate:{a.arm_id}] resume (検証済み n{r['validated'].get('chosen_n')})")
                r["model"] = r["validated"]["chosen"]
            elif ckpts:
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
            out[a.arm_id] = r
        return out

    adapted = _full_adapt(to_adapt)
    _write_stage(run_dir, "s07_adapt", adapted)

    # S7b: 参照の fresh 変種を S8a-2 と同じ相手列 (s08a_reference、screen_max 戦) で測り、参照の variant を選び直す。
    # 以降の参照 (ref_arm) は teampreview / generic / cheap / fresh の最善
    ref_fresh = (adapted.get(ref.arm_id) or {}) if reference_full_adapt else {}
    if reference_full_adapt and ref_fresh.get("model"):
        fresh_ref = _variant_arm(ref, "fresh", {ref.arm_id: ref_fresh["model"]}, generic)
        R.measure_round([fresh_ref], screen_max, 0, seed, split, "search", BUILD_FOLD_EVAL, eval_dir, "s08a_reference",
                        parallel=parallel)
        ref_wr["fresh"] = (sum(fresh_ref.outcomes) / len(fresh_ref.outcomes)) if fresh_ref.outcomes else None
        ref_variants.append(fresh_ref)
        ref_variant = best_by_win_rate(ref_wr, ref_order + ("fresh",)) or ref_variant
        ref_best = next(a for a in ref_variants if split_variant(a.arm_id)[1] == ref_variant)
        summary["reference_variant"] = {"variant": ref_variant, "win_rates": ref_wr, "selection_model": ref_best.selection_model,
                                        "pick_policy": ref_best.pick_policy,
                                        "fresh": {"model": ref_fresh.get("model"), "n_battles": ref_fresh.get("n_battles"),
                                                  "chosen_n": (ref_fresh.get("validated") or {}).get("chosen_n")}}
        log(f"S7b reference variant (fresh を加えて選び直し): {ref_variant} "
            + " ".join(f"{k}={v}" for k, v in ref_wr.items()))
        gap = reference_production_gap(ref_wr)
        if gap:
            # 本番の選出モデルが現行チームで弱い (1003: 本番 0.417 / 適応 0.73)。実際の助言の選出に影響するので記録し、
            # 適応したモデルを registry に候補として登録する (昇格は人手: tools.team_build.promote)
            gap.update({"fresh_model": ref_fresh.get("model"), "production_model": production,
                        "n_battles": ref_fresh.get("n_battles"), "chosen_n": (ref_fresh.get("validated") or {}).get("chosen_n")})
            if registry is not None and ref_fresh.get("model"):
                try:
                    row = registry.register("selection_model", Path(ref_fresh["model"]),
                                            meta={"candidate_id": "reference", "for": "registered_team", "recommend_production": True,
                                                  "gap": gap["gap"], "win_rates": ref_wr},
                                            run_id=run_dir.name, status="candidate")
                    gap["artifact_id"] = row["id"]
                except Exception as e:
                    gap["registry_error"] = repr(e)
            summary["reference_production_gap"] = gap
            _write_stage(run_dir, "reference_model_gap", gap)
            log(f"S7b 注意: 本番の選出モデルが現行チームで弱い (本番 {gap['production']} / 適応 {gap['fresh']}、差 {gap['gap']:+.3f})。"
                f"evaluation/reference_model_gap.json に記録" + (f"、registry {gap.get('artifact_id')}" if gap.get("artifact_id") else ""))
    elif reference_full_adapt:
        log(f"S7b reference fresh: 適応に失敗 ({ref_fresh.get('stop_reason')}) → 参照は S8a の variant ({ref_variant}) のまま")

    # S8b: チーム × variant (teampreview / generic / fresh) × 参照 (variant の最善)。variant はチームごとに測定で選ぶ
    fresh_models = {cid: r.get("model") for cid, r in adapted.items() if cid != ref.arm_id}
    arms8b = []
    for a in cands:
        if a.arm_id not in adapted:
            continue
        for v in variants:
            arm = _variant_arm(a, v, fresh_models, generic, plan_prior=plan_prior)
            if arm:
                arms8b.append(arm)
    res8b = _load_json(eval_dir / "s08b_adapted.json") if resume else None
    if res8b and {split_variant(a["arm_id"])[0] for a in res8b.get("arms", [])} >= set(adapted):
        log("S8b: resume (s08b_adapted.json を再利用)")
    else:
        res8b = R.race(arms8b, ref_arm(), split, "search", seed + s08b_seed_offset, eval_dir, stage="s08b_adapted",
                       fold=BUILD_FOLD_EVAL, steps=steps, max_battles=max_battles, parallel=parallel, log=log)
    chosen = choose_variants(res8b)
    contenders = team_survivors(chosen, None)
    summary["s08b_variants"] = chosen
    summary["s08b_contenders"] = contenders
    if plan_prior == "ab":
        summary["plan_ab"] = plan_ab_pairs(res8b)
        log("S8b plan prior A/B (fresh_plan − fresh): " + ", ".join(f"{c}={v['diff']:+.3f}" for c, v in summary["plan_ab"].items()))
    log("S8b contenders: " + ", ".join(f"{c}={chosen[c]['variant']}({chosen[c]['delta']:+.3f})" for c in contenders))
    # テーマの検査 (判断 #3・#4): エース / 固定枠 / 技の指定が選出の単位で効いているかを記録する (報告。門は BUILD_THEME_GATE)
    theme = TC.theme_of_spec(_load_json(run_dir / "request.json") or {})

    def _theme_check(cids: list, sources: list) -> dict:
        rows_now = {r.get("candidate_id"): r for r in json.loads((run_dir / "s06_sets.json").read_text(encoding="utf-8"))}
        recs_by = {cid: [rec for prefix, res in sources for rec in _records_of(eval_dir, prefix, arms_of_team(res, cid))] for cid in cids}
        return TC.check_run(theme, rows_now, recs_by)
    if TC.active(theme):
        try:
            tc8b = _theme_check(contenders, [("s08b_adapted", res8b)])
            summary["theme_check_s08b"] = tc8b
            _write_stage(run_dir, "theme_check_s08b", tc8b)
            log("S8b " + TC.format_line(tc8b))
        except Exception as e:
            log(f"S8b theme check: error {e!r}")
    if stop_after == "s08b":
        summary["result"] = "stopped_after_s08b"
        _write_stage(run_dir, "summary", summary)
        return summary

    # S9 (2 周目): S8b の上位の並びを診断 → 修理モードの変種 → cheap adaptation + racing (S8b と同じ段階) → 生存した変種を
    # S10 の contenders に加える (変種の選出モデルは cheap。LLM の仮説は使わない: D-28)
    if repair_rounds >= 2 and (contenders or identical):
        rows_by_all = {r.get("candidate_id"): r for r in json.loads((run_dir / "s06_sets.json").read_text(encoding="utf-8"))}
        parents = repair_parents(res8b, contenders, rows_by_all, identical=identical, chosen=chosen)
        log(f"S9 repair 2: 親 {[(c, len(a)) for c, a in parents]}")
        new_arms, chosen_r = _repair_round(run_dir, eval_dir, 2, parents, "s08b_adapted", split, seed + s08b_seed_offset,
                                           models_dir, generic, ref_arm, screen_adapt, adapt_chunk, parallel, screen_variants,
                                           steps, max_battles, BUILD_EQUIV_EPS, log, resume, n_threats, plan_prior)
        if new_arms:
            cands = cands + new_arms
            chosen.update(chosen_r)
            added = team_survivors(chosen_r, None)
            contenders = contenders + [c for c in added if c not in contenders]
            summary["s08b_variants"] = chosen
            summary["s08b_contenders"] = contenders
            summary["s09_repair2"] = {"parents": [p[0] for p in parents], "variants": [a.arm_id for a in new_arms],
                                      "survivors": added,
                                      "changes": change_counts(json.loads((run_dir / "s06_sets.json").read_text(encoding="utf-8")),
                                                               [a.arm_id for a in new_arms])}
            log(f"S9 repair 2: 変種 {len(new_arms)} のうち生存 {len(added)} を S10 の contenders に加える: "
                + ", ".join(f"{c}={chosen_r[c]['variant']}({chosen_r[c]['delta']:+.3f})" for c in added))
            if BUILD_REPAIR_FULL_ADAPT_ROUND2 and added:
                # 2 周目の変種にも S7 と同じ適応を与えてから S10 に出す (他の並びは収束まで適応したモデル、変種だけ cheap 1000 戦の
                # モデルかモデルなし、という不公平を無くす。2026-10-04)
                arms_r2 = [a for a in new_arms if a.arm_id in added]
                ad2 = _full_adapt(arms_r2)
                adapted.update(ad2)
                _write_stage(run_dir, "s07_adapt", adapted)
                for a in arms_r2:
                    r2 = ad2.get(a.arm_id) or {}
                    if r2.get("model") and Path(r2["model"]).exists():
                        chosen[a.arm_id].update({"selection_model": r2["model"], "variant": "fresh", "pick_policy": "advisor",
                                                 "adapted_n": r2.get("n_battles"),
                                                 "plan_file": (a.plan_file if plan_prior == "on" else None)})
                        log(f"S9 repair 2: {a.arm_id} の選出モデルを S7 の適応 (n={r2.get('n_battles')}) に置き換えて S10 へ")
                    else:
                        log(f"S9 repair 2: {a.arm_id} の適応に失敗 ({r2.get('stop_reason')}) → screening の variant のまま")
                summary["s08b_variants"] = chosen

    # S10: SELECTION で比較 (チームごとに選んだ variant で)
    team_of = {a.arm_id: a.team_file for a in cands}
    arms10 = [R.Arm(cid, team_of[cid], chosen[cid]["selection_model"], models_dir, pick_policy=chosen[cid]["pick_policy"],
                    plan_file=chosen[cid].get("plan_file"))
              for cid in contenders]
    res10 = R.race(arms10, ref_arm(), split, "selection", seed + 2, eval_dir, stage="s10",
                   steps=steps, max_battles=max_battles, parallel=parallel, log=log)
    finalists = R.contenders(res10)
    if not finalists:
        log("S10: contenders が残らなかった (全候補が参照に劣る)。run は失敗")
        summary["result"] = "no_contender"
        _write_stage(run_dir, "summary", summary)
        return summary
    deltas10 = {a["arm_id"]: (a.get("result") or {}).get("mean") for a in res10["arms"]}
    ranked = sorted(finalists, key=lambda cid: -(deltas10[cid] if deltas10.get(cid) is not None else -1.0))
    if TC.active(theme):
        from champions_agent.config import BUILD_THEME_GATE
        try:
            tc = _theme_check(finalists, [("s08b_adapted", res8b), ("s10", res10)])
            summary["theme_check"] = tc
            _write_stage(run_dir, "theme_check", tc)
            log("S10 " + TC.format_line(tc))
            ranked, demoted = TC.apply_gate(ranked, tc["teams"], BUILD_THEME_GATE, deltas=deltas10, eps=BUILD_EQUIV_EPS)
            summary["theme_gate"] = {"enabled": BUILD_THEME_GATE, "demoted": demoted, "none_pass": tc["none_pass"], "eps": BUILD_EQUIV_EPS}
            if demoted:
                log(f"S10 theme gate: テーマを満たす並び {ranked[0]} が 1 位 {demoted[0]} と同等 (Δ の差 ≤ {BUILD_EQUIV_EPS}) なので入れ替える")
            if tc["none_pass"]:
                log("S10 theme gate: どの並びもテーマを満たさない (none_pass) → 順位はそのまま、印だけ残す")
        except Exception as e:
            log(f"S10 theme check: error {e!r}")
    winner = ranked[0]
    summary["winner"] = winner
    summary["winner_variant"] = chosen[winner]["variant"]
    log(f"S10 winner: {winner} variant={chosen[winner]['variant']} (finalists {finalists})")
    s10_delta = deltas10.get(winner)
    s08b_delta = chosen[winner].get("delta")
    summary["repro_gate"] = {"s08b_delta": s08b_delta, "s10_delta": s10_delta,
                             "ok": repro_gate_ok(s08b_delta, s10_delta), "enabled": BUILD_REPRO_GATE}
    if BUILD_REPRO_GATE and not repro_gate_ok(s08b_delta, s10_delta):
        log(f"再現性の門: 勝者 {winner} の Δ が両分割で非負でない (S8b {s08b_delta} / S10 {s10_delta})。holdout に進めず終了")
        summary["result"] = "not_reproducible"
        _write_stage(run_dir, "summary", summary)
        return summary

    # 複数の方向性の最終候補 (2026-09-17 ユーザー決定): 再現性の門を通った候補から、既に選んだ候補との共通メンバーが
    # finalist_max_shared 以下のものを Δ 順に finalists_k 並び。1 位はこれまでどおりの勝者。
    # 以降の S11 (再学習) / S11b (行動 adapter) / S12 の封印 holdout は最終候補ごとに行い、STRESS と ablation は 1 位だけ
    eligible = [cid for cid in ranked
                if not BUILD_REPRO_GATE or repro_gate_ok(chosen[cid].get("delta"), deltas10.get(cid))]
    sets_rows = json.loads((run_dir / "s06_sets.json").read_text(encoding="utf-8"))
    rows_by = {r.get("candidate_id"): r for r in sets_rows}
    members_by = {cid: list(r.get("members") or []) for cid, r in rows_by.items()}
    fam_doc = _load_json(run_dir / "s04_concepts.json") or {}
    fams = fam_doc.get("families") or []
    # 方向性ラベルは並びに実際に居る個体で書く (2026-09-25): 近傍・交配は元の concept id を引き継ぐので系統の core が居ないことがある。
    # 持ち込んだ並び (…_from_<run>) は元 run の s04_concepts.json から系統を引く
    from tools.team_build.interaction import _mega_stone_ids
    fam_by, parents_by = {}, {}
    for cid in eligible:
        fam, par = FN.family_of(cid, fams), FN.parent_families(cid, fams)
        imp = (rows_by.get(cid) or {}).get("imported_from")
        if fam is None and not par and imp:
            fam, par = FN.imported_family(imp, lambda rid: (_load_json(run_dir.parent / rid / "s04_concepts.json") or {}).get("families") or [])
        fam_by[cid], parents_by[cid] = fam, par
    stones = _mega_stone_ids()
    megas_by = {cid: FN.mega_holders((rows_by.get(cid) or {}).get("sets") or [], stones) for cid in eligible}
    origins_by = {cid: (rows_by.get(cid) or {}).get("origin") or {} for cid in eligible}
    picks = FN.pick_finalists(eligible, deltas10, members_by, fam_by, k=finalists_k, max_shared=finalist_max_shared,
                              megas_by=megas_by, origins_by=origins_by, parents_by=parents_by)
    summary["finalists"] = picks
    summary["finalists_skipped_similar"] = FN.skipped_as_similar(eligible, picks, members_by, finalist_max_shared)
    log(f"最終候補 (方向性の違う並び、共通メンバー ≤ {finalist_max_shared}): "
        + " / ".join(f"{p['rank']}. {p['candidate_id']} [{p['direction_ja']}] S10 {(p['delta_s10'] or 0):+.3f}" for p in picks)
        + (f" (近い方向で外れた: {[s['candidate_id'] for s in summary['finalists_skipped_similar']]})"
           if summary["finalists_skipped_similar"] else ""))

    # S11 / S11b / S12 (封印 holdout) を最終候補ごとに。STRESS と ablation は 1 位だけ (費用)
    pop = ST.policy_population(run_dir / "advisors" / "population")
    pkg, hold = None, None
    for p in picks:
        cid = p["candidate_id"]
        is_top = cid == winner
        tag = "" if is_top else f"_{cid}"
        arm_c = next(a for a in cands if a.arm_id == cid)
        final_model = chosen[cid]["selection_model"]
        final_pick = chosen[cid]["pick_policy"]
        # S11 (任意): 選出モデルを SEARCH + SELECTION で再学習 (variant が fresh のとき)。既定 off
        if s11 and chosen[cid]["variant"] == "fresh":
            r11 = AD.adapt_selection(f"{cid}_final", arm_c.team_file, split, run_dir / "advisors", seed + 3,
                                     min_battles=adapt_min, chunk=adapt_chunk, max_battles=adapt_max, log=log,
                                     registry=registry, tiers=(("search", None), ("selection", None)))
            final_model = r11.get("model") or final_model
            _write_stage(run_dir, f"s11_final_adapt{tag}", r11)
        elif is_top:
            log(f"S11: 省略 (s11={s11}, variant={chosen[cid]['variant']}) → S7 の検証済みモデルを最終モデルにする")
        # S11b: 行動方策 adapter (構築と連動した学習): その並びに固定して短く微調整し、基底との対応差で採否
        final_models_dir = models_dir
        if adapt_action:
            ra = AD.adapt_action(cid, arm_c.team_file, models_dir, run_dir / "advisors", split, seed + 7,
                                 chunk_steps=action_steps, eval_battles=action_eval, log=log, registry=registry)
            final_models_dir = ra.get("models_dir") or models_dir
            p["action_adapter"] = {k: ra.get(k) for k in ("use_adapted", "reason", "elapsed_s", "artifact_id")}
            if is_top:
                summary["action_adapter"] = p["action_adapter"]
            _write_stage(run_dir, f"s11b_action_adapt{tag}", ra)
            log(f"S11b action adapter [{cid}]: use_adapted={ra.get('use_adapted')} ({ra.get('reason')})")
        final_arm = R.Arm(cid, arm_c.team_file, final_model, final_models_dir, pick_policy=final_pick,
                          plan_file=chosen[cid].get("plan_file"))
        h = None
        if is_top or finalist_holdout_all:
            h = HO.final_holdout(final_arm, ref_arm(), split, doc["sealed_id"], run_dir, seed + 4,
                                 candidate_key=f"{cid}:{Path(final_model or '').name}", steps=steps,
                                 max_battles=max_battles, log=log, parallel=min(2, parallel),
                                 stage=f"s12_holdout{tag}")
        p.update({"holdout": h, "variant": chosen[cid]["variant"], "pick_policy": final_pick,
                  "selection_model": final_model, "models_dir": final_models_dir,
                  "delta_s08b": chosen[cid].get("delta")})
        if is_top:
            hold = h
            summary["holdout"] = hold
            from champions_agent.config import BUILD_STRESS_ONLY_ON_PASS
            if should_run_stress((hold or {}).get("verdict"), BUILD_STRESS_ONLY_ON_PASS):
                rob = ST.run_stress(final_arm, ref_arm(), split, run_dir, seed + 5, n=stress_n, log=log, parallel=parallel)
                summary["robustness_worst"] = rob.get("worst_sensitivity_candidate")
                # ablation の A1: adapter を採用したらそれ (action 効果 = adapter − 基底)、無ければ前世代のチェックポイント
                alt_dir = final_models_dir if final_models_dir != models_dir else pop.get("prev")
                abl = AB.ablation_grid(arm_c.team_file, ref.team_file, final_model, ref_best.selection_model, models_dir,
                                       alt_dir, split, run_dir, seed + 6, n=ablation_n, log=log, parallel=parallel,
                                       ref_pick_policy=ref_best.pick_policy)
                summary["ablation"] = abl.get("effects")
            else:
                # 判断 #1 (2026-10-05): STRESS と ablation は PASS のときだけ (INCONCLUSIVE / FAIL の run では約 2.5 時間を省く)
                summary["stress_skipped"] = {"reason": f"holdout {(hold or {}).get('verdict')} (PASS でない)", "saved_stages": ["STRESS", "ablation"]}
                log(f"STRESS / ablation: 省略 (holdout {(hold or {}).get('verdict')}、BUILD_STRESS_ONLY_ON_PASS)")
        # S13: Package (1 位は final/、他の最終候補は final/alternatives/<cid>/)
        species = members_by.get(cid, [])
        out_dir = (run_dir / "final") if is_top else (run_dir / "final" / "alternatives" / cid)
        pk = build_package(run_dir, cid, arm_c.team_file, Path(final_model) if final_model else None, species,
                           registry=None, out_dir=out_dir, holdout_name=f"s12_holdout{tag}",
                           extra_manifest={"models_dir": final_models_dir, "base_models_dir": models_dir, "seed": seed,
                                           "pick_variant": chosen[cid]["variant"], "pick_policy": final_pick,
                                           "reference_variant": ref_variant, "finalist_rank": p["rank"],
                                           "direction": p.get("direction")})
        p["package_dir"] = pk.get("final_dir")
        if is_top:
            pkg = pk
        summary["finalists"] = picks
        _write_stage(run_dir, "summary", summary)
    summary["result"] = (hold or {}).get("verdict")
    _write_stage(run_dir, "summary", summary)
    # 記事 (表示専用): 1 位は LLM があれば Sonnet、他の最終候補はテンプレート (比較表つき)。registry 登録は記事を書いてから
    try:
        from tools.team_build.report import write_report
        write_report(run_dir, winner, provider=llm_provider)
        for p in picks[1:]:
            if p.get("package_dir"):
                write_report(run_dir, p["candidate_id"], provider=None,
                             out_path=Path(p["package_dir"]) / "build_report.md")
    except Exception as e:
        log(f"S13 report error: {e!r}")
    if registry is not None:
        for p in picks:
            if not p.get("package_dir"):
                continue
            try:
                row = registry.register("package", Path(p["package_dir"]),
                                        meta={"candidate_id": p["candidate_id"], "species": members_by.get(p["candidate_id"], []),
                                              "holdout": p.get("holdout"), "finalist_rank": p["rank"],
                                              "direction_ja": p.get("direction_ja")},
                                        run_id=run_dir.name, status="candidate")
                p["artifact_id"] = row["id"]
                if p["candidate_id"] == winner and pkg is not None:
                    pkg["artifact_id"] = row["id"]
            except Exception as e:
                log(f"registry error [{p['candidate_id']}]: {e!r}")
    summary["package"] = pkg
    summary["finalists"] = picks
    _write_stage(run_dir, "summary", summary)
    log(f"S13 package: {(pkg or {}).get('artifact_id')} verdict={(hold or {}).get('verdict')} 最終候補 {len(picks)} 並び")
    return summary
