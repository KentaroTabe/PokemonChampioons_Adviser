"""候補ごとの助言方策の適応 (S7 / S11): 選出モデル。

- 収集: 候補チームを固定し、相手は SEARCH-A (fold 0) から (cross-fitting: 評価は SEARCH-B)
  → tools.collect_selection_data --team-file --opp-split FILE:search:0 --out <run>/advisors/<cid>/selection_data.npz
- 学習: 汎用モデルを起点に候補データで微調整 → train_selection --adapt-data --adapt-out
- 停止: 最低 BUILD_ADAPT_MIN_BATTLES、以後は chunk ごとに再学習し、held-out の改善が BUILD_ADAPT_PATIENCE 回
  連続で BUILD_ADAPT_EPS_TRAIN 未満なら停止 (docs/TEAM_BUILDING_IMPLEMENTATION.md §9-6)
- 成果物は registry に shadow (candidate) で登録する
行動方策 (RL) のチーム条件付き adapter は adapt_action として後続 (学習環境の自チーム固定が必要)。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from champions_agent.config import (
    BUILD_ADAPT_EPS_TRAIN, BUILD_ADAPT_MIN_BATTLES, BUILD_ADAPT_PATIENCE, BUILD_ADAPT_VALIDATE_MAX_CKPTS,
    BUILD_ADAPT_VALIDATE_N, BUILD_FOLD_ADAPT, BUILD_FOLD_VALIDATE)

REPO = Path(__file__).resolve().parent.parent.parent
CHUNK = 1000                 # 1 回の収集戦数 (config 化候補)
MAX_BATTLES = 20000          # 収束しなくてもここで止める


# ---------------------------------------------------------------------------
# checkpoint 選択 (2026-09-07): val_mse は勝率を保証しない (learning curve で N=3000 の checkpoint が収束比 −0.17)。
# 適応中に残した checkpoint を独立 fold (V) の同一相手列で測り、勝率で選ぶ
# ---------------------------------------------------------------------------
def pick_checkpoints_evenly(ckpts: dict, k: int) -> list:
    """{N: path} から最初と最後を含めて等間隔に k 個の N を選ぶ (純粋)"""
    ns = sorted(ckpts)
    if k <= 0 or not ns:
        return []
    if len(ns) <= k:
        return ns
    if k == 1:
        return [ns[-1]]
    idx = sorted({round(i * (len(ns) - 1) / (k - 1)) for i in range(k)})
    return [ns[i] for i in idx]


def choose_best_checkpoint(results: dict) -> Optional[int]:
    """{N: {"win_rate", "n"}} から勝率最大の N (同率なら大きい N = 学習の進んだ方)。無ければ None"""
    rows = [(v.get("win_rate"), N) for N, v in results.items() if v and v.get("win_rate") is not None]
    if not rows:
        return None
    rows.sort(key=lambda r: (r[0], r[1]))
    return rows[-1][1]


def checkpoints_from_history(history: list) -> dict:
    """adapt_selection の history → {N: checkpoint path} (keep_checkpoints=True で残したもの)"""
    out = {}
    for rep in history or []:
        if rep.get("checkpoint") and rep.get("n_battles"):
            out[int(rep["n_battles"])] = rep["checkpoint"]
    return out


def select_checkpoint(candidate_id: str, team_file: Path, ckpts: dict, split_file: Path, seed: int, models_dir: str,
                      out_dir: Path, parallel: int = 4, log=print, fold: int = BUILD_FOLD_VALIDATE,
                      n: int = BUILD_ADAPT_VALIDATE_N, max_ckpts: int = BUILD_ADAPT_VALIDATE_MAX_CKPTS) -> dict:
    """checkpoint を独立 fold の実測勝率で選ぶ (同一相手列、pick_policy advisor + その checkpoint)。
    戻り値: {"chosen": path or None, "chosen_n", "results": {N: {"win_rate", "n"}}, "fold", "n", "candidates": [N...]}"""
    from concurrent.futures import ThreadPoolExecutor
    from tools.team_build import racing as R
    picks = pick_checkpoints_evenly(ckpts, max_ckpts)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    jobs = []
    for N in picks:
        arm = R.Arm(f"{candidate_id}_n{N}", Path(team_file), str(ckpts[N]), models_dir, pick_policy="advisor")
        out_json = out_dir / f"validate_{candidate_id}_n{N}_0_{n}.json"
        cmd = R.measure_cmd(arm, n, 0, seed, Path(split_file), "search", fold, out_json,
                            out_dir / "battles" / f"validate_{candidate_id}_n{N}.jsonl")
        jobs.append((N, out_json, cmd, out_dir / f"validate_{candidate_id}_n{N}.log"))
    results = {}
    timeout = 180 + R.SEC_PER_BATTLE * n
    with ThreadPoolExecutor(max_workers=max(1, parallel)) as ex:
        futs = {ex.submit(R._run_one, cmd, log_path, timeout): (N, out_json) for N, out_json, cmd, log_path in jobs}
        for fut in futs:
            N, out_json = futs[fut]
            rc = fut.result()
            if rc == 0 and out_json.exists():
                d = json.loads(out_json.read_text(encoding="utf-8"))
                results[N] = {"win_rate": d.get("win_rate"), "n": d.get("n_battles")}
            else:
                log(f"[validate:{candidate_id}] n{N} failed rc={rc}")
    best = choose_best_checkpoint(results)
    log(f"[validate:{candidate_id}] fold={fold} n={n} " +
        " ".join(f"n{N}={results[N]['win_rate']}" for N in sorted(results)) + f" → chosen n{best}")
    return {"chosen": str(ckpts[best]) if best is not None else None, "chosen_n": best,
            "results": {str(k): v for k, v in results.items()}, "fold": fold, "n": n, "candidates": picks}


def _run(cmd: list, log_path: Path, timeout: int = 4 * 3600) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as lf:
        return subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT, cwd=str(REPO), timeout=timeout).returncode


def collect_chunk(team_file: Path, split_file: Path, out_npz: Path, n: int, seed: int, log_path: Path,
                  explore: float = 0.5, fold: Optional[int] = 0, tier: str = "search") -> int:
    spec = f"{split_file}:{tier}" + (f":{fold}" if fold is not None else "")
    cmd = [sys.executable, "-m", "tools.collect_selection_data", "--battles", str(n), "--explore", str(explore),
           "--team-file", str(team_file), "--opp-split", spec, "--opp-seed", str(seed),
           "--out", str(out_npz)]
    return _run(cmd, log_path)


def train_candidate(data_npz: Path, out_model: Path, report_json: Path, log_path: Path,
                    epochs: int = 200) -> Optional[dict]:
    cmd = [sys.executable, "-m", "champions_agent.train.train_selection", "--adapt-data", str(data_npz),
           "--adapt-out", str(out_model), "--adapt-report", str(report_json), "--epochs", str(epochs)]
    rc = _run(cmd, log_path)
    if rc != 0 or not report_json.exists():
        return None
    return json.loads(report_json.read_text(encoding="utf-8"))


def adapt_selection(candidate_id: str, team_file: Path, split_file: Path, out_dir: Path, seed: int,
                    min_battles: int = BUILD_ADAPT_MIN_BATTLES, chunk: int = CHUNK,
                    patience: int = BUILD_ADAPT_PATIENCE, eps_train: float = BUILD_ADAPT_EPS_TRAIN,
                    max_battles: int = MAX_BATTLES, log=print, registry=None,
                    tiers: tuple = (("search", BUILD_FOLD_ADAPT),), keep_checkpoints: bool = False) -> dict:
    """候補 1 つの選出モデル適応。戻り値: {"model": path, "n_battles", "history", "stop_reason", "artifact_id"}

    tiers: 収集に使う (階層, fold) の列。chunk ごとに巡回する (S11 は SEARCH 全体 + SELECTION)。
    keep_checkpoints: 学習のたびに selection_model_n{N}.pt を残す (learning curve の測定用)
    """
    import shutil
    out_dir = Path(out_dir) / candidate_id
    out_dir.mkdir(parents=True, exist_ok=True)
    data = out_dir / "selection_data.npz"
    model = out_dir / "selection_model.pt"
    report = out_dir / "adapt_report.json"
    log_path = out_dir / "adapt.log"
    history, n_total, stale, last_val = [], 0, 0, None
    stop_reason = "max_battles"
    t0 = time.time()
    while n_total < max_battles:
        tier, fold = tiers[(n_total // chunk) % len(tiers)]
        rc = collect_chunk(team_file, split_file, data, chunk, seed + n_total // chunk, log_path, fold=fold, tier=tier)
        if rc != 0:
            stop_reason = f"collect_failed(rc={rc})"
            break
        n_total += chunk
        if n_total < min_battles:
            continue
        rep = train_candidate(data, model, report, log_path)
        if rep is None:
            stop_reason = "train_failed"
            break
        rep["n_battles"] = n_total
        if keep_checkpoints and model.exists():
            ckpt = out_dir / f"selection_model_n{n_total}.pt"
            shutil.copyfile(model, ckpt)
            rep["checkpoint"] = str(ckpt)
        history.append(rep)
        log(f"[adapt:{candidate_id}] n={n_total} val_mse={rep['val_mse']} gain={rep['gain_pct']:+.1f}%")
        if last_val is not None and (last_val - rep["val_mse"]) < eps_train * max(last_val, 1e-9):
            stale += 1
        else:
            stale = 0
        last_val = rep["val_mse"] if last_val is None else min(last_val, rep["val_mse"])
        if stale >= patience:
            stop_reason = "converged"
            break
    result = {"candidate_id": candidate_id, "model": str(model) if model.exists() else None,
              "data": str(data), "n_battles": n_total, "history": history, "stop_reason": stop_reason,
              "elapsed_s": round(time.time() - t0, 1), "artifact_id": None}
    if registry is not None and model.exists():
        try:
            row = registry.register("selection_model", model,
                                    meta={"candidate_id": candidate_id, "n_battles": n_total,
                                          "val_mse": history[-1]["val_mse"] if history else None},
                                    run_id=Path(out_dir).parent.parent.name, status="candidate")
            result["artifact_id"] = row["id"]
        except Exception as e:
            result["registry_error"] = repr(e)
    (out_dir / "adapt_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n",
                                                encoding="utf-8")
    return result


# ---------------------------------------------------------------------------
# 行動方策の adapter (§9-6): 汎用 RL 方策を候補チーム固定の自己対戦で短く微調整する。
# - 基底のピン dir を候補専用 dir にコピーし、CHAMPIONS_MODELS_DIR をそこへ向けて tools.smoke_train --resume
#   --own-team-file を chunk ごとに回す (production のチェックポイントには触れない)
# - 忘却対策: 小さい学習率 (TRAIN_LR)、更新幅の制限 (TRAIN_TARGET_KL)、EMA (基底の ema から始まる平均方策) を
#   実際の推論に使う。SB3 の MaskablePPO には基底方策への KL 罰則が無いため、この 3 つで代替する
# - 収束: chunk ごとに候補チームで「adapter vs 基底」を同一相手列 (SEARCH-B) で対応比較し、
#   improved が出れば採用候補、patience 回連続で改善なしなら停止。採用は「基底に対して degraded でない」こと
# ---------------------------------------------------------------------------
ACTION_CHUNK_STEPS = 100_000      # 1 chunk の学習ステップ (実測 約 10k step/分)
ACTION_EVAL_BATTLES = 100         # chunk ごとの対応比較の戦数
ACTION_LR = "3e-5"                # 微調整の学習率 (新規学習 3e-4 の 1/10)
ACTION_TARGET_KL = "0.02"


def decide_action_adapt(history: list, eps_train: float = 0.0) -> dict:
    """chunk ごとの対応差 (adapter − 基底) の履歴から採否を決める (純粋関数)。
    history: [{"n_steps", "delta", "state"}]。最良の chunk が improved なら採用、それ以外は基底を維持"""
    if not history:
        return {"use_adapted": False, "best_index": None, "reason": "no_history"}
    best_i = max(range(len(history)), key=lambda i: (history[i].get("delta") or -1.0))
    best = history[best_i]
    if best.get("state") == "improved" and (best.get("delta") or 0.0) > eps_train:
        return {"use_adapted": True, "best_index": best_i, "reason": f"improved {best.get('delta'):+.3f}"}
    return {"use_adapted": False, "best_index": best_i, "reason": f"not improved (best {best.get('state')} {best.get('delta')})"}


def adapt_action(candidate_id: str, team_file: Path, base_models_dir: str, out_dir: Path, split_file: Path,
                 seed: int, chunk_steps: int = ACTION_CHUNK_STEPS, min_chunks: int = 2, max_chunks: int = 6,
                 patience: int = BUILD_ADAPT_PATIENCE, eval_battles: int = ACTION_EVAL_BATTLES,
                 play_style: str = "balance", n_envs: int = 2, log=print, registry=None) -> dict:
    """候補 1 つの行動方策 adapter。戻り値: {"models_dir": 採用する dir (adapter or 基底), "use_adapted", "history", ...}"""
    import os
    import shutil
    from tools.team_build import racing as R
    from tools.team_build.verdict import verdict4

    out = Path(out_dir) / candidate_id / "action"
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(base_models_dir, out)
    # ピン dir は ema だけのことがある (pin_models.sh)。resume は current (battle_policy_<style>.zip) を読むので
    # 無ければ ema を current の起点にする (基底 = 配布している平均方策)
    cur, ema = out / f"battle_policy_{play_style}.zip", out / f"battle_policy_{play_style}_ema.zip"
    if not cur.exists() and ema.exists():
        shutil.copy2(ema, cur)
    log_path = out / "adapt_action.log"
    history, stale, best_delta = [], 0, None
    t0 = time.time()
    env = dict(os.environ, CHAMPIONS_MODELS_DIR=str(out), TRAIN_LR=ACTION_LR, TRAIN_TARGET_KL=ACTION_TARGET_KL)
    for k in range(max_chunks):
        cmd = [sys.executable, "-m", "tools.smoke_train", "--resume", "--timesteps", str(chunk_steps),
               "--play-style", play_style, "--n-envs", str(n_envs), "--own-team-file", str(team_file),
               "--timeout", str(int(chunk_steps / 10000 * 60 * 3) + 600)]
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as lf:
            rc = subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT, cwd=str(REPO), env=env,
                                timeout=int(chunk_steps / 10000 * 60 * 3) + 900).returncode
        if rc != 0:
            history.append({"chunk": k, "error": f"train rc={rc}"})
            break
        # 対応比較: 同じ候補チームで adapter (out) vs 基底 (base_models_dir)
        arms = [R.Arm(f"{candidate_id}_adapted", team_file, None, str(out)),
                R.Arm(f"{candidate_id}_base", team_file, None, str(base_models_dir))]
        R.measure_round(arms, eval_battles, k * eval_battles, seed, split_file, "search", 1, out / "eval",
                        f"action_k{k}", parallel=2)
        v = verdict4(arms[0].outcomes, arms[1].outcomes).to_dict()
        rec = {"chunk": k, "n_steps": (k + 1) * chunk_steps, "delta": v.get("mean"), "state": v.get("state"),
               "ci": [v.get("ci_low"), v.get("ci_high")], "wr_adapted": (sum(arms[0].outcomes) / len(arms[0].outcomes)) if arms[0].outcomes else None,
               "wr_base": (sum(arms[1].outcomes) / len(arms[1].outcomes)) if arms[1].outcomes else None}
        history.append(rec)
        log(f"[adapt_action:{candidate_id}] chunk {k} steps={rec['n_steps']} Δ={rec['delta']} ({rec['state']})")
        if best_delta is None or (rec["delta"] or -1) > best_delta + BUILD_ADAPT_EPS_TRAIN:
            best_delta = rec["delta"] or -1
            stale = 0
            shutil.copytree(out, out.parent / "action_best", dirs_exist_ok=True)
        else:
            stale += 1
        if k + 1 >= min_chunks and stale >= patience:
            break
    decision = decide_action_adapt([h for h in history if "delta" in h])
    chosen = str(out.parent / "action_best") if decision["use_adapted"] and (out.parent / "action_best").exists() else str(base_models_dir)
    result = {"candidate_id": candidate_id, "models_dir": chosen, "use_adapted": decision["use_adapted"],
              "reason": decision["reason"], "history": history, "elapsed_s": round(time.time() - t0, 1),
              "base_models_dir": str(base_models_dir), "artifact_id": None}
    if registry is not None and decision["use_adapted"]:
        try:
            row = registry.register("rl_checkpoint", out.parent / "action_best",
                                    meta={"candidate_id": candidate_id, "history": history[-1]},
                                    run_id=Path(out_dir).parent.name, status="candidate")
            result["artifact_id"] = row["id"]
        except Exception as e:
            result["registry_error"] = repr(e)
    (out.parent / "adapt_action_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n",
                                                          encoding="utf-8")
    return result
