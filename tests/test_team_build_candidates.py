"""多様性保存つきビーム探索 (candidates) の純粋関数テスト。

    python -m tests.test_team_build_candidates
"""
from __future__ import annotations

from tools.team_build import candidates as C


def _feats():
    threats = ["t1", "t2", "t3", "t4"]
    def f(sid, cov, roles=None, types=(), mega=False, mates=None):
        return C.SpeciesFeature(sid, dict(zip(threats, cov)), roles or {}, types, mega, 0, 0.0, mates or {})
    feats = {
        "a": f("a", [0.9, 0.1, 0.1, 0.1], {"hazard": 1.0}, ("steel",), True, {"b": 60}),
        "b": f("b", [0.1, 0.9, 0.1, 0.1], {"priority": 1.0}, ("dark",), False, {"a": 60}),
        "c": f("c", [0.1, 0.1, 0.9, 0.1], {"speed": 1.0}, ("water",)),
        "d": f("d", [0.1, 0.1, 0.1, 0.9], {"setup": 1.0}, ("dragon",)),
        "e": f("e", [0.5, 0.5, 0.5, 0.5], {"bulk": 1.0}, ("steel",)),
        "f": f("f", [0.2, 0.2, 0.2, 0.2], {}, ("steel",), True),        # メガ 2 体目 (冗長)
        "g": f("g", [0.6, 0.0, 0.6, 0.0], {"pivot": 1.0}, ("electric",)),
        "h": f("h", [0.0, 0.6, 0.0, 0.6], {"status": 1.0}, ("fairy",)),
    }
    return feats, threats


def test_scores_and_beam():
    feats, threats = _feats()
    assert abs(C.team_coverage(("a", "b", "c", "d"), feats, threats) - 0.9) < 1e-9
    assert C.redundancy(("a", "f"), feats) > C.redundancy(("a", "b"), feats)      # メガ重複とタイプ重複
    assert C.synergy(("a", "b"), feats) == 0.6
    res = C.beam_complete(("a", "b"), list(feats), feats, threats, "balance", width=4)
    assert res and all(len(l.members) == 6 for l in res)
    best = res[0].members
    assert "a" in best and "b" in best and "f" not in best, best                # メガ 2 体目は避ける
    assert "c" in best and "d" in best, best                                     # 被覆の穴を埋める
    # 多様性: 上位同士は 5 体同じにならない
    for i in range(len(res)):
        for j in range(i + 1, len(res)):
            assert C.distance(res[i].members, res[j].members) >= 0.34
    chosen = C.select_with_quotas(res, {"best": 1, "coverage": 1, "novelty": 1})
    assert chosen and chosen[0].tag == "best" and len({c.members for c in chosen}) == len(chosen)
    assert abs(C.distance(("a", "b", "c"), ("a", "b", "c")) - 0.0) < 1e-9
    assert abs(C.distance(("a", "b"), ("c", "d")) - 1.0) < 1e-9
    print("test_scores_and_beam OK")


if __name__ == "__main__":
    test_scores_and_beam()
