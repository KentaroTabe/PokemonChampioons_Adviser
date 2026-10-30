"""引き継ぎ評価 段階 A の F / R の標本 (tools/inheritance_eval/fsample) の純粋な部分のテスト: 保存フレームの名前、決定画面の窓、
セッション、フレームの選び方、F の抽出・除外・分割、R の根拠画像の有無。合成の対戦ログとファイル名だけで、原本は読まない。

    python -m tests.test_inheritance_fsample
"""
from __future__ import annotations

from tools.inheritance_eval import fsample as FS
from vision.scenes import (
    SCENE_BATTLE_HUD, SCENE_COMMAND, SCENE_FIELD, SCENE_MOVE_SELECT, SCENE_SELECTION, SCENE_WATCH)


def _state(active=0, picked=(True, True, False, False, False, False)):
    party = [{"species": f"mon{i}", "ja": f"ポケ{i}", "hp": 100.0 if i == active else None, "hp_raw": [150, 150],
              "moves": [["tackle", 30]] if i == active else [], "item": "leftovers", "picked": p}
             for i, p in enumerate(picked)]
    return {"scene": "command", "field": {"weather": "rain", "terrain": None, "trick_room": False},
            "player": {"active": active, "remaining": 3, "screens": {"reflect": True, "light_screen": False}, "party": party},
            "opponent": {"active": 0, "remaining": 3, "screens": {}, "party": [{"species": "foe", "hp": 50.0, "picked": False}]}}


def _scene(t, scene, turn=1):
    return {"type": "scene", "t": t, "scene": scene, "turn": turn, "state": _state()}


def _advice(t, aid, turn=1):
    return {"type": "advice", "kind": "battle", "t": t, "turn": turn, "advice_id": aid, "state": _state(),
            "advice": {"ok": True, "t_gen": t, "best": {"kind": "move", "id": "tackle", "name": "たいあたり"}}}


def _battle(t0, kind="real"):
    """選出 (t0+0〜20) → 行動 ターン 1 (t0+30〜36、途中で様子見) → 解決 → ターン 2 (t0+50〜55) → 解決 (t0+60) → 最後の行 t0+70"""
    return [
        {"type": "session", "source": "organic", "dataset_kind": kind, "data_quality": "trusted", "t": t0},
        _scene(t0 + 0.0, SCENE_SELECTION, 0),
        _scene(t0 + 20.0, SCENE_FIELD, 0),
        _scene(t0 + 30.0, SCENE_COMMAND, 1),
        _advice(t0 + 31.0, f"{int(t0)}-0001", 1),
        _scene(t0 + 32.0, SCENE_WATCH, 1),
        _scene(t0 + 34.0, SCENE_MOVE_SELECT, 1),
        _scene(t0 + 36.0, SCENE_BATTLE_HUD, 1),
        _scene(t0 + 50.0, SCENE_COMMAND, 2),
        _scene(t0 + 55.0, SCENE_FIELD, 2),
        {"type": "events", "t": t0 + 70.0},
    ]


def _frames(names):
    return FS.FrameIndex([FS.parse_frame(n) for n in names])


def test_parse_frame():
    f = FS.parse_frame("frame_1792453815.png")
    assert f == {"name": "frame_1792453815.png", "kind": "frame_", "t": 1792453815.0, "burst_id": None}
    assert FS.parse_frame("sel_1792454069.png")["kind"] == "sel_"
    b = FS.parse_frame("burst/burst_1791502993/1791503007691.jpg")
    assert b["kind"] == FS.KIND_BURST and abs(b["t"] - 1791503007.691) < 1e-6 and b["burst_id"] == "burst_1791502993"
    for bad in ("frame_x.png", "other_123.png", "burst/b/abc.jpg", "burst/123.jpg", "x/frame_1.png"):
        assert FS.parse_frame(bad) is None, bad
    print("test_parse_frame OK")


