"""tests/test_advisor_team_validate のうち poke-env が要る部分 (ローカル専用: scripts/run_test.sh の LOCAL_ONLY_TESTS。CI の最小依存 requirements-ci.txt に poke-env が無いため
scripts/ci_tests.sh から外した。2026-10-31)。

check_advisor_player._remembering_teambuilder と tools.team_build.opponents.SequenceTeambuilder は poke-env の Teambuilder を継ぐ
(join_team / parse_showdown_team を使う)。合法性の検査・飛ばしの記録・列の進め方の純粋な部分は tests/test_advisor_team_validate (CI)。
validate-team・Showdown・使用率 DB には依存しない (検査は合成の関数で差し替える)。

    python -m tests.test_advisor_team_validate_local
"""
from __future__ import annotations

import random

from tests.test_advisor_team_validate import BAD, TEXTS, _BAD1, _BAD2, _raw_legal_sequence, _validate
from tools import check_advisor_player as C


def test_teambuilder_skips_and_correspondence():
    """RankedTeambuilder / --own-pool の経路: 不合法は送らず texts にも入れない。同じ seed の 2 条件で列が一致する"""
    seed = 20260904 + 1
    runs = []
    for _ in range(2):
        st: dict = {}
        lg = C.TeamLegality("gen9championsbssregmb", validate=_validate([]))
        tb = C._remembering_teambuilder(C._PoolSource(TEXTS, random.Random(seed)), legality=lg, stats=st)
        packed = [tb.yield_team() for _ in range(30)]
        assert all("airballoon" not in p and "rockyhelmet" not in p for p in packed)
        assert [tb.text_for_battle(k) for k in range(30)] == tb.texts and tb.last_text == tb.texts[-1]
        runs.append((tb.texts, st))
    exp, exp_skip = _raw_legal_sequence(seed, 30)
    assert runs[0][0] == runs[1][0] == exp                                  # 条件間の対応 (対戦 k の自チームが同じ)
    assert runs[0][1] == runs[1][1] and runs[0][1]["own_team_skipped"] == exp_skip > 0
    assert set(runs[0][1]["own_team_skip_reasons"]) <= set(BAD.values())
    # legality なしは従来どおり (不合法も引く)
    tb0 = C._remembering_teambuilder(C._PoolSource(TEXTS, random.Random(seed)))
    for _ in range(30):
        tb0.yield_team()
    rng = random.Random(seed)
    assert tb0.texts == [rng.choice(TEXTS) for _ in range(30)]
    print("test_teambuilder_skips_and_correspondence OK")


def test_opponent_ranked_skips_and_correspondence():
    """相手の RankedTeambuilder (上位 60、Random(相手 seed)): 同じラッパーで不合法を飛ばし、stats は opp_team_*。
    同じ seed の 2 腕で相手の列が一致し、自チームの stats とは混ざらない"""
    seed = 20260904
    runs = []
    for _ in range(2):
        st: dict = {}
        lg = C.TeamLegality("gen9championsbssregmb", validate=_validate([]))
        own = C._remembering_teambuilder(C._PoolSource(TEXTS, random.Random(seed + 1)), legality=lg, stats=st)
        opp = C._remembering_teambuilder(C._PoolSource(TEXTS, random.Random(seed)), legality=lg, stats=st,
                                         stat_prefix=C.OPP_STATS)
        for _ in range(30):
            own.yield_team()
            opp.yield_team()
        runs.append((own.texts, opp.texts, st))
    exp_opp, opp_skip = _raw_legal_sequence(seed, 30)
    exp_own, own_skip = _raw_legal_sequence(seed + 1, 30)
    assert runs[0][1] == runs[1][1] == exp_opp and runs[0][0] == runs[1][0] == exp_own
    st = runs[0][2]
    assert st == runs[1][2]
    assert st["opp_team_skipped"] == opp_skip > 0 and st["own_team_skipped"] == own_skip > 0
    assert sum(st["opp_team_skip_reasons"].values()) == opp_skip and sum(st["own_team_skip_reasons"].values()) == own_skip
    print("test_opponent_ranked_skips_and_correspondence OK")


def test_sequence_teambuilder_skips():
    """--opp-split の SequenceTeambuilder: is_legal で不合法を飛ばす。id_for_battle(k) は実際に出した順、offset は対戦の数で数える
    (追加測定が続きになる)。同じ列・同じ判定の 2 腕で一致。is_legal なしは従来どおり"""
    from tools.team_build.opponents import SequenceTeambuilder, opponent_sequence
    texts = {f"t{i}": t for i, t in enumerate(TEXTS)}
    ids = list(texts)
    seq = opponent_sequence(ids, 80, 5)
    assert opponent_sequence(ids, 40, 5) == seq[:40]                      # 長さを伸ばしても先頭は同じ (余裕を持たせて作る前提)
    legal_seq = [i for i in seq if texts[i] not in BAD]

    def make(offset=0):
        st: dict = {}
        lg = C.TeamLegality("f", validate=_validate([]))
        tb = SequenceTeambuilder(seq, texts, offset=offset, is_legal=lg,
                                 on_skip=lambda sk: C.record_skips(st, sk, C.OPP_STATS))
        return tb, st

    a, sa = make()
    b, sb = make()
    for _ in range(20):
        a.yield_team()
        b.yield_team()
    assert a.sent_ids == b.sent_ids == legal_seq[:20] and sa == sb
    assert [a.id_for_battle(k) for k in range(20)] == legal_seq[:20] and a.id_for_battle(20) is None
    assert a.last_id == legal_seq[19]
    n_bad, n_legal = 0, 0
    for i in seq:                                                          # 合法が 20 個揃うまでに出た不合法の数
        if n_legal == 20:
            break
        if texts[i] in BAD:
            n_bad += 1
        else:
            n_legal += 1
    assert sa["opp_team_skipped"] == n_bad > 0 and set(sa["opp_team_skip_reasons"]) <= set(BAD.values())
    c, _ = make(offset=12)                                                 # 2 回目の測定: 対戦 12 から続ける
    for _ in range(8):
        c.yield_team()
    assert c.sent_ids == legal_seq[12:20]
    old = SequenceTeambuilder(seq, texts, offset=3)                       # is_legal なし: 従来どおり (不合法も出す)
    for _ in range(5):
        old.yield_team()
    assert old.last_id == seq[7] and old.id_for_battle(2) == seq[5]
    try:
        bad_only = {"x": _BAD1, "y": _BAD2}
        SequenceTeambuilder(["x", "y"], bad_only, is_legal=C.TeamLegality("f", validate=_validate([]))).yield_team()
        raise AssertionError("全件不合法で止まらなかった")
    except RuntimeError:
        pass
    print("test_sequence_teambuilder_skips OK")


def main():
    test_teambuilder_skips_and_correspondence()
    test_opponent_ranked_skips_and_correspondence()
    test_sequence_teambuilder_skips()
    print("ALL OK")


if __name__ == "__main__":
    main()
