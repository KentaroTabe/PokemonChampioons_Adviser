"""実機の受入の道具 (tools/real_accept) の純粋な部分と、記録の欄 (battle_logger の scoring_mode / fallback_reason)、
操作パネルの方式の表示 (tools/control_panel)、録画再生の方式比較の並び (tools/shadow_load_check) のテスト (2026-10-16)。

    python -m tests.test_real_accept
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from tools import real_accept as RA
from vision.scenes import SCENE_BATTLE_HUD, SCENE_COMMAND, SCENE_FIELD, SCENE_MOVE_SELECT


def _mon(sp, moves=(), hp=100.0, status=None, picked=True, mega=False, item=None):
    return {"species": sp, "ja": sp, "hp": hp, "status": status, "moves": [list(m) for m in moves], "picked": picked,
            "mega": mega, "item": item}


def _compact(active=0, party=None, scene=SCENE_COMMAND):
    party = party if party is not None else [
        _mon("indeedee", (("expandingforce", 16), ("encore", 8), ("substitute", 16), ("mysticalfire", 16))),
        _mon("blastoise", item="blastoisinite"), _mon("sneasler"), _mon("garchomp", picked=False)]
    return {"scene": scene, "player": {"active": active, "party": party}, "opponent": {"active": 0, "party": [_mon("dragonite")]}}


def _adv(kind, ident, actions=None, my_sp="indeedee", idx=0, ok=True):
    best = {"kind": kind, "id": ident}
    return {"ok": ok, "best": best, "actions": actions if actions is not None else [best],
            "position_key": [["my", idx, my_sp, False], ["opp", 0, "dragonite", False]]}


def _is_mega(sid):
    return str(sid).endswith("mega")


def _base(sid):
    s = str(sid)
    return s[:-4] if s.endswith("mega") else s


CK = {"is_mega": _is_mega, "base_of": _base, "is_status_move": lambda m: m in ("encore", "substitute", "swordsdance"),
      "choice_items": ("choicescarf", "choiceband", "choicespecs")}


def test_thin_and_rows():
    rows = [{"t": t} for t in (0.0, 3.0, 8.0, 9.0, 20.0)]
    assert [r["t"] for r in RA.thin_by_gap(rows, 8.0)] == [0.0, 8.0, 20.0]
    recs = [
        {"type": "advice", "kind": "battle", "advice": {"ok": True}, "state": _compact(), "t": 1},
        {"type": "advice", "kind": "battle", "advice": {"ok": True, "provisional": True}, "state": _compact(), "t": 2},
        {"type": "advice", "kind": "battle", "advice": {"ok": False}, "state": _compact(), "t": 3},
        {"type": "advice", "kind": "selection", "advice": {"ok": True}, "state": _compact(), "t": 4},
        {"type": "advice", "kind": "battle", "advice": {"ok": True}, "state": {"player": {"party": []}}, "t": 5},
        {"type": "scene", "scene": SCENE_COMMAND, "t": 6},
    ]
    assert [r["t"] for r in RA.battle_advice_rows(recs)] == [1]
    tl = RA.scene_timeline([{"type": "scene", "scene": SCENE_FIELD, "t": 5.0}, {"type": "scene", "scene": SCENE_COMMAND, "t": 2.0}])
    assert tl == [(2.0, SCENE_COMMAND), (5.0, SCENE_FIELD)]
    assert RA.scene_before(tl, 1.0) is None and RA.scene_before(tl, 3.0) == SCENE_COMMAND and RA.scene_before(tl, 5.0) == SCENE_FIELD
    print("test_thin_and_rows OK")


def test_decision_open_time():
    from vision.scenes import SCENE_WATCH
    tl = [(1.0, SCENE_FIELD), (2.0, SCENE_MOVE_SELECT), (2.5, SCENE_BATTLE_HUD),           # 2.0 = 演出中の誤分類 (一瞬)
          (10.0, SCENE_FIELD), (20.0, SCENE_COMMAND), (21.0, SCENE_WATCH), (22.0, SCENE_MOVE_SELECT),
          (23.0, SCENE_BATTLE_HUD)]
    assert RA.decision_open_time(tl, 0.0, 23.0) == 20.0          # 直前の決定画面の続きの最初 (誤分類の 2.0 ではない)
    assert RA.decision_open_time(tl, 0.0, 22.99) == 20.0         # 解決の scene 行が t_decided より少し前でも同じ
    assert RA.decision_open_time(tl, 0.0, 9.0) == 2.0
    assert RA.decision_open_time(tl, 2.5, 19.0) is None
    print("test_decision_open_time OK")


def test_stale_reasons():
    assert RA.stale_reasons({"hp_stale": {"player": 1.0, "opponent": 2.0}}, SCENE_COMMAND, 30.0) == []
    assert RA.stale_reasons({"hp_stale": {"player": 31.0, "opponent": None}}, SCENE_COMMAND, 30.0) == ["hp_stale"]
    assert RA.stale_reasons({"hp_stale": {"player": None, "opponent": None}}, SCENE_BATTLE_HUD, 30.0) == ["resolve_scene"]
    assert RA.stale_reasons({}, None, 30.0) == []
    print("test_stale_reasons OK")


def test_check_best_moves():
    c = _compact()
    ok = RA.check_best(_adv("move", "expandingforce"), c, **CK)
    assert ok == {"violations": [], "unconfirmed": []}, ok
    assert RA.check_best(_adv("move", "hydropump"), c, **CK)["violations"] == ["move_not_owned"]
    # PP 0
    c0 = _compact(party=[_mon("indeedee", (("expandingforce", 0), ("encore", 8), ("substitute", 1), ("mysticalfire", 1))),
                         _mon("blastoise")])
    assert RA.check_best(_adv("move", "expandingforce"), c0, **CK)["violations"] == ["move_pp0"]
    # 技が 4 つ未満なら登録で補う (補った技は未確認として数え、違反にしない)
    c2 = _compact(party=[_mon("indeedee", (("expandingforce", 16),)), _mon("blastoise")])
    r = RA.check_best(_adv("move", "encore"), c2, registered=["expandingforce", "encore"], **CK)
    assert r == {"violations": [], "unconfirmed": ["moves_from_registration"]}, r
    assert RA.check_best(_adv("move", "hydropump"), c2, registered=["encore"], **CK)["violations"] == ["move_not_owned"]
    c3 = _compact(party=[_mon("indeedee"), _mon("blastoise")])
    assert RA.check_best(_adv("move", "encore"), c3, **CK)["unconfirmed"] == ["moves_unknown"]
    # ひんしの場の個体に技
    cf = _compact(party=[_mon("indeedee", (("expandingforce", 16),), hp=0.0, status="fainted"), _mon("blastoise")])
    assert "move_while_fainted" in RA.check_best(_adv("move", "expandingforce"), cf, **CK)["violations"]
    # アンコール / こだわり / ちょうはつ / かなしばり (move_locks)
    enc = {"player": {"volatiles": ["encore"], "encore_move": "encore"}, "turn": 3, "last_move": {"player": "encore"}}
    assert RA.check_best(_adv("move", "expandingforce"), c, enc, **CK)["violations"] == ["move_locked"]
    assert RA.check_best(_adv("move", "encore"), c, enc, **CK)["violations"] == []
    enc_unknown = {"player": {"volatiles": ["encore"]}, "turn": 3, "last_move": {}}
    assert RA.check_best(_adv("move", "expandingforce"), c, enc_unknown, **CK)["unconfirmed"] == ["lock_unknown"]
    cs = _compact(party=[_mon("indeedee", (("expandingforce", 16), ("mysticalfire", 16)), item="choicespecs"), _mon("blastoise")])
    ch = {"turn": 3, "last_move": {"player": "mysticalfire"}}
    assert RA.check_best(_adv("move", "expandingforce"), cs, ch, **CK)["violations"] == ["move_locked"]
    assert RA.check_best(_adv("move", "expandingforce"), c, ch, **CK)["violations"] == []        # こだわりでなければ固定なし
    tau = {"player": {"volatiles": ["taunt", "disable_mysticalfire"]}, "turn": 3, "last_move": {}}
    assert RA.check_best(_adv("move", "encore"), c, tau, **CK)["violations"] == ["move_taunted"]
    assert RA.check_best(_adv("move", "mysticalfire"), c, tau, **CK)["violations"] == ["move_disabled"]
    assert RA.check_best(_adv("move", "expandingforce"), c, tau, **CK)["violations"] == []
    print("test_check_best_moves OK")


def test_check_best_switch_and_identity():
    c = _compact()
    assert RA.check_best(_adv("switch", "blastoise"), c, **CK)["violations"] == []
    assert RA.check_best(_adv("switch", "indeedee"), c, **CK)["violations"] == ["switch_self"]
    assert RA.check_best(_adv("switch", "garchomp"), c, **CK)["violations"] == ["switch_unpicked", "candidate_unpicked"]
    assert RA.check_best(_adv("switch", "pikachu"), c, **CK)["violations"] == ["switch_missing"]
    assert RA.check_best(_adv("switch", "blastoisemega"), c, **CK)["violations"] == ["switch_form"]
    cf = _compact(party=[_mon("indeedee", (("expandingforce", 16),)), _mon("blastoise", hp=0.0, status="fainted")])
    assert RA.check_best(_adv("switch", "blastoise"), cf, **CK)["violations"] == ["switch_fainted"]
    # 第一候補は合法でも、候補に未選出への交代があれば違反
    acts = [{"kind": "move", "id": "expandingforce"}, {"kind": "switch", "id": "garchomp"}]
    assert RA.check_best(_adv("move", "expandingforce", actions=acts), c, **CK)["violations"] == ["candidate_unpicked"]
    # 選出が分からなければ未選出の照合なし
    cu = _compact(party=[_mon("indeedee", (("expandingforce", 16),), picked=False), _mon("garchomp", picked=False)])
    r = RA.check_best(_adv("switch", "garchomp"), cu, **CK)
    assert r == {"violations": [], "unconfirmed": ["selection_unknown"]}, r
    # 助言の場の個体: 別の個体 / メガの表記
    assert RA.check_best(_adv("move", "expandingforce", my_sp="blastoise"), c, **CK)["violations"] == ["active_mismatch"]
    assert RA.check_best(_adv("move", "expandingforce", idx=1), c, **CK)["violations"] == ["active_mismatch"]
    cm = _compact(party=[_mon("blastoise", (("hydropump", 8),), mega=True, item="blastoisinite"), _mon("indeedee")])
    assert RA.check_best(_adv("move", "hydropump", my_sp="blastoise"), cm, **CK)["violations"] == ["active_form"]
    assert RA.check_best(_adv("move", "hydropump", my_sp="blastoisemega"), cm, **CK)["violations"] == []
    cn = _compact(party=[_mon("blastoise", (("hydropump", 8),)), _mon("indeedee")])
    assert RA.check_best(_adv("move", "hydropump", my_sp="blastoisemega"), cn, **CK)["violations"] == ["active_form"]
    # position_key がタプル (evaluate の戻り値そのもの) でも同じ
    a = _adv("move", "expandingforce")
    a["position_key"] = (("my", 0, "indeedee", False),)
    assert RA.check_best(a, c, **CK) == {"violations": [], "unconfirmed": []}
    a["position_key"] = None
    assert RA.check_best(a, c, **CK)["unconfirmed"] == ["no_position_key"]
    assert RA.check_best({"ok": False, "reason": "x"}, c, **CK) == {"violations": [], "unconfirmed": ["no_best"]}
    print("test_check_best_switch_and_identity OK")


def _adv_rec(aid, t, best="expandingforce", hidden_stale=False, provisional=False, mode=None):
    a = {"ok": True, "best": {"kind": "move", "id": best}, "actions": [{"kind": "move", "id": best}], "t_gen": t}
    if provisional:
        a["provisional"] = True
    if mode:
        a["scoring_mode"] = mode
    return {"type": "advice", "kind": "battle", "advice_id": aid, "t": t, "advice": a, "state": _compact(),
            "hp_stale": {"player": 99.0 if hidden_stale else 1.0, "opponent": 1.0}}


def test_display_rows():
    recs = [
        # 決定 1: 決定画面 10.0 → 確定の助言 10.5 (表示 10.6) → 解決 12.0。遅延 0.6 秒
        {"type": "scene", "scene": SCENE_COMMAND, "t": 10.0},
        _adv_rec("a-1", 10.2, provisional=True),
        {"type": "display", "advice_id": "a-1", "t_shown": 10.25, "t": 10.25},
        _adv_rec("a-2", 10.5),
        {"type": "display", "advice_id": "a-2", "t_shown": 10.6, "t": 10.6},
        {"type": "scene", "scene": SCENE_FIELD, "t": 12.0},
        {"type": "decision", "advice_id": "a-2", "turn": 1, "t_gen": 10.5, "t_shown": 10.6, "t_decided": 12.0},
        # 決定 2: 決定画面 20.0、助言の生成なし (前の助言が残っていただけ)
        {"type": "scene", "scene": SCENE_MOVE_SELECT, "t": 20.0},
        {"type": "scene", "scene": SCENE_FIELD, "t": 21.0},
        {"type": "decision", "advice_id": "a-2", "turn": 2, "t_gen": 10.5, "t_shown": 10.6, "t_decided": 21.0},
        # 決定 3: 隠れたページで表示
        {"type": "scene", "scene": SCENE_COMMAND, "t": 30.0},
        _adv_rec("a-3", 30.5),
        {"type": "display", "advice_id": "a-3", "t_shown": 30.6, "t": 30.6, "hidden": True},
        {"type": "scene", "scene": SCENE_FIELD, "t": 31.0},
        {"type": "decision", "advice_id": "a-3", "turn": 3, "t_gen": 30.5, "t_shown": None, "t_decided": 31.0},
        # 決定 4: 古い局面の助言しかない
        {"type": "scene", "scene": SCENE_COMMAND, "t": 40.0},
        _adv_rec("a-4", 40.5, hidden_stale=True),
        {"type": "display", "advice_id": "a-4", "t_shown": 40.6, "t": 40.6},
        {"type": "scene", "scene": SCENE_FIELD, "t": 41.0},
        {"type": "decision", "advice_id": "a-4", "turn": 4, "t_gen": 40.5, "t_shown": 40.6, "t_decided": 41.0},
        # 決定 5: 決定画面の scene 行が無い (判定不能)
        _adv_rec("a-5", 50.5, mode="transition"),
        {"type": "display", "advice_id": "a-5", "t_shown": 50.6, "t": 50.6},
        {"type": "decision", "advice_id": "a-5", "turn": 5, "t_gen": 50.5, "t_shown": 50.6, "t_decided": 51.0},
        # 決定 6: 表示が遅い (期限 10 秒超え)。前の解決後の演出中の誤分類 (55.0 move_select) は決定開始にしない
        {"type": "scene", "scene": SCENE_MOVE_SELECT, "t": 55.0},
        {"type": "scene", "scene": SCENE_BATTLE_HUD, "t": 55.5},
        {"type": "scene", "scene": SCENE_COMMAND, "t": 60.0},
        _adv_rec("a-6", 60.5),
        {"type": "display", "advice_id": "a-6", "t_shown": 71.0, "t": 71.0},
        {"type": "scene", "scene": SCENE_FIELD, "t": 72.0},
        {"type": "decision", "advice_id": "a-6", "turn": 6, "t_gen": 60.5, "t_shown": 71.0, "t_decided": 72.0},
    ]
    rows = RA.display_rows(recs, deadline_sec=10.0, stale_hp_sec=30.0)
    assert [r["status"] for r in rows] == ["shown", "no_advice", "hidden", "stale_only", "no_open", "shown"], rows
    assert rows[0]["latency"] == 0.6 and rows[0]["in_time"] is True and rows[0]["gen_to_shown"] == 0.1
    assert rows[1]["latency"] is None and rows[1]["in_time"] is False          # 前の助言が残っていただけ = 遅延 0 にしない
    assert rows[5]["latency"] == 11.0 and rows[5]["in_time"] is False
    assert rows[0]["mode"] == "legacy" and rows[0]["mode_recorded"] is False and rows[4]["mode"] == "transition"
    s = RA.summarize_display(rows)
    lg = s["legacy"]
    assert lg["n"] == 5 and lg["n_front"] == 4 and lg["n_in_time"] == 1 and lg["in_time_rate"] == 0.25, lg
    assert lg["status"]["hidden"] == 1 and lg["latency_ms"]["n"] == 2 and lg["n_mode_unrecorded"] == 5
    assert s["transition"]["status"]["no_open"] == 1 and s["transition"]["in_time_rate"] == 0.0
    print("test_display_rows OK")


def _dec(stale=(), legacy=None, transition=None, log_best=None, log_mode="legacy"):
    return {"log": "battle_x.jsonl", "advice_id": "x-1", "turn": 1, "active": "indeedee", "stale": list(stale),
            "log_best": log_best, "log_mode": log_mode, "modes": {"legacy": legacy, "transition": transition}}


def _res(best="expandingforce", viol=(), unconf=(), ms=100.0, fb=None, kind="move"):
    return {"ok": True, "best": {"kind": kind, "id": best}, "violations": list(viol), "unconfirmed": list(unconf),
            "elapsed_ms": ms, "fallback_reason": fb, "scoring_used": "legacy" if fb else "transition"}


def test_summarize_and_acceptance():
    ds = [
        _dec(legacy=_res(ms=100.0), transition=_res(ms=200.0), log_best={"kind": "move", "id": "expandingforce"}),
        _dec(legacy=_res(ms=110.0), transition=_res("encore", ms=250.0, fb="budget")),
        _dec(legacy=_res(ms=90.0), transition=_res(ms=300.0, fb="opp_unsupported:protect:kingsshield")),
        _dec(legacy=_res("garchomp", viol=["switch_unpicked", "candidate_unpicked"], kind="switch", ms=95.0),
             transition=_res(ms=180.0), log_best={"kind": "move", "id": "encore"}),
        _dec(stale=["hp_stale"], legacy=_res(viol=["move_not_owned"], ms=120.0), transition=_res(ms=210.0)),
        _dec(legacy=_res(ms=100.0), transition={"ok": False, "error": "ValueError: x", "elapsed_ms": 5.0, "best": None,
                                                 "violations": [], "unconfirmed": []}),
    ]
    s = RA.summarize(ds)
    assert s["n"] == 6 and s["n_valid"] == 5 and s["n_stale"] == 1 and s["stale_reasons"]["hp_stale"] == 1
    lv = s["modes"]["legacy"]["valid"]
    assert lv["n_eval"] == 5 and lv["n_violations"] == 2 and lv["n_legality"] == 2 and lv["n_identity"] == 0
    assert lv["n_violation_decisions"] == 1 and lv["examples"]["switch_unpicked"][0]["best"]["id"] == "garchomp"
    assert s["modes"]["legacy"]["stale"]["violations"]["move_not_owned"] == 1
    assert s["modes"]["transition"]["valid"]["n_error"] == 1 and s["modes"]["transition"]["valid"]["n_eval"] == 4
    ag = s["agreement"]
    assert ag["n"] == 4 and ag["n_agree"] == 2 and ag["rate"] == 0.5, ag
    fb = s["fallback"]
    assert fb["n"] == 4 and fb["n_fallback"] == 2 and fb["n_budget"] == 1 and fb["budget_rate"] == 0.25 and fb["rate"] == 0.5
    assert fb["by_reason"] == {"budget": 1, "opp_unsupported:protect:kingsshield": 1}, fb
    assert s["modes"]["legacy"]["elapsed_ms"]["n"] == 6 and s["elapsed_diff_ms"]["n"] == 6
    assert s["log_best_match"] == {"n": 2, "n_match": 1, "rate": 0.5}

    disp = {"legacy": {"n": 10, "n_front": 9, "n_mode_unrecorded": 10,
                       "status": {k: 0 for k in RA.DISPLAY_STATUS_LABELS} | {"shown": 9, "hidden": 1},
                       "latency_ms": {"n": 9, "p50": 500.0, "p95": 2500.0, "max": 2600.0},
                       "gen_to_shown_ms": {"n": 9, "p50": 5.0, "p95": 10.0, "max": 12.0}, "n_in_time": 9, "in_time_rate": 1.0}}
    acc = RA.acceptance(s, disp, {"n_logs": 2, "n_logs_ok": 1})
    by = {(r["item"].split(":")[0], r["mode"]): r for r in acc}
    rows = {r["item"] + "|" + r["mode"]: r["status"] for r in acc}
    assert rows["正しさ: 違反 (有効な決定、再生)|legacy"] == RA.NG
    assert rows["正しさ: 違反 (有効な決定、再生)|transition"] == RA.OK
    assert rows["退避: 予算超過 (transition、再生)|transition"] == RA.NG           # 0.25 > 0.03
    assert rows["退避: 全理由 (transition、再生)|transition"] == RA.NG             # 0.5 > 0.20
    assert rows["遅延: 決定開始 → 有効な助言の表示の p95 (実戦ログ)|legacy"] == RA.OK
    assert rows["遅延: legacy より増える分 (実戦ログの p95 の差)|transition"] == RA.UNKNOWN
    assert any(k.startswith("表示:") and v == RA.OK for k, v in rows.items())
    assert any(k.startswith("負荷:") and v == RA.UNKNOWN for k, v in rows.items())
    assert any(k.startswith("記録:") and v == RA.NG for k, v in rows.items())
    assert by[("遅延", "transition")]["note"].startswith("実戦ログに両方式の決定が無い")
    # 両方式の実戦ログがあれば増分を判定する
    disp2 = dict(disp, transition=dict(disp["legacy"], latency_ms={"n": 9, "p50": 600.0, "p95": 3200.0, "max": 3300.0}))
    rows2 = {r["item"] + "|" + r["mode"]: r for r in RA.acceptance(s, disp2, {"n_logs": 1, "n_logs_ok": 1})}
    d = rows2["遅延: legacy より増える分 (実戦ログの p95 の差)|transition"]
    assert d["status"] == RA.NG and d["value"] == 0.7, d
    assert rows2["遅延: 決定開始 → 有効な助言の表示の p95 (実戦ログ)|transition"]["status"] == RA.NG
    # 評価できた決定が無ければ判定不能
    empty = RA.summarize([])
    rows3 = {r["item"] + "|" + r["mode"]: r["status"] for r in RA.acceptance(empty, {}, {"n_logs": 0, "n_logs_ok": 0})}
    assert rows3["正しさ: 違反 (有効な決定、再生)|legacy"] == RA.UNKNOWN
    assert rows3["退避: 全理由 (transition、再生)|transition"] == RA.UNKNOWN
    # Markdown が組める
    result = {"conditions": {"at": "2026-10-16 12:00", "code": {"commit": "abc", "dirty": False, "engine": "/x/engine.py"},
                             "modes": list(RA.MODES), "rl_blend_weight": 25.0, "rl_style": "balance", "transition_budget_ms": 300.0,
                             "transition_sensitivity": True, "gap_sec": 8.0, "stale_hp_sec": 30.0, "n_advice_rows": 10,
                             "logs": [{"name": "battle_x.jsonl", "n_advice_rows": 10, "n_decisions": 6, "n_decision_rows": 5,
                                       "modes_used": {"legacy": 10}, "has_move_locks": True}]},
              "summary": s, "display": disp, "acceptance": acc}
    md = RA.render_markdown(result)
    assert "## 受入条件との照合" in md and "未選出の個体への交代 (第一候補)" in md and "garchomp" in md and "budget" in md
    print("test_summarize_and_acceptance OK")


def test_record_check():
    good = [{"type": "version", "scoring_mode": "legacy"},
            {"type": "advice", "kind": "battle", "scoring_mode": "legacy", "scoring_used": "legacy", "fallback_reason": None},
            {"type": "advice", "kind": "selection"}]
    old = [{"type": "version"}, {"type": "advice", "kind": "battle", "advice": {"scoring_mode": "legacy"}}]
    assert RA.record_check_of([{"name": "a", "records": good}, {"name": "b", "records": old}]) == {"n_logs": 2, "n_logs_ok": 1}
    assert RA.advice_mode(good[1]) == ("legacy", True)
    assert RA.advice_mode(old[1]) == ("legacy", True)
    assert RA.advice_mode({"advice": {}}) == ("legacy", False)
    assert RA.advice_mode({"scoring_mode": "transition", "advice": {"scoring_mode": "legacy"}}) == ("transition", True)
    print("test_record_check OK")


def test_logger_scoring_fields():
    """battle_logger: version 行の scoring_mode、battle の advice 行の scoring_mode / scoring_used / fallback_reason (必ず書く)"""
    import battle_logger as BL
    assert BL.scoring_fields_of({"ok": True, "scoring_mode": "legacy"}, "legacy") == \
        {"scoring_mode": "legacy", "scoring_used": "legacy", "fallback_reason": None}
    assert BL.scoring_fields_of({"ok": True, "scoring_mode": "transition", "scoring_used": "legacy", "scoring_fallback": "budget"},
                                "transition") == {"scoring_mode": "transition", "scoring_used": "legacy", "fallback_reason": "budget"}
    assert BL.scoring_fields_of({"ok": False, "reason": "x"}, "transition") == \
        {"scoring_mode": "transition", "scoring_used": None, "fallback_reason": None}
    assert BL.scoring_mode_banner({"scoring_mode": "transition", "scoring_mode_env": "transition"}) == \
        "採点方式: transition (環境変数 ADVICE_SCORING_MODE=transition)"
    assert "既定" in BL.scoring_mode_banner({"scoring_mode": "legacy", "scoring_mode_env": None})
    info = BL.scoring_mode_info()
    assert set(info) == {"scoring_mode", "scoring_mode_env"} and info["scoring_mode"] in RA.MODES
    with tempfile.TemporaryDirectory() as td:
        lg = BL.BattleLogger(log_dir=Path(td))
        st = {"scene": SCENE_COMMAND, "turn": 2, "field": {}, "player": {"active_index": 0, "party": [{"species_id": "scizor"}]},
              "opponent": {"active_index": 0, "party": []}}
        lg.on_advice({"ok": True, "scoring_mode": "transition", "scoring_used": "legacy", "scoring_fallback": "budget",
                      "best": {"kind": "move", "id": "uturn"}, "actions": []}, "battle", st)
        lg.on_advice({"ok": True, "scoring_mode": "legacy", "best": {"kind": "move", "id": "uturn"}, "actions": []}, "battle", st)
        lg.on_advice({"ok": True, "recommend": []}, "selection", st)
        rows = [json.loads(x) for x in lg._file.read_text(encoding="utf-8").splitlines()]
        ver = [r for r in rows if r["type"] == "version"][0]
        assert ver["scoring_mode"] == info["scoring_mode"] and "scoring_mode_env" in ver and ver.get("version_id")
        advs = [r for r in rows if r["type"] == "advice"]
        assert {k: advs[0][k] for k in ("scoring_mode", "scoring_used", "fallback_reason")} == \
            {"scoring_mode": "transition", "scoring_used": "legacy", "fallback_reason": "budget"}
        assert {k: advs[1][k] for k in ("scoring_mode", "scoring_used", "fallback_reason")} == \
            {"scoring_mode": "legacy", "scoring_used": "legacy", "fallback_reason": None}
        assert "scoring_mode" not in advs[2] and "fallback_reason" not in advs[2]          # 選出の助言には書かない
        assert RA.record_check_of([{"name": "t", "records": rows}]) == {"n_logs": 1, "n_logs_ok": 1}
    print("test_logger_scoring_fields OK")


def test_control_panel_scoring():
    from tools import control_panel as CP
    f = CP.scoring_mode_from_ps
    assert f("") is None
    assert f("python uvicorn server:app_asgi PATH=/bin HOME=/x") == {"mode": "legacy", "env": None, "source": "default"}
    assert f("python uvicorn server:app_asgi ADVICE_SCORING_MODE=transition PATH=/bin") == \
        {"mode": "transition", "env": "transition", "source": "env"}
    assert f("python uvicorn ADVICE_SCORING_MODE=Legacy") == {"mode": "legacy", "env": "Legacy", "source": "env"}
    assert f("python uvicorn ADVICE_SCORING_MODE=fast") == {"mode": "legacy", "env": "fast", "source": "invalid"}
    assert f("python uvicorn X_ADVICE_SCORING_MODE=transition")["source"] == "default"     # 別の変数名に一致しない

    class Probe:
        def listener(self, port):
            return {"pid": 11, "command": "python uvicorn server:app_asgi --port 8000"} if port == 8000 else None

        def pgrep(self, pattern):
            return []

        def lan_ip(self):
            return "localhost"

        def process_env(self, pid):
            assert pid == 11
            return "python uvicorn server:app_asgi ADVICE_SCORING_MODE=transition"

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "logs").mkdir()
        s = CP.Settings(port=0, bind="127.0.0.1", poll_sec=1.0, tail_lines=10, path_prefix="", server_log_tail_bytes=1024,
                        advisor_port=8000, frontend_port=3000, showdown_port=8100, smoke_battles=20)
        st = CP.Panel(root, s, probe=Probe()).status()
        assert st["scoring"] == {"mode": "transition", "env": "transition", "source": "env"}, st["scoring"]

        class Stopped(Probe):
            def listener(self, port):
                return None
        assert CP.Panel(root, s, probe=Stopped()).status()["scoring"] is None
    assert "採点方式 (アドバイザー)" in CP.render_page(s, CP.ACTIONS)
    print("test_control_panel_scoring OK")


def test_shadow_load_check_compare():
    from tools.shadow_load_check import abba_order, run_summary
    assert abba_order("legacy", "transition", 2) == ["legacy", "transition", "transition", "legacy"] * 2
    assert abba_order("a", "b", 0) == ["a", "b", "b", "a"]
    s = run_summary([100.0], [10.0], [5.0, 6.0, 7.0], 4, 1, 3.0)
    assert s["proc_rate"] == 0.75 and run_summary([], [], [], 0, 0, 3.0)["proc_rate"] is None
    print("test_shadow_load_check_compare OK")


# ------------------------------------------------------------------ 2026-10-16 レビューの 3 点と遅延の判定規則
def test_display_after_decision():
    """レビュワーの例: 開始 10 / 決定 12 / 表示 13 は遅延 3 秒・期限内にしない (決定後の表示として別に数え、分母には残す)"""
    recs = [
        {"type": "scene", "scene": SCENE_COMMAND, "t": 10.0},
        _adv_rec("b-1", 10.5),
        {"type": "scene", "scene": SCENE_FIELD, "t": 12.0},
        {"type": "decision", "advice_id": "b-1", "turn": 1, "t_gen": 10.5, "t_shown": 13.0, "t_decided": 12.0},
        {"type": "display", "advice_id": "b-1", "t_shown": 13.0, "t": 13.0},
        # 期限 (1 秒) を過ぎたが決定より前の表示: 遅延は数えるが期限内ではない
        {"type": "scene", "scene": SCENE_COMMAND, "t": 20.0},
        _adv_rec("b-2", 20.2),
        {"type": "display", "advice_id": "b-2", "t_shown": 21.5, "t": 21.5},
        {"type": "scene", "scene": SCENE_FIELD, "t": 22.0},
        {"type": "decision", "advice_id": "b-2", "turn": 2, "t_gen": 20.2, "t_shown": 21.5, "t_decided": 22.0},
        # 決定の時刻ちょうどの表示は決定前に数える。期限 (開始 + 1 秒) 内
        {"type": "scene", "scene": SCENE_COMMAND, "t": 30.0},
        _adv_rec("b-3", 30.2),
        {"type": "display", "advice_id": "b-3", "t_shown": 30.8, "t": 30.8},
        {"type": "scene", "scene": SCENE_FIELD, "t": 30.8},
        {"type": "decision", "advice_id": "b-3", "turn": 3, "t_gen": 30.2, "t_shown": 30.8, "t_decided": 30.8},
    ]
    rows = RA.display_rows(recs, deadline_sec=10.0, stale_hp_sec=30.0)
    r0 = rows[0]
    assert r0["status"] == "after_decision" and r0["latency"] is None and r0["in_time"] is False, r0
    s = RA.summarize_display(rows[:1])["legacy"]
    assert s["n_front"] == 1 and s["n_in_time"] == 0 and s["in_time_rate"] == 0.0 and s["latency_ms"]["n"] == 0, s
    assert s["status"]["after_decision"] == 1
    rows1 = RA.display_rows(recs, deadline_sec=1.0, stale_hp_sec=30.0)
    assert rows1[1]["status"] == "shown" and rows1[1]["latency"] == 1.5 and rows1[1]["in_time"] is False, rows1[1]
    assert rows1[2]["status"] == "shown" and rows1[2]["latency"] == 0.8 and rows1[2]["in_time"] is True, rows1[2]
    print("test_display_after_decision OK")


def test_display_mode_from_version():
    """レビュワーの例: version 行が transition で advice 行が無い決定は transition の分母に残す。記録が無ければ方式不明"""
    recs = [
        {"type": "version", "version_id": "v1", "scoring_mode": "transition"},
        {"type": "scene", "scene": SCENE_COMMAND, "t": 10.0},
        {"type": "scene", "scene": SCENE_FIELD, "t": 12.0},
        {"type": "decision", "advice_id": None, "turn": 1, "t_gen": None, "t_shown": None, "t_decided": 12.0},
        # advice 行の記録 (legacy) が version 行 (transition) と食い違う: version 行を採り、食い違いとして数える
        {"type": "scene", "scene": SCENE_COMMAND, "t": 20.0},
        _adv_rec("c-2", 20.5, mode="legacy"),
        {"type": "display", "advice_id": "c-2", "t_shown": 20.6, "t": 20.6},
        {"type": "scene", "scene": SCENE_FIELD, "t": 21.0},
        {"type": "decision", "advice_id": "c-2", "turn": 2, "t_gen": 20.5, "t_shown": 20.6, "t_decided": 21.0},
    ]
    rows = RA.display_rows(recs, deadline_sec=10.0, stale_hp_sec=30.0, unrecorded_mode=RA.UNKNOWN_MODE)
    assert rows[0]["mode"] == "transition" and rows[0]["mode_source"] == "version" and rows[0]["status"] == "no_advice", rows[0]
    assert rows[1]["mode"] == "transition" and rows[1]["mode_mismatch"] is True, rows[1]
    s = RA.summarize_display(rows)
    assert set(s) == {"transition"} and s["transition"]["n"] == 2 and s["transition"]["n_front"] == 2
    assert s["transition"]["in_time_rate"] == 0.5 and s["transition"]["n_mode_mismatch"] == 1
    # version 行にも advice 行にも記録が無ければ方式不明 (legacy に寄せない)
    old = [r for r in recs if r.get("type") != "version"]
    ai = next(i for i, r in enumerate(old) if r.get("type") == "advice")
    old[ai] = _adv_rec("c-2", 20.5)
    rows2 = RA.display_rows(old, deadline_sec=10.0, stale_hp_sec=30.0, unrecorded_mode=RA.UNKNOWN_MODE)
    assert [r["mode"] for r in rows2] == [RA.UNKNOWN_MODE, RA.UNKNOWN_MODE] and not any(r["mode_recorded"] for r in rows2)
    assert "legacy" not in RA.summarize_display(rows2)
    # version 行に記録が無く advice 行にあればそれを採る
    old[ai] = _adv_rec("c-2", 20.5, mode="transition")
    rows3 = RA.display_rows(old, deadline_sec=10.0, stale_hp_sec=30.0, unrecorded_mode=RA.UNKNOWN_MODE)
    assert rows3[1]["mode"] == "transition" and rows3[1]["mode_source"] == "advice" and rows3[0]["mode"] == RA.UNKNOWN_MODE
    # 単体: decision_mode
    assert RA.decision_mode({"scoring_mode": "legacy"}, None) == \
        {"mode": "legacy", "recorded": True, "source": "version", "mismatch": False, "advice_mode": None}
    assert RA.decision_mode(None, {"advice": {}})["mode"] == RA.UNKNOWN_MODE
    # 途中で version 行が変われば、その後の決定は新しい設定
    recs4 = recs[:4] + [{"type": "version", "version_id": "v2", "scoring_mode": "legacy"}] + recs[4:]
    rows4 = RA.display_rows(recs4, deadline_sec=10.0, stale_hp_sec=30.0, unrecorded_mode=RA.UNKNOWN_MODE)
    assert [r["mode"] for r in rows4] == ["transition", "legacy"] and rows4[1]["mode_mismatch"] is False
    print("test_display_mode_from_version OK")


def test_check_best_detail():
    c = _compact()
    r = RA.check_best(_adv("move", "expandingforce"), c, {"player": {}, "last_move": {}}, detail=True, **CK)
    assert r["violations"] == [] and r["unconfirmed"] == []
    ck = r["checks"]
    assert all(ck[k] == "ok" for k in ("best", "move_owned", "move_pp", "move_fainted", "move_lock", "move_taunt",
                                       "move_disable", "candidate_picked", "active_identity")), ck
    assert all(ck[k] is None for k in ("switch_party", "switch_form", "switch_self", "switch_fainted", "switch_picked"))
    # 既定 (detail なし) は従来の形のまま
    assert set(RA.check_best(_adv("move", "expandingforce"), c, **CK)) == {"violations", "unconfirmed"}
    # move_locks の欄が無い行: 技の固定・かなしばりは照合できない (変化技なら ちょうはつ も)
    ck = RA.check_best(_adv("move", "encore"), c, None, detail=True, **CK)["checks"]
    assert ck["move_lock"] == "unknown" and ck["move_disable"] == "unknown" and ck["move_taunt"] == "unknown"
    ck = RA.check_best(_adv("move", "expandingforce"), c, None, detail=True, **CK)["checks"]
    assert ck["move_taunt"] == "ok"                       # 変化技でなければ ちょうはつ に関係なく合法
    # アンコールの対象が分からない
    enc_unknown = {"player": {"volatiles": ["encore"]}, "turn": 3, "last_move": {}}
    r = RA.check_best(_adv("move", "expandingforce"), c, enc_unknown, detail=True, **CK)
    assert r["checks"]["move_lock"] == "unknown" and r["unconfirmed"] == ["lock_unknown"]
    # PP・HP が読めていない / 登録からの補完
    cu = _compact(party=[_mon("indeedee", (("expandingforce", None),), hp=None), _mon("blastoise", hp=None)])
    ck = RA.check_best(_adv("move", "encore"), cu, {}, registered=["encore"], detail=True, **CK)["checks"]
    assert ck["move_owned"] == "unknown" and ck["move_pp"] == "unknown" and ck["move_fainted"] == "unknown", ck
    ck = RA.check_best(_adv("switch", "blastoise"), cu, {}, detail=True, **CK)["checks"]
    assert ck["switch_fainted"] == "unknown" and ck["switch_party"] == "ok" and ck["switch_picked"] == "ok", ck
    # 違反は ng
    ck = RA.check_best(_adv("switch", "garchomp"), c, {}, detail=True, **CK)["checks"]
    assert ck["switch_picked"] == "ng" and ck["candidate_picked"] == "ng"
    # 選出が分からない
    cs = _compact(party=[_mon("indeedee", (("expandingforce", 16),), picked=False), _mon("garchomp", picked=False)])
    ck = RA.check_best(_adv("switch", "garchomp"), cs, {}, detail=True, **CK)["checks"]
    assert ck["switch_picked"] == "unknown" and ck["candidate_picked"] == "unknown"
    ck = RA.check_best({"ok": False}, c, {}, detail=True, **CK)["checks"]
    assert ck["best"] == "unknown"
    print("test_check_best_detail OK")


def test_correctness_unknown_not_ok():
    """レビュワーの例: lock_unknown だけの入力を「正しさを満たす」にしない"""
    item = "正しさ: 違反 (有効な決定、再生)"
    ds = [_dec(legacy=_res(unconf=["lock_unknown"]), transition=_res(unconf=["lock_unknown"]))]
    s = RA.summarize(ds)
    assert s["modes"]["legacy"]["valid"]["n_unknown_decisions"] == 1
    rows = {r["item"] + "|" + r["mode"]: r for r in RA.acceptance(s, {}, {"n_logs": 1, "n_logs_ok": 1})}
    r = rows[item + "|legacy"]
    assert r["status"] == RA.PARTIAL and r["status"] != RA.OK and r["value"] == 0, r
    assert "照合できた範囲で違反 0" in r["note"] and "技の固定の対象の技が分からない" in r["note"], r["note"]
    # checks に判定不能があれば同じ (unconfirmed が空でも)
    ck = {k: None for k in RA.CHECK_LABELS} | {"best": "ok", "move_pp": "unknown"}
    s2 = RA.summarize([_dec(legacy=dict(_res(), checks=ck), transition=dict(_res(), checks=dict(ck, move_pp="ok")))])
    rows2 = {r["item"] + "|" + r["mode"]: r for r in RA.acceptance(s2, {}, {"n_logs": 1, "n_logs_ok": 1})}
    assert rows2[item + "|legacy"]["status"] == RA.PARTIAL and "技の PP" in rows2[item + "|legacy"]["note"]
    assert rows2[item + "|transition"]["status"] == RA.OK                 # 全検査が照合できて違反 0
    assert s2["modes"]["legacy"]["valid"]["checks"]["move_pp"] == {"ok": 0, "ng": 0, "unknown": 1}
    # 違反があれば判定不能が残っても 満たさない
    s3 = RA.summarize([_dec(legacy=_res(viol=["move_not_owned"], unconf=["lock_unknown"]), transition=_res())])
    rows3 = {r["item"] + "|" + r["mode"]: r for r in RA.acceptance(s3, {}, {"n_logs": 1, "n_logs_ok": 1})}
    assert rows3[item + "|legacy"]["status"] == RA.NG
    print("test_correctness_unknown_not_ok OK")


def _dblock(p95_ms, n=10, log=None):
    return {"n": n, "n_front": n, "n_mode_unrecorded": 0, "n_mode_mismatch": 0, "mode_sources": {"version": n, "advice": 0},
            "status": {k: 0 for k in RA.DISPLAY_STATUS_LABELS} | {"shown": n},
            "latency_ms": {"n": n, "p50": 500.0, "p95": p95_ms, "max": p95_ms},
            "gen_to_shown_ms": {"n": n, "p50": 5.0, "p95": 10.0, "max": 12.0}, "n_in_time": n, "in_time_rate": 1.0}


def test_latency_rule():
    """遅延: 各回と合算の両方に基準を当て、1 回でも超えたら合算で隠さない。版・設定が揃わなければ判定不能"""
    s = RA.summarize([_dec(legacy=_res(), transition=_res())])
    item = "遅延: 決定開始 → 有効な助言の表示の p95 (実戦ログ)"
    per = "遅延: 決定開始 → 有効な助言の表示の p95 (実戦ログ、回ごと)"
    agg = dict(_dblock(2800.0, 20), by_log={"a.jsonl": _dblock(3200.0), "b.jsonl": _dblock(2000.0)})
    ok_cons = {"ok": True, "notes": [], "values": {}}
    grp = {"target_fixed": True, "by_mode": {"transition": ok_cons}, "delta": ok_cons}
    rows = {r["item"] + "|" + r["mode"]: r for r in RA.acceptance(s, {"transition": agg}, {"n_logs": 2, "n_logs_ok": 2}, group=grp)}
    a = rows[item + "|transition"]
    assert a["status"] == RA.NG and a["value"] == 2.8 and a["note"].startswith("合算。" + RA.PENDING_NOTE), a
    assert "a.jsonl 3.20 秒" in a["note"]
    assert rows[per + "|transition / a.jsonl"]["status"] == RA.NG and rows[per + "|transition / b.jsonl"]["status"] == RA.OK
    # 全回が基準内で版・設定が揃えば 満たす
    agg_ok = dict(_dblock(2800.0, 20), by_log={"a.jsonl": _dblock(2900.0), "b.jsonl": _dblock(2000.0)})
    rows = {r["item"] + "|" + r["mode"]: r for r in RA.acceptance(s, {"transition": agg_ok}, {"n_logs": 2, "n_logs_ok": 2}, group=grp)}
    assert rows[item + "|transition"]["status"] == RA.OK
    # 版・設定が揃わなければ判定不能 (超えた回が無いとき)。超えた回があれば未達のまま
    bad = {"ok": False, "notes": ["version_id が揃わない (v1 / v2)"], "values": {}}
    grp_bad = {"target_fixed": False, "by_mode": {"transition": bad}, "delta": bad}
    rows = {r["item"] + "|" + r["mode"]: r for r in RA.acceptance(s, {"transition": agg_ok}, {"n_logs": 2, "n_logs_ok": 2},
                                                                     group=grp_bad)}
    r = rows[item + "|transition"]
    assert r["status"] == RA.UNKNOWN and "version_id が揃わない" in r["note"] and "事前に固定していない" in r["note"], r
    rows = {r["item"] + "|" + r["mode"]: r for r in RA.acceptance(s, {"transition": agg}, {"n_logs": 2, "n_logs_ok": 2},
                                                                     group=grp_bad)}
    assert rows[item + "|transition"]["status"] == RA.NG
    # 増分: 両方式があっても版が揃わなければ判定不能。再生の差は参考値と明記
    disp2 = {"legacy": dict(_dblock(2000.0), by_log={}), "transition": dict(_dblock(2200.0), by_log={})}
    rows = {r["item"] + "|" + r["mode"]: r for r in RA.acceptance(s, disp2, {"n_logs": 2, "n_logs_ok": 2},
                                                                     group=dict(grp, delta=bad))}
    d = rows["遅延: legacy より増える分 (実戦ログの p95 の差)|transition"]
    assert d["status"] == RA.UNKNOWN and "実機の表示の増分の代わりにしない" in d["note"], d
    # 方式不明の行は「不明」として別行
    rows = {r["item"] + "|" + r["mode"]: r for r in RA.acceptance(s, {RA.UNKNOWN_MODE: dict(_dblock(2000.0), by_log={})},
                                                                     {"n_logs": 1, "n_logs_ok": 0})}
    assert rows[item + "|不明"]["status"] == RA.OK and "方式不明" in rows[item + "|不明"]["note"]
    print("test_latency_rule OK")


def test_versions_and_consistency():
    recs = [{"type": "version", "version_id": "v1", "scoring_mode": "transition"},
            {"type": "client", "served_commit": "abc123"}, {"type": "client", "served_commit": "abc123"}]
    v = RA.log_versions(recs)
    assert v == {"version_id": ["v1"], "scoring_mode": ["transition"], "served_commit": ["abc123"],
                 "served_commit_source": "client"}, v
    v2 = RA.log_versions([{"type": "version", "version_id": "v1", "scoring_mode": "transition", "served_commit": "def"}])
    assert v2["served_commit"] == ["def"] and v2["served_commit_source"] == "version"
    li = lambda n, x: {"name": n, "versions": x}
    assert RA.target_consistency([li("a", v), li("b", v)])["ok"] is True
    c = RA.target_consistency([li("a", v), li("b", dict(v, version_id=["v2"]))])
    assert c["ok"] is False and any("version_id が揃わない" in n for n in c["notes"])
    c = RA.target_consistency([li("a", v), li("b", dict(v, scoring_mode=[]))])
    assert c["ok"] is False and any("scoring_mode の記録なし 1 本" in n for n in c["notes"])
    # 増分の照合は方式を除く欄だけ
    assert RA.target_consistency([li("a", v), li("b", dict(v, scoring_mode=["legacy"]))],
                                 fields=("served_commit", "version_id"))["ok"] is True
    assert RA.target_consistency([])["ok"] is False
    print("test_versions_and_consistency OK")


def test_render_markdown_new_sections():
    s = RA.summarize([_dec(legacy=dict(_res(unconf=["lock_unknown"]), checks={"best": "ok", "move_lock": "unknown"}),
                           transition=_res())])
    disp = {"legacy": dict(_dblock(3200.0), by_log={"a.jsonl": _dblock(3200.0)}),
            RA.UNKNOWN_MODE: dict(_dblock(1000.0), by_log={"b.jsonl": _dblock(1000.0)})}
    cons = {"ok": False, "notes": ["scoring_mode の記録なし 1 本 (b.jsonl)"], "values": {}}
    grp = {"target_fixed": True, "by_mode": {"legacy": {"ok": True, "notes": [], "values": {}}, RA.UNKNOWN_MODE: cons},
           "delta": cons}
    acc = RA.acceptance(s, disp, {"n_logs": 2, "n_logs_ok": 1}, group=grp)
    result = {"conditions": {"at": "2026-10-16 12:00", "code": {"commit": "abc", "dirty": False, "engine": "/x/engine.py"},
                             "modes": list(RA.MODES), "rl_blend_weight": 25.0, "rl_style": "balance", "transition_budget_ms": 300.0,
                             "transition_sensitivity": True, "gap_sec": 8.0, "stale_hp_sec": 30.0, "n_advice_rows": 10,
                             "target": {"fixed": True, "by_mode": grp["by_mode"], "delta": cons},
                             "logs": [{"name": "a.jsonl", "n_advice_rows": 10, "n_decisions": 6, "n_decision_rows": 5,
                                       "modes_used": {"legacy": 10}, "has_move_locks": True,
                                       "versions": {"version_id": ["v1"], "scoring_mode": ["legacy"], "served_commit": ["c1"],
                                                    "served_commit_source": "client"}}]},
              "summary": s, "display": disp, "display_all": _dblock(3100.0), "acceptance": acc,
              "profile": {"legacy": "ncalls tottime\n"}}
    md = RA.render_markdown(result)
    assert RA.PARTIAL in md and RA.PENDING_NOTE in md and "検査ごとの照合" in md and "| 技の固定 (アンコール / こだわり) | 0 / 0 / 1 |" in md
    assert "不明 / b.jsonl" in md and "全体 (方式を問わない、参考)" in md and "事前に決めた接続テスト群" in md
    assert "c1 (client 行)" in md and "evaluate の内訳" in md
    print("test_render_markdown_new_sections OK")


def main() -> None:
    test_thin_and_rows()
    test_decision_open_time()
    test_stale_reasons()
    test_check_best_moves()
    test_check_best_switch_and_identity()
    test_display_rows()
    test_summarize_and_acceptance()
    test_record_check()
    test_logger_scoring_fields()
    test_control_panel_scoring()
    test_shadow_load_check_compare()
    test_display_after_decision()
    test_display_mode_from_version()
    test_check_best_detail()
    test_correctness_unknown_not_ok()
    test_latency_rule()
    test_versions_and_consistency()
    test_render_markdown_new_sections()
    print("\nALL OK")


if __name__ == "__main__":
    main()
