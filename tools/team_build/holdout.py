"""封印 holdout の最終測定 (S12)。

- 決定候補 (Team + 適応済み選出モデル) と参照 (production) を HOLDOUT 階層で一度だけ対応比較する
- 探索プロセスに返すのは PASS / FAIL / INCONCLUSIVE のみ。人向けに ΔWR と CI。
  相手別の結果・敗因・battle log は sealed/ に書き、pipeline も LLM も読まない (run 終了後に人が開く)
- 同じ封印 holdout を同じ候補に使い回さない: holdout_usage.jsonl に記録し、再利用は警告
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from champions_agent.config import BUILD_EQUIV_EPS, BUILD_RACE_DEFAULT_MAX, BUILD_RACE_STEPS
from tools.team_build import racing as R
from tools.team_build.verdict import DEGRADED, EQUIVALENT, IMPROVED, UNCERTAIN

REPO = Path(__file__).resolve().parent.parent.parent
USAGE_LOG = REPO / "logs" / "build_search" / "holdout_usage.jsonl"
STATE_TO_VERDICT = {IMPROVED: "PASS", EQUIVALENT: "PASS_EQUIVALENT", DEGRADED: "FAIL", UNCERTAIN: "INCONCLUSIVE"}


def holdout_used_before(sealed_id: str, candidate_key: str) -> list:
    if not USAGE_LOG.exists():
        return []
    rows = [json.loads(l) for l in USAGE_LOG.read_text(encoding="utf-8").splitlines() if l.strip()]
    return [r for r in rows if r.get("sealed_id") == sealed_id and r.get("candidate_key") == candidate_key]


def final_holdout(candidate: R.Arm, reference: R.Arm, split_file: Path, sealed_id: str, run_dir: Path,
                  seed: int, candidate_key: str, steps: tuple = BUILD_RACE_STEPS,
                  max_battles: int = BUILD_RACE_DEFAULT_MAX, eps: float = BUILD_EQUIV_EPS,
                  log=print, parallel: int = 2) -> dict:
    """戻り値 (process 向け): {"verdict": PASS|PASS_EQUIVALENT|FAIL|INCONCLUSIVE, "delta", "ci", "n", "sealed_id"}.
    詳細は run_dir/sealed/holdout_<sealed_id>/ に封印"""
    prior = holdout_used_before(sealed_id, candidate_key)
    if prior:
        log(f"[holdout] ⚠ 同じ封印 holdout {sealed_id} を同じ候補 {candidate_key} に再利用 (前回: {prior[-1].get('at')})。"
            "修正→再評価は holdout ではない。次 run は新しい封印を使うこと")
    sealed_dir = run_dir / "sealed" / f"holdout_{sealed_id}"
    sealed_dir.mkdir(parents=True, exist_ok=True)
    res = R.race([candidate], reference, split_file, "holdout", seed, sealed_dir, stage="s12_holdout",
                 steps=steps, max_battles=max_battles, eps=eps, parallel=parallel, log=lambda m: None,
                 compare_to_best=False)
    arm = res["arms"][0]
    r = arm.get("result") or {}
    verdict = STATE_TO_VERDICT.get(arm.get("state"), "INCONCLUSIVE")
    summary = {"verdict": verdict, "delta": r.get("mean"), "ci": [r.get("ci_low"), r.get("ci_high")],
               "n": arm.get("n_done"), "sealed_id": sealed_id, "eps": eps,
               "note": "詳細 (相手別・敗因・記録) は sealed/ に封印。この holdout で同じ候補を修正→再評価しない"}
    (run_dir / "evaluation").mkdir(parents=True, exist_ok=True)
    (run_dir / "evaluation" / "s12_holdout.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with USAGE_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"sealed_id": sealed_id, "candidate_key": candidate_key, "run_id": run_dir.name,
                            "verdict": verdict, "at": time.strftime("%Y-%m-%d %H:%M:%S")}, ensure_ascii=False) + "\n")
    log(f"[holdout] {verdict} Δ={summary['delta']} CI={summary['ci']} n={summary['n']} (sealed {sealed_id})")
    return summary
