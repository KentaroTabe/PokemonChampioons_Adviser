"""6 体候補の多様性保存つき探索 (S5)。純粋関数: 入力は事前計算した特徴だけ。

- コンセプト (core ids) を種に、所持プールから残り枠をビーム探索で埋める
- Score(T) = 脅威被覆 (脅威ごとの max coverage の平均) + 役割充足 + 共起 − 冗長 (種族/タイプの重複)
- 単純な上位 N 保持ではなく quota (best overall / style 別 / alternative core / novelty) と
  候補間距離 (1 − Jaccard) で多様性を保つ (docs/TEAM_BUILDING_IMPLEMENTATION.md §9-11, §10)
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Optional

ROLE_KEYS = ("hazard", "removal", "priority", "setup", "status", "speed", "pivot", "bulk")
DEFAULT_ROLE_WEIGHTS = {"hazard": 0.6, "priority": 0.5, "speed": 0.5, "setup": 0.3,
                        "status": 0.2, "removal": 0.2, "pivot": 0.3, "bulk": 0.3}
STYLE_ROLE_BONUS = {
    "offense": {"priority": 0.4, "setup": 0.4, "speed": 0.3},
    "bulky_offense": {"bulk": 0.4, "setup": 0.3},
    "balance": {"hazard": 0.3, "bulk": 0.3, "pivot": 0.3},
    "cycle": {"pivot": 0.6, "bulk": 0.4, "removal": 0.3},
    "setup": {"setup": 0.6, "hazard": 0.3},
    "speed_control": {"speed": 0.6, "priority": 0.3},
    "stall": {"bulk": 0.6, "status": 0.4, "removal": 0.4},
    "anti_meta": {},
    "any": {},
}


@dataclass
class SpeciesFeature:
    species_id: str
    coverage: dict                 # threat_id -> 0..1 (Interaction の coverage_value)
    roles: dict = field(default_factory=dict)      # role -> 0..1
    types: tuple = ()
    mega: bool = False
    speed: int = 0
    usage: float = 0.0
    teammates: dict = field(default_factory=dict)  # teammate_id -> co-occurrence %


@dataclass
class Lineup:
    members: tuple
    concept: str
    score: float
    parts: dict
    tag: str = ""

    def to_dict(self) -> dict:
        return {"members": list(self.members), "concept": self.concept, "score": round(self.score, 4),
                "parts": {k: round(v, 4) for k, v in self.parts.items()}, "tag": self.tag}


def team_coverage(members: tuple, feats: dict, threats: list) -> float:
    if not threats:
        return 0.0
    tot = 0.0
    for t in threats:
        tot += max((feats[m].coverage.get(t, 0.0) for m in members if m in feats), default=0.0)
    return tot / len(threats)


def role_fulfillment(members: tuple, feats: dict, style: str) -> float:
    weights = dict(DEFAULT_ROLE_WEIGHTS)
    for k, v in STYLE_ROLE_BONUS.get(style, {}).items():
        weights[k] = weights.get(k, 0.0) + v
    tot, wsum = 0.0, 0.0
    for role, w in weights.items():
        have = max((feats[m].roles.get(role, 0.0) for m in members if m in feats), default=0.0)
        tot += w * min(1.0, have)
        wsum += w
    return tot / wsum if wsum else 0.0


def synergy(members: tuple, feats: dict) -> float:
    """共起 (使用率 DB の teammate_usage) の平均。0..1 に正規化 (100% = 1)"""
    pairs = list(itertools.combinations(members, 2))
    if not pairs:
        return 0.0
    tot = 0.0
    for a, b in pairs:
        tot += max(feats[a].teammates.get(b, 0.0) if a in feats else 0.0,
                   feats[b].teammates.get(a, 0.0) if b in feats else 0.0) / 100.0
    return tot / len(pairs)


def redundancy(members: tuple, feats: dict) -> float:
    """タイプの重複 (同じタイプを持つ個体数が多いほど大きい) とメガ枠の重複"""
    counts: dict = {}
    megas = 0
    for m in members:
        f = feats.get(m)
        if not f:
            continue
        megas += int(f.mega)
        for t in f.types:
            counts[t] = counts.get(t, 0) + 1
    dup = sum(max(0, c - 1) for c in counts.values())
    return dup / max(1, len(members)) + (0.5 * max(0, megas - 1))


def lineup_score(members: tuple, feats: dict, threats: list, style: str,
                 weights: Optional[dict] = None) -> tuple:
    w = {"coverage": 1.0, "roles": 0.5, "synergy": 0.3, "redundancy": 0.4}
    if weights:
        w.update(weights)
    parts = {"coverage": team_coverage(members, feats, threats),
             "roles": role_fulfillment(members, feats, style),
             "synergy": synergy(members, feats),
             "redundancy": redundancy(members, feats)}
    score = (w["coverage"] * parts["coverage"] + w["roles"] * parts["roles"]
             + w["synergy"] * parts["synergy"] - w["redundancy"] * parts["redundancy"])
    return score, parts


def distance(a: tuple, b: tuple) -> float:
    sa, sb = set(a), set(b)
    return 1.0 - len(sa & sb) / len(sa | sb) if (sa | sb) else 0.0


def beam_complete(core: tuple, pool: list, feats: dict, threats: list, style: str,
                  width: int = 8, team_size: int = 6, min_distance: float = 0.34,
                  banned: Optional[set] = None, concept: str = "") -> list:
    """core から team_size 体まで 1 体ずつ足すビーム探索。各段で候補間距離が min_distance 未満の
    重複 (5 体同じ等) を落として多様性を保つ。戻り値: [Lineup] (スコア降順)"""
    banned = banned or set()
    beam = [tuple(core)]
    while beam and len(beam[0]) < team_size:
        expanded = {}
        for members in beam:
            for sid in pool:
                if sid in members or sid in banned or sid not in feats:
                    continue
                new = tuple(sorted(members + (sid,)))
                if new in expanded:
                    continue
                sc, parts = lineup_score(new, feats, threats, style)
                expanded[new] = (sc, parts)
        ranked = sorted(expanded.items(), key=lambda kv: -kv[1][0])
        kept: list = []
        for members, (sc, parts) in ranked:
            if any(distance(members, k) < min_distance for k in kept):
                continue
            kept.append(members)
            if len(kept) >= width:
                break
        beam = kept
    out = []
    for members in beam:
        sc, parts = lineup_score(members, feats, threats, style)
        out.append(Lineup(members=members, concept=concept, score=sc, parts=parts))
    return sorted(out, key=lambda l: -l.score)


def select_with_quotas(lineups: list, quotas: dict, min_distance: float = 0.34) -> list:
    """quota ごとに最良を 1 つずつ採り (best overall / coverage / roles / synergy / novelty)、
    既採用との距離が min_distance 未満なら次点へ。残りはスコア順で埋める"""
    chosen: list = []

    def take(cands: list, tag: str) -> None:
        for l in cands:
            if any(l.members == c.members or distance(l.members, c.members) < min_distance for c in chosen):
                continue
            l.tag = tag
            chosen.append(l)
            return

    by_score = sorted(lineups, key=lambda l: -l.score)
    for tag, n in quotas.items():
        for _ in range(n):
            if tag == "best":
                take(by_score, "best")
            elif tag in ("coverage", "roles", "synergy"):
                take(sorted(lineups, key=lambda l: -l.parts.get(tag, 0.0)), tag)
            elif tag == "novelty":
                far = sorted(lineups, key=lambda l: -min((distance(l.members, c.members) for c in chosen), default=1.0))
                take(far, "novelty")
    return chosen
