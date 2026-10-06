"""レートの読みの並びを対戦をまたいで解き、不明・推定の勝敗を埋める (tools.battle_outcome.solve_rate_chain / apply_rate_chain) のテスト。

2026-10-06 第18回接続テスト (15 戦) の実際の読みと確定した勝敗を入力にすると、記録が「負け (推定)」だった 2 戦目は勝ち、
「不明」だった 10・14 戦目は勝ちと決まる (保存されたフレームで確認済みの実際と一致)。

    scripts/run_test.sh test_rate_chain
"""
from __future__ import annotations

from champions_agent.config import RATE_CHAIN_GAP_SEC, RATE_INFER_MIN_DELTA, RATE_MAX_DELTA_PER_BATTLE
from tools.battle_outcome import apply_rate_chain, rate_reads_of, solve_rate_chain

MIN, MAX = RATE_INFER_MIN_DELTA, RATE_MAX_DELTA_PER_BATTLE

# 第18回: (確定した勝敗 (文言 / 3 体目のひんし。推定・不明は None), ランク画面で読めたレート)
CT18 = [("loss", [1701.762]), (None, [1682.807]), ("win", []), ("loss", [1717.762]), ("win", [1704.741]),
        ("loss", [1705.853]), ("win", []), ("loss", [1724.041]), (None, [1705.906, 1686.715]), (None, []),
        ("loss", [1698.933, 1685.152]), (None, [1668.228]), ("win", [1682.774]), (None, []), ("win", [1713.324])]
CT18_TRUTH = {1: "win", 8: "loss", 9: "win", 11: "loss", 13: "win"}   # 0 始まりの index → フレームで確かめた実際


def _battles(spec):
    return [{"outcome": o, "reads": list(r)} for o, r in spec]


def test_connection_test_18_chain():
    res = solve_rate_chain(_battles(CT18), MIN, MAX)
    got = {i: r["by_rate"] for i, r in enumerate(res) if r["by_rate"]}
    assert got == CT18_TRUTH, got
    assert all(r["n_solutions"] >= 1 for r in res) and not any(r["suspect_reads"] for r in res)
    print("test_connection_test_18_chain OK")


def test_two_reads_and_ambiguity():
    # 同じ対戦で 2 つ読めた → その差で決まる (他に何も無くても)
    res = solve_rate_chain(_battles([(None, [1705.906, 1686.715])]), MIN, MAX)
    assert res[0]["by_rate"] == "loss"
    # 1 つだけの読みが 2 戦続いただけ (前後どちらの値か分からない) → 決まらない
    res = solve_rate_chain(_battles([("loss", [1701.762]), (None, [1682.807])]), MIN, MAX)
    assert res[1]["by_rate"] is None and res[1]["n_solutions"] >= 2, res
    # 直前の対戦の後の値が分かっていれば決まる (2 つ読めた対戦のあとの 1 つの読み)
    res = solve_rate_chain(_battles([("loss", [1698.933, 1685.152]), (None, [1668.228])]), MIN, MAX)
    assert res[1]["by_rate"] == "loss"
    # 読みが無い → 何も決まらない
    res = solve_rate_chain(_battles([(None, []), (None, [])]), MIN, MAX)
    assert all(r["by_rate"] is None for r in res)
    assert solve_rate_chain([], MIN, MAX) == []
    print("test_two_reads_and_ambiguity OK")


def test_suspect_reads_are_dropped():
    # 3 戦目の読みが数字の誤読 (2500) で並びが成り立たない → その対戦の読みを除いて解き、印を付ける
    spec = [("loss", [1698.933, 1685.152]), (None, [1668.228]), ("win", [2500.0]), (None, [1682.774, 1697.3])]
    res = solve_rate_chain(_battles(spec), MIN, MAX)
    assert res[2]["suspect_reads"] is True and res[1]["by_rate"] == "loss" and res[3]["by_rate"] == "win", res
    # 確定した勝敗と読みが両立しない (誤読が 2 つ以上) → 何も決めない
    spec = [("win", [1600.0, 1580.0]), ("win", [1580.0, 1560.0])]
    res = solve_rate_chain(_battles(spec), MIN, MAX)
    assert all(r["by_rate"] is None and r["n_solutions"] == 0 for r in res), res
    print("test_suspect_reads_are_dropped OK")


