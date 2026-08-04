"""定説ヒューリスティック (H1/H6) のテスト。

    python -m tests.test_heuristics

- H1: 状況依存リスク調整 (優勢=保証値寄り / 劣勢=期待値寄り)
- H6: 起点警告 (最悪応手が積み技の行動を検出)
"""
from __future__ import annotations


def test_dynamic_risk_weight_shape():
    from advisor.search import RISK_WEIGHT, dynamic_risk_weight as dw
    assert dw(0.5) == 0.6            # 優勢: 堅く
    assert dw(-0.5) == 0.15          # 劣勢: 賭ける
    assert abs(dw(0.0) - RISK_WEIGHT) < 0.05   # 中立: 従来と連続
    assert dw(-0.3) < dw(0.0) < dw(0.3)        # 単調
    print("test_dynamic_risk_weight_shape OK")


def test_losing_position_prefers_upside():
    """劣勢では「期待値は低いが逆転の目がある」行動が浮上する。

    行動A: 期待-0.40 / 保証-0.40 (安定して負け)
    行動B: 期待-0.35 / 保証-0.60 (読みが通れば軽傷、外すと深手)
    劣勢の重み w=0.15 では B (期待値が上) が上に来るべき。
    従来の固定 w=0.4 では A が上だった (逆転の目を捨てていた):
      旧: rec(B) = 0.6*(-0.35)+0.4*(-0.60) = -0.45 < rec(A) = -0.40
    """
    from advisor.search import dynamic_risk_weight as dw
    a = {"expected": -0.40, "worst": -0.40}
    b = {"expected": -0.35, "worst": -0.60}
    w = dw(max(a["expected"], b["expected"]))

    def rec(x):
        return (1 - w) * x["expected"] + w * x["worst"]
    assert rec(b) > rec(a), (rec(a), rec(b), w)
    # 優勢の同型 (符号反転) では安定択が上に来る
    a2 = {"expected": 0.40, "worst": 0.40}
    b2 = {"expected": 0.45, "worst": 0.05}
    w2 = dw(max(a2["expected"], b2["expected"]))

    def rec2(x):
        return (1 - w2) * x["expected"] + w2 * x["worst"]
    assert rec2(a2) > rec2(b2), (rec2(a2), rec2(b2), w2)
    print("test_losing_position_prefers_upside OK")


def test_setup_bait_detection():
    """最悪応手が積み技の行動が setup_bait に載り、表示に出る"""
    from advisor.search import SETUP_MOVE_IDS
    assert "swordsdance" in SETUP_MOVE_IDS
    gt = {
        "summary_lines": ["protect: 期待+0.1 保証-0.2 (最悪応手: swordsdance)"],
        "setup_bait": [{"my": "protect", "opp": "swordsdance"}],
        "actions": [],
    }
    advice = {
        "ok": True,
        "best": {"kind": "move", "name": "まもる", "score": 10.0},
        "actions": [{"kind": "move", "name": "まもる", "score": 10.0,
                     "reason": "様子見"}],
        "gtheory": gt,
    }
    from advisor.service import Advisor
    text = Advisor.__new__(Advisor).format_advice(advice)
    assert "起点注意" in text and "swordsdance" in text, text
    print("test_setup_bait_detection OK")


def test_search_emits_new_fields():
    """実探索が risk_weight / position / setup_bait を返す (配線確認)"""
    from advisor.dex import get_dex
    from advisor.search import SimSide, search
    from advisor.damage import MonView

    dex = get_dex()

    def mon(sid):
        sp = dex.species(sid)
        return MonView(species_id=sid, name_ja=sid, types=sp["types"],
                       base=sp["baseStats"],
                       ev={"atk": 252, "spa": 252, "spe": 252})

    me = SimSide(active=mon("garchomp"), active_hp=1.0, bench=[])
    opp = SimSide(active=mon("gyarados"), active_hp=1.0, bench=[])
    r = search(me, opp, ["earthquake", "protect"],
               [("dragondance", 0.5), ("waterfall", 0.5)], depth=1)
    assert "risk_weight" in r and "position" in r and "setup_bait" in r, \
        list(r.keys())
    assert r["actions"], "行動が空"
    print(f"test_search_emits_new_fields OK "
          f"(w={r['risk_weight']}, bait={len(r['setup_bait'])})")


if __name__ == "__main__":
    test_dynamic_risk_weight_shape()
    test_losing_position_prefers_upside()
    test_setup_bait_detection()
    test_search_emits_new_fields()
    print("\nALL OK")
