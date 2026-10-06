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


def test_lineup_to_dict_keeps_text_parts():
    """parts に文字列 (同時探索の utility_kinds) が入っていても to_dict が落ちない (数値だけ丸める)"""
    d = C.Lineup(("a", "b"), "C001", 0.123456, {"coverage": 0.987654, "attackers": 4, "utility_kinds": "hazard,priority"}).to_dict()
    assert d["parts"] == {"coverage": 0.9877, "attackers": 4, "utility_kinds": "hazard,priority"} and d["score"] == 0.1235
    print("test_lineup_to_dict_keeps_text_parts OK")


def test_incumbent_branch():
    """現行チーム (較正点) と近傍: 固定枠は入替えず、除外種を入れず、入替枠を散らして上位"""
    from tools.team_build.run import incumbent_branch
    feats, threats = _feats()
    ids = ["a", "b", "c", "d", "e", "g"]
    pool = list(feats)                      # 入替先は f, h
    inc, neigh = incumbent_branch(ids, pool, feats, threats, "balance", banned={"f"}, favorites={"a"},
                                  threat_weights=None, n_neighbors=3)
    assert inc is not None and inc.tag == "incumbent" and inc.members == tuple(sorted(ids))
    assert 1 <= len(neigh) <= 3
    for l in neigh:
        assert l.tag == "incumbent_mut" and l.concept == "INC"
        assert "a" in l.members and "f" not in l.members and "h" in l.members     # 固定枠維持 / 除外なし / 入替先は h
        assert len(set(l.members) ^ set(ids)) == 2                                   # 1 枠入替
    # 入替枠が散っている (同じ枠の入替ばかりにならない)
    out_slots = [next(iter(set(ids) - set(l.members))) for l in neigh]
    assert len(set(out_slots)) == len(out_slots), out_slots
    # 現行に除外種が含まれるときは現行そのものは候補にしない (近傍は作る)
    inc2, neigh2 = incumbent_branch(ids, pool, feats, threats, "balance", banned={"e"}, favorites=set(),
                                    threat_weights=None, n_neighbors=2)
    assert inc2 is None and neigh2 and all("e" not in l.members for l in neigh2)
    # 未登録 / プール外は空
    assert incumbent_branch(["a", "b", "zz", "c", "d", "e"], pool, feats, threats, "balance", set(), set(), None, 2) == (None, [])
    print("test_incumbent_branch OK")


def test_scores_and_beam():
    feats, threats = _feats()
    # 脅威ごとに 最良 0.7 + 次善 0.3: 各脅威で 0.9 と 0.1 → 0.66
    assert abs(C.team_coverage(("a", "b", "c", "d"), feats, threats) - 0.66) < 1e-9
    # 使用率加重: t1 だけ重いと a の被覆が効く
    assert C.team_coverage(("a",), feats, threats, {"t1": 10.0, "t2": 1.0, "t3": 1.0, "t4": 1.0}) > \
        C.team_coverage(("a",), feats, threats)
    assert C.redundancy(("a", "f"), feats) > C.redundancy(("a", "b"), feats)      # タイプ重複 (はがね 2 体)
    # メガ石は free_stones 個までは罰しない (実構築の 6 割が石 2 個)。3 個目から 0.5 ずつ
    assert C.redundancy(("a", "f"), feats, free_stones=2) == C.redundancy(("a", "f"), feats, free_stones=3)
    assert abs(C.redundancy(("a", "f"), feats, free_stones=1) - C.redundancy(("a", "f"), feats, free_stones=2) - 0.5) < 1e-9
    # 石持ちが複数なら、メガシンカで最も得をする 1 体だけメガ後の被覆、他は素の姿の被覆 (coverage_base)
    feats["f"].coverage_base = {"t1": 0.15, "t2": 0.15, "t3": 0.15, "t4": 0.15}   # f の得 0.2
    feats["a"].coverage_base = {"t1": 0.5, "t2": 0.1, "t3": 0.1, "t4": 0.1}       # a の得 0.4 → a がメガシンカする
    assert C.mega_user(("a", "f", "b"), feats, threats) == "a"
    assert C.mega_user(("b", "c"), feats, threats) is None and C.mega_user(("a", "b"), feats, threats) == "a"
    covs = C.member_coverages(("a", "f"), feats, threats)
    user = C.mega_user(("a", "f"), feats, threats)
    other = "f" if user == "a" else "a"
    assert covs[user] == feats[user].coverage and covs[other] == feats[other].coverage_base
    # 2 体目の石持ちは素の被覆で数えるので、メガ後の被覆で数えるより低い
    full = 0.7 * 0.9 + 0.3 * 0.2                                                   # t1: a 0.9, f (メガ後) 0.2 のとき
    assert C.team_coverage(("a", "f"), feats, ["t1"]) <= full + 1e-9
    feats["a"].coverage_base = None
    feats["f"].coverage_base = None
    # ビームのメガ石の上限: max_megas=1 なら 2 体目の石持ちは積まない、2 なら積める
    one = C.beam_complete(("a",), list(feats), feats, threats, "balance", width=4, team_size=3, max_megas=1)
    assert all(sum(1 for m in l.members if feats[m].mega) <= 1 for l in one)
    two = C.beam_complete(("a",), list(feats), feats, threats, "balance", width=50, team_size=3, min_distance=0.0, max_megas=2)
    assert any(sum(1 for m in l.members if feats[m].mega) == 2 for l in two)     # 幅が十分なら a + f (石 2 個) の並びが残る
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
    assert chosen and chosen[0].tag == "concept" and len({c.members for c in chosen}) == len(chosen)
    for i in range(len(chosen)):
        for j in range(i + 1, len(chosen)):
            assert C.distance(chosen[i].members, chosen[j].members) >= C.MIN_DISTANCE
    assert abs(C.distance(("a", "b", "c"), ("a", "b", "c")) - 0.0) < 1e-9
    assert abs(C.distance(("a", "b"), ("c", "d")) - 1.0) < 1e-9
    print("test_scores_and_beam OK")


