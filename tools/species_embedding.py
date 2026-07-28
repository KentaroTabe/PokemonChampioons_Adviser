"""ポケモンのベクトル化 (組合せ爆発の圧縮用)。

「似ている」には性質の違う3種類があり、用途で使い分ける必要がある:

  functional (機能): メタ上位N体それぞれへの1v1対面スコア。
      「同じ相手に強い/弱い」= 役割が似ている。ダメージ計算から導出するので
      使用率データが薄い種族でも作れる。選出・対策の判断に向く。
  context (文脈/2次): 共起ベクトルどうしの類似度。
      「同じ相方と使われる」= 置き換え可能。構築の候補圧縮に向く。
  synergy (共起/1次): 一緒に使われる度合い。
      ペリッパー↔ラグラージのような相方関係。構築生成のシナジーに向く。

⚠ word2vec を素朴に共起へ適用すると synergy と context が混ざる
  (skip-gramは「文脈に共に出る語」を近づけるため、相方も近くなる)。
  置き換え可能性が欲しい場合は必ず context (2次類似) を使うこと。
共起データは116種族・847ペアと薄いためニューラルではなくSVDで圧縮する。

    python -m tools.species_embedding --species ガブリアス   # 近い種族を見る
    python -m tools.species_embedding --kind context --top 8
    python -m tools.species_embedding --build               # キャッシュ再生成
"""
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CACHE = REPO / "champions_agent" / "data" / "species_embedding.json"
META_TOP_N = 20      # 機能ベクトルの基底にするメタ上位数
SVD_DIM = 12         # 共起埋め込みの次元


