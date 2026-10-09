"""助言の追跡 (advisor.versions / battle_logger の advice_id・display / tools.advice_trace / tools.scene_eval) の純粋関数テスト。

    python -m tests.test_advice_trace
"""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from advisor import versions as V
from tools import advice_trace as T
from tools import scene_eval as S


def _compact(active_species="scizor", moves=(("uturn", 20), ("bulletpunch", 30)), bench=(("garchomp", None, True),), hp=100.0,
             status=None, picked=True):
    party = [{"species": active_species, "ja": "A", "hp": hp, "status": status, "moves": [list(m) for m in moves], "picked": picked}]
    for sp, st, pk in bench:
        party.append({"species": sp, "ja": sp, "hp": 100.0, "status": st, "moves": [], "picked": pk})
    return {"scene": "command", "player": {"active": 0, "party": party}, "opponent": {"active": 0, "party": [{"species": "charizard"}]}}


def test_versions():
    doc = V.summarize_versions("pkg1", "/m/sel.pt", "abc123", "pkg1", ["a", "b", "c", "d", "e", "f"], ["a", "b", "c", "d", "e", "f"],
                               "/m/rl.zip", "def456", True, None, "teamsha", "gitsha", "dex", "eff", "v1")
    assert doc["selection_model"]["fallback_reason"] is None and doc["version_id"] == V.digest(doc)
    assert doc["selection_model"]["source"] == "package"
    # 登録チーム向けのモデル (registered:<鍵>) は source registered。Package のラベルがあればその退避理由は残る
    dr = V.summarize_versions("pkg1", "/m/reg.pt", "r1", "registered:abcd", ["a", "b", "c", "d", "e", "f"], ["a", "b", "c", "d", "e", "x"],
                              None, None, None, None, "t", "g", "d", "e", "v1")
    assert dr["selection_model"]["source"] == "registered" and dr["selection_model"]["fallback_reason"] == "party_not_in_package"
    assert V.summarize_versions(None, "/m/deployed.pt", "z", None, None, ["a"], None, None, None, None, "t", "g", "d", "e", "v1")["selection_model"]["source"] == "deployed"
    assert V.digest(dict(doc, collected_at=1.0)) == doc["version_id"]                       # 時刻は digest に入らない
    # ラベルはあるが登録パーティが Package の 6 体に含まれない → 配布版に退避
    d2 = V.summarize_versions("pkg1", "/m/deployed.pt", "zzz", None, ["a", "b", "c", "d", "e", "f"], ["a", "b", "c", "d", "e", "x"],
                              None, None, None, None, "t", "g", "d", "e", "v1")
    assert d2["selection_model"]["fallback_reason"] == "party_not_in_package" and d2["version_id"] != doc["version_id"]
    d3 = V.summarize_versions("pkg1", "/m/deployed.pt", "zzz", None, [], ["a"], None, None, None, None, "t", "g", "d", "e", "v1")
    assert d3["selection_model"]["fallback_reason"] == "package_model_missing"
    d4 = V.summarize_versions(None, "/m/deployed.pt", None, None, None, ["a"], None, None, False, "load_failed", "t", "g", "d", "e", "v1")
    assert d4["selection_model"]["fallback_reason"] == "selection_model_missing" and d4["action_policy"]["loaded"] is False
    assert V.sha256_text("x") == V.sha256_text("x") and V.sha256_file("/nonexistent/file") is None
    print("test_versions OK")


