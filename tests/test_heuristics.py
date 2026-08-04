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


def test_sacrifice_note_ranking_and_wincon():
    """H2: 死に出しは無償降臨価値で選び、勝ち筋は温存する"""
    from advisor.engine import _sacrifice_note
    actions = [
        {"kind": "move", "name": "たきのぼり", "score": 10.0},
        # A: 打点は高いが勝ち筋 -> 温存されるべき
        {"kind": "switch", "name": "ラグラージ", "score": 5.0,
         "counter": 90.0, "incoming": 40.0, "hazard": 0.0},
        # B: 次点の打点 -> こちらが捨て先/後続になる
        {"kind": "switch", "name": "ペリッパー", "score": 4.0,
         "counter": 70.0, "incoming": 30.0, "hazard": 0.0},
    ]
    endgame = "勝ち筋: ラグラージ が相手の残り全員に勝てる見込み。"
    note = _sacrifice_note(actions, endgame)
    assert "ペリッパー" in note and "無償" in note, note
    assert "温存" in note and "ラグラージ" in note, note
    # 勝ち筋でなければ最大打点がそのまま選ばれる
    note2 = _sacrifice_note(actions, "")
    assert "ラグラージ" in note2 and "温存" not in note2, note2
    # 設置技のダメージは無償降臨でも受けるため割り引かれる
    actions3 = [
        {"kind": "switch", "name": "A", "score": 0,
         "counter": 80.0, "incoming": 0.0, "hazard": 25.0},
        {"kind": "switch", "name": "B", "score": 0,
         "counter": 70.0, "incoming": 0.0, "hazard": 0.0},
    ]
    note3 = _sacrifice_note(actions3, "")
    assert "B を無償で出す" in note3, note3
    print("test_sacrifice_note_ranking_and_wincon OK")


def test_sacrifice_note_shown_in_text():
    from advisor.service import Advisor
    advice = {
        "ok": True,
        "best": {"kind": "move", "name": "なみのり", "score": 20.0},
        "actions": [{"kind": "move", "name": "なみのり", "score": 20.0,
                     "reason": "削り"}],
        "sacrifice_note": "死に出しプラン: ...",
    }
    text = Advisor.__new__(Advisor).format_advice(advice)
    assert "🪦" in text and "死に出しプラン" in text, text
    print("test_sacrifice_note_shown_in_text OK")


def test_wincon_preservation_in_search():
    """H3: 勝ち筋を消耗させる行動が、勝ち筋指定時に相対的に下がる"""
    from advisor.dex import get_dex
    from advisor.search import SimSide, search
    from advisor.damage import MonView

    dex = get_dex()

    def mon(sid):
        sp = dex.species(sid)
        return MonView(species_id=sid, name_ja=sid, types=sp["types"],
                       base=sp["baseStats"],
                       ev={"atk": 252, "spa": 252, "spe": 252})

    # 自分: アクティブ=ガブリアス / 控え=勝ち筋のラグラージ。
    # 相手: ギャラドス (こちらの交代先にも打点がある)
    me = SimSide(active=mon("garchomp"), active_hp=0.6,
                 bench=[(mon("swampert"), 1.0)])
    opp = SimSide(active=mon("gyarados"), active_hp=1.0, bench=[])
    kw = dict(my_moves=["earthquake", "stoneedge"],
              opp_move_pool=[("waterfall", 0.6), ("icefang", 0.4)],
              depth=1)
    base = search(me, opp, **kw)
    guarded = search(me, opp, wincon_sid="swampert", **kw)

    def rec(result, label):
        return next(a["recommended"] for a in result["actions"]
                    if a["label"] == label)

    sw = "交代:swampert"
    stay = "earthquake"
    # 勝ち筋指定で「勝ち筋を交代で晒す」行動の相対値が下がる
    rel_base = rec(base, sw) - rec(base, stay)
    rel_guarded = rec(guarded, sw) - rec(guarded, stay)
    assert rel_guarded < rel_base, (rel_base, rel_guarded)
    print(f"test_wincon_preservation_in_search OK "
          f"(相対値 {rel_base:+.3f} → {rel_guarded:+.3f})")


def test_team_style_counts():
    """H4: ピボット技/起点作り技のカウント"""
    from tools.evolve_teams import _team_style_counts
    text = (
        "Pelipper @ damprock\nLevel: 50\n- uturn\n- hurricane\n\n"
        "Swampert @ swampertite\nLevel: 50\n- flipturn\n- stealthrock\n\n"
        "Garchomp @ focussash\nLevel: 50\n- earthquake\n- spikes\n"
    )
    c = _team_style_counts(text)
    assert c == {"pivots": 2, "hazards": 2}, c
    print("test_team_style_counts OK")


if __name__ == "__main__":
    test_dynamic_risk_weight_shape()
    test_losing_position_prefers_upside()
    test_setup_bait_detection()
    test_search_emits_new_fields()
    test_sacrifice_note_ranking_and_wincon()
    test_sacrifice_note_shown_in_text()
    test_wincon_preservation_in_search()
    test_team_style_counts()
    print("\nALL OK")