def test_decision_windows_rules():
    t0 = 1000.0
    ws = FS.decision_windows(_battle(t0), t0 + 70.0)
    assert [(w["family"], w["turn"]) for w in ws] == [("selection", 0), ("turn", 1), ("turn", 2)]
    sel, t1, t2 = ws
    assert (sel["t_open"], sel["t_close"]) == (t0, t0 + 20.0)
    # 様子見は窓を閉じない。決定画面の区間は様子見の間を除く
    assert (t1["t_open"], t1["t_close"]) == (t0 + 30.0, t0 + 36.0)
    assert t1["intervals"] == [[t0 + 30.0, t0 + 32.0], [t0 + 34.0, t0 + 36.0]]
    assert t1["scenes"] == [SCENE_COMMAND, SCENE_MOVE_SELECT]
    assert t2["t_close"] == t0 + 55.0 and not t2["closed_by_end"]
    # 閉じない最後の窓は対戦ログの最後で閉じる
    recs = [_scene(5.0, SCENE_COMMAND, 3)]
    w = FS.decision_windows(recs, 9.0)[0]
    assert w["t_close"] == 9.0 and w["closed_by_end"] and w["intervals"] == [[5.0, 9.0]]
    print("test_decision_windows_rules OK")


def test_decision_windows_merge():
    # 同じターンの開き直し: merge_sec 以内ならまとめる、超えたら別
    recs = [_scene(10.0, SCENE_COMMAND, 4), _scene(12.0, SCENE_BATTLE_HUD, 4), _scene(14.0, SCENE_MOVE_SELECT, 4),
            _scene(16.0, SCENE_FIELD, 4), _scene(30.0, SCENE_COMMAND, 4), _scene(33.0, SCENE_FIELD, 4)]
    ws = FS.decision_windows(recs, 40.0, merge_sec=3.0)
    assert len(ws) == 2
    assert (ws[0]["t_open"], ws[0]["t_close"], ws[0]["n_reopen"]) == (10.0, 16.0, 1)
    assert ws[0]["spans"] == [[10.0, 12.0], [14.0, 16.0]]
    # 別のターンならまとめない
    recs2 = [_scene(10.0, SCENE_COMMAND, 4), _scene(11.0, SCENE_FIELD, 4), _scene(12.0, SCENE_COMMAND, 5)]
    assert len(FS.decision_windows(recs2, 20.0, merge_sec=3.0)) == 2
    # ターン 0 の選出は間隔によらず 1 回にまとめる (直前の窓も選出のとき)
    recs3 = [_scene(0.0, SCENE_SELECTION, 0), _scene(20.0, SCENE_FIELD, 0), _scene(40.0, SCENE_SELECTION, 0),
             _scene(41.0, SCENE_FIELD, 0)]
    ws3 = FS.decision_windows(recs3, 50.0, merge_sec=3.0)
    assert len(ws3) == 1 and ws3[0]["spans"] == [[0.0, 20.0], [40.0, 41.0]]
    print("test_decision_windows_merge OK")


def test_frames_of_window_and_pick():
    recs = [_scene(10.0, SCENE_COMMAND, 1), _scene(12.0, SCENE_WATCH, 1), _scene(16.0, SCENE_MOVE_SELECT, 1),
            _scene(18.0, SCENE_FIELD, 1)]
    w = FS.decision_windows(recs, 30.0)[0]
    # 決定画面の区間のフレームを優先 (様子見の間の 13 は使わない)
    fidx = _frames(["frame_11.png", "frame_13.png", "frame_17.png", "frame_25.png"])
    fs, basis = FS.frames_of_window(fidx, w)
    assert basis == "decision" and [f["name"] for f in fs] == ["frame_11.png", "frame_17.png"]
    # 決定画面の区間に無ければ様子見の間のフレーム (info_only)
    fs2, basis2 = FS.frames_of_window(_frames(["frame_13.png"]), w)
    assert basis2 == "info_only" and [f["name"] for f in fs2] == ["frame_13.png"]
    assert FS.frames_of_window(_frames(["frame_25.png"]), w) == ([], None)
    # pick_frames: PNG があれば PNG だけ、等間隔に最大 k 枚、代表は真ん中
    many = [FS.parse_frame(f"sel_{t}.png") for t in range(100, 110)] + [FS.parse_frame("burst/b/105500.jpg")]
    chosen, primary = FS.pick_frames(many, 3)
    assert [c["name"] for c in chosen] == ["sel_100.png", "sel_104.png", "sel_109.png"] and primary["name"] == "sel_104.png"
    burst_only = [FS.parse_frame(f"burst/b/{t}000.jpg") for t in range(200, 205)]
    chosen2, primary2 = FS.pick_frames(burst_only, 2)
    assert [c["name"] for c in chosen2] == ["burst/b/200000.jpg", "burst/b/204000.jpg"] and primary2["name"] == "burst/b/204000.jpg"
    assert FS.pick_frames([], 3) == ([], None)
    # 窓の外の最も近いフレームまでの秒
    fidx2 = _frames(["frame_5.png", "frame_40.png"])
    assert fidx2.nearest_gap(10.0, 20.0) == 5.0 and fidx2.nearest_gap(4.0, 6.0) == 0.0
    assert FS.FrameIndex([]).nearest_gap(1.0, 2.0) is None
    print("test_frames_of_window_and_pick OK")