def test_sample_stratified():
    import random
    ls = [C.Lineup(tuple(f"m{i}"), f"c{i}", 1.0 - i * 0.05, {}) for i in range(12)]
    rng = random.Random(1)
    out = C.sample_stratified(ls, 4, 4, rng)
    assert len(out) == 4 and len({l.concept for l in out}) == 4
    # 4 層 (3 並びずつ) から 1 つずつ: 各層の点の範囲に 1 つずつ入る
    bands = sorted(int(round((1.0 - l.score) / 0.05)) // 3 for l in out)
    assert bands == [0, 1, 2, 3]
    assert C.sample_stratified(ls[:3], 4, 4, rng) == ls[:3] and C.sample_stratified([], 4, 4, rng) == [] and C.sample_stratified(ls, 0, 4, rng) == []
    out2 = C.sample_stratified(ls, 6, 2, random.Random(2))
    assert len(out2) == 6 and sum(1 for l in out2 if l.score > 0.7) == 3
    print("test_sample_stratified OK")


def test_worst_hole_penalty():
    """穴の罰則: 1 脅威だけ誰も見ていない並びは、平均では僅差でも点で下がる。重みは最大を 1 に正規化"""
    feats, threats = _feats()
    # a,b,c,d は t1..t4 を 1 体ずつ 0.9 で見る。a,b,c,h は t4 を h の 0.6 で見る (穴ではない)、
    # a,b,c,g は t4 を最大 0.1 (a/b/c) でしか見ない → 不足 0.4 − 0.1 = 0.3
    assert abs(C.worst_hole(("a", "b", "c", "d"), feats, threats, threshold=0.4)) < 1e-9
    assert abs(C.worst_hole(("a", "b", "c", "g"), feats, threats, threshold=0.4) - 0.3) < 1e-9
    assert abs(C.worst_hole(("a", "b", "c", "h"), feats, threats, threshold=0.4)) < 1e-9
    # 重み: t4 が軽い (0.1 vs 1.0) と穴の罰則も軽い
    assert abs(C.worst_hole(("a", "b", "c", "g"), feats, threats, {"t1": 1.0, "t2": 1.0, "t3": 1.0, "t4": 0.1},
                            threshold=0.4) - 0.03) < 1e-9
    s_full, p_full = C.lineup_score(("a", "b", "c", "d"), feats, threats, "any")
    s_hole, p_hole = C.lineup_score(("a", "b", "c", "g"), feats, threats, "any")
    assert p_full["hole"] == 0.0 and abs(p_hole["hole"] - 0.3) < 1e-9 and s_hole < s_full
    # 罰則の重みを 0 にすると穴の項は効かない
    s0, _ = C.lineup_score(("a", "b", "c", "g"), feats, threats, "any", weights={"hole": 0.0})
    assert s0 > s_hole
    print("test_worst_hole_penalty OK")


if __name__ == "__main__":
    test_scores_and_beam()
    test_lineup_to_dict_keeps_text_parts()
    test_incumbent_branch()
    test_worst_hole_penalty()