def test_logger_records():
    from battle_logger import BattleLogger, state_digest
    with tempfile.TemporaryDirectory() as td:
        lg = BattleLogger(log_dir=Path(td))
        st = {"scene": "command", "turn": 2, "field": {}, "player": {"active_index": 0, "party": [{"species_id": "scizor"}]},
              "opponent": {"active_index": 0, "party": []}}
        adv = {"ok": True, "best": {"kind": "move", "id": "uturn"}, "actions": [{"kind": "move", "id": "uturn", "score": 1}], "text": "x"}
        aid = lg.on_advice(adv, "battle", st)
        assert adv["advice_id"] == aid and adv["t_gen"] and aid.endswith("-0001")
        lg.on_display(aid, time.time() + 0.5, "battle")
        aid2 = lg.on_advice({"ok": True, "recommend": [{"name": "A"}], "primary": "rule", "model_pick": {"model": "deployed"}}, "selection", st)
        rows = [json.loads(l) for l in lg._file.read_text(encoding="utf-8").splitlines()]
        types = [r["type"] for r in rows]
        assert types[:2] == ["session", "version"] and types[2:] == ["advice", "display", "advice"], types
        a = rows[2]
        assert a["advice_id"] == aid and a["state_id"] == state_digest(a["state"]) and a["turn"] == 2 and "text" not in a["advice"]
        assert rows[3]["advice_id"] == aid and rows[3]["t_shown"] > a["advice"]["t_gen"]
        assert rows[4]["policy"]["selection"] == "deployed" and rows[4]["policy"]["primary"] == "rule" and aid2.endswith("-0002")
        assert rows[1].get("version_id") and a["version_id"] == rows[1]["version_id"]
        # 表示の行は助言を書いた対戦のファイルに帰属させる (2026-10-06: タブが隠れている間の表示が次の対戦のファイルに混ざっていた)。
        # 隠れていた旨 (hidden) も残す
        first = lg._file
        lg._open_new()
        assert lg._file != first
        lg.on_display(aid2, time.time() + 300, "selection", hidden=True)
        lg.on_display("unknown-9999", time.time(), "battle")                       # 知らない id は今のファイルへ
        rows_first = [json.loads(l) for l in first.read_text(encoding="utf-8").splitlines()]
        rows_now = [json.loads(l) for l in lg._file.read_text(encoding="utf-8").splitlines()]
        late = rows_first[-1]
        assert late["type"] == "display" and late["advice_id"] == aid2 and late["hidden"] is True and late["attributed"] == "advice_battle"
        assert [r["type"] for r in rows_now] == ["session", "version", "display"] and rows_now[-1]["advice_id"] == "unknown-9999"
    print("test_logger_records OK")


def _records(stale_turn=False, display=True, latency=0.8):
    t0 = 1000.0
    c1 = _compact()
    recs = [{"t": t0, "type": "session", "source": "experiment", "package_id": "pkg1"},
            {"t": t0, "type": "version", "version_id": "v1", "designated_package": "pkg1",
             "selection_model": {"package": "pkg1", "path": "/p", "sha256": "abcdef0123456789", "fallback_reason": None},
             "action_policy": {"loaded": True}, "team": {"species": ["a", "b"]}},
            {"t": t0 + 1, "type": "scene", "scene": "command", "turn": 1, "state": c1},
            {"t": t0 + 2, "type": "advice", "kind": "battle", "advice_id": "x-0001", "version_id": "v1", "state_id": "s1", "turn": 1,
             "state": c1, "advice": {"ok": True, "t_gen": t0 + 2, "best": {"kind": "move", "id": "uturn", "name": "とんぼ"},
                                     "actions": [{"kind": "move", "id": "uturn", "score": 50}]}},
            {"t": t0 + 2.1, "type": "advice", "kind": "battle", "advice_id": "x-0002", "version_id": "v1", "turn": 1, "state": c1,
             "advice": {"ok": True, "provisional": True, "t_gen": t0 + 2.1, "best": {"kind": "move", "id": "uturn"}}}]
    if stale_turn:
        recs.append({"t": t0 + 2.5, "type": "scene", "scene": "field", "turn": 2, "state": _compact(active_species="garchomp")})
    if display:
        recs.append({"t": t0 + 2 + latency, "type": "display", "advice_id": "x-0001", "t_shown": t0 + 2 + latency, "kind": "battle"})
    recs.append({"t": t0 + 9, "type": "events", "turn": 1, "fired": ["move_player_uturn"], "texts": []})
    return recs


def test_display_hidden():
    """タブが隠れていて描画されずに送られた表示 (hidden) は表示とは数えず、n_hidden に数える (2026-10-06)"""
    recs = _records(display=False)
    recs.append({"t": 1300.0, "type": "display", "advice_id": "x-0001", "t_shown": 1300.0, "kind": "battle", "hidden": True})
    rows = [r for r in T.display_rows(recs) if r["kind"] == "battle"]
    assert len(rows) == 1 and rows[0]["displayed"] is False and rows[0]["hidden"] is True and rows[0]["latency"] is None
    s = T.summarize(recs)["display"]
    assert s["n_displayed"] == 0 and s["n_hidden"] == 1 and s["n_late"] == 0
    # 同じ助言に後から本物の表示が来れば表示に数える
    recs.append({"t": 1301.0, "type": "display", "advice_id": "x-0001", "t_shown": 1301.0, "kind": "battle", "hidden": False})
    rows2 = [r for r in T.display_rows(recs) if r["kind"] == "battle"]
    assert rows2[0]["displayed"] is True and rows2[0]["hidden"] is False
    assert T.summarize(_records())["display"]["n_hidden"] == 0
    print("test_display_hidden OK")


