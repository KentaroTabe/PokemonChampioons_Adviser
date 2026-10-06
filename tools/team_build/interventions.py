"""修理の変種の系譜 (lineage) の記録。測定からの戻りの本体は tools/team_build/repair.py (診断 → 制約 → 修理モード)。

2026-10-02 (D-28) で LLM の修正仮説 (hypotheses) の経路は廃止され、2026-10-05 (判断 #28) で仮説の検証・生成・規則の mutation の
コードも削除した。残るのは変更の分類 (classify_change) と系譜の記録 (record_lineage) だけ。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from champions_agent.config import BUILD_MAX_CHANGES


def classify_change(parent_members: list, child_members: list, max_changes: int = BUILD_MAX_CHANGES) -> str:
    """変更枠数で repair (同系統) か new_branch (新系統) かを決める"""
    diff = len(set(parent_members) ^ set(child_members)) // 2
    return "repair" if diff <= max_changes else "new_branch"


def record_lineage(run_dir: Path, parent_id: str, variants: list) -> Path:
    p = Path(run_dir) / "lineage.json"
    data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"nodes": []}
    for v in variants:
        data["nodes"].append({"id": v["variant_id"], "parent": parent_id, "kind": v["kind"], "members": v["members"],
                              "changes": v["changes"], "at": time.strftime("%Y-%m-%d %H:%M:%S")})
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return p
