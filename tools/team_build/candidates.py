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

from champions_agent.config import (BUILD_LINEUP_HOLE_THRESHOLD, BUILD_LINEUP_HOLE_WEIGHT, BUILD_MAX_MEGA_STONES,
                                    BUILD_MEGA_FREE_STONES)

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
    coverage: dict                 # threat_id -> 0..1 (Interaction の coverage_value。メガ石を持つ型はメガ後)
    roles: dict = field(default_factory=dict)      # role -> 0..1
    types: tuple = ()
    mega: bool = False
    speed: int = 0
    usage: float = 0.0
    teammates: dict = field(default_factory=dict)  # teammate_id -> co-occurrence %
    coverage_base: Optional[dict] = None           # メガ石を持つ型の、メガシンカしない (素の姿の) 被覆。無ければ coverage と同じ

    def base_coverage(self) -> dict:
        return self.coverage_base if self.coverage_base is not None else self.coverage


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


MIN_DISTANCE = 0.5     # 保持する候補同士は 6 体中 3 体以上違う


def mega_user(members: tuple, feats: dict, threats: list, weights: Optional[dict] = None) -> Optional[str]:
    """メガ石を持つ個体が複数いるとき、1 試合に 1 体しかメガシンカできないので「メガシンカで最も得をする 1 体」を決める
    (メガ後の被覆 − 素の被覆 の重みつき和が最大)。石持ちが 1 体以下ならその個体 (居なければ None)"""
    stones = [m for m in members if m in feats and feats[m].mega]
    if len(stones) <= 1:
        return stones[0] if stones else None
    w = weights or {}

    def gain(m: str) -> float:
        f = feats[m]
        base = f.base_coverage()
        return sum(float(w.get(t, 1.0)) * (f.coverage.get(t, 0.0) - base.get(t, 0.0)) for t in threats)
    return max(stones, key=lambda m: (gain(m), m))


def member_coverages(members: tuple, feats: dict, threats: list, weights: Optional[dict] = None) -> dict:
    """{member: coverage dict}。メガ石持ちはメガシンカする 1 体だけメガ後の被覆、他は素の姿の被覆"""
    user = mega_user(members, feats, threats, weights)
    out = {}
    for m in members:
        if m not in feats:
            continue
        f = feats[m]
        out[m] = f.coverage if (not f.mega or m == user) else f.base_coverage()
    return out


def team_coverage(members: tuple, feats: dict, threats: list, weights: Optional[dict] = None) -> float:
    """脅威ごとに「最良 0.7 + 次善 0.3」(1 体に依存しない厚み) を使用率で加重平均。
    メガ石を持つ個体が複数いても、メガ後の被覆を使えるのは 1 体だけ (他は素の姿)"""
    if not threats:
        return 0.0
    w = weights or {}
    covs = member_coverages(members, feats, threats, weights)
    tot, wsum = 0.0, 0.0
    for t in threats:
        vals = sorted((c.get(t, 0.0) for c in covs.values()), reverse=True)
        v = 0.7 * (vals[0] if vals else 0.0) + 0.3 * (vals[1] if len(vals) > 1 else 0.0)
        wt = float(w.get(t, 1.0))
        tot += wt * v
        wsum += wt
    return tot / wsum if wsum else 0.0


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


def redundancy(members: tuple, feats: dict, free_stones: int = BUILD_MEGA_FREE_STONES) -> float:
    """タイプの重複 (同じタイプを持つ個体数が多いほど大きい) とメガ石の過剰 (free_stones 個までは罰しない。
    実構築の 6 割が石 2 個: メガシンカする個体を相手に読ませないため)"""
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
    return dup / max(1, len(members)) + (0.5 * max(0, megas - int(free_stones)))


