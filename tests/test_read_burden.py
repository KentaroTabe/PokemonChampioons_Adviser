"""読み負荷 (重い択) 測定のテスト。

    python -m tests.test_read_burden
"""
from __future__ import annotations


def test_top_gap():
    from champions_agent.env.read_burden import top_gap
    actions = [
        {"label": "a", "expected": 0.5, "worst": 0.1, "recommended": 0.38},
        {"label": "b", "expected": 0.4, "worst": 0.38, "recommended": 0.39},
    ]
    # recommended最大は b (0.39) -> gap = 0.4-0.38 = 0.02 (読み不要の局面)
    g = top_gap(actions)
    assert abs(g - 0.02) < 1e-9, g
    assert top_gap([]) is None
    assert top_gap([{"label": "x"}]) is None
    print("test_top_gap OK")


def test_burden_meter_counts():
    from champions_agent.env import read_burden as rb

    class _P:
        def choose_move(self, battle):
            return "order"

    p = _P()
    rb.attach_burden_meter(p)
    # search_optionsを差し替えて3ターン分の局面を模す
    seq = [
        {"actions": [{"expected": 0.5, "worst": 0.1,
                      "recommended": 0.34}]},   # gap 0.4 -> 重い
        {"actions": [{"expected": 0.3, "worst": 0.25,
                      "recommended": 0.28}]},   # gap 0.05 -> 軽い
        None,                                   # 評価不能 -> 数えない
    ]
    calls = {"i": 0}

    def fake_options(battle, depth=1):
        r = seq[calls["i"] % len(seq)]
        calls["i"] += 1
        return r

    import champions_agent.env.search_expert as se
    orig = se.search_options
    se.search_options = fake_options
    try:
        for _ in range(3):
            assert p.choose_move(object()) == "order"
    finally:
        se.search_options = orig

    assert p.read_burden["turns"] == 2, p.read_burden
    assert p.read_burden["heavy"] == 1, p.read_burden
    s = rb.summarize(p, n_battles=2)
    assert s["heavy_per_battle"] == 0.5, s
    print("test_burden_meter_counts OK")


def test_battle_burden_on_real_log():
    """実ログでレポート計算が動くこと (択評価つきの対戦がある前提)"""
    import glob
    from tools.read_burden_report import battle_burden
    found = None
    for f in sorted(glob.glob(
            "logs/battles/battle_20260804_*.jsonl"), reverse=True):
        r = battle_burden(f)
        if r and r["n_advice"] > 0:
            found = r
            break
    if not found:
        print("test_battle_burden_on_real_log SKIP (該当ログなし)")
        return
    assert found["heavy"] <= found["n_advice"]
    assert 0 <= found["gap_mean"] < 5
    print(f"test_battle_burden_on_real_log OK ({found['file']}: "
          f"重い択{found['heavy']}/{found['n_advice']})")


if __name__ == "__main__":
    test_top_gap()
    test_burden_meter_counts()
    test_battle_burden_on_real_log()
    print("\nALL OK")