def test_assign_sessions():
    spans = {"battle_20261020_084841.jsonl": (1000.0, 1300.0), "battle_20261020_085420.jsonl": (1320.0, 1600.0),
             "battle_20261020_120000.jsonl": (1600.0 + 1800.0 + 1, 4000.0), "battle_20261020_130000.jsonl": (4000.0 + 1800.0, 6000.0)}
    s = FS.assign_sessions(spans, gap=1800.0)
    assert s["battle_20261020_084841.jsonl"] == s["battle_20261020_085420.jsonl"] == "S20261020_0848"
    assert s["battle_20261020_120000.jsonl"] == "S20261020_1200"          # 1800 秒を超えた
    assert s["battle_20261020_130000.jsonl"] == "S20261020_1200"          # ちょうど 1800 秒は同じセッション
    print("test_assign_sessions OK")


def _world():
    """3 対戦 (A: 画像あり 3 決定 / B: 画像あり 1 決定 + 実戦以外のログ C) と保存フレーム"""
    A, B, C = 10000.0, 20000.0, 30000.0
    battles = [("battle_20261020_084841.jsonl", _battle(A)), ("battle_20261020_085420.jsonl", _battle(B)),
               ("battle_20261020_090000.jsonl", _battle(C, kind="sim"))]
    frames = [f"sel_{int(A + 5)}.png", f"sel_{int(A + 10)}.png", f"frame_{int(A + 31)}.png", f"frame_{int(A + 52)}.png",
              f"burst/burst_{int(B)}/{int((B + 35) * 1000)}.jpg", f"frame_{int(B + 58)}.png",
              f"frame_{int(C + 31)}.png"]
    return battles, _frames(frames)


def test_build_f_cases_and_exclusions():
    battles, fidx = _world()
    sessions = FS.assign_sessions({n: FS.battle_span(r) for n, r in battles})
    f = FS.build_f(battles, fidx, sessions, r_marks=[], dev_refs={"battle_20261020_084841.jsonl": ["docs/X.md"]}, seed=1, target=60)
    cases = f["cases"]
    assert [(c["battle"][7:22], c["family"], c["turn"]) for c in cases] == [
        ("20261020_084841", "selection", 0), ("20261020_084841", "turn", 1), ("20261020_084841", "turn", 2),
        ("20261020_085420", "turn", 1)]
    c0, c1 = cases[0], cases[1]
    assert c0["case_id"] == c0["scene_id"] == "F001" and c0["frames"] == [f"sel_{10005}.png", f"sel_{10010}.png"]
    assert c1["frame_primary"] == "frame_10031.png" and c1["advice_present"] and c1["advice_id"] == "10000-0001"
    assert c1["system_advice"] == {"kind": "move", "id": "tackle", "name": "たいあたり"}
    assert c0["advice_present"] is False and c0["system_advice"] is None
    assert c1["past_data_diagnostic"] and not cases[3]["past_data_diagnostic"]
    # 正解欄は空・未記入
    for c in cases:
        tr = c["truth"]
        assert c["labels_status"] == "unlabeled" and c["labeler"] is None and c["labeled_at"] is None
        assert tr["legal_actions"] is None and tr["unknown_fields"] is None and tr["unreadable"] is None and tr["scene"] is None
        assert tr["state"]["player"] == {"active": None, "party": None} and tr["state"]["weather"] is None
    # システムの観測の要約 (選出の 3 値・壁・天候)
    ss = c1["system_state"]
    assert ss["weather"] == "rain" and ss["player"]["screens"] == ["reflect"]
    assert [m["pick"] for m in ss["player"]["party"]] == ["picked", "picked", "unknown", "unknown", "unknown", "unknown"]
    assert ss["player"]["party"][0]["moves"] == ["tackle"]
    # 除外: 実戦以外 (C の 3 窓) と画像の無い窓 (B の選出・ターン 2。窓の外の最も近いフレームの秒つき)
    reasons = sorted((e["battle"][16:22], e["reason"]) for e in f["excluded"])
    assert reasons == [("085420", "no_frame"), ("085420", "no_frame"), ("090000", "dataset_kind"), ("090000", "dataset_kind"),
                       ("090000", "dataset_kind")]
    gaps = sorted(e["near_gap"] for e in f["excluded"] if e["reason"] == "no_frame")
    assert gaps == [3.0, 15.0]
    # 分割は対戦単位 (同じ対戦が両方に入らない)、決定は概ね半分
    by = {}
    for c in cases:
        by.setdefault(c["battle"], set()).add(c["split"])
    assert all(len(v) == 1 for v in by.values())
    assert {c["split"] for c in cases} == {"dev", "confirm"}
    print("test_build_f_cases_and_exclusions OK")


