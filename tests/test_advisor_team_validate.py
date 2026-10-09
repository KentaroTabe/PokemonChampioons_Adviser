"""check_advisor_player の「対戦ごとに引く自チームの合法性」と「黙って止まらない」仕組み (2026-10-09: アークの再測定 (M-B) が
3 腕とも 7 戦目で停滞) のテスト。validate-team・Showdown・使用率 DB には依存しない (検査は合成の関数で差し替える)。

    python -m tests.test_advisor_team_validate
"""
from __future__ import annotations

import asyncio
import random

from champions_agent import config as CFG
from tools import check_advisor_player as C
from tools.team_build.sets import VALIDATE_RUN_FAILED

_A = "Garchomp @ Choice Scarf\nEVs: 32 Atk / 32 Spe\nJolly Nature\n- Earthquake"
_B = "Lopunny @ Lopunnite\nEVs: 32 HP / 32 Spe\nJolly Nature\n- Fake Out"
_BAD1 = "Overqwil @ Air Balloon\nEVs: 32 Atk / 32 Spe\nJolly Nature\n- Crunch"
_BAD2 = "Hippowdon @ Rocky Helmet\nEVs: 32 HP / 32 Def\nImpish Nature\n- Earthquake"
TEXTS = [_A, _BAD1, _B, _BAD2, _A.replace("Earthquake", "Outrage"), _B.replace("Fake Out", "Return")]
BAD = {_BAD1: "Overqwil's item Air Balloon does not exist in Gen 9.",
       _BAD2: "Hippowdon's item Rocky Helmet does not exist in Gen 9."}


def _validate(calls: list):
    def v(text, fmt):
        calls.append((text, fmt))
        return (False, [BAD[text]]) if text in BAD else (True, [])
    return v


def _raw_legal_sequence(seed: int, n: int) -> tuple:
    """飛ばさない生の列から不合法を除いた先頭 n 個と、そこまでに飛ばした数 (期待値の作り方: draw_legal の説明どおり)"""
    rng = random.Random(seed)
    out, skipped = [], 0
    while len(out) < n:
        t = rng.choice(TEXTS)
        if t in BAD:
            skipped += 1
        else:
            out.append(t)
    return out, skipped


def test_legality_cache_and_unavailable():
    calls: list = []
    lg = C.TeamLegality("gen9championsbssregmb", validate=_validate(calls))
    assert lg(_A) == (True, []) and lg(_BAD1) == (False, [BAD[_BAD1]])
    for _ in range(3):
        lg(_A)
        lg(_BAD1)
    assert len(calls) == 2 and lg.n_checked == 2                       # 1 チーム 1 回
    assert all(fmt == "gen9championsbssregmb" for _, fmt in calls)     # 対戦フォーマットで検査
    broken = C.TeamLegality("f", validate=lambda t, f: (False, [f"{VALIDATE_RUN_FAILED}: FileNotFoundError('node')"]))
    try:
        broken(_A)
        raise AssertionError("検査が動かないのに不合法として扱った")
    except C.ValidatorUnavailable:
        pass
    assert broken.n_checked == 0                                       # 動かなかった結果は覚えない
    print("test_legality_cache_and_unavailable OK")


def test_draw_legal_sequence_and_all_bad():
    lg = C.TeamLegality("f", validate=_validate([]))
    rng = random.Random(7)
    got, n_skip = [], 0
    for _ in range(40):
        t, sk = C.draw_legal(rng, TEXTS, lg)
        got.append(t)
        n_skip += len(sk)
        assert t not in BAD and all(s in BAD for s, _ in sk)
    exp, exp_skip = _raw_legal_sequence(7, 40)
    assert got == exp and n_skip == exp_skip and n_skip > 0             # 飛ばした分だけ列が進む
    only_bad = [_BAD1, _BAD2, _BAD1]
    try:
        C.draw_legal(random.Random(1), only_bad, lg)
        raise AssertionError("全件不合法で止まらなかった")
    except RuntimeError as e:
        assert "全 2 チーム" in str(e)
    print("test_draw_legal_sequence_and_all_bad OK")


def test_record_skips_and_reason():
    st: dict = {}
    C.record_skips(st, [])
    assert st == {}
    C.record_skips(st, [(_BAD1, [BAD[_BAD1]]), (_BAD2, [BAD[_BAD2], "x"])])
    C.record_skips(st, [(_BAD1, [BAD[_BAD1]])])
    assert st["own_team_skipped"] == 3
    assert st["own_team_skip_reasons"] == {BAD[_BAD1]: 2, BAD[_BAD2]: 1}
    assert C.skip_reason([]) == "(理由なし)" and C.skip_reason(["", "  y "]) == "y" and len(C.skip_reason(["z" * 300])) == 120
    C.record_skips(None, [(_BAD1, ["a"])])                             # stats なしでも落ちない
    print("test_record_skips_and_reason OK")


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


