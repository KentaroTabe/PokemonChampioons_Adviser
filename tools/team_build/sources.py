"""候補源の多系統化 (§10): LLM 以外の候補生成 — historical (上位実構築の所持部分集合を軸に)、mutation (1 体入替)、
crossover (2 並びの交差)、novelty (既存候補から最も遠い並び)。純粋関数。"""
from __future__ import annotations

import itertools
import random
from typing import Optional

from tools.team_build.candidates import distance


def historical_cores(pool_teams: list, owned: set, min_owned: int = 3, max_cores: int = 12) -> list:
    """上位実構築のうち所持種を min_owned 体以上含むものを、所持部分集合 (2〜3 体) の軸として返す"""
    out, seen = [], set()
    for t in sorted(pool_teams, key=lambda x: x.rank):
        have = sorted(s for s in t.species if s in owned)
        if len(have) < min_owned:
            continue
        core = tuple(have[:3])
        if core in seen:
            continue
        seen.add(core)
        out.append({"name": f"historical:{t.team_id}", "core_ids": list(core), "mega_id": (t.mega if t.mega in core else None),
                    "win_condition": "offense_trade", "support_roles": ["speed_control"], "weak_to": [],
                    "source": "historical", "rank": t.rank})
        if len(out) >= max_cores:
            break
    return out


def mutations(lineups: list, pool: list, feats: dict, threats: list, style: str, k: int = 1,
              banned: Optional[set] = None, seed: int = 0, per_lineup: int = 3,
              keep: Optional[set] = None) -> list:
    """各並びから 1 体入替の近傍を作り、スコアの高い順に返す (candidates.lineup_score)。keep (固定枠) は入替えない"""
    from tools.team_build.candidates import Lineup, lineup_score
    rng = random.Random(seed)
    banned = banned or set()
    keep = keep or set()
    out = []
    for l in lineups:
        members = list(l.members)
        cands = [s for s in pool if s not in members and s not in banned and s in feats]
        rng.shuffle(cands)
        scored = []
        for out_m in members:
            if out_m in keep:
                continue
            for in_m in cands[:12]:
                new = tuple(sorted([in_m if m == out_m else m for m in members]))
                sc, parts = lineup_score(new, feats, threats, style)
                scored.append(Lineup(new, l.concept, sc, parts, tag="mutation"))
        scored.sort(key=lambda x: -x.score)
        out.extend(scored[:per_lineup])
    return out


def crossovers(lineups: list, feats: dict, threats: list, style: str, max_pairs: int = 20) -> list:
    """2 並びの交差: 共通部分 + 片方ずつから補完して 6 体にする"""
    from tools.team_build.candidates import Lineup, lineup_score
    out = []
    for a, b in itertools.islice(itertools.combinations(lineups, 2), max_pairs):
        common = sorted(set(a.members) & set(b.members))
        rest = [m for m in list(a.members) + list(b.members) if m not in common]
        if len(common) >= 5 or len(rest) < 6 - len(common):
            continue
        new = tuple(sorted(common + rest[: 6 - len(common)]))
        if len(set(new)) != 6:
            continue
        sc, parts = lineup_score(new, feats, threats, style)
        out.append(Lineup(new, f"{a.concept}x{b.concept}", sc, parts, tag="crossover"))
    return sorted(out, key=lambda x: -x.score)


def novelty_pick(lineups: list, chosen: list, n: int = 2) -> list:
    """既採用から最も遠い並びを n 個"""
    far = sorted(lineups, key=lambda l: -min((distance(l.members, c.members) for c in chosen), default=1.0))
    picked = []
    for l in far:
        if l in chosen or any(l.members == c.members for c in picked):
            continue
        l.tag = "novelty"
        picked.append(l)
        if len(picked) >= n:
            break
    return picked
