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


if __name__ == "__main__":
    test_clean_decision()
    test_move_select_does_not_reset_open_time()
    test_late_and_mismatch_and_no_advice()
    test_stale_advice_counts_as_zero_latency()
    test_heavy_swing_flag()
    test_switch_agreement_uses_next_active()
    test_selection_audit()
    test_lead_switch_is_not_a_battle_decision()
    test_session_file_filter()
    test_render_and_file_e2e()
    print("\nALL OK")