def test_too_many_variables_are_skipped():
    spec = [(None, [1600.0 + i]) for i in range(20)]    # 1 つだけの読み 20 + 不明 19 → 変数が多すぎる
    res = solve_rate_chain(_battles(spec), MIN, MAX)
    assert all(r["by_rate"] is None for r in res)
    print("test_too_many_variables_are_skipped OK")


def test_apply_rate_chain_fills_unknown_and_fixes_inferred():
    battles = []
    for i, (o, reads) in enumerate(CT18):
        battles.append({"file": f"b{i + 1}", "t0": 1000.0 + i * 400, "t1": 1000.0 + i * 400 + 380,
                        "outcome": o if o else ("loss" if i in (1, 8, 11) else "unknown"),     # 記録: 2・9・12 戦目は推定の負け
                        "inferred": o is None and i in (1, 8, 11), "reads": list(reads)})
    changed = apply_rate_chain(battles, RATE_CHAIN_GAP_SEC, MIN, MAX)
    # 2 戦目 (推定の負け → 勝ち)、10・14 戦目 (不明 → 勝ち) が変わる。9・12 戦目の推定 (負け) は並びとも合うので変わらない
    assert changed == 3, changed
    assert battles[1]["outcome"] == "win" and battles[1]["by_rate"] is True and battles[1]["outcome_recorded"] == "loss"
    assert battles[9]["outcome"] == "win" and battles[13]["outcome"] == "win" and battles[9]["inferred"] is True
    assert battles[8]["outcome"] == "loss" and "by_rate" not in battles[8]
    # 確定した勝敗は変えない
    assert [b["outcome"] for b in battles if not b.get("inferred")] == [o for o, _r in CT18 if o]
    # 起動の区切り: 間が空いた対戦列は別に解く (前の列の読みを持ち込まない)
    far = [{"file": "x", "t0": 1.0, "t1": 10.0, "outcome": "loss", "inferred": False, "reads": [1698.933, 1685.152]},
           {"file": "y", "t0": 10.0 + RATE_CHAIN_GAP_SEC + 1, "t1": 10.0 + RATE_CHAIN_GAP_SEC + 300, "outcome": "unknown",
            "inferred": False, "reads": [1668.228]}]
    assert apply_rate_chain(far, RATE_CHAIN_GAP_SEC, MIN, MAX) == 0 and far[1]["outcome"] == "unknown"
    far[1]["t0"] = 100.0
    assert apply_rate_chain(far, RATE_CHAIN_GAP_SEC, MIN, MAX) == 1 and far[1]["outcome"] == "loss"
    print("test_apply_rate_chain_fills_unknown_and_fixes_inferred OK")


def test_rate_reads_of_dedupes_consecutive_values():
    recs = [{"type": "rate", "value": 1705.906}, {"type": "scene"}, {"type": "rate", "value": 1705.906},
            {"type": "rate", "value": 1686.715}, {"type": "rate", "value": None}]
    assert rate_reads_of(recs) == [1705.906, 1686.715]
    print("test_rate_reads_of_dedupes_consecutive_values OK")


def main() -> None:
    test_connection_test_18_chain()
    test_two_reads_and_ambiguity()
    test_suspect_reads_are_dropped()
    test_too_many_variables_are_skipped()
    test_apply_rate_chain_fills_unknown_and_fixes_inferred()
    test_rate_reads_of_dedupes_consecutive_values()
    print("ALL OK")


if __name__ == "__main__":
    main()
