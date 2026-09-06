"""選出方策 ablation: S8a の脱落規則を決めるための実験 (docs/TEAM_BUILDING_IMPLEMENTATION.md §12)。

同じチームを選出方策だけ変えて同一相手列 (SEARCH fold 1、同 seed、offset 0〜N) と戦わせる。

  teampreview : poke-env の teampreview_order (現行 S8a の screening 条件。production モデルの
                分布外チームで実助言が落ちる先でもある)
  generic     : 汎用基底の選出モデル GENERAL_MODEL_PATH を分布内判定を迂回して使う
                (S7 適応の起点。候補 screening に使えるかを見る)
  fresh       : そのチーム専用に汎用基底から新規適応した選出モデル (S7 と同じ手順、収集は SEARCH fold 0)
  production  : 実運用の MODEL_PATH (汎用 + 登録チームでの微調整) を参照だけに強制
                (参照が分布外なら実助言では teampreview に落ちる。強制した値は「分布内なら得られた値」)

出すもの (判定は 1 回なので z = BUILD_CI_Z):
  U_total   = fresh − teampreview        選出適応の上げ幅の合計
  U_generic = generic − teampreview      汎用モデルにするだけの上げ幅
  U_adapt   = fresh − generic            候補専用に微調整する上げ幅
  B         = production − fresh (参照)  登録チーム微調整による現行チームの優位
  Δ_tp / Δ_generic / Δ_fresh / Δ_final   候補 − 参照 (screening 条件 / 汎用同士 / 適応同士 / 候補 fresh vs 参照 production)
  順位反転  teampreview 順・generic 順が fresh 順と入れ替わった候補対と、その差が CI を超えるか (resolved)

S8a の teampreview 結果 (同 seed・同 fold・offset 連続) は再利用し、足りない分だけ測る。

    python -m tools.team_build.pick_ablation --run-id chat_0906 --candidates L00_C001,L02_C023,L03_C027
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from champions_agent.config import BUILD_CI_Z, BUILD_EQUIV_EPS, BUILD_PICK_ABLATION_N
from tools.team_build import adapt as AD
from tools.team_build import racing as R
from tools.team_build.verdict import binomial_halfwidth, verdict4

REPO = Path(__file__).resolve().parent.parent.parent
REFERENCE_ID = "reference"
CONDITIONS = ("teampreview", "generic", "fresh", "production")
STAGES = {"teampreview": "abl_tp", "generic": "abl_generic", "fresh": "abl_fresh", "production": "abl_prod"}


def log_line(log_path: Optional[Path], msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    if log_path:
        with log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


# ------------------------------------------------------------------ 再利用
def reuse_outcomes(eval_dir: Path, stage: str, arm_id: str, n_max: int) -> list:
    """<stage>_<arm>_<offset>_<n>.json を offset 順に連結し、0 から連続している範囲の勝敗列を返す (最大 n_max)"""
    files = []
    for p in eval_dir.glob(f"{stage}_{arm_id}_*_*.json"):
        parts = p.stem[len(f"{stage}_{arm_id}_"):].split("_")
        if len(parts) != 2 or not all(x.isdigit() for x in parts):
            continue
        files.append((int(parts[0]), int(parts[1]), p))
    out, expect = [], 0
    for offset, n, p in sorted(files):
        if offset != expect:
            break
        d = json.loads(p.read_text(encoding="utf-8"))
        outs = [int(x) for x in d.get("outcomes") or []]
        if len(outs) != n:
            break
        out.extend(outs)
        expect += n
        if len(out) >= n_max:
            break
    return out[:n_max]


# ------------------------------------------------------------------ 解析 (純粋)
def _cell(outcomes: list, z: float) -> dict:
    n = len(outcomes)
    wr = (sum(outcomes) / n) if n else None
    return {"n": n, "wins": int(sum(outcomes)), "win_rate": wr,
            "ci_halfwidth": binomial_halfwidth(wr, n, z) if n else None}


def _paired(a: list, b: list, eps: float, z: float) -> Optional[dict]:
    if not a or not b:
        return None
    return verdict4(a, b, eps=eps, z=z).to_dict()


def _order(cells: dict, teams: list, cond: str) -> list:
    rows = [(t, cells.get(f"{t}/{cond}", {}).get("win_rate")) for t in teams]
    rows = [(t, w) for t, w in rows if w is not None]
    return [t for t, _ in sorted(rows, key=lambda r: -r[1])]


def _reversals(results: dict, order_a: list, order_b: list, cond_a: str, cond_b: str,
               eps: float, z: float) -> list:
    """cond_a の順位で上だった対が cond_b で逆転した対。差が両条件で CI を超えていれば resolved"""
    out = []
    for i, a in enumerate(order_a):
        for b in order_a[i + 1:]:
            if a in order_b and b in order_b and order_b.index(a) > order_b.index(b):
                d_a = _paired(results.get((a, cond_a)), results.get((b, cond_a)), eps, z) or {}
                d_b = _paired(results.get((a, cond_b)), results.get((b, cond_b)), eps, z) or {}
                resolved = bool(d_a.get("ci_low") is not None and d_b.get("ci_high") is not None
                                and d_a["ci_low"] > 0 and d_b["ci_high"] < 0)
                out.append({"above_in_" + cond_a: a, "above_in_" + cond_b: b,
                            "delta_" + cond_a: d_a, "delta_" + cond_b: d_b, "resolved": resolved})
    return out


def analyze(results: dict, candidates: list, reference_id: str = REFERENCE_ID,
            eps: float = BUILD_EQUIV_EPS, z: float = BUILD_CI_Z) -> dict:
    """results[(team_id, condition)] = 勝敗列 (同一相手列、offset 順)。純粋関数"""
    def get(team, cond):
        return list(results.get((team, cond)) or [])

    teams = list(candidates) + [reference_id]
    cells = {f"{t}/{c}": _cell(get(t, c), z) for (t, c) in results if get(t, c)}
    uplift = {}
    for t in teams:
        u = {"total": _paired(get(t, "fresh"), get(t, "teampreview"), eps, z),
             "generic": _paired(get(t, "generic"), get(t, "teampreview"), eps, z),
             "adapt": _paired(get(t, "fresh"), get(t, "generic"), eps, z)}
        if any(v for v in u.values()):
            uplift[t] = u
    incumbent = _paired(get(reference_id, "production"), get(reference_id, "fresh"), eps, z)
    vs_ref = {}
    for t in candidates:
        d = {"delta_tp": _paired(get(t, "teampreview"), get(reference_id, "teampreview"), eps, z),
             "delta_generic": _paired(get(t, "generic"), get(reference_id, "generic"), eps, z),
             "delta_fresh": _paired(get(t, "fresh"), get(reference_id, "fresh"), eps, z),
             "delta_final": _paired(get(t, "fresh"), get(reference_id, "production") or get(reference_id, "fresh"),
                                    eps, z)}
        vs_ref[t] = {k: v for k, v in d.items() if v}
    orders = {c: _order(cells, candidates, c) for c in ("teampreview", "generic", "fresh")}
    reversals = {"teampreview_vs_fresh": _reversals(results, orders["teampreview"], orders["fresh"],
                                                    "teampreview", "fresh", eps, z),
                 "generic_vs_fresh": _reversals(results, orders["generic"], orders["fresh"],
                                                "generic", "fresh", eps, z)}
    return {"cells": cells, "uplift": uplift, "incumbent_advantage": incumbent, "vs_reference": vs_ref,
            "orders": orders, "reversals": reversals, "eps": eps, "z": z}


def _fmt(p: Optional[dict]) -> str:
    if not p or p.get("mean") is None:
        return "-"
    return f"{p['mean']:+.3f} [{p['ci_low']:+.3f}, {p['ci_high']:+.3f}] {p['state']}"


def to_markdown(a: dict, candidates: list, reference_id: str = REFERENCE_ID) -> str:
    teams = list(candidates) + [reference_id]
    lines = ["| Team | " + " | ".join(CONDITIONS) + " |", "|---|" + "---:|" * len(CONDITIONS)]
    for t in teams:
        row = [t]
        for c in CONDITIONS:
            cell = a["cells"].get(f"{t}/{c}")
            row.append(f"{cell['win_rate']:.3f} (n={cell['n']})" if cell else "-")
        lines.append("| " + " | ".join(row) + " |")
    lines += ["", "| Team | U_total (fresh − tp) | U_generic (generic − tp) | U_adapt (fresh − generic) |",
              "|---|---|---|---|"]
    for t in teams:
        u = a["uplift"].get(t, {})
        lines.append(f"| {t} | {_fmt(u.get('total'))} | {_fmt(u.get('generic'))} | {_fmt(u.get('adapt'))} |")
    lines += ["", f"B_incumbent (参照: production − fresh) = {_fmt(a.get('incumbent_advantage'))}", "",
              "| 候補 | Δ_tp | Δ_generic | Δ_fresh | Δ_final (候補 fresh − 参照 production) |", "|---|---|---|---|---|"]
    for t in candidates:
        d = a["vs_reference"].get(t, {})
        lines.append(f"| {t} | {_fmt(d.get('delta_tp'))} | {_fmt(d.get('delta_generic'))} | "
                     f"{_fmt(d.get('delta_fresh'))} | {_fmt(d.get('delta_final'))} |")
    lines.append("")
    for c, order in a["orders"].items():
        lines.append(f"順位 ({c}): {' > '.join(order) if order else '-'}")
    for key, revs in a["reversals"].items():
        cond_a, cond_b = key.split("_vs_")
        if not revs:
            lines.append(f"- 順位反転なし ({cond_a} → {cond_b})")
        for r in revs:
            lines.append(f"- 反転 ({cond_a} → {cond_b}): {r['above_in_' + cond_a]} > {r['above_in_' + cond_b]} "
                         f"が逆転。差 {cond_a} {_fmt(r['delta_' + cond_a])} / {cond_b} {_fmt(r['delta_' + cond_b])} / "
                         f"{'CI 超え (resolved)' if r['resolved'] else '誤差の範囲'}")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ 実行
def _run_jobs(jobs: list, parallel: int, log) -> dict:
    """jobs: [(key, arm, offset, n, out_json, cmd, log_path)] → {key: {"outcomes", "stats"} | None}"""
    results = {}
    timeout = 180 + R.SEC_PER_BATTLE * max((j[3] for j in jobs), default=0)
    with ThreadPoolExecutor(max_workers=max(1, parallel)) as ex:
        futs = {ex.submit(R._run_one, cmd, log_path, timeout): (key, out_json)
                for key, arm, offset, n, out_json, cmd, log_path in jobs}
        for fut in futs:
            key, out_json = futs[fut]
            rc = fut.result()
            if rc != 0 or not out_json.exists():
                log(f"[ablation] {key} failed rc={rc}")
                results[key] = None
                continue
            d = json.loads(out_json.read_text(encoding="utf-8"))
            st = d.get("stats") or {}
            log(f"[ablation] {key}: wr={d.get('win_rate')} n={d.get('n_battles')} "
                f"pick_model={st.get('pick_model', 0)} pick_fallback={st.get('pick_fallback', 0)}")
            results[key] = {"outcomes": [int(x) for x in d.get("outcomes") or []], "stats": st}
    return results


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--candidates", required=True, help="s06_sets の候補 id (カンマ区切り)")
    ap.add_argument("--n", type=int, default=BUILD_PICK_ABLATION_N)
    ap.add_argument("--parallel", type=int, default=R.PARALLEL)
    ap.add_argument("--seed", type=int, default=None, help="既定は S8a の seed (同一相手列で再利用するため)")
    ap.add_argument("--reuse-stage", default="s08a_screen", help="teampreview 結果を再利用する stage 名 (空で再利用しない)")
    ap.add_argument("--skip-adapt", action="store_true", help="既存の適応モデル (adapt_dir) をそのまま使う")
    ap.add_argument("--adapt-dir", default=None, help="既定 run/advisors_ablation")
    ap.add_argument("--out", default=None, help="既定 run/evaluation/pick_ablation")
    args = ap.parse_args(argv)

    from champions_agent.agent.selection_model import GENERAL_MODEL_PATH, MODEL_PATH
    from champions_agent.agent import selection_model as sm
    from tools.team_build.opponents import parse_team_text

    run_dir = REPO / "logs" / "build_search" / "runs" / args.run_id
    eval_dir = run_dir / "evaluation"
    out_dir = Path(args.out) if args.out else eval_dir / "pick_ablation"
    out_dir.mkdir(parents=True, exist_ok=True)
    adapt_dir = Path(args.adapt_dir) if args.adapt_dir else run_dir / "advisors_ablation"
    log_path = out_dir / "ablation.log"
    log = lambda m: log_line(log_path, m)   # noqa: E731

    s8a_path = eval_dir / f"{args.reuse_stage}.json"
    s8a = json.loads(s8a_path.read_text(encoding="utf-8")) if args.reuse_stage and s8a_path.exists() else {}
    seed = args.seed if args.seed is not None else int(s8a.get("seed", 0))
    fold = s8a.get("fold", 1)
    tier = s8a.get("tier", "search")
    models_dir = (s8a.get("reference") or {}).get("models_dir")
    split_file = run_dir / "opponent_families.json"
    candidates = [c.strip() for c in args.candidates.split(",") if c.strip()]
    teams = {cid: run_dir / "s06_sets" / f"{cid}.txt" for cid in candidates}
    teams[REFERENCE_ID] = run_dir / "reference_team.txt"
    for cid, tf in teams.items():
        if not tf.exists():
            raise SystemExit(f"チーム本文が無い: {tf}")
    if not Path(GENERAL_MODEL_PATH).exists():
        raise SystemExit(f"汎用選出モデルが無い: {GENERAL_MODEL_PATH}")
    log(f"pick ablation run={args.run_id} candidates={candidates} n={args.n} seed={seed} tier={tier} fold={fold} "
        f"models_dir={models_dir} generic={GENERAL_MODEL_PATH} production={MODEL_PATH}")
    ref_species = sorted(parse_team_text(teams[REFERENCE_ID].read_text(encoding="utf-8"))[0])
    ref_in_dist = bool(sm.is_in_distribution(ref_species))
    log(f"reference in_distribution={ref_in_dist} species={ref_species} "
        f"(分布外なら実助言の選出は teampreview に落ちている。production 条件は強制して測る)")

    # 1) fresh 適応 (S7 と同じ手順、汎用起点、SEARCH fold 0)。順次 (学習ループと CPU を奪い合わないため)
    models = {}
    for cid, tf in teams.items():
        model = adapt_dir / cid / "selection_model.pt"
        if args.skip_adapt and model.exists():
            models[cid] = str(model)
            log(f"[adapt] {cid}: 既存モデルを再利用 {model}")
            continue
        t0 = time.time()
        r = AD.adapt_selection(cid, tf, split_file, adapt_dir, seed, log=log)
        models[cid] = r.get("model")
        log(f"[adapt] {cid}: model={models[cid]} n={r.get('n_battles')} stop={r.get('stop_reason')} "
            f"{time.time() - t0:.0f}s")
    (out_dir / "adapt_models.json").write_text(json.dumps(models, ensure_ascii=False, indent=1) + "\n",
                                                encoding="utf-8")

    # 2) 測定 (同一相手列)。teampreview は S8a を再利用して不足分だけ
    def job(cid, cond, model, offset, n):
        stage = STAGES[cond]
        arm = R.Arm(cid, teams[cid], model, models_dir, pick_policy="teampreview" if cond == "teampreview" else "advisor")
        out_json = out_dir / f"{stage}_{cid}_{offset}_{n}.json"
        cmd = R.measure_cmd(arm, n, offset, seed, split_file, tier, fold, out_json,
                            out_dir / "battles" / f"{stage}_{cid}.jsonl")
        return ((cid, cond), arm, offset, n, out_json, cmd, out_dir / f"{stage}_{cid}.log")

    results, jobs = {}, []
    for cid in teams:
        have = reuse_outcomes(eval_dir, args.reuse_stage, cid, args.n) if args.reuse_stage else []
        results[(cid, "teampreview")] = have
        if len(have) < args.n:
            jobs.append(job(cid, "teampreview", None, len(have), args.n - len(have)))
        jobs.append(job(cid, "generic", str(GENERAL_MODEL_PATH), 0, args.n))
        if models.get(cid):
            jobs.append(job(cid, "fresh", models[cid], 0, args.n))
    jobs.append(job(REFERENCE_ID, "production", str(MODEL_PATH), 0, args.n))
    log(f"measure jobs={len(jobs)} parallel={args.parallel} "
        f"battles={sum(j[3] for j in jobs)} reused_tp={ {k[0]: len(v) for k, v in results.items()} }")
    measured = _run_jobs(jobs, args.parallel, log)
    stats = {}
    for key, r in measured.items():
        if r is None:
            continue
        results[key] = list(results.get(key) or []) + r["outcomes"]
        stats[f"{key[0]}/{key[1]}"] = r["stats"]

    # 3) 解析
    a = analyze(results, candidates)
    a["stats"] = stats
    a["config"] = {"run_id": args.run_id, "n": args.n, "seed": seed, "tier": tier, "fold": fold,
                   "models_dir": models_dir, "adapt_models": models, "generic_model": str(GENERAL_MODEL_PATH),
                   "production_model": str(MODEL_PATH), "reference_in_distribution": ref_in_dist,
                   "note": "1 回判定なので z=BUILD_CI_Z。S8a と同じ相手列 (同 seed・同 fold) を再利用。"
                           "generic / production は --selection-model で分布内判定を迂回して強制"}
    (out_dir / "pick_ablation.json").write_text(json.dumps(a, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    md = to_markdown(a, candidates)
    (out_dir / "pick_ablation.md").write_text(md, encoding="utf-8")
    log("done")
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
