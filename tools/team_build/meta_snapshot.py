"""Meta Snapshot (S1): 使用率 DB の固定スナップショットから環境を 1 つの JSON に固定する。

- top: 上位種 (使用率% 上位 BUILD_META_TOP_N ∪ ゲーム内使用率順位 上位 BUILD_META_INGAME_N。代表型 = meta_sets、共起上位)。
  各行の weight = 使用率% と「ゲーム内順位 r を使用率曲線の r 番目に読み替えた値」の大きい方 (脅威の重み)
- threats: 脅威リスト (weight 上位 BUILD_META_THREATS_N 種の id)
- local_meta: 接続テストの実戦ログで実際に当たった相手の頻度 (由来: real)。順位決定には使わず STRESS と報告用
- legal: 参戦種 id 集合
run 開始時に作り、以後その run では変更しない (manifest に snapshot_id を記録)。
2026-09-18: 使用率% (pokedb の上位ランカー構築の採用頻度) だけでは、上位構築に載らないがゲーム内では上位の種
(M-C 序盤のボーマンダ 1 位 / グソクムシャ 5 位) が脅威に入らなかった。ゲーム内順位 (pokemon_usage.rank) を和集合で足す。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from champions_agent.config import (BUILD_META_INGAME_N, BUILD_META_THREATS_N, BUILD_META_TOP_N,
                                    USAGE_TARGET_FORMAT)
from champions_agent.data import database as db

TOP_N = BUILD_META_TOP_N            # 環境スナップショットに含める上位種 (使用率%)
THREATS_N = BUILD_META_THREATS_N    # 脅威リスト
INGAME_N = BUILD_META_INGAME_N      # ゲーム内使用率順位で足す上位種
TEAMMATES_N = 5


def usage_at_rank(rank: Optional[int], usage_desc: list) -> float:
    """順位 r (1 始まり) → 使用率曲線 (使用率% の降順リスト) の r 番目。曲線より下は最小値、曲線が無ければ 0。純粋"""
    if rank is None or not usage_desc:
        return 0.0
    i = max(0, min(len(usage_desc) - 1, int(rank) - 1))
    return float(usage_desc[i])


def merge_ranked(usage_rows: list, rank_rows: list, top_n: int = TOP_N, ingame_n: int = INGAME_N) -> list:
    """usage_rows: [(id, 使用率%)]、rank_rows: [(id, ゲーム内順位)] → 上位種の行 [{id, usage, ingame_rank, weight,
    from_ingame_only}] を weight 降順で。和集合 = 使用率% 上位 top_n ∪ 順位上位 ingame_n。
    weight = max(使用率%, usage_at_rank(順位)): 「ゲーム内で r 位」を pokedb の使用率曲線の r 番目の値に読み替えて
    同じ尺度に載せる。順位が使用率% の順と同じ (2026-09-18 以前のスナップショット) なら結果は使用率% 上位 top_n と同じ。純粋"""
    usage_by = {sid: float(u) for sid, u in usage_rows}
    rank_by = {}
    for sid, r in rank_rows:
        if r is not None:
            rank_by[sid] = int(r)
    curve = [u for _s, u in sorted(usage_by.items(), key=lambda kv: -kv[1])]
    by_usage = [sid for sid, _u in sorted(usage_by.items(), key=lambda kv: (-kv[1], kv[0]))[:max(0, top_n)]]
    by_rank = [sid for sid, _r in sorted(rank_by.items(), key=lambda kv: (kv[1], kv[0]))[:max(0, ingame_n)]]
    chosen = set(by_usage)
    out = []
    for sid in dict.fromkeys(by_usage + by_rank):
        u = usage_by.get(sid, 0.0)
        r = rank_by.get(sid)
        w = max(u, usage_at_rank(r, curve)) if r is not None else u
        out.append({"id": sid, "usage": u, "ingame_rank": r, "weight": w, "from_ingame_only": sid not in chosen})
    out.sort(key=lambda e: (-e["weight"], e["ingame_rank"] if e["ingame_rank"] is not None else 10 ** 6, e["id"]))
    return out


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
                   include_local: bool = True, ingame_n: int = INGAME_N) -> dict:
    from tools.team_build.spec import legal_species_ids
    with db.get_connection() as conn:
        if snapshot_id is None:
            # 候補生成の環境は最新 (引き継ぎ保護つき) を使う。評価側の相手セットは META_PIN で固定され、
            # 両方の id を manifest に記録する
            snapshot_id = db.latest_snapshot_id(conn, fmt=fmt)
        snap = conn.execute("SELECT id, source, fetched_at, number_of_battles FROM usage_snapshot WHERE id=?",
                            (snapshot_id,)).fetchone()
        usage_rows = [(r[0], float(r[1])) for r in conn.execute(
            "SELECT pokemon_name, usage_percent FROM pokemon_usage WHERE snapshot_id=? "
            "ORDER BY usage_percent DESC, pokemon_name", (snapshot_id,)).fetchall()]
        rank_rows = [(r[0], int(r[1])) for r in conn.execute(
            "SELECT pokemon_name, rank FROM pokemon_usage WHERE snapshot_id=? AND rank IS NOT NULL "
            "ORDER BY rank ASC, pokemon_name", (snapshot_id,)).fetchall()]
        merged = merge_ranked(usage_rows, rank_rows, top_n, ingame_n)
        top = []
        for e in merged:
            sid = e["id"]
            mates = [(m[0], m[1]) for m in conn.execute(
                "SELECT teammate_name, usage_percent FROM teammate_usage WHERE snapshot_id=? AND pokemon_name=? "
                "ORDER BY usage_percent DESC LIMIT ?", (snapshot_id, sid, TEAMMATES_N))]
            top.append({"id": sid, "usage": e["usage"], "ingame_rank": e["ingame_rank"], "weight": e["weight"],
                        "from_ingame_only": e["from_ingame_only"],
                        "set": _sets_for(conn, snapshot_id, sid), "teammates": mates})
    legal = legal_species_ids()
    doc = {
        "schema_version": "1",
        "snapshot": {"id": snap[0], "source": snap[1], "fetched_at": snap[2],
                     "number_of_battles": snap[3]} if snap else {"id": snapshot_id},
        "top": top,
        "threats": [t["id"] for t in top[:threats_n]],
        "threat_source": {"usage_top_n": top_n, "ingame_top_n": ingame_n, "threats_n": threats_n,
                          "ingame_only": [t["id"] for t in top if t.get("from_ingame_only")]},
        "legal": sorted(legal),
        "local_meta": local_meta() if include_local else {"n_battles": 0, "encounters": {}},
    }
    return doc


def threat_weight(entry: dict) -> float:
    """top の行 → 脅威の重み (weight。無ければ使用率%)。並びの被覆・型生成・穴の採点の重みに使う"""
    w = entry.get("weight")
    if w is None:
        w = entry.get("usage")
    return float(w or 0.0)


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