def worst_hole(members: tuple, feats: dict, threats: list, weights: Optional[dict] = None,
               threshold: float = BUILD_LINEUP_HOLE_THRESHOLD) -> float:
    """穴の大きさ: 脅威ごとの (threshold − 最良被覆)+ に脅威の重み (最大を 1 に正規化) を掛けた最大値。
    平均に薄まる 1 体の穴 (例: カイリューに対して 6 体とも 0.18) を候補間の差と同じ桁で罰するための項"""
    if not threats:
        return 0.0
    w = weights or {}
    wmax = max((float(w.get(t, 1.0)) for t in threats), default=1.0) or 1.0
    worst = 0.0
    for t in threats:
        best = max((feats[m].coverage.get(t, 0.0) for m in members if m in feats), default=0.0)
        worst = max(worst, max(0.0, threshold - best) * float(w.get(t, 1.0)) / wmax)
    return worst


def lineup_score(members: tuple, feats: dict, threats: list, style: str,
                 weights: Optional[dict] = None, threat_weights: Optional[dict] = None) -> tuple:
    w = {"coverage": 1.0, "roles": 0.5, "synergy": 0.3, "redundancy": 0.4, "hole": BUILD_LINEUP_HOLE_WEIGHT}
    if weights:
        w.update(weights)
    parts = {"coverage": team_coverage(members, feats, threats, threat_weights),
             "roles": role_fulfillment(members, feats, style),
             "synergy": synergy(members, feats),
             "redundancy": redundancy(members, feats),
             "hole": worst_hole(members, feats, threats, threat_weights)}
    score = (w["coverage"] * parts["coverage"] + w["roles"] * parts["roles"]
             + w["synergy"] * parts["synergy"] - w["redundancy"] * parts["redundancy"]
             - w["hole"] * parts["hole"])
    return score, parts


def distance(a: tuple, b: tuple) -> float:
    sa, sb = set(a), set(b)
    return 1.0 - len(sa & sb) / len(sa | sb) if (sa | sb) else 0.0


def beam_complete(core: tuple, pool: list, feats: dict, threats: list, style: str,
                  width: int = 8, team_size: int = 6, min_distance: float = 0.34,
                  banned: Optional[set] = None, concept: str = "",
                  threat_weights: Optional[dict] = None, max_megas: int = BUILD_MAX_MEGA_STONES) -> list:
    """core から team_size 体まで 1 体ずつ足すビーム探索。各段で候補間距離が min_distance 未満の
    重複 (5 体同じ等) を落として多様性を保つ。メガ石は max_megas 個まで (1 試合 1 回のメガシンカとは別。
    実構築は石 2 個が 6 割)。戻り値: [Lineup] (スコア降順)"""
    banned = banned or set()
    beam = [tuple(core)]
    while beam and len(beam[0]) < team_size:
        expanded = {}
        for members in beam:
            n_mega = sum(1 for m in members if m in feats and feats[m].mega)
            for sid in pool:
                if sid in members or sid in banned or sid not in feats:
                    continue
                if feats[sid].mega and n_mega >= max_megas:
                    continue          # メガ石の上限 (BUILD_MAX_MEGA_STONES)
                new = tuple(sorted(members + (sid,)))
                if new in expanded:
                    continue
                sc, parts = lineup_score(new, feats, threats, style, threat_weights=threat_weights)
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
        sc, parts = lineup_score(members, feats, threats, style, threat_weights=threat_weights)
        out.append(Lineup(members=members, concept=concept, score=sc, parts=parts))
    return sorted(out, key=lambda l: -l.score)


def select_with_quotas(lineups: list, quotas: dict, min_distance: float = MIN_DISTANCE) -> list:
    """まずコンセプト系統ごとの最良を 1 つずつ採り、次に quota (best overall / coverage / roles / synergy /
    novelty)。既採用との距離が min_distance 未満なら次点へ (「性質の違う有望候補」を残す)"""
    chosen: list = []
    best_by_concept: dict = {}
    for l in sorted(lineups, key=lambda l: -l.score):
        best_by_concept.setdefault(l.concept, l)
    for l in best_by_concept.values():
        if all(distance(l.members, c.members) >= min_distance for c in chosen):
            l.tag = "concept"
            chosen.append(l)

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