def test_pool_rng_counts_battles():
    """--opp-offset (2 夜分割): is_legal ありなら「対戦 skip 回ぶん」進め、通しの列の続きと一致する。無ければ従来どおり"""
    lg = C.TeamLegality("f", validate=_validate([]))
    full, _ = _raw_legal_sequence(42, 25)
    rng = C.pool_rng(42, TEXTS, 10, is_legal=lg)
    tail = [C.draw_legal(rng, TEXTS, lg)[0] for _ in range(15)]
    assert tail == full[10:]
    r_old, r_ref = C.pool_rng(42, TEXTS, 10), random.Random(42)
    for _ in range(10):
        r_ref.choice(TEXTS)
    assert [r_old.choice(TEXTS) for _ in range(5)] == [r_ref.choice(TEXTS) for _ in range(5)]
    allgood = C.TeamLegality("f", validate=lambda t, f: (True, []))
    assert C.pool_rng(3, TEXTS, 7, is_legal=allgood).random() == C.pool_rng(3, TEXTS, 7).random()   # 全件合法なら同じ
    print("test_pool_rng_counts_battles OK")


def test_rejection_and_stall_pure():
    rej = ("|popup|Your team was rejected for the following reasons:||||- Overqwil's item Air Balloon does not exist in Gen 9.")
    assert C.is_team_rejection(rej) and C.is_team_rejection(">lobby\n" + rej)
    for ok in ("|popup|Your team is valid for [Gen 9 Champions] BSS Reg M-B.", "|pm| a| b|Your team was rejected",
               ">battle-x\n|j|ADv1", "", None):
        assert not C.is_team_rejection(ok), ok
    assert C.stall_exceeded(1000.0, 300.0, 600) and not C.stall_exceeded(900.0, 300.0, 600)
    assert not C.stall_exceeded(10 ** 6, 0.0, 0)                        # 0 は見張らない
    print("test_rejection_and_stall_pure OK")


class _FakeClient:
    def __init__(self):
        self.seen: list = []

    async def _handle_message(self, message):
        self.seen.append(message)


class _FakePlayer:
    def __init__(self, name):
        self.username = name
        self.ps_client = _FakeClient()


def test_battle_guard():
    now = [0.0]
    exits: list = []
    logs: list = []
    g = C.BattleGuard(600, on_exit=exits.append, clock=lambda: now[0], log=logs.append, describe=lambda: "完了 6/600 戦")
    p = _FakePlayer("ADv1")
    g.wrap(p)
    asyncio.run(p.ps_client._handle_message(">battle-1\n|turn|3"))
    assert p.ps_client.seen == [">battle-1\n|turn|3"] and exits == []    # 元の処理に渡す
    now[0] = 500.0
    assert not g.check() and exits == []
    asyncio.run(p.ps_client._handle_message("|updatesearch|{}"))          # 受信で時計が戻る
    now[0] = 1000.0
    assert not g.check()
    now[0] = 1101.0
    assert g.check() and exits == [CFG.ADVISOR_PLAYER_EXIT_STALLED] and "600 秒" in logs[-1] and "完了 6/600" in logs[-1]
    g.check()
    assert exits == [CFG.ADVISOR_PLAYER_EXIT_STALLED]                      # 2 回目は終了を呼ばない
    # 拒否の popup → 即座に異常終了 (受け取った本文は記録に残す)
    exits2: list = []
    logs2: list = []
    g2 = C.BattleGuard(600, on_exit=exits2.append, clock=lambda: 0.0, log=logs2.append)
    q = _FakePlayer("ADv2")
    g2.wrap(q)
    asyncio.run(q.ps_client._handle_message(
        "|popup|Your team was rejected for the following reasons:||||- Overqwil's item Air Balloon does not exist in Gen 9."))
    assert exits2 == [CFG.ADVISOR_PLAYER_EXIT_TEAM_REJECTED]
    assert "ADv2" in logs2[-1] and "Air Balloon" in logs2[-1] and g2.failed[0] == CFG.ADVISOR_PLAYER_EXIT_TEAM_REJECTED
    assert CFG.ADVISOR_PLAYER_EXIT_TEAM_REJECTED != CFG.ADVISOR_PLAYER_EXIT_STALLED and CFG.ADVISOR_PLAYER_STALL_SEC > 0
    g3 = C.BattleGuard(0, on_exit=exits2.append)
    g3.start()                                                             # 0 は見張らない (スレッドを立てない)
    assert not g3.check()
    print("test_battle_guard OK")


def main():
    test_legality_cache_and_unavailable()
    test_draw_legal_sequence_and_all_bad()
    test_record_skips_and_reason()
    test_teambuilder_skips_and_correspondence()
    test_opponent_ranked_skips_and_correspondence()
    test_sequence_teambuilder_skips()
    test_pool_rng_counts_battles()
    test_rejection_and_stall_pure()
    test_battle_guard()


if __name__ == "__main__":
    main()