def test_trace_functions():
    recs = _records()
    v = T.version_check(recs, "abcdef0123456789" + "0" * 48)
    assert v["match_package"] is True and v["version_id"] == "v1" and v["fallback_reason"] is None
    assert T.version_check(recs, "ffff")["match_package"] is False and T.version_check(recs, None)["match_package"] is None
    assert T.version_check([{"type": "session"}])["has_version"] is False
    rows = T.display_rows(recs)
    assert len(rows) == 1 and rows[0]["advice_id"] == "x-0001" and rows[0]["latency"] == 0.8 and rows[0]["stale"] is False
    rows2 = T.display_rows(_records(stale_turn=True, latency=1.0))
    assert rows2[0]["stale"] is True, rows2                        # 表示時点で局面が進んでいた
    rows3 = T.display_rows(_records(display=False))
    assert rows3[0]["displayed"] is False and rows3[0]["latency"] is None and rows3[0]["stale"] is None
    # 実行不能: システムの状態で選べるか
    c = _compact()
    assert T.feasible_in_state({"kind": "move", "id": "uturn"}, c) is True
    assert T.feasible_in_state({"kind": "move", "id": "flamethrower"}, c) is False
    assert T.feasible_in_state({"kind": "move", "id": "uturn"}, _compact(moves=(("uturn", None),))) is None       # PP が読めていない
    assert T.feasible_in_state({"kind": "move", "id": "uturn"}, _compact(moves=())) is None
    assert T.feasible_in_state({"kind": "move", "id": "uturn"}, _compact(status="fainted")) is False
    assert T.feasible_in_state({"kind": "switch", "id": "garchomp"}, c) is True
    assert T.feasible_in_state({"kind": "switch", "id": "garchomp"}, _compact(bench=(("garchomp", "fainted", True),))) is False
    assert T.feasible_in_state({"kind": "switch", "id": "garchomp"}, _compact(bench=(("garchomp", None, False),))) is False    # 未選出
    assert T.feasible_in_state({"kind": "switch", "id": "zzz"}, c) is None and T.feasible_in_state(None, c) is None
    f = T.feasibility_rows(recs)
    assert len(f) == 1 and f[0]["feasible_system"] is True and f[0]["feasible_truth"] is None
    s = T.summarize(recs, "abcdef0123456789")
    assert s["version"]["match_package"] is True and s["display"]["n_displayed"] == 1 and s["feasibility"]["infeasible_rate_system"] == 0.0
    ch = T.chain_rows(recs)
    assert [r["advice_id"] for r in ch] == ["x-0001", "x-0002"] and ch[0]["recommend"] == "move:uturn" and ch[0]["t_shown"] == 1002.8
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "battle_1.jsonl"
        p.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")
        agg = T.summarize_paths([p, Path(td) / "none.jsonl"], "abcdef0123456789")
        assert agg["n_battles"] == 1 and agg["version"]["n_match"] == 1 and agg["display"]["latency_p50"] == 0.8
        assert "版:" in T.format_chain("b1", recs, "abcdef0123456789")
    print("test_trace_functions OK")


