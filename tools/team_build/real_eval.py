"""実戦評価 (遵守率つき): 接続テストの実戦ログ (logs/battles、由来ラベルつき) から
実勝率の CI、助言への遵守率、実戦/合成の重みづけ Score を出す。

- 200 戦は early signal。w_N = real_weight(有効標本, CI 半幅) で実戦の重みを増やす (§9-5)
- 遵守率: decision_audit の「従えたか」(助言と実行の一致) を使う。ユーザー行動を模倣教師にはしない
- 反事実評価 (助言 vs 実行を探索器で再評価) は advice_replay の経路を使う (後続)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from tools.team_build.verdict import binomial_halfwidth, blended_score, real_weight

REPO = Path(__file__).resolve().parent.parent.parent
BATTLES_DIR = REPO / "logs" / "battles"


def read_battle_labels(path: Path) -> dict:
    """1 対戦ログの由来ラベル (session 行) と勝敗、助言/実行の件数"""
    out = {"file": Path(path).name, "source": "organic", "package_id": None, "outcome": None,
           "n_advice": 0, "n_manual_fix": 0}
    try:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            t = d.get("type")
            if t == "session":
                out["source"] = d.get("source", "organic")
                out["package_id"] = d.get("package_id")
            elif t == "outcome" and d.get("outcome") in ("win", "loss"):
                out["outcome"] = d["outcome"]
            elif t == "advice":
                out["n_advice"] += 1
            elif t == "manual_fix":
                out["n_manual_fix"] += 1
    except Exception:
        pass
    return out


def real_summary(package_id: Optional[str] = None, source: Optional[str] = None,
                 battles_dir: Path = BATTLES_DIR) -> dict:
    rows = [read_battle_labels(p) for p in sorted(battles_dir.glob("battle_*.jsonl"))]
    if package_id:
        rows = [r for r in rows if r["package_id"] == package_id]
    if source:
        rows = [r for r in rows if r["source"] == source]
    decided = [r for r in rows if r["outcome"]]
    n = len(decided)
    wins = sum(1 for r in decided if r["outcome"] == "win")
    wr = wins / n if n else None
    hw = binomial_halfwidth(wr, n) if n else None
    return {"n_logs": len(rows), "n_decided": n, "wins": wins, "win_rate": wr,
            "ci_halfwidth": (round(hw, 4) if hw is not None else None),
            "weight": (real_weight(n, hw) if n else 0.0),
            "by_source": {s: sum(1 for r in rows if r["source"] == s) for s in ("organic", "recommended", "experiment")}}


def compliance_from_audit(audit_rows: list) -> Optional[float]:
    """decision_audit --json の行 (各決定の followed: bool) から遵守率"""
    vals = [bool(r.get("followed")) for r in audit_rows if "followed" in r]
    return round(sum(vals) / len(vals), 3) if vals else None


def blended(real: dict, wr_synthetic: float) -> dict:
    w = real.get("weight", 0.0)
    return {"w_real": round(w, 3), "wr_real": real.get("win_rate"), "wr_synthetic": wr_synthetic,
            "score": round(blended_score(real.get("win_rate"), wr_synthetic, w), 4)}
