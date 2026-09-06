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
    BUILD_FAMILY_JACCARD, BUILD_POOL_TOP_N, BUILD_SEARCH_FOLDS, BUILD_SPLIT_RATIOS)
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


def pool_teams(top_n: int = BUILD_POOL_TOP_N, meta_snapshot_id: Optional[int] = None) -> tuple:
    """(Team 一覧, team_id → チーム本文)。順位はプールの並び順 (上位が先)"""
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
                teams: Optional[list] = None, by_id: Optional[dict] = None) -> dict:
    """系統化 → 層化分割 → fold → 封印。結果を out_dir/opponent_families.json に保存して返す。"""
    if teams is None or by_id is None:
        teams, by_id = pool_teams(top_n=top_n, meta_snapshot_id=meta_snapshot_id)
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
        "meta_snapshot_id": meta_snapshot_id, "min_jaccard": min_jaccard, "ratios": ratios,
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

    def __init__(self, sequence: list, texts: dict):
        self.sequence = list(sequence)
        self.texts = texts
        self.i = 0
        self.last_id = None

    def id_for_battle(self, k: int) -> str:
        """k 番目 (0 始まり) の対戦の相手 id。対戦は逐次なので相手列の順と一致する"""
        return self.sequence[k % len(self.sequence)]

    def yield_team(self) -> str:
        tid = self.sequence[self.i % len(self.sequence)]
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