def test_scene_eval():
    recs = _records()
    cands = S.extract_candidates("battle_1.jsonl", recs)
    assert len(cands) == 1 and cands[0]["category"] == "consistent" and cands[0]["labels_status"] == "unlabeled"
    # display の行が 1 つも無いログ (表示記録に未対応) → 判定不能 (判断 9、2026-10-07。旧: advice_stop)
    assert S.extract_candidates("b", _records(display=False))[0]["category"] == "display_unknown"
    assert S.extract_candidates("b", _records(stale_turn=True))[0]["category"] == "stale"
    assert S.extract_candidates("b", _records(latency=12.0))[0]["category"] == "late"
    fixed = _records() + [{"t": 1003.0, "type": "manual_fix", "turn": 1, "text": "hp"}]
    assert S.extract_candidates("b", fixed)[0]["category"] == "hp_stuck"
    # 選び方: 整合 n + 失敗 n (カテゴリを回す)、決定的
    pool = ([{"category": "consistent", "i": i} for i in range(30)] + [{"category": "hp_stuck", "i": i} for i in range(5)]
            + [{"category": "advice_stop", "i": i} for i in range(2)] + [{"category": "late", "i": i} for i in range(9)])
    picked = S.pick_scenes(pool, 20, 10, seed=1)
    cats = [p["category"] for p in picked]
    assert cats.count("consistent") == 20 and len(picked) == 30 and cats.count("advice_stop") == 2 and cats.count("hp_stuck") >= 3
    assert S.pick_scenes(pool, 20, 10, seed=1) == picked
    assert S.decision_windows(recs) == [{"t_open": 1001.0, "turn": 1}]
    assert S.compare_best({"kind": "move", "id": "a"}, {"kind": "move", "id": "a", "name": "x"}) is True and S.compare_best(None, {}) is None
    assert S.legal_by_label({"kind": "move", "id": "a"}, [{"kind": "move", "id": "b"}]) is False and S.legal_by_label({"kind": "move", "id": "a"}, None) is None
    assert S.displayed_in_time(10.0, 15.0, 10.0) is True and S.displayed_in_time(10.0, 25.0, 10.0) is False and S.displayed_in_time(None, 1, 1) is None
    # 評価: 偽のエンジン (復元した状態の場の個体で推奨を変える)
    def fake_engine(state, resolver=None):
        sp = state["player"]["party"][state["player"]["active_index"]]["species_id"]
        return {"ok": True, "best": {"kind": "move", "id": "uturn" if sp == "scizor" else "earthquake", "name": "x"}, "actions": []}
    scene = dict(cands[0], scene_id="s001")
    r = S.evaluate_scene(scene, engine=fake_engine, resolver=None, t_open=1001.0)
    assert r["advice_system"]["id"] == "uturn" and r["same_as_logged"] is True and r["advice_truth"] is None and r["truth_vs_system"] is None
    assert r["feasible_system"] is True and r["feasible_truth"] is None and r["displayed_in_time"] is None       # 期限のラベル無し
    scene2 = dict(scene, truth={"state": _compact(active_species="garchomp"), "legal_actions": [{"kind": "move", "id": "earthquake"}],
                                "deadline_s": 1.0, "notes": "ひんしを見逃していた"})
    r2 = S.evaluate_scene(scene2, engine=fake_engine, resolver=None, t_open=1001.0)
    assert r2["advice_truth"]["id"] == "earthquake" and r2["truth_vs_system"] is False
    assert r2["feasible_system"] is True and r2["feasible_truth"] is False                    # システムでは選べ、正解では選べない
    assert r2["displayed_in_time"] is False                                                    # 表示 1002.8 − 開いた 1001.0 > 1.0
    summ = S.summarize_eval([r, r2])
    assert summ["all"]["n"] == 2 and summ["all"]["infeasible_truth"] == 1 and summ["all"]["feasible_truth_unknown"] == 1
    assert "実戦全体の発生率ではない" in summ["note"] and summ["by_category"]["consistent"]["n"] == 2
    print("test_scene_eval OK")


def _later_advice(t, aid, shown_at=None, hidden=False):
    """x-0001 の後に生成された別の助言 (と、その表示の行)"""
    c1 = _compact()
    rows = [{"t": t, "type": "advice", "kind": "battle", "advice_id": aid, "version_id": "v1", "turn": 1, "state": c1,
             "advice": {"ok": True, "t_gen": t, "best": {"kind": "move", "id": "uturn"}, "actions": [{"kind": "move", "id": "uturn"}]}}]
    if shown_at is not None:
        rows.append({"t": shown_at + 0.05, "type": "display", "advice_id": aid, "t_shown": shown_at, "kind": "battle", "hidden": hidden})
    return rows


def test_scene_eval_display_regression():
    """scene_eval.classify の表示の判定 (判断 9) の回帰: 旧ログ (display の行なし) / 正常表示 / 通知欠落 の 3 系統。
    通知欠落は decision_audit と同じ規則で、表示経路の欠陥と確認できたときだけ advice_stop、それ以外は表示未確認"""
    def cat(recs):
        return {c["source"]["advice_id"]: c["category"] for c in S.extract_candidates("b", recs)}
    # 旧ログ: display の行が 1 つも無い → 全部 display_unknown (advice_stop にしない)
    old = _records(display=False) + _later_advice(1005.0, "x-0003")
    assert set(cat(old).values()) == {"display_unknown"}, cat(old)
    # 正常表示: 表示された助言は consistent (表示の遅れ・古い状態の判定は従来どおり)
    assert cat(_records())["x-0001"] == "consistent"
    # 通知欠落 (a): 後に生成された助言が 30 秒以内に見えるページで表示された → この助言は表示経路の欠陥と確認 → advice_stop
    lost = _records(display=False) + _later_advice(1005.0, "x-0003", shown_at=1005.2)
    c = cat(lost)
    assert c["x-0001"] == "advice_stop" and c["x-0003"] == "consistent", c
    # 通知欠落 (b): 後の表示が遠い (確認できない) → display_unconfirmed (失敗に数えない)
    far = _records(display=False) + _later_advice(1005.0, "x-0003", shown_at=1100.0)
    assert cat(far)["x-0001"] == "display_unconfirmed", cat(far)
    # 通知欠落 (c): 後の表示が隠れたタブ → 描画待ちで説明できるので確認できない → display_unconfirmed。隠れたタブの助言は display_hidden
    hid = _records(display=False) + _later_advice(1005.0, "x-0003", shown_at=1005.2, hidden=True)
    ch = cat(hid)
    assert ch["x-0001"] == "display_unconfirmed" and ch["x-0003"] == "display_hidden", ch
    # 判定不能の分類は失敗として選ばない (固定失敗集に入らない)
    assert "display_unconfirmed" not in S.FAILURE_CATEGORIES and "display_unknown" not in S.FAILURE_CATEGORIES
    assert all(k in S.CATEGORIES for k in ("display_unknown", "display_unconfirmed", "display_hidden"))
    print("test_scene_eval_display_regression OK")


