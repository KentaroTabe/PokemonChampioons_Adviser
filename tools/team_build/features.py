"""所持種ごとの特徴 (S3 の材料): 代表型で脅威との Interaction を計算し、役割・タイプ・共起をまとめる。

候補探索 (candidates.py) は SpeciesFeature だけを見る純粋関数なので、ここで DB・図鑑への依存を閉じる。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from champions_agent.data import database as db
from tools.team_build.candidates import SpeciesFeature
from tools.team_build.interaction import (
    HAZARD_REMOVE, HAZARD_SET, STATUS_MOVES, coverage_value, matrix, view_from_set)
from tools.team_build.sets import representative_set

PRIORITY_MOVES = ("suckerpunch", "bulletpunch", "shadowsneak", "aquajet", "extremespeed", "machpunch",
                  "iceshard", "quickattack", "accelerock", "vacuumwave", "firstimpression", "jetpunch",
                  "thunderclap", "grassyglide", "upperhand")
PIVOT_MOVES = ("uturn", "voltswitch", "flipturn", "partingshot", "teleport", "batonpass", "chillyreception")
SPEED_ITEMS = ("choicescarf",)


def roles_from_set(species_id: str, set_row: dict, view, threat_views: dict) -> dict:
    """技・持ち物・種族値から役割 0..1 を機械的に出す"""
    from advisor.damage import effective_speed
    from advisor.search import SETUP_MOVES
    moves = list(set_row.get("moves") or [])
    roles = {
        "hazard": 1.0 if any(m in HAZARD_SET for m in moves) else 0.0,
        "removal": 1.0 if any(m in HAZARD_REMOVE for m in moves) else 0.0,
        "priority": 1.0 if any(m in PRIORITY_MOVES for m in moves) else 0.0,
        "setup": 1.0 if any(m in SETUP_MOVES for m in moves) else 0.0,
        "status": min(1.0, sum(1 for m in moves if m in STATUS_MOVES) / 2.0),
        "pivot": 1.0 if any(m in PIVOT_MOVES for m in moves) else 0.0,
    }
    spe = effective_speed(view)
    if threat_views:
        faster = sum(1 for (tv, _m) in threat_views.values() if spe > effective_speed(tv))
        roles["speed"] = faster / len(threat_views)
    else:
        roles["speed"] = 0.0
    if (set_row.get("item") or "") in SPEED_ITEMS:
        roles["speed"] = max(roles["speed"], 0.8)
    # 耐久: HP×B×D の幾何平均を上位種の中央値相当 (基準 100^3) で正規化
    hp, de, sd = view.max_hp(), view.stat("def"), view.stat("spd")
    bulk = (hp * de * sd) ** (1 / 3) / 150.0
    roles["bulk"] = max(0.0, min(1.0, bulk - 0.4))
    return roles


def species_features(owned: list, snapshot_doc: dict, threat_views: dict,
                     snapshot_id: Optional[int] = None) -> dict:
    """{species_id: SpeciesFeature}。代表型が無い種は落とす (報告用に missing を返す)"""
    from tools.team_build.interaction import _mega_stone_ids
    stones = _mega_stone_ids()
    by_id = {t["id"]: t for t in snapshot_doc.get("top", [])}
    feats, missing = {}, []
    with db.get_connection() as conn:
        sid_snap = snapshot_id or snapshot_doc["snapshot"]["id"]
        for sid in owned:
            rep = representative_set(conn, sid_snap, sid)
            if rep is None:
                missing.append(sid)
                continue
            row = rep.as_row()
            try:
                view, moves = view_from_set(sid, row)
            except Exception:
                missing.append(sid)
                continue
            rows = matrix({sid: (view, moves)}, threat_views)[sid]
            cov = {t: coverage_value(r) for t, r in rows.items() if "error" not in r}
            top = by_id.get(sid) or {}
            feats[sid] = SpeciesFeature(
                species_id=sid, coverage=cov, roles=roles_from_set(sid, row, view, threat_views),
                types=tuple(view.types), mega=bool((row.get("item") or "") in stones),
                speed=view.stat("spe"), usage=float(top.get("usage") or 0.0),
                teammates={m: float(u) for m, u in (top.get("teammates") or [])})
    return {"features": feats, "missing": missing}


def features_to_json(res: dict) -> dict:
    return {"missing": res["missing"],
            "features": {sid: {"coverage": f.coverage, "roles": f.roles, "types": list(f.types),
                               "mega": f.mega, "speed": f.speed, "usage": f.usage, "teammates": f.teammates}
                         for sid, f in res["features"].items()}}


def save_features(res: dict, run_dir: Path) -> Path:
    p = Path(run_dir) / "species_features.json"
    p.write_text(json.dumps(features_to_json(res), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return p
