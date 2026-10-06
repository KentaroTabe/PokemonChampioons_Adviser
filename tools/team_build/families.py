"""相手構築の系統化と階層分割 (純粋関数)。

- 系統 (family): 種族集合の Jaccard が閾値以上 (6体中4体共通) でメガ軸が同じ構築をまとめる。
  技だけ違う構築が別の階層に散る情報漏洩を防ぐ (docs/TEAM_BUILDING_PLAN.md v3 §3.1)。
- 階層: SEARCH 50 / SELECTION 30 / HOLDOUT 20 を **系統単位** で層化して割り当てる
  (順位順に歩き、各系統を「目標配分に対する不足が最大の階層」へ入れる)。
- SEARCH 内は cross-fitting 用に k 分割 (A: 適応 / B: 評価)。
- holdout は封印する (id 列の sha256 を sealed_id とし、run 終了まで詳細を読まない)。
"""
from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from champions_agent.config import (
    BUILD_FAMILY_JACCARD, BUILD_SEARCH_FOLDS, BUILD_SPLIT_RATIOS)

TIERS = ("search", "selection", "holdout")


@dataclass(frozen=True)
class Team:
    team_id: str
    species: frozenset
    mega: Optional[str] = None
    rank: int = 0
    usage: float = 0.0
    style: Optional[str] = None


@dataclass
class Family:
    family_id: str
    teams: list = field(default_factory=list)

    @property
    def best_rank(self) -> int:
        return min(t.rank for t in self.teams)

    @property
    def weight(self) -> float:
        return float(len(self.teams))

    @property
    def mega(self) -> Optional[str]:
        return self.teams[0].mega if self.teams else None


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / len(sa | sb)


def cluster_families(teams: list, min_jaccard: float = BUILD_FAMILY_JACCARD,
                     same_mega: bool = True) -> list:
    """順位順に走査し、既存系統のいずれかの構築と閾値以上似ていれば併合 (単連結)。"""
    fams: list = []
    for t in sorted(teams, key=lambda x: (x.rank, x.team_id)):
        placed = None
        for fam in fams:
            if same_mega and fam.mega != t.mega:
                continue
            if any(jaccard(t.species, u.species) >= min_jaccard for u in fam.teams):
                placed = fam
                break
        if placed is None:
            fams.append(Family(family_id=f"F{len(fams) + 1:03d}", teams=[t]))
        else:
            placed.teams.append(t)
    return fams


def stratified_split(families: list, ratios: dict = BUILD_SPLIT_RATIOS,
                     seed: int = 0) -> dict:
    """系統を順位順に歩き、目標配分に対する不足が最大の階層へ割り当てる (決定的)。

    戻り値: {"search": [team_id], "selection": [...], "holdout": [...],
             "families": {family_id: tier}}
    """
    tiers = list(ratios.keys())
    total = sum(ratios.values())
    target = {k: ratios[k] / total for k in tiers}
    rng = random.Random(seed)
    order = sorted(families, key=lambda f: (f.best_rank, f.family_id))
    # 同順位帯のタイブレークだけ seed で揺らす (順位の情報は保つ)
    assigned_w = {k: 0.0 for k in tiers}
    fam_tier: dict = {}
    cum = 0.0
    for fam in order:
        cum += fam.weight
        deficit = {k: target[k] * cum - assigned_w[k] for k in tiers}
        best = max(deficit.values())
        cands = [k for k in tiers if abs(deficit[k] - best) < 1e-9]
        tier = cands[0] if len(cands) == 1 else rng.choice(cands)
        fam_tier[fam.family_id] = tier
        assigned_w[tier] += fam.weight
    out = {k: [] for k in tiers}
    for fam in order:
        out[fam_tier[fam.family_id]].extend(t.team_id for t in fam.teams)
    out["families"] = fam_tier
    return out


def cross_fit_folds(families: list, k: int = BUILD_SEARCH_FOLDS, seed: int = 0) -> list:
    """SEARCH 階層の系統を k 個の互いに素な fold に分ける (系統は分割しない)。"""
    order = sorted(families, key=lambda f: (f.best_rank, f.family_id))
    folds = [[] for _ in range(k)]
    weights = [0.0] * k
    rng = random.Random(seed)
    for fam in order:
        m = min(weights)
        cands = [i for i in range(k) if abs(weights[i] - m) < 1e-9]
        i = cands[0] if len(cands) == 1 else rng.choice(cands)
        folds[i].extend(t.team_id for t in fam.teams)
        weights[i] += fam.weight
    return folds


def sealed_id(team_ids: Iterable[str]) -> str:
    h = hashlib.sha256("\n".join(sorted(team_ids)).encode("utf-8")).hexdigest()
    return h[:16]


def seal_holdout(team_ids: list, run_id: str, out_dir: Path, version: int = 1) -> dict:
    """holdout の id 列を封印ファイルに書き、封印 id を返す。pipeline は結果詳細を読まない。"""
    sid = sealed_id(team_ids)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    meta = {"sealed_id": sid, "run_id": run_id, "version": version,
            "n_teams": len(team_ids), "team_ids": sorted(team_ids)}
    (out_dir / f"holdout_{sid}.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return meta


def summarize(split: dict, families: list) -> dict:
    """階層ごとの構築数・系統数 (報告用)"""
    fam_by_id = {f.family_id: f for f in families}
    out = {}
    for tier in TIERS:
        ids = split.get(tier, [])
        fams = [fid for fid, t in split.get("families", {}).items() if t == tier]
        out[tier] = {"teams": len(ids), "families": len(fams),
                     "best_rank": min((fam_by_id[f].best_rank for f in fams), default=None)}
    return out
