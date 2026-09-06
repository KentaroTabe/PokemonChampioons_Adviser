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
    BUILD_ADAPT_EPS_TRAIN, BUILD_ADAPT_MIN_BATTLES, BUILD_ADAPT_PATIENCE)

REPO = Path(__file__).resolve().parent.parent.parent
CHUNK = 1000                 # 1 回の収集戦数 (config 化候補)
MAX_BATTLES = 20000          # 収束しなくてもここで止める


def _run(cmd: list, log_path: Path, timeout: int = 4 * 3600) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as lf:
        return subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT, cwd=str(REPO), timeout=timeout).returncode


def collect_chunk(team_file: Path, split_file: Path, out_npz: Path, n: int, seed: int, log_path: Path,
                  explore: float = 0.5, fold: int = 0) -> int:
    cmd = [sys.executable, "-m", "tools.collect_selection_data", "--battles", str(n), "--explore", str(explore),
           "--team-file", str(team_file), "--opp-split", f"{split_file}:search:{fold}", "--opp-seed", str(seed),
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
                    max_battles: int = MAX_BATTLES, log=print, registry=None) -> dict:
    """候補 1 つの選出モデル適応。戻り値: {"model": path, "n_battles", "history", "stop_reason", "artifact_id"}"""
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
        rc = collect_chunk(team_file, split_file, data, chunk, seed + len(history), log_path)
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
