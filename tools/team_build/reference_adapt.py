"""参照チームの対照実験: 候補と同じ深さの選出モデル適応 (S7 相当) を参照 (登録チーム) にも与え、
勝者・参照 (手順どおりの選出方策)・参照+適応 の 3 腕を同一相手列 (SELECTION 階層) で対応比較する。

  python -m tools.team_build.reference_adapt --run-id rule_0913 [--n 600] [--parallel 3] [--seed 20260913]
  背景で回す: bash scripts/team_build_reference_adapt.sh rule_0913 [引数...]

背景: 測定の手順 (pipeline.run_measurement) では参照は S8a の cheap adaptation (BUILD_SCREEN_ADAPT_BATTLES 戦で
1 回学習) までしか適応されず、候補は S7 で収束まで (BUILD_ADAPT_MIN_BATTLES 戦以上) 適応される。rule_0913 の
ablation は「チーム単独 (同じ選出方策) の効果 −0.093、候補専用の選出モデルの効果 +0.147」で、候補の優位が
適応の深さの差だけで説明できる可能性がある。参照+適応が同じだけ上がるなら、候補のチームそのものに優位は無い。

出すもの (evaluation/reference_full.json、run.log に [ref_full] の行):
  adapt_effect    = 参照+適応 − 参照      参照が適応で上がる幅 (候補の pick 効果 +0.147 と比べる)
  winner_vs_full  = 勝者 − 参照+適応      公平な条件での勝者の優位 (本命)
  winner_vs_base  = 勝者 − 参照           手順どおりの比較の再現 (S10 / holdout との整合の確認)
封印 holdout の相手列は使わない (SELECTION 階層、seed は run の seed + 11)。
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Optional

from champions_agent.config import (BUILD_ADAPT_MIN_BATTLES, BUILD_ADAPT_VALIDATE_MAX_CKPTS, BUILD_ADAPT_VALIDATE_N,
                                    BUILD_FOLD_VALIDATE, BUILD_REFERENCE_CONTROL_BATTLES)
from tools.team_build import adapt as AD
from tools.team_build import racing as R
from tools.team_build.verdict import verdict4

REPO = Path(__file__).resolve().parent.parent.parent
ARM_REF = "reference"
ARM_FULL = "reference_full"
STAGE = "reference_full"
SEED_ADAPT, SEED_VALIDATE, SEED_MEASURE = 9, 10, 11      # run の seed からのずらし (pipeline は 0〜8 を使う)


def _log(run_dir: Path, msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] [ref_full] {msg}"
    print(line, flush=True)
    with (run_dir / "run.log").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _load(p: Path) -> Optional[dict]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


# ------------------------------------------------------------------ 純粋関数 (テスト対象)
def control_arms(run_dir: Path, summary: dict, manifest: dict, full_model: Optional[str]) -> list:
    """3 腕: 参照 (手順どおり: S8a で選んだ variant)、参照+適応 (full_model を advisor で使う)、
    勝者 (Package の選出モデル。無ければ S8b で選んだ variant のモデル)"""
    models_dir = summary.get("models_dir")
    rv = summary.get("reference_variant") or {}
    ref_team = run_dir / "reference_team.txt"
    winner = summary.get("winner")
    wv = (summary.get("s08b_variants") or {}).get(winner) or {}
    pkg_model = run_dir / "final" / "advisor_policy" / "selection_model.pt"
    win_model = str(pkg_model) if pkg_model.exists() else wv.get("selection_model")
    return [
        R.Arm(ARM_REF, ref_team, rv.get("selection_model"), models_dir, pick_policy=rv.get("pick_policy") or "teampreview"),
        R.Arm(ARM_FULL, ref_team, full_model, models_dir, pick_policy="advisor"),
        R.Arm(winner, run_dir / "s06_sets" / f"{winner}.txt", win_model, manifest.get("models_dir") or models_dir,
              pick_policy=wv.get("pick_policy") or "advisor"),
    ]


def summarize(outcomes: dict, winner: str) -> dict:
    """勝敗列 {arm_id: [1/0, ...]} → 勝率と 3 つの対応比較"""
    def wr(k):
        o = outcomes.get(k) or []
        return round(sum(o) / len(o), 4) if o else None

    def d(a, b):
        return verdict4(outcomes.get(a) or [], outcomes.get(b) or []).to_dict()

    return {"win_rates": {k: wr(k) for k in outcomes},
            "adapt_effect": d(ARM_FULL, ARM_REF),
            "winner_vs_full": d(winner, ARM_FULL),
            "winner_vs_base": d(winner, ARM_REF)}


def _fmt(x: Optional[float]) -> str:
    return f"{x:+.3f}" if isinstance(x, (int, float)) else "?"


def interpret(res: dict) -> str:
    """1 行の読み: 参照が適応でどれだけ上がり、公平な条件で勝者が残るか"""
    ae, wf, wb = res["adapt_effect"], res["winner_vs_full"], res["winner_vs_base"]
    return (f"参照の適応 {_fmt(ae.get('mean'))} ({ae.get('state')}) / 勝者−参照+適応 {_fmt(wf.get('mean'))} "
            f"[{_fmt(wf.get('ci_low'))}, {_fmt(wf.get('ci_high'))}] ({wf.get('state')}) / "
            f"勝者−参照 {_fmt(wb.get('mean'))} ({wb.get('state')})")


# ------------------------------------------------------------------ 実行
def adapt_reference(run_dir: Path, ref_team: Path, split: Path, seed: int, models_dir: str, parallel: int, log) -> dict:
    """S7 と同じ手順: fold A で収束まで適応 (checkpoint を残す) → 独立 fold V の実測で checkpoint を選ぶ。
    advisors/reference_full/adapt_result.json があれば再利用 (途中で止めた実験の再開)"""
    out = run_dir / "advisors" / ARM_FULL
    prev = _load(out / "adapt_result.json")
    if prev and prev.get("model") and Path(prev["model"]).exists():
        log(f"adapt: resume (n={prev.get('n_battles')}{', 検証済み' if (prev.get('validated') or {}).get('chosen') else ''})")
        r = prev
    else:
        r = AD.adapt_selection(ARM_FULL, ref_team, split, run_dir / "advisors", seed + SEED_ADAPT,
                               min_battles=BUILD_ADAPT_MIN_BATTLES, log=log, keep_checkpoints=True)
    ckpts = AD.checkpoints_from_history(r.get("history"))
    if (r.get("validated") or {}).get("chosen") and Path(r["validated"]["chosen"]).exists():
        r["model"] = r["validated"]["chosen"]
    elif ckpts:
        sel = AD.select_checkpoint(ARM_FULL, ref_team, ckpts, split, seed + SEED_VALIDATE, models_dir, out / "validate",
                                   parallel=parallel, log=log, fold=BUILD_FOLD_VALIDATE, n=BUILD_ADAPT_VALIDATE_N,
                                   max_ckpts=BUILD_ADAPT_VALIDATE_MAX_CKPTS)
        r["validated"] = sel
        if sel.get("chosen"):
            r["model_last"] = r.get("model")
            r["model"] = sel["chosen"]
        (out / "adapt_result.json").write_text(json.dumps(r, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return r


def main() -> None:
    ap = argparse.ArgumentParser(description="参照チームの対照実験 (参照にも候補と同じ深さの選出モデル適応を与えて比べる)")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--n", type=int, default=BUILD_REFERENCE_CONTROL_BATTLES, help="3 腕それぞれの戦数 (同一相手列)")
    ap.add_argument("--parallel", type=int, default=R.PARALLEL)
    ap.add_argument("--seed", type=int, default=None, help="省略時は final/manifest.json の seed (run と同じ)")
    args = ap.parse_args()
    run_dir = REPO / "logs" / "build_search" / "runs" / args.run_id
    summary = _load(run_dir / "evaluation" / "summary.json") or {}
    manifest = _load(run_dir / "final" / "manifest.json") or {}
    if not summary.get("winner"):
        raise SystemExit("evaluation/summary.json に winner が無い (測定が終わった run を指定する)")
    seed = args.seed if args.seed is not None else int(manifest.get("seed") or 0)
    split = run_dir / "opponent_families.json"
    models_dir = summary["models_dir"]

    def log(msg: str) -> None:
        _log(run_dir, msg)

    log(f"start: winner={summary['winner']} n={args.n} seed={seed} parallel={args.parallel} models_dir={models_dir}")
    r = adapt_reference(run_dir, run_dir / "reference_team.txt", split, seed, models_dir, args.parallel, log)
    if not r.get("model"):
        log(f"adapt failed ({r.get('stop_reason')}) → 終了")
        raise SystemExit(1)
    log(f"adapt done: n={r.get('n_battles')} stop={r.get('stop_reason')} model={r['model']}")
    arms = control_arms(run_dir, summary, manifest, r["model"])
    eval_dir = run_dir / "evaluation"
    log("measure: " + ", ".join(f"{a.arm_id}({a.pick_policy}{', model' if a.selection_model else ''})" for a in arms))
    R.measure_round(arms, args.n, 0, seed + SEED_MEASURE, split, "selection", None, eval_dir, STAGE, parallel=args.parallel)
    res = summarize({a.arm_id: [int(x) for x in a.outcomes] for a in arms}, summary["winner"])
    res.update({"n": args.n, "seed": seed + SEED_MEASURE, "tier": "selection", "reference_full_model": r["model"],
                "reference_full_n_battles": r.get("n_battles"), "validated": r.get("validated"),
                "arms": [a.to_dict() for a in arms]})
    (eval_dir / f"{STAGE}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    log("完了: " + interpret(res) + f" (勝率 {res['win_rates']})")


if __name__ == "__main__":
    main()
