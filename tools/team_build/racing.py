"""Confidence racing (S8 / S10 / S12): 候補 × 参照を同一相手列で対応比較し、必要な精度に達するまで戦数を足す。

- 各候補と参照 (現行パーティ + その選出モデル) を check_advisor_player で同じ階層・同じ seed・同じ offset で回す
- 対応差 Δ の CI と ε で 4 状態 (improved / equivalent / degraded / uncertain)
- uncertain の候補だけ次の戦数段階へ (BUILD_RACE_STEPS)。degraded は脱落。
  上限は既定 BUILD_RACE_DEFAULT_MAX (uncertain 終了可)。重要候補は max_battles で延長できる
- 「best である可能性が残る候補」= 現在の best に対して degraded でない候補
- n_candidates_seen を記録し、探索時の最高値を期待勝率として報告しない (Winner's curse)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from champions_agent.config import (BUILD_EQUIV_EPS, BUILD_RACE_DEFAULT_MAX, BUILD_RACE_MIN_TERMINAL_N,
                                    BUILD_RACE_STEPS)
from tools.team_build.verdict import (DEGRADED, EQUIVALENT, IMPROVED, UNCERTAIN, look_z, n_looks, next_step,
                                      verdict4)

REPO = Path(__file__).resolve().parent.parent.parent
PARALLEL = 5                # 同時に回す測定プロセス数 (config 化候補)


@dataclass
class Arm:
    arm_id: str
    team_file: Path
    selection_model: Optional[str] = None
    models_dir: Optional[str] = None
    extra_args: list = field(default_factory=list)
    pick_policy: Optional[str] = None      # arm 固有の選出方策 (None なら round の指定に従う)
    plan_file: Optional[str] = None        # 構築の選出計画 (s06_sets/<cid>.plan.json)。advisor の選出で計画の事前を足す
    outcomes: list = field(default_factory=list)
    n_done: int = 0
    state: str = UNCERTAIN
    result: Optional[dict] = None
    history: list = field(default_factory=list)
    eliminated_at: Optional[int] = None

    def to_dict(self) -> dict:
        return {"arm_id": self.arm_id, "team_file": str(self.team_file), "selection_model": self.selection_model,
                "models_dir": self.models_dir, "extra_args": self.extra_args, "pick_policy": self.pick_policy,
                "plan_file": self.plan_file, "n_done": self.n_done,
                "wins": int(sum(self.outcomes)), "win_rate": (sum(self.outcomes) / len(self.outcomes)) if self.outcomes else None,
                "state": self.state, "result": self.result, "history": self.history,
                "eliminated_at": self.eliminated_at}


def measure_cmd(arm: Arm, n: int, offset: int, seed: int, split_file: Path, tier: str,
                fold: Optional[int], out_json: Path, battle_log: Path, pick_policy: str = "advisor") -> list:
    cmd = [sys.executable, "-m", "tools.check_advisor_player", "--battles", str(n), "--opp-seed", str(seed),
           "--skip-random", "--belief-k", "0", "--team-file", str(arm.team_file),
           "--opp-split", f"{split_file}:{tier}" + (f":{fold}" if fold is not None else ""),
           "--opp-offset", str(offset), "--pick-policy", arm.pick_policy or pick_policy,
           "--battle-log", str(battle_log), "--json", str(out_json), "--candidate-id", arm.arm_id]
    if arm.selection_model:
        cmd += ["--selection-model", arm.selection_model]
    if arm.models_dir:
        cmd += ["--models-dir", arm.models_dir]
    if arm.plan_file and (arm.pick_policy or pick_policy) == "advisor":
        cmd += ["--selection-plan", str(arm.plan_file)]
    cmd += list(arm.extra_args)
    return cmd


def _run_one(cmd: list, log_path: Path, timeout: int) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as lf:
        try:
            res = subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT, cwd=str(REPO), timeout=timeout)
        except subprocess.TimeoutExpired:
            lf.write(f"\n[racing] timeout {timeout}s\n")
            return 124
    return res.returncode


SEC_PER_BATTLE = 40         # subprocess timeout の見積もり (実測 5 並列で約 6 秒/戦 + 起動)


def _load_measured(out_json: Path, n: int) -> Optional[dict]:
    """測定済み JSON (outcomes が n 件そろっているもの) を読む。無ければ None"""
    if not out_json.exists():
        return None
    try:
        d = json.loads(out_json.read_text(encoding="utf-8"))
    except Exception:
        return None
    outs = [int(x) for x in d.get("outcomes") or []]
    if len(outs) != n:
        return None
    d["outcomes"] = outs
    return d


def measure_round(arms: list, n: int, offset: int, seed: int, split_file: Path, tier: str, fold: Optional[int],
                  out_dir: Path, stage: str, parallel: int = PARALLEL, timeout: Optional[int] = None,
                  pick_policy: str = "advisor") -> None:
    """全 arm を同じ (offset, n) で回し、outcomes を追記する。timeout は戦数に比例 (未指定時)"""
    out_dir.mkdir(parents=True, exist_ok=True)
    if timeout is None:
        timeout = 180 + SEC_PER_BATTLE * n
    jobs = []
    for arm in arms:
        out_json = out_dir / f"{stage}_{arm.arm_id}_{offset}_{n}.json"
        prev = _load_measured(out_json, n)
        if prev is not None:
            # 同じ (stage, arm, offset, n) の測定済み JSON があればそのまま使う (途中で止めた run の再開)
            arm.outcomes.extend(prev["outcomes"])
            arm.n_done = len(arm.outcomes)
            arm.history.append({"offset": offset, "n": n, "win_rate": prev.get("win_rate"), "reused": True,
                                "latency_p50_ms": prev.get("latency_p50_ms"), "stats": prev.get("stats")})
            continue
        cmd = measure_cmd(arm, n, offset, seed, split_file, tier, fold, out_json,
                          out_dir / "battles" / f"{stage}_{arm.arm_id}.jsonl", pick_policy)
        jobs.append((arm, out_json, cmd))
    with ThreadPoolExecutor(max_workers=max(1, parallel)) as ex:
        futs = {ex.submit(_run_one, cmd, out_dir / f"{stage}_{arm.arm_id}.log", timeout): (arm, out_json)
                for arm, out_json, cmd in jobs}
        for fut in futs:
            arm, out_json = futs[fut]
            rc = fut.result()
            if rc != 0 or not out_json.exists():
                arm.history.append({"offset": offset, "n": n, "error": f"rc={rc}"})
                continue
            d = json.loads(out_json.read_text(encoding="utf-8"))
            arm.outcomes.extend(int(x) for x in d.get("outcomes") or [])
            arm.n_done = len(arm.outcomes)
            arm.history.append({"offset": offset, "n": n, "win_rate": d.get("win_rate"),
                                "latency_p50_ms": d.get("latency_p50_ms"), "stats": d.get("stats")})


def race(candidates: list, reference: Arm, split_file: Path, tier: str, seed: int, out_dir: Path,
         stage: str = "s08", fold: Optional[int] = None, steps: tuple = BUILD_RACE_STEPS,
         max_battles: int = BUILD_RACE_DEFAULT_MAX, eps: float = BUILD_EQUIV_EPS,
         parallel: int = PARALLEL, log=print, compare_to_best: bool = True,
         pick_policy: str = "advisor", min_terminal_n: int = BUILD_RACE_MIN_TERMINAL_N,
         run_to_max: bool = False) -> dict:
    """候補群 vs 参照の confidence racing。戻り値は evaluation/<stage>.json と同じ dict。
    improved / equivalent は min_terminal_n 戦未満では確定させず測り続ける (degraded の早期脱落は残す)。
    run_to_max なら状態によらず上限まで測る (封印 holdout)"""
    arms = list(candidates)
    active = list(arms)
    offset = 0
    t0 = time.time()
    rounds = []
    # 段階ごとに同じ候補を判定し直す (optional stopping) ので、段数 K に応じた z を使う
    k_looks = n_looks(steps, cap=max_battles)
    z = look_z(k_looks)
    log(f"[racing:{stage}] looks={k_looks} z={z:.3f} eps={eps}")
    while active:
        target = next_step(offset, steps, cap=max_battles)
        if target is None:
            break
        n = target - offset
        log(f"[racing:{stage}] round offset={offset} n={n} arms={len(active)}+ref")
        measure_round(active + [reference], n, offset, seed, split_file, tier, fold, out_dir, stage,
                      parallel=parallel, pick_policy=pick_policy)
        offset = target
        # 参照との対応差
        for arm in active:
            r = verdict4(arm.outcomes, reference.outcomes, eps=eps, z=z)
            arm.result = r.to_dict()
            arm.state = r.state
        # best (参照との Δ が最大) に対する対応差で「可能性が残る候補」を判定
        best = max(active, key=lambda a: (a.result or {}).get("mean") or -1.0)
        still = []
        for arm in active:
            if arm is best:
                still.append(arm)
                continue
            vb = verdict4(arm.outcomes, best.outcomes, eps=eps, z=z)
            if compare_to_best and vb.state == DEGRADED:
                arm.eliminated_at = offset
                arm.state = DEGRADED if arm.state == UNCERTAIN else arm.state
                arm.history.append({"eliminated_vs_best": best.arm_id, "delta": vb.mean, "se": vb.se})
                continue
            still.append(arm)
        rounds.append({"offset": offset, "n_active": len(still),
                       "states": {a.arm_id: a.state for a in active}})
        # 参照との判定が確定 (improved / degraded / equivalent) した候補は追加測定しない。ただし improved / equivalent は
        # min_terminal_n 戦未満では暫定扱いで測り続ける (2026-09-10: 100 戦の improved が別分割で反転した件)。
        # run_to_max (封印 holdout) は状態によらず上限まで
        active = [a for a in still if run_to_max or a.state == UNCERTAIN
                  or (a.state in (IMPROVED, EQUIVALENT) and offset < min_terminal_n)]
        log(f"[racing:{stage}] after {offset}: " +
            ", ".join(f"{a.arm_id}={a.state}({(a.result or {}).get('mean') or 0:+.3f})" for a in arms))
    result = {
        "stage": stage, "tier": tier, "fold": fold, "seed": seed, "eps": eps, "steps": list(steps),
        "pick_policy": pick_policy, "n_looks": k_looks, "z": z, "min_terminal_n": min_terminal_n, "run_to_max": run_to_max,
        "max_battles": max_battles, "n_candidates_seen": len(arms), "elapsed_s": round(time.time() - t0, 1),
        "reference": reference.to_dict(), "arms": [a.to_dict() for a in arms], "rounds": rounds,
        "note": "探索時の最高値は期待勝率ではない (Winner's curse)。採否は holdout の結果だけで決める",
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{stage}.json").write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return result


def contenders(result: dict) -> list:
    """racing 結果から「best である可能性が残る候補」(脱落していない候補) の arm_id を返す"""
    return [a["arm_id"] for a in result["arms"] if a.get("eliminated_at") is None and a["state"] != DEGRADED]