def test_build_f_r_overlap_and_mid_battle_selection():
    battles, fidx = _world()
    # A のターン 1 の窓に R の局面 (助言の時刻)
    f = FS.build_f(battles[:1], fidx, {}, r_marks=[("battle_20261020_084841.jsonl", 10031.0)], seed=1)
    assert [c["turn"] for c in f["cases"]] == [0, 2]
    assert [e["reason"] for e in f["excluded"]] == ["r_overlap"]
    # ターン 1 以降の選出の判定は除外
    recs = _battle(10000.0) + [_scene(10069.0, SCENE_SELECTION, 5)]
    fidx2 = _frames(["sel_10069.png", "frame_10031.png"])
    f2 = FS.build_f([("battle_20261020_084841.jsonl", recs)], fidx2, {}, seed=1)
    assert [c["turn"] for c in f2["cases"]] == [1]
    assert sorted(e["reason"] for e in f2["excluded"]) == ["no_frame", "no_frame", "selection_mid_battle"]
    print("test_build_f_r_overlap_and_mid_battle_selection OK")


def test_determinism_and_sampling():
    battles, fidx = _world()
    a = FS.build_f(battles, fidx, {}, seed=7)
    b = FS.build_f(battles, fidx, {}, seed=7)
    strip = lambda f: [(c["case_id"], c["battle"], c["t"], c["split"], c["frames"]) for c in f["cases"]]  # noqa: E731
    assert strip(a) == strip(b)
    # 目標を超えたら対戦ごとに順に回す: 2 件なら 2 対戦から 1 件ずつ
    f = FS.build_f(battles, fidx, {}, seed=7, target=2)
    assert len(f["cases"]) == 2 and len({c["battle"] for c in f["cases"]}) == 2 and len(f["not_sampled"]) == 2
    pool = [{"battle": "x", "t_open": float(i)} for i in range(5)] + [{"battle": "y", "t_open": 9.0}]
    got = FS.sample_round_robin(pool, 3, seed=3)
    assert len(got) == 3 and sum(1 for g in got if g["battle"] == "y") == 1
    assert FS.sample_round_robin(pool, 10, seed=3) == pool
    print("test_determinism_and_sampling OK")


def test_split_by_battle():
    counts = {"a": 7, "b": 5, "c": 5, "d": 3, "e": 1, "f": 1}
    s1 = FS.split_by_battle(counts, seed=5)
    assert s1 == FS.split_by_battle(counts, seed=5)
    tot = {k: sum(counts[n] for n, v in s1.items() if v == k) for k in ("dev", "confirm")}
    assert set(s1) == set(counts) and abs(tot["dev"] - tot["confirm"]) <= 1
    assert FS.split_by_battle({"only": 4}, seed=1) == {"only": "dev"}
    assert FS.split_by_battle({}, seed=1) == {}
    print("test_split_by_battle OK")


