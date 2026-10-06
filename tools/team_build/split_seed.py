"""探索の層の分割 (S2) の seed を規制ごとに固定する (2026-10-06 判断、TEAM_BUILD_PENDING_1005 §7.4-3)。

S2 は 相手プール → 系統 → SEARCH / SELECTION / HOLDOUT の層化分割 → SEARCH の fold を seed で決める。run ごとに seed が違うと
分割が変わり、参照 (登録チーム) の適応モデルや fold の記録を run 間で再利用できない (1 run で数時間)。規制 (レギュレーション) ごとに
最初の run の seed を logs/registry/split_seeds.json に記録し、以後の run は同じ規制ならそれを S2 に使う。

- holdout は封印のまま: 分割の seed が同じなら同じ系統が封印される。採否は封印した holdout が守るので、分割への過適合の心配より
  再利用の利点が大きいと判断した。
- 測定の相手列 (S8a / S8b / S10 / holdout の対戦の順) の seed は run の seed のまま。同じ対戦を繰り返さない。
- 同じ seed でも入力 (使用率のスナップショット、mixed の実在の構築) が変われば分割は変わる (同じ入力 + 同じ seed で決定的)。
  季節内の再利用を確実にするなら、プールのスナップショットも季節で固定する必要がある (判断待ち)。
- run の manifest には run の seed (seed) と分割の seed (split_seed、split_seed_source) の両方を残す。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from champions_agent.config import BUILD_SPLIT_SEED_FIXED

REPO = Path(__file__).resolve().parent.parent.parent
SEEDS_PATH = REPO / "logs" / "registry" / "split_seeds.json"


def resolve_split_seed(regulation: str, run_seed: int, table: Optional[dict], fixed: bool = True,
                       override: Optional[int] = None) -> tuple:
    """(分割の seed, 由来, 更新した表) を返す (純粋)。
    由来: "arg" (--split-seed の指定) / "run" (固定しない: run の seed) / "fixed:<規制>" (表にある) / "fixed:new" (この run の seed を表に登録)"""
    table = dict(table or {})
    if override is not None:
        return int(override), "arg", table
    if not fixed:
        return int(run_seed), "run", table
    key = str(regulation or "").strip() or "default"
    entry = table.get(key)
    if entry and entry.get("seed") is not None:
        return int(entry["seed"]), f"fixed:{key}", table
    table[key] = {"seed": int(run_seed), "created_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    return int(run_seed), "fixed:new", table


def load_table(path: Optional[Path] = None) -> dict:
    p = Path(path) if path is not None else SEEDS_PATH
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        return dict(doc) if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def save_table(table: dict, path: Optional[Path] = None) -> Path:
    p = Path(path) if path is not None else SEEDS_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(table, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return p


def split_seed_for(regulation: str, run_seed: int, path: Optional[Path] = None, fixed: Optional[bool] = None,
                   override: Optional[int] = None) -> tuple:
    """run が S2 に使う seed と由来。表に無い規制なら run の seed を登録する"""
    fixed = BUILD_SPLIT_SEED_FIXED if fixed is None else bool(fixed)
    table = load_table(path)
    seed, source, new_table = resolve_split_seed(regulation, run_seed, table, fixed=fixed, override=override)
    if new_table != table:
        save_table(new_table, path)
    return seed, source