def test_decision_and_hp_stale_summary():
    """2026-10-07 段 0: decision 行 (表示から決定まで・第一候補との一致) と hp_stale 欄の要約。無い古いログは 0 件"""
    recs = [{"type": "advice", "kind": "battle", "advice_id": "a1", "advice": {"ok": True}, "hp_stale": {"player": 2.0, "opponent": None}},
            {"type": "advice", "kind": "battle", "advice_id": "a2", "advice": {"ok": True}, "hp_stale": {"player": 6.0, "opponent": 1.0}},
            {"type": "advice", "kind": "battle", "advice_id": "a3", "advice": {"provisional": True}, "hp_stale": {"player": 99.0}},
            {"type": "display", "advice_id": "a1", "t_shown": 100.0},
            {"type": "decision", "advice_id": "a1", "t_shown": None, "t_decided": 104.5, "match": True},
            {"type": "decision", "advice_id": "a2", "t_shown": 200.0, "t_decided": 201.0, "match": None}]
    rows = T.decision_rows(recs)
    assert [r["shown_to_decided"] for r in rows] == [4.5, 1.0]          # 行に表示時刻が無ければ display の行から
    s = T.summarize(recs)
    assert s["decision"] == {"n": 2, "n_match": 1, "n_mismatch": 0, "n_unknown": 1, "shown_to_decided_p50": 4.5}
    assert s["hp_stale"]["n"] == 2 and s["hp_stale"]["player_max"] == 6.0 and s["hp_stale"]["opponent_unread"] == 1
    old = T.summarize(_records())
    assert old["decision"]["n"] == 0 and old["hp_stale"]["n"] == 0
    assert "決定: 2" in T.format_chain("x", recs)
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "b.jsonl"
        p.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")
        agg = T.summarize_paths([p])
        assert agg["decision"]["n"] == 2 and agg["hp_stale"]["n"] == 2
    print("test_decision_and_hp_stale_summary OK")


def test_selection_record_join():
    """選出の候補・相手の選出の予測は selection_record 行にあり、advice_id で選出の advice 行に結ぶ (行が無い古いログは結べない)"""
    recs = [{"type": "advice", "kind": "selection", "advice_id": "s1", "advice": {"ok": True}},
            {"type": "advice", "kind": "selection", "advice_id": "s2", "advice": {"ok": True}},
            {"type": "advice", "kind": "selection", "advice_id": "s3", "advice": {"ok": False}},
            {"type": "selection_record", "advice_id": "s2",
             "candidates": {"rule": {"names": ["A"]}, "registered": None, "reasons": {}, "primary": "rule"},
             "opp_pick_pred": {"combos": [{"p": 1.0}] * 20, "incomplete": False}},
            {"type": "selection_record", "advice_id": "zz", "candidates": {}}]
    rows = T.selection_rows(recs)
    assert [(r["advice_id"], r["has_record"]) for r in rows] == [("s1", False), ("s2", True)]
    assert rows[1]["n_combos"] == 20 and rows[1]["methods"] == ["rule"]
    assert T.summarize(recs)["selection"] == {"n_advice": 2, "n_record": 1, "n_full_distribution": 1}
    assert "selection_record と結べた 1" in T.format_chain("x", recs)
    assert T.summarize(_records())["selection"]["n_record"] == 0
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "b.jsonl"
        p.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")
        assert T.summarize_paths([p])["selection"]["n_record"] == 1
    print("test_selection_record_join OK")


def main() -> None:
    test_display_hidden()
    test_versions()
    test_logger_records()
    test_trace_functions()
    test_scene_eval()
    test_scene_eval_display_regression()
    test_decision_and_hp_stale_summary()
    test_selection_record_join()
    print("ALL OK")


if __name__ == "__main__":
    main()
