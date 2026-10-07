"""決定監査 (tools/decision_audit) のテスト。

    python -m tests.test_decision_audit

接続テストA (アドバイザー追従) では1決定=1テストケースになる。
助言の欠落 / 遅延 / 不一致 / 大失点 の検出と、選出の突き合わせを検証する。
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from tools.decision_audit import audit_battle, render_text
from vision.scenes import (
    SCENE_COMMAND, SCENE_FIELD, SCENE_MOVE_SELECT, SCENE_SELECTION,
)


def _scene(t, scene, party_picked=None, active=None):
    rec = {"type": "scene", "t": t, "scene": scene, "state": {"player": {}}}
    if party_picked is not None:
        rec["state"]["player"]["party"] = [
            {"species": f"mon{i}", "ja": f"モン{i}", "picked": bool(p)}
            for i, p in enumerate(party_picked)]
    if active is not None:
        party = rec["state"]["player"].setdefault(
            "party", [{"species": f"mon{i}", "ja": f"モン{i}"} for i in range(6)])
        rec["state"]["player"]["active"] = active
    return rec


def _advice(t, best_kind="move", best_id="earthquake", name="じしん",
            score=80.0, second=60.0):
    return {"type": "advice", "kind": "battle", "t": t,
            "advice": {"ok": True,
                       "best": {"kind": best_kind, "id": best_id, "name": name,
                                "score": score},
                       "actions": [
                           {"kind": best_kind, "id": best_id, "score": score},
                           {"kind": "move", "id": "surf", "score": second}]}}


def _action_move(t, turn, move_id="earthquake"):
    return {"type": "events", "t": t, "turn": turn,
            "fired": [f"move_player_{move_id}"], "texts": []}


def _hp(t, turn, side, frm, to):
    return {"type": "hp", "t": t, "turn": turn, "text": "",
            "detail": {"side": side, "from": frm, "to": to}}


def test_clean_decision():
    """助言あり・即時・一致・軽微スイング → 欠陥ゼロ"""
    recs = [
        _scene(100.0, SCENE_COMMAND),
        _advice(101.5),
        _scene(103.0, SCENE_FIELD),
        _action_move(104.0, 1),
        _hp(105.0, 1, "opponent", 100, 60),
        _hp(106.0, 1, "player", 100, 90),
        {"type": "outcome", "outcome": "win", "t": 200.0},
    ]
    a = audit_battle(recs)
    assert a["n_decisions"] == 1 and a["n_with_advice"] == 1
    d = a["decisions"][0]
    assert d["agree"] and d["flags"] == [], d
    assert abs(d["latency"] - 1.5) < 0.01, d["latency"]
    assert d["swing"] == 30.0, d["swing"]   # 自分-10 相手-40
    assert a["defects"] == []
    print("test_clean_decision OK")


def test_move_select_does_not_reset_open_time():
    """command→move_select の往復で決定開始時刻が上書きされない"""
    recs = [
        _scene(100.0, SCENE_COMMAND),
        _scene(102.0, SCENE_MOVE_SELECT),
        _advice(103.0),
        _scene(104.0, SCENE_FIELD),
        _action_move(105.0, 1),
    ]
    a = audit_battle(recs)
    assert abs(a["decisions"][0]["latency"] - 3.0) < 0.01, a["decisions"][0]
    print("test_move_select_does_not_reset_open_time OK")


def test_late_and_mismatch_and_no_advice():
    recs = [
        # 決定1: 助言が15秒後 (遅延) かつ 実行と不一致
        _scene(100.0, SCENE_COMMAND),
        _advice(115.0, best_id="surf", name="なみのり"),
        _scene(116.0, SCENE_FIELD),
        _action_move(117.0, 1, move_id="earthquake"),
        # 決定2: 助言なし
        _scene(130.0, SCENE_COMMAND),
        _scene(131.0, SCENE_FIELD),
        _action_move(200.0, 2),
    ]
    a = audit_battle(recs)
    d1, d2 = a["decisions"]
    assert "late" in d1["flags"] and "mismatch" in d1["flags"], d1
    assert d1["latency"] == 15.0, d1
    assert "no_advice" in d2["flags"], d2
    assert len(a["defects"]) == 2
    print("test_late_and_mismatch_and_no_advice OK")


def test_stale_advice_counts_as_zero_latency():
    """前の決定の助言が画面に残っている場合は遅延0扱い"""
    recs = [
        _advice(95.0),
        _scene(100.0, SCENE_COMMAND),
        _scene(101.0, SCENE_FIELD),
        _action_move(102.0, 1),
    ]
    a = audit_battle(recs)
    assert a["decisions"][0]["latency"] == 0.0, a["decisions"][0]
    print("test_stale_advice_counts_as_zero_latency OK")


def test_heavy_swing_flag():
    recs = [
        _scene(100.0, SCENE_COMMAND),
        _advice(100.5),
        _scene(101.0, SCENE_FIELD),
        _action_move(102.0, 3),
        _hp(103.0, 3, "player", 100, 40),   # 自分-60, 相手ダメージなし
    ]
    a = audit_battle(recs)
    d = a["decisions"][0]
    assert d["swing"] == -60.0 and "heavy_swing" in d["flags"], d
    print("test_heavy_swing_flag OK")


def test_switch_agreement_uses_next_active():
    recs = [
        _scene(100.0, SCENE_COMMAND),
        _advice(100.5, best_kind="switch", best_id="mon2", name="モン2"),
        _scene(101.0, SCENE_FIELD),
        {"type": "events", "t": 102.0, "turn": 4,
         "fired": ["switch_player"], "texts": []},
        _scene(103.0, SCENE_FIELD, active=2),
    ]
    a = audit_battle(recs)
    d = a["decisions"][0]
    assert d["agree"], d
    assert d["executed_id"] == "mon2", d
    print("test_switch_agreement_uses_next_active OK")


def test_selection_audit():
    recs = [
        {"type": "advice", "kind": "selection", "t": 50.0, "advice": {
            "ok": True, "picked": 3, "done": True,
            "recommend": [
                {"index": 4, "name": "モン4", "lead": True},
                {"index": 0, "name": "モン0", "lead": False},
                {"index": 2, "name": "モン2", "lead": False}]}},
        _scene(60.0, SCENE_SELECTION,
               party_picked=[True, False, True, False, True, False]),
        {"type": "events", "t": 70.0, "turn": 0,
         "fired": ["switch_player"], "texts": []},
        _scene(71.0, SCENE_FIELD, active=4),
    ]
    a = audit_battle(recs)
    sel = a["selection"]
    assert sel["members_match"] is True, sel      # {0,2,4} == {4,0,2}
    assert sel["lead_match"] is True, sel         # 先発 モン4
    print("test_selection_audit OK")


def test_lead_switch_is_not_a_battle_decision():
    """対戦冒頭の先発繰り出し (決定画面も助言もまだ無い) は決定に数えない"""
    recs = [
        {"type": "events", "t": 90.0, "turn": 0,
         "fired": ["switch_player"], "texts": []},   # 先発の「ゆけっX」
        _scene(100.0, SCENE_COMMAND),
        _advice(101.0),
        _scene(102.0, SCENE_FIELD),
        _action_move(103.0, 1),
    ]
    a = audit_battle(recs)
    assert a["n_decisions"] == 1, a["decisions"]   # 先発は数えない
    assert a["decisions"][0]["executed_kind"] == "move"
    print("test_lead_switch_is_not_a_battle_decision OK")


def test_churn_followed_displayed_best_is_agreement():
    """表示中に従った後、押下後に推奨が反転しても不一致にしない (churn計上)"""
    recs = [
        _scene(100.0, SCENE_COMMAND),
        _advice(101.0, best_id="bravebird", name="ブレイブバード"),
        _advice(107.0, best_id="doubleedge", name="すてみタックル"),  # 押下後の反転
        _scene(107.2, SCENE_FIELD),
        _action_move(110.0, 1, move_id="bravebird"),
    ]
    a = audit_battle(recs)
    d = a["decisions"][0]
    assert d["agree"] is True, d
    assert d["churn"] is True and d.get("followed_earlier_best") is True, d
    assert d["flags"] == [], d
    assert a["n_churn"] == 1
    # 窓のどのbestとも一致しない場合は従来どおり不一致
    recs2 = recs[:-1] + [_action_move(110.0, 1, move_id="surf")]
    a2 = audit_battle(recs2)
    assert "mismatch" in a2["decisions"][0]["flags"], a2["decisions"][0]
    print("test_churn_followed_displayed_best_is_agreement OK")


def test_selection_uses_most_complete_pick_state():
    """選出途中 (1/3) のスナップショットではなく、揃った時点と比較する"""
    recs = [
        {"type": "advice", "kind": "selection", "t": 50.0, "advice": {
            "ok": True, "picked": 3, "done": True,
            "recommend": [
                {"index": 4, "name": "モン4", "lead": True},
                {"index": 0, "name": "モン0", "lead": False},
                {"index": 2, "name": "モン2", "lead": False}]}},
        _scene(55.0, SCENE_SELECTION,
               party_picked=[False, False, False, False, True, False]),  # 1/3
        _scene(60.0, SCENE_SELECTION,
               party_picked=[True, False, True, False, True, False]),    # 3/3
    ]
    a = audit_battle(recs)
    assert a["selection"]["members_match"] is True, a["selection"]
    print("test_selection_uses_most_complete_pick_state OK")


def test_selection_falls_back_to_observed_members():
    """pickedフラグが不完全なら、実際に場に出た種族で判定する"""
    base = [
        {"type": "advice", "kind": "selection", "t": 50.0, "advice": {
            "ok": True, "picked": 1, "done": False,
            "recommend": [
                {"index": 4, "name": "モン4", "lead": True},
                {"index": 0, "name": "モン0", "lead": False},
                {"index": 2, "name": "モン2", "lead": False}]}},
        # pickedは1体しか取れていない (実測の抽出欠け)
        _scene(55.0, SCENE_SELECTION,
               party_picked=[False, False, False, False, True, False]),
    ]
    # 場に出た3体が推奨と一致 → 一致 (basis=observed)
    recs = base + [_scene(70.0, SCENE_FIELD, active=4),
                   _scene(80.0, SCENE_FIELD, active=0),
                   _scene(90.0, SCENE_FIELD, active=2)]
    sel = audit_battle(recs)["selection"]
    assert sel["members_match"] is True and "observed" in sel["members_basis"], sel
    # 推奨外の種族が場に出た → 不一致
    recs2 = base + [_scene(70.0, SCENE_FIELD, active=5)]
    sel2 = audit_battle(recs2)["selection"]
    assert sel2["members_match"] is False, sel2
    # 2体しか確認できず矛盾なし → 確認不能 (None)
    recs3 = base + [_scene(70.0, SCENE_FIELD, active=4),
                    _scene(80.0, SCENE_FIELD, active=0)]
    sel3 = audit_battle(recs3)["selection"]
    assert sel3["members_match"] is None, sel3
    print("test_selection_falls_back_to_observed_members OK")


def test_provisional_advice_is_not_displayed_history():
    """確定前 (provisional) の助言はフロント未表示なので、
    遅延測定にも一致判定の表示履歴にも数えない (第3回の安定化ゲート対応)"""
    prov = _advice(101.0, best_id="surf", name="なみのり")
    prov["advice"]["provisional"] = True
    recs = [
        _scene(100.0, SCENE_COMMAND),
        prov,                                    # 確定前 (未表示)
        _advice(105.0),                          # 確定 (じしん)
        _scene(106.0, SCENE_FIELD),
        _action_move(107.0, 1),                  # じしん実行
    ]
    a = audit_battle(recs)
    d = a["decisions"][0]
    assert d["agree"] is True, d                  # 確定助言とのみ突き合わせ
    assert abs(d["latency"] - 5.0) < 0.01, d      # 遅延は確定助言の時刻まで
    assert d.get("churn") is not True, d          # 未表示の揺れはchurnに数えない
    print("test_provisional_advice_is_not_displayed_history OK")


def test_session_file_filter():
    """--session はマーカー時刻以降の対戦ログだけを対象にする"""
    import os
    from tools import decision_audit as da
    d = Path(tempfile.mkdtemp())
    old = d / "battle_old.jsonl"
    new = d / "battle_new.jsonl"
    for p in (old, new):
        p.write_text("{}\n", encoding="utf-8")
    os.utime(old, (1000.0, 1000.0))
    os.utime(new, (2000.0, 2000.0))
    picked = da._files_since([str(old), str(new)], 1500.0)
    assert picked == [str(new)], picked
    print("test_session_file_filter OK")


def test_render_and_file_e2e():
    recs = [
        _scene(100.0, SCENE_COMMAND),
        _advice(101.0),
        _scene(102.0, SCENE_FIELD),
        _action_move(103.0, 1),
        {"type": "outcome", "outcome": "loss", "t": 200.0},
    ]
    d = Path(tempfile.mkdtemp())
    p = d / "battle_test.jsonl"
    with p.open("w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    from tools.decision_audit import _load
    a = audit_battle(_load(str(p)))
    text = render_text(p.name, a, late_sec=10.0)
    assert "決定 1" in text and "負け" in text, text
    assert "✅" in text, text
    print("test_render_and_file_e2e OK")


def _advice_id(t, aid, **kw):
    rec = _advice(t, **kw)
    rec["advice_id"] = aid
    rec["advice"]["advice_id"] = aid
    rec["advice"]["t_gen"] = t
    return rec


def _display(aid, t_shown, t_recv, hidden=False):
    return {"type": "display", "advice_id": aid, "t_shown": t_shown, "t": t_recv, "kind": "battle", "hidden": hidden}


def test_unknown_latency_is_not_timely():
    """決定画面の開始を捉えられない決定 (遅延が測れない) は「時間内」にも分母にも入れず、判定不能として別に数える。
    欠陥なしの表示でも「成功に含めていないもの」として出す (2026-10-07 §10 の確認)"""
    recs = [
        _advice(101.0),                 # 決定画面 (command) の行が無い
        _scene(102.0, SCENE_FIELD),
        _action_move(103.0, 1),
    ]
    a = audit_battle(recs)
    d = a["decisions"][0]
    assert d["latency"] is None and d["latency_basis"] is None and d["flags"] == [], d
    assert a["n_latency_known"] == 0 and a["n_timely"] == 0 and a["n_latency_unknown"] == 1, a
    text = render_text("x", a, late_sec=10.0)
    assert "判定不能 1 件" in text and "成功に含めていないもの" in text and "遅延の判定不能 1" in text, text
    print("test_unknown_latency_is_not_timely OK")


def test_carried_latency_is_counted_separately():
    """前の決定の助言が残っていて遅延 0 とした決定は、時間内に含めたまま件数を別に出す"""
    recs = [
        _advice(95.0),
        _scene(100.0, SCENE_COMMAND),
        _scene(101.0, SCENE_FIELD),
        _action_move(102.0, 1),
    ]
    a = audit_battle(recs)
    assert a["decisions"][0]["latency_basis"] == "carried", a["decisions"][0]
    assert a["n_latency_carried"] == 1 and a["n_timely"] == 1, a
    # 決定画面の後に出た助言は measured
    b = audit_battle([_scene(100.0, SCENE_COMMAND), _advice(101.5), _scene(103.0, SCENE_FIELD), _action_move(104.0, 1)])
    assert b["decisions"][0]["latency_basis"] == "measured" and b["n_latency_carried"] == 0, b
    print("test_carried_latency_is_counted_separately OK")


def test_display_status_kinds():
    """表示 / 隠れたタブ / 表示なし / 判定不能 (display の行が無いログ) を分けて数える"""
    def battle(disp_rows):
        recs = []
        t = 100.0
        for i in range(4):
            aid = f"A{i}"
            recs += [_scene(t, SCENE_COMMAND), _advice_id(t + 1.0, aid), _scene(t + 2.0, SCENE_FIELD),
                     _action_move(t + 3.0, i + 1)]
            t += 10.0
        return recs + disp_rows

    a = audit_battle(battle([_display("A0", 101.2, 101.25), _display("A1", 111.3, 111.32, hidden=True),
                             _display("A3", 131.1, 131.2)]))
    ds = [d["display"] for d in a["decisions"]]
    assert ds == ["shown", "hidden", "not_shown", "shown"], ds
    assert (a["n_display_shown"], a["n_display_hidden"], a["n_display_not_shown"], a["n_display_unknown"]) == (2, 1, 1, 0), a
    assert abs(a["decisions"][0]["display_latency"] - 0.2) < 1e-6 and a["n_clock_skew"] == 0, a["decisions"][0]
    # 欠陥の件数は従来どおり (表示なしは flags に入れない)、欠陥なしの行に表示されなかった助言を出す
    assert a["defects"] == [] and a["has_display_rows"] is True
    text = render_text("x", a, late_sec=10.0)
    assert "表示なし 1" in text and "表示されなかった助言 2" in text, text
    # display の行が 1 つも無いログ → 全部判定不能
    b = audit_battle(battle([]))
    assert b["n_display_unknown"] == 4 and b["has_display_rows"] is False, b
    assert "display の行が無いログ" in render_text("x", b, late_sec=10.0)
    print("test_display_status_kinds OK")


def test_clock_skew_makes_display_latency_unknown():
    """ブラウザの時計 (t_shown) とサーバーの受信時刻 (t) の差が閾値を超えたら時計差: 表示までの遅れは判定不能"""
    from tools.decision_audit import _display_index, display_status
    recs = [_scene(100.0, SCENE_COMMAND), _advice_id(101.0, "A0"), _display("A0", 106.0, 101.1),
            _scene(103.0, SCENE_FIELD), _action_move(104.0, 1)]
    a = audit_battle(recs)
    d = a["decisions"][0]
    assert d["display"] == "shown" and d["clock_skew"] is True and d["display_latency"] is None, d
    assert abs(d["clock_offset"] - 4.9) < 1e-6 and a["n_clock_skew"] == 1, d
    # 純粋関数: 閾値の境界と、advice_id が無い助言は判定不能
    idx = _display_index([_display("B", 10.5, 10.0)])
    assert display_status(["B"], {"B": 10.0}, idx, clock_skew_sec=1.0)["clock_skew"] is False
    assert display_status(["B"], {"B": 10.0}, idx, clock_skew_sec=0.4)["clock_skew"] is True
    assert display_status([None], {}, idx)["display"] == "unknown"
    print("test_clock_skew_makes_display_latency_unknown OK")


if __name__ == "__main__":
    test_unknown_latency_is_not_timely()
    test_carried_latency_is_counted_separately()
    test_display_status_kinds()
    test_clock_skew_makes_display_latency_unknown()
    test_clean_decision()
    test_move_select_does_not_reset_open_time()
    test_late_and_mismatch_and_no_advice()
    test_stale_advice_counts_as_zero_latency()
    test_heavy_swing_flag()
    test_switch_agreement_uses_next_active()
    test_selection_audit()
    test_lead_switch_is_not_a_battle_decision()
    test_churn_followed_displayed_best_is_agreement()
    test_selection_uses_most_complete_pick_state()
    test_selection_falls_back_to_observed_members()
    test_provisional_advice_is_not_displayed_history()
    test_session_file_filter()
    test_render_and_file_e2e()
    print("\nALL OK")