def test_f_summary_shortfall():
    battles, fidx = _world()
    sessions = FS.assign_sessions({n: FS.battle_span(r) for n, r in battles})
    f = FS.build_f(battles, fidx, sessions, seed=1)
    s = FS.f_summary(f, sessions)
    # A と B は 1800 秒より離れているので別のセッション (C は実戦以外で F に入らない)
    assert (s["decisions"], s["battles"], s["sessions"]) == (4, 2, 2)
    assert s["shortfall"]["sessions"] == max(0, FS.INH_FSAMPLE_TARGET_SESSIONS - 2)
    assert s["shortfall"]["decisions"] == FS.INH_FSAMPLE_TARGET_DECISIONS - 4
    assert s["shortfall"]["battles"] == FS.INH_FSAMPLE_TARGET_BATTLES - 2
    assert s["by_family"] == {"selection": 1, "turn": 3} and s["excluded"] == {"no_frame": 2, "dataset_kind": 3}
    h = FS.near_gap_hist(f["excluded"], tols=(5.0, 10.0))
    assert h == {"<=5s": 1, "<=10s": 1, "no_frame_total": 2, "no_frame_at_all": 0}
    print("test_f_summary_shortfall OK")


def test_r_rows():
    battles, fidx = _world()
    logs = dict(battles)
    rows = [{"scene_id": "s001", "category": "consistent", "labels_status": "unlabeled",
             "source": {"file": "battle_20261020_084841.jsonl", "t": 10032.0, "turn": 1, "advice_id": "10000-0001"}},
            {"scene_id": "s002", "category": "stale", "labels_status": "unlabeled",
             "source": {"file": "battle_20261020_085420.jsonl", "t": 20020.0, "turn": 0}},
            {"scene_id": "s003", "category": "late", "labels_status": "unlabeled",
             "source": {"file": "battle_20261006_000000.jsonl", "t": 5.0, "turn": 0}}]
    r = FS.r_from_scene_set(rows, "set1", logs, fidx, tol=10.0)
    assert [x["evidence"] for x in r] == ["image_available", "no_image", "no_log"]
    assert r[0]["frames"] == ["frame_10031.png"] and r[0]["included_in_r"] and r[0]["decision_window"]["turn"] == 1
    assert r[1]["nearest_frame_sec"] == 15.0 and not r[1]["included_in_r"]
    assert all(x["labels_status"] == "unlabeled" and x["truth"]["state"]["weather"] is None for x in r)
    assert r[0]["r_id"] == "R-set1-s001" and r[0]["category"] == "consistent"
    # 時刻が対戦ログの時間の幅の外のフレームは対応に数えない
    lone = FS.r_row("R-x", "known_issue", "E-x", "battle_20261020_084841.jsonl", 10069.0, logs["battle_20261020_084841.jsonl"],
                    _frames(["frame_10075.png"]), tol=10.0)
    assert lone["evidence"] == "no_image" and lone["nearest_frame_sec"] == 6.0
    nl = FS.r_row("R-y", "known_issue", "E-y", None, None, None, fidx)
    assert nl["evidence"] == "not_located" and not nl["included_in_r"]
    print("test_r_rows OK")


def test_locate_and_doc_parsing():
    recs = _battle(10000.0)
    assert FS.locate({"kind": "advice_id", "advice_id": "10000-0001"}, recs) == 10031.0
    assert FS.locate({"kind": "advice_id", "advice_id": "nope"}, recs) is None
    assert FS.locate({"kind": "offset", "from_sec": 30.0, "to_sec": 32.0}, recs) == 10031.0
    assert FS.locate({"kind": "offset", "from_sec": 40.0, "to_sec": 50.0}, recs) is None
    assert FS.locate({"kind": "advice_id", "advice_id": "x"}, None) is None
    text = "| E-ev-1 a |\n| E-accept-1 b battle_20261020_085420 |\nx | E-no-1 |\n| E-ev-1 again |\n"
    assert FS.known_issue_ids(text) == ["E-ev-1", "E-accept-1"]
    assert FS.battle_stems(text + "battle_20260916_033459.jsonl") == ["20260916_033459", "20261020_085420"]
    # 運用側の対応表は E-* の id の形
    assert all(k.startswith("E-") for k in FS.KNOWN_ISSUE_LOCATORS)
    print("test_locate_and_doc_parsing OK")


def main() -> None:
    test_parse_frame()
    test_decision_windows_rules()
    test_decision_windows_merge()
    test_frames_of_window_and_pick()
    test_assign_sessions()
    test_build_f_cases_and_exclusions()
    test_build_f_r_overlap_and_mid_battle_selection()
    test_determinism_and_sampling()
    test_split_by_battle()
    test_f_summary_shortfall()
    test_r_rows()
    test_locate_and_doc_parsing()
    print("ALL OK")


if __name__ == "__main__":
    main()
