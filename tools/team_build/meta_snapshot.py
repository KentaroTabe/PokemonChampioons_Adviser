"""Meta Snapshot (S1): 使用率 DB の固定スナップショットから環境を 1 つの JSON に固定する。

- top: 上位 N 種 (使用率、代表型 = meta_sets、共起上位)
- threats: 脅威リスト (上位 BUILD_THREATS_N 種の id)
- local_meta: 接続テストの実戦ログで実際に当たった相手の頻度 (由来: real)。順位決定には使わず STRESS と報告用
- legal: 参戦種 id 集合
run 開始時に作り、以後その run では変更しない (manifest に snapshot_id を記録)。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from champions_agent.config import USAGE_TARGET_FORMAT
from champions_agent.data import database as db

TOP_N = 60            # 環境スナップショットに含める上位種 (config 化候補)
THREATS_N = 30        # 脅威リスト
TEAMMATES_N = 5


def _sets_for(conn, snapshot_id: int, species_id: str) -> Optional[dict]:
    r = conn.execute(
        "SELECT ability_name, item_name, nature, evs, move1, move2, move3, move4, weight FROM meta_sets "
        "WHERE snapshot_id=? AND pokemon_name=?", (snapshot_id, species_id)).fetchone()
    if r is None:
        return None
    return {"ability": r[0], "item": r[1], "nature": r[2], "evs": r[3],
            "moves": [m for m in (r[4], r[5], r[6], r[7]) if m], "weight": r[8]}


def local_meta(days: Optional[float] = None, last: Optional[int] = None) -> dict:
    """実戦ログ (logs/battles) の遭遇頻度 {species_id: count}。読めなければ空"""
    try:
        from tools.analyze_battles import load_battles, summarize
        from vision.normalize import NameResolver
        battles = load_battles(days=days, last=last)
        enc = summarize(battles)["encounters"]
        resolver = NameResolver()
        out = {}
        for ja, n in enc.items():
            r = resolver.resolve_species(ja, cutoff=0.9)
            if r:
                out[r[1]] = out.get(r[1], 0) + int(n)
        return {"n_battles": len(battles), "encounters": dict(sorted(out.items(), key=lambda kv: -kv[1]))}
    except Exception as e:
        return {"n_battles": 0, "encounters": {}, "error": repr(e)}


def build_snapshot(snapshot_id: Optional[int] = None, top_n: int = TOP_N,
                   threats_n: int = THREATS_N, fmt: str = USAGE_TARGET_FORMAT,
                   include_local: bool = True) -> dict:
    from tools.team_build.spec import legal_species_ids
    with db.get_connection() as conn:
        if snapshot_id is None:
            # 候補生成の環境は最新 (引き継ぎ保護つき) を使う。評価側の相手セットは META_PIN で固定され、
            # 両方の id を manifest に記録する
            snapshot_id = db.latest_snapshot_id(conn, fmt=fmt)
        snap = conn.execute("SELECT id, source, fetched_at, number_of_battles FROM usage_snapshot WHERE id=?",
                            (snapshot_id,)).fetchone()
        rows = conn.execute(
            "SELECT pokemon_name, usage_percent FROM pokemon_usage WHERE snapshot_id=? "
            "ORDER BY usage_percent DESC LIMIT ?", (snapshot_id, top_n)).fetchall()
        top = []
        for r in rows:
            sid = r[0]
            mates = [(m[0], m[1]) for m in conn.execute(
                "SELECT teammate_name, usage_percent FROM teammate_usage WHERE snapshot_id=? AND pokemon_name=? "
                "ORDER BY usage_percent DESC LIMIT ?", (snapshot_id, sid, TEAMMATES_N))]
            top.append({"id": sid, "usage": r[1], "set": _sets_for(conn, snapshot_id, sid), "teammates": mates})
    legal = legal_species_ids()
    doc = {
        "schema_version": "1",
        "snapshot": {"id": snap[0], "source": snap[1], "fetched_at": snap[2],
                     "number_of_battles": snap[3]} if snap else {"id": snapshot_id},
        "top": top,
        "threats": [t["id"] for t in top[:threats_n]],
        "legal": sorted(legal),
        "local_meta": local_meta() if include_local else {"n_battles": 0, "encounters": {}},
    }
    return doc


def threat_sets(doc: dict, n: Optional[int] = None) -> dict:
    """脅威リストの型 {id: (MonView, moves)} (Interaction Matrix 用)。型が無い種は落とす"""
    from tools.team_build.interaction import view_from_set
    out = {}
    ids = doc["threats"][:n] if n else doc["threats"]
    by_id = {t["id"]: t for t in doc["top"]}
    for sid in ids:
        st = (by_id.get(sid) or {}).get("set")
        if not st or not st.get("moves"):
            continue
        try:
            out[sid] = view_from_set(sid, st)
        except Exception:
            continue
    return out


def save_snapshot(doc: dict, run_dir: Path) -> Path:
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    p = run_dir / "meta_snapshot.json"
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return p


def load_snapshot(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