def _to_id(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


# ------------------------------------------------------------------
# functional: メタ上位への対面スコアベクトル
# ------------------------------------------------------------------
def build_functional() -> dict:
    """species_id -> [メタ上位N体への対面スコア]。

    team_advice の margin_vec と同じ考え方 (duel_score) を全種族へ広げ、
    再利用できるようキャッシュする。
    """
    from advisor.endgame import duel_score
    from advisor.team_advice import build_meta_view, meta_top

    metas = meta_top(META_TOP_N)
    basis = []
    for entry in metas:
        sid = entry[0] if isinstance(entry, (list, tuple)) else entry
        view, moves = build_meta_view(sid)
        if view is not None:
            basis.append((sid, view, moves))
    out = {"basis": [sid for sid, _, _ in basis], "vectors": {}}
    for sid, view, moves in basis:
        vec = []
        for _osid, oview, omoves in basis:
            s = duel_score(view, 1.0, moves, oview, 1.0, omoves)
            vec.append(round(s if s is not None else 0.0, 4))
        out["vectors"][sid] = vec
    return out


# ------------------------------------------------------------------
# synergy / context: 共起行列とそのSVD圧縮
# ------------------------------------------------------------------
def _cooccurrence() -> tuple:
    """(species_idリスト, 共起行列) を使用率DBから作る"""
    from champions_agent.config import USAGE_TARGET_FORMAT
    from champions_agent.data import database as db
    with db.get_connection() as conn:
        snap = db.latest_snapshot_id(conn, fmt=USAGE_TARGET_FORMAT)
        rows = conn.execute(
            """SELECT pokemon_name, teammate_name, usage_percent
               FROM teammate_usage WHERE snapshot_id = ?""",
            (snap,)).fetchall()
    ids = sorted({_to_id(r["pokemon_name"]) for r in rows}
                 | {_to_id(r["teammate_name"]) for r in rows})
    index = {sid: i for i, sid in enumerate(ids)}
    mat = [[0.0] * len(ids) for _ in ids]
    for r in rows:
        a, b = index[_to_id(r["pokemon_name"])], index[_to_id(r["teammate_name"])]
        v = float(r["usage_percent"] or 0.0)
        mat[a][b] = max(mat[a][b], v)
        mat[b][a] = max(mat[b][a], v)   # 共起は対称として扱う
    return ids, mat


def build_cooccurrence() -> dict:
    """共起 (synergy) と、そのSVD圧縮 (context) を返す"""
    import numpy as np
    ids, mat = _cooccurrence()
    m = np.array(mat, dtype=np.float64)
    # PPMI風の正規化: 生の使用率は人気種族に引きずられるため対数で圧縮する
    m = np.log1p(m)
    dim = min(SVD_DIM, max(1, min(m.shape) - 1))
    try:
        u, s, _ = np.linalg.svd(m, full_matrices=False)
        emb = (u[:, :dim] * s[:dim])
    except Exception:
        emb = m[:, :dim]
    return {
        "ids": ids,
        "synergy": {sid: [round(v, 4) for v in row]
                    for sid, row in zip(ids, m.tolist())},
        "context": {sid: [round(v, 4) for v in row]
                    for sid, row in zip(ids, emb.tolist())},
    }


def build_all() -> dict:
    data = {"functional": build_functional(), "cooccurrence": build_cooccurrence()}
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(f"[species_embedding] 生成: 機能{len(data['functional']['vectors'])}種 / "
          f"共起{len(data['cooccurrence']['ids'])}種 → {CACHE}")
    return data


_cache = None


def load(rebuild: bool = False) -> dict:
    global _cache
    if _cache is not None and not rebuild:
        return _cache
    if CACHE.exists() and not rebuild:
        try:
            _cache = json.loads(CACHE.read_text(encoding="utf-8"))
            return _cache
        except (json.JSONDecodeError, OSError):
            pass
    _cache = build_all()
    return _cache


def vector(species_id: str, kind: str = "functional") -> list | None:
    """種族のベクトル (kind: functional / context / synergy)"""
    data = load()
    sid = _to_id(species_id)
    if kind == "functional":
        return data["functional"]["vectors"].get(sid)
    return data["cooccurrence"].get(kind, {}).get(sid)


def _cos(a: list, b: list) -> float:
    num = sum(x * y for x, y in zip(a, b))
    da = math.sqrt(sum(x * x for x in a))
    db = math.sqrt(sum(y * y for y in b))
    return num / (da * db) if da and db else 0.0


def similar(species_id: str, kind: str = "functional", top: int = 8) -> list:
    """似ている種族 [(species_id, 類似度)] を返す"""
    data = load()
    src = vector(species_id, kind)
    if src is None:
        return []
    pool = (data["functional"]["vectors"] if kind == "functional"
            else data["cooccurrence"].get(kind, {}))
    sid = _to_id(species_id)
    scored = [(other, _cos(src, vec)) for other, vec in pool.items()
              if other != sid]
    scored.sort(key=lambda x: -x[1])
    return scored[:top]


def main() -> None:
    ap = argparse.ArgumentParser(description="ポケモンのベクトル化")
    ap.add_argument("--species", default=None, help="近い種族を表示する種族名")
    ap.add_argument("--kind", default="functional",
                    choices=["functional", "context", "synergy"])
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--build", action="store_true", help="キャッシュ再生成")
    args = ap.parse_args()

    if args.build:
        build_all()
        if not args.species:
            return
    if not args.species:
        data = load()
        print(f"機能ベクトル: {len(data['functional']['vectors'])}種族 "
              f"(基底=メタ上位{len(data['functional']['basis'])}体)")
        print(f"共起ベクトル: {len(data['cooccurrence']['ids'])}種族 "
              f"(context次元={SVD_DIM})")
        return

    from advisor.infer import species_ja_name
    from vision.normalize import NameResolver
    r = NameResolver().resolve_species(args.species, cutoff=0.7)
    sid = r[1] if r else _to_id(args.species)
    kind_ja = {"functional": "機能 (同じ相手に強い/弱い)",
               "context": "文脈 (置き換え可能)",
               "synergy": "共起 (相方)"}[args.kind]
    print(f"=== {species_ja_name(sid) or sid} に似た種族 / 基準: {kind_ja} ===")
    hits = similar(sid, args.kind, args.top)
    if not hits:
        print("(このベクトルは未収録。--build で再生成するか別のkindを試す)")
        return
    for other, score in hits:
        print(f"  {species_ja_name(other) or other}: {score:.3f}")


if __name__ == "__main__":
    main()
