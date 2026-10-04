"""相手構築プールの系統化・階層分割・対応比較用の相手列。

- pool_teams: POOL_PIN の上位実構築 (env/ranked_teams) を Team (種族集合・メガ軸・順位) に写す
- build_split: 系統化 → 層化分割 (SEARCH/SELECTION/HOLDOUT) → SEARCH の cross-fit fold → holdout 封印
  を 1 つの JSON (opponent_families.json) に保存する。同じ入力・seed なら決定的
- opponent_sequence: 階層 (と fold) から対応比較用の相手列を決定的に作る
  (全候補・参照が同じ順で同じ相手と戦う)
- SequenceTeambuilder: 相手列の順に yield_team する Teambuilder
"""
from __future__ import annotations

import json
import random
import re
from pathlib import Path
from typing import Optional

from champions_agent.config import (
    BUILD_FAMILY_JACCARD, BUILD_MAX_MEGA_STONES, BUILD_POOL_SOURCE, BUILD_POOL_TEAMMATE_MIX, BUILD_POOL_TOP_N,
    BUILD_SEARCH_FOLDS, BUILD_SPLIT_RATIOS)
from tools.team_build import families as F


def _toid(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _mega_stone_ids() -> set:
    try:
        from tools.check_mega_items import mega_stones
        return {sid for (_sp, _disp, _name, sid) in mega_stones() if sid}
    except Exception:
        return set()


def parse_team_text(text: str, stones: Optional[set] = None) -> tuple:
    """Showdown 形式 → (種族 id の frozenset, メガ軸の種族 id or None)"""
    stones = _mega_stone_ids() if stones is None else stones
    species, mega = [], None
    for block in text.strip().split("\n\n"):
        head = block.strip().splitlines()[0] if block.strip() else ""
        if not head:
            continue
        name, _, item = head.partition("@")
        sid = _toid(name)
        species.append(sid)
        if item and _toid(item) in stones and mega is None:
            mega = sid
    return frozenset(species), mega


def team_id_of(text: str) -> str:
    import hashlib
    return "t" + hashlib.sha1(text.strip().encode("utf-8")).hexdigest()[:10]


def compose_team(rng: random.Random, weights: dict, teammates: dict, base_key, stone_of: dict, size: int = 6,
                 max_megas: int = BUILD_MAX_MEGA_STONES, mix: float = BUILD_POOL_TEAMMATE_MIX) -> list:
    """最新の環境から 1 チーム分の種 id を選ぶ (純粋)。先頭は重み (使用率% ∪ ゲーム内順位の読み替え) で、以降は
    (1 − mix) × 重み + mix × 選んだ種との共起 (teammate_usage、0..100 を 0..1 に) の得点で抽選する。
    ベース種の重複なし (base_key)、メガ石を持つ種 (stone_of[sid] が石) は max_megas まで。候補が尽きたら size 未満で返す"""
    ids = [s for s, w in weights.items() if w > 0]
    if not ids:
        return []
    wmax = max(weights[s] for s in ids) or 1.0
    chosen: list = []
    used_base: set = set()
    n_mega = 0
    while len(chosen) < size:
        cands, scores = [], []
        for s in ids:
            if s in chosen or base_key(s) in used_base:
                continue
            if stone_of.get(s) and n_mega >= max_megas:
                continue
            w = weights[s] / wmax
            if chosen:
                co = sum(float((teammates.get(m) or {}).get(s, 0.0)) for m in chosen) / (100.0 * len(chosen))
                score = (1.0 - mix) * w + mix * min(1.0, co)
            else:
                score = w
            if score > 0:
                cands.append(s)
                scores.append(score)
        if not cands:
            break
        pick = rng.choices(cands, weights=scores, k=1)[0]
        chosen.append(pick)
        used_base.add(base_key(pick))
        if stone_of.get(pick):
            n_mega += 1
    return chosen


def _meta_tables(snapshot_id: Optional[int] = None) -> dict:
    """合成に要る表 (使用率の重み / 共起 / 種ごとの代表型 / 持ち物の予備 / メガ石) を最新 (または指定) のスナップショットから読む"""
    from champions_agent.data import database as db
    from champions_agent.env import team_builder as TB
    from champions_agent.env.legality import fill_moves
    from tools.team_build.meta_snapshot import merge_ranked
    with db.get_connection() as conn:
        snap = snapshot_id if snapshot_id is not None else db.latest_snapshot_id(conn)
        if snap is None:
            raise RuntimeError("usage_snapshot が無い (bash champions_agent/scripts/update_usage_db.sh)")
        usage_rows = [(r[0], float(r[1])) for r in conn.execute(
            "SELECT pokemon_name, usage_percent FROM pokemon_usage WHERE snapshot_id=?", (snap,))]
        rank_rows = [(r[0], int(r[1])) for r in conn.execute(
            "SELECT pokemon_name, rank FROM pokemon_usage WHERE snapshot_id=? AND rank IS NOT NULL", (snap,))]
        pool = TB._fetch_meta_pool(conn, snap)
        move_pool = TB._fetch_move_pool(conn, snap)
        fallback_items = TB._fetch_fallback_items(conn, snap)
        teammates: dict = {}
        for r in conn.execute("SELECT pokemon_name, teammate_name, usage_percent FROM teammate_usage WHERE snapshot_id=?",
                              (snap,)):
            teammates.setdefault(r[0], {})[r[1]] = float(r[2])
    merged = merge_ranked(usage_rows, rank_rows, top_n=len(usage_rows), ingame_n=len(rank_rows))
    weights = {e["id"]: float(e["weight"]) for e in merged}
    rows_by: dict = {}
    for r in pool:
        r = dict(r)
        moves = fill_moves(r["pokemon_name"], [r["move1"], r["move2"], r["move3"], r["move4"]],
                           move_pool.get(r["pokemon_name"], []))
        if not moves:
            continue
        for i in range(4):
            r[f"move{i + 1}"] = moves[i] if i < len(moves) else None
        rows_by.setdefault(r["pokemon_name"], r)
    stones = _mega_stone_ids()
    stone_of = {s: (r.get("item_name") if (r.get("item_name") or "") in stones else None) for s, r in rows_by.items()}
    weights = {s: w for s, w in weights.items() if s in rows_by}
    return {"snap": snap, "weights": weights, "teammates": teammates, "rows_by": rows_by, "fallback_items": fallback_items,
            "stones": stones, "stone_of": stone_of}


def _team_from_ids(ids: list, tables: dict, by_id: dict, rank: int) -> Optional[tuple]:
    """種 id の列 (6 体) → (Team, 本文)。種の型は合成 (代表型)。既にある構築 (同じ本文) や型の無い種があれば None"""
    from champions_agent.env import team_builder as TB
    rows_by = tables["rows_by"]
    if len(ids) != 6 or any(s not in rows_by for s in ids):
        return None
    sets = [TB.PokemonSet(species=TB.to_showdown_name(TB._sanitize_species(s)), ability=rows_by[s]["ability_name"],
                          item=TB._sanitize_item(rows_by[s]["item_name"]), tera_type=rows_by[s]["tera_type"],
                          nature=rows_by[s]["nature"], evs=rows_by[s]["evs"],
                          moves=[m for m in (rows_by[s][f"move{k}"] for k in (1, 2, 3, 4)) if m]) for s in ids]
    TB._enforce_item_clause(sets, tables["fallback_items"])
    text = "\n\n".join(p.to_showdown_text() for p in sets)
    tid = team_id_of(text)
    if tid in by_id:
        return None
    species, mega = parse_team_text(text, tables["stones"])
    return F.Team(team_id=tid, species=species, mega=mega, rank=rank), text


def synthetic_pool(n: int = BUILD_POOL_TOP_N, seed: int = 0, snapshot_id: Optional[int] = None, tables: Optional[dict] = None,
                   teams: Optional[list] = None, by_id: Optional[dict] = None) -> tuple:
    """最新の使用率スナップショットの全種から合成した相手プール (Team 一覧, team_id → 本文, snapshot_id)。
    種の重みは meta_snapshot.merge_ranked (使用率% ∪ ゲーム内順位)、型は meta_sets (技は champions mod の learnset で検査)、
    共起は teammate_usage。同じ seed なら同じプール。ブラックリスト (所持の方針) は適用しない (2026-09-18 ユーザー決定)。
    teams / by_id を渡すとその続きに n まで足す (mixed: 実在の構築の後を合成で埋める)"""
    from champions_agent.env import team_builder as TB
    tables = tables or _meta_tables(snapshot_id)
    rng = random.Random(seed)
    teams = list(teams or [])
    by_id = dict(by_id or {})
    attempts = 0
    while len(teams) < n and attempts < n * 4:
        attempts += 1
        ids = compose_team(rng, tables["weights"], tables["teammates"], TB._base_species_key, tables["stone_of"])
        if len(ids) < 6:
            continue
        made = _team_from_ids(ids, tables, by_id, len(teams) + 1)
        if made is None:
            continue
        teams.append(made[0])
        by_id[made[0].team_id] = made[1]
    return teams, by_id, tables["snap"]


def _base_species(sid: str) -> str:
    for suf in ("megax", "megay", "mega"):
        if sid.endswith(suf) and len(sid) > len(suf):
            return sid[: -len(suf)]
    return sid


def real_rosters(days: Optional[float] = None, min_n: int = 1, battles_dir: Optional[Path] = None) -> list:
    """実戦で当たった構築 (6 体の組) とその遭遇回数 [(ids (昇順), n)] (遭遇の多い順)。
    対象は整合した対戦だけ: 選出画面で相手 6 体が読めて、対戦中に見えた相手がその 6 体に含まれ、自分の 6 体も記録されている
    (experiments/env_validity.read_real_battle の consistent)。メガ形態は基本種に丸める"""
    from champions_agent.config import BUILD_POOL_REAL_DAYS, BUILD_POOL_REAL_MIN_N
    from tools.team_build.env_match import load_real_battles
    days = BUILD_POOL_REAL_DAYS if days is None else days
    min_n = BUILD_POOL_REAL_MIN_N if min_n is None else min_n
    counts: dict = {}
    for b in load_real_battles(days, battles_dir):
        if not (b.get("opp_full") and b.get("consistent")):
            continue
        ids = tuple(sorted({_base_species(s) for s in b.get("opp_species") or []}))
        if len(ids) != 6:
            continue
        counts[ids] = counts.get(ids, 0) + 1
    return sorted(((list(k), n) for k, n in counts.items() if n >= min_n), key=lambda kv: (-kv[1], kv[0]))


def mixed_pool(n: int = BUILD_POOL_TOP_N, seed: int = 0, snapshot_id: Optional[int] = None, days: Optional[float] = None,
               min_n: Optional[int] = None, battles_dir: Optional[Path] = None, log=None) -> tuple:
    """実戦で当たった構築 (6 体の組は実在、型は合成) を先に入れ、足りない分だけ合成で埋めた相手プール
    (Team 一覧, team_id → 本文, snapshot_id, 実在の構築数)。2026-10-05 判断 #1 (構築単位の一致率 17.8% → 季節内に 40% が目標)"""
    tables = _meta_tables(snapshot_id)
    teams: list = []
    by_id: dict = {}
    skipped = 0
    for ids, _cnt in real_rosters(days, min_n, battles_dir):
        if len(teams) >= n:
            break
        made = _team_from_ids(ids, tables, by_id, len(teams) + 1)
        if made is None:
            skipped += 1
            continue
        teams.append(made[0])
        by_id[made[0].team_id] = made[1]
    n_real = len(teams)
    if log:
        log(f"S2 mixed pool: 実在の構築 {n_real} (型の無い種などで除外 {skipped}) + 合成 {max(0, n - n_real)}")
    teams, by_id, snap = synthetic_pool(n=n, seed=seed, tables=tables, teams=teams, by_id=by_id)
    return teams, by_id, snap, n_real


def pool_teams(top_n: int = BUILD_POOL_TOP_N, meta_snapshot_id: Optional[int] = None,
               source: Optional[str] = None, seed: int = 0) -> tuple:
    """(Team 一覧, team_id → チーム本文)。順位はプールの並び順 (上位が先)。
    source: ranked = POOL_PIN の上位ランカー構築 (型は meta_snapshot_id の meta_sets) / latest = 最新スナップショットからの合成 /
    mixed = 実戦で当たった構築を先に、残りを合成 (既定 config BUILD_POOL_SOURCE)"""
    source = source or BUILD_POOL_SOURCE
    if source == "latest":
        teams, by_id, _snap = synthetic_pool(n=top_n or BUILD_POOL_TOP_N, seed=seed)
        return teams, by_id
    if source == "mixed":
        teams, by_id, _snap, _n_real = mixed_pool(n=top_n or BUILD_POOL_TOP_N, seed=seed)
        return teams, by_id
    from champions_agent.env.ranked_teams import build_ranked_teams
    texts = build_ranked_teams(top_n=top_n, include_external=False,
                               meta_snapshot_id=meta_snapshot_id)
    stones = _mega_stone_ids()
    teams, by_id = [], {}
    for i, text in enumerate(texts):
        tid = team_id_of(text)
        if tid in by_id:
            continue
        species, mega = parse_team_text(text, stones)
        teams.append(F.Team(team_id=tid, species=species, mega=mega, rank=i + 1))
        by_id[tid] = text
    return teams, by_id


def build_split(run_id: str, out_dir: Path, seed: int, top_n: int = BUILD_POOL_TOP_N,
                meta_snapshot_id: Optional[int] = None, ratios: dict = BUILD_SPLIT_RATIOS,
                min_jaccard: float = BUILD_FAMILY_JACCARD, folds: int = BUILD_SEARCH_FOLDS,
                teams: Optional[list] = None, by_id: Optional[dict] = None,
                pool_source: Optional[str] = None) -> dict:
    """系統化 → 層化分割 → fold → 封印。結果を out_dir/opponent_families.json に保存して返す。
    pool_source: ranked (POOL_PIN の上位構築) / latest (最新スナップショットからの合成)。既定 config BUILD_POOL_SOURCE"""
    pool_source = pool_source or BUILD_POOL_SOURCE
    pool_snapshot = meta_snapshot_id
    n_real = 0
    if teams is None or by_id is None:
        if pool_source == "latest":
            teams, by_id, pool_snapshot = synthetic_pool(n=top_n, seed=seed)
        elif pool_source == "mixed":
            teams, by_id, pool_snapshot, n_real = mixed_pool(n=top_n, seed=seed)
        else:
            teams, by_id = pool_teams(top_n=top_n, meta_snapshot_id=meta_snapshot_id, source=pool_source)
    fams = F.cluster_families(teams, min_jaccard=min_jaccard)
    split = F.stratified_split(fams, ratios=ratios, seed=seed)
    search_fams = [f for f in fams if split["families"][f.family_id] == "search"]
    fold_ids = F.cross_fit_folds(search_fams, k=folds, seed=seed)
    out_dir = Path(out_dir)
    sealed = F.seal_holdout(split["holdout"], run_id, out_dir / "sealed")
    fam_rows = [{"family_id": f.family_id, "tier": split["families"][f.family_id],
                 "best_rank": f.best_rank, "mega": f.mega,
                 "teams": [t.team_id for t in f.teams]} for f in fams]
    doc = {
        "schema_version": "1", "run_id": run_id, "seed": seed, "top_n": top_n,
        "meta_snapshot_id": meta_snapshot_id, "pool_source": pool_source, "pool_snapshot": pool_snapshot, "n_real_teams": n_real,
        "min_jaccard": min_jaccard, "ratios": ratios,
        "n_teams": len(teams), "n_families": len(fams),
        "tiers": {"search": split["search"], "selection": split["selection"],
                  "holdout": split["holdout"]},
        "search_folds": fold_ids,
        "sealed_id": sealed["sealed_id"],
        "families": fam_rows,
        "teams": {t.team_id: {"rank": t.rank, "species": sorted(t.species), "mega": t.mega}
                  for t in teams},
        "texts": by_id,
        "summary": F.summarize(split, fams),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "opponent_families.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return doc


def load_split(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def tier_ids(doc: dict, tier: str, fold: Optional[int] = None) -> list:
    """階層 (search/selection/holdout) の team_id 列。search は fold 指定で A/B を選べる"""
    if tier == "search" and fold is not None:
        return list(doc["search_folds"][fold])
    return list(doc["tiers"][tier])


def opponent_sequence(ids: list, n: int, seed: int) -> list:
    """対応比較用の相手列 (決定的)。n が候補数を超えたら繰り返しになるが順序は固定"""
    rng = random.Random(seed)
    pool = list(ids)
    seq = []
    while len(seq) < n:
        rng.shuffle(pool)
        seq.extend(pool)
    return seq[:n]


try:
    from poke_env.teambuilder import Teambuilder as _PokeEnvTeambuilder
except Exception:  # pragma: no cover - poke-env 無しの環境
    _PokeEnvTeambuilder = object


class SequenceTeambuilder(_PokeEnvTeambuilder):
    """相手列 (team_id の並び) の順にチームを出す。last_id で今出したチームが分かる"""

    def __init__(self, sequence: list, texts: dict, offset: int = 0):
        self.sequence = list(sequence)
        self.texts = texts
        self.offset = int(offset)
        self.i = 0
        self.last_id = None

    def id_for_battle(self, k: int) -> str:
        """k 番目 (0 始まり) の対戦の相手 id。対戦は逐次なので相手列の順と一致する"""
        return self.sequence[(self.offset + k) % len(self.sequence)]

    def yield_team(self) -> str:
        tid = self.sequence[(self.offset + self.i) % len(self.sequence)]
        self.i += 1
        self.last_id = tid
        return self.join_team(self.parse_showdown_team(self.texts[tid]))


def main() -> None:
    """opponent_families.json を作る: python -m tools.team_build.opponents --run-id R --out DIR --seed N"""
    import argparse
    from champions_agent.env.ranked_teams import pinned_meta_snapshot_id
    ap = argparse.ArgumentParser(description="相手構築の系統化と階層分割 (SEARCH/SELECTION/HOLDOUT)")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--out", required=True, help="出力ディレクトリ (opponent_families.json と sealed/)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--top-n", type=int, default=BUILD_POOL_TOP_N)
    args = ap.parse_args()
    doc = build_split(args.run_id, Path(args.out), seed=args.seed, top_n=args.top_n,
                      meta_snapshot_id=pinned_meta_snapshot_id())
    print(f"[opponents] teams={doc['n_teams']} families={doc['n_families']} "
          f"sealed={doc['sealed_id']} summary={doc['summary']}")
    print(f"[opponents] 保存: {Path(args.out) / 'opponent_families.json'}")


if __name__ == "__main__":
    main()
