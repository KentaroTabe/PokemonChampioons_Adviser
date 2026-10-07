"""段 0 (2026-10-07) で対戦ログに足した欄・行の検証 (battle_logger)。

hp_stale / frames / roster_change / guess_confirm / opp_picks (選出ラベル 3 値) / manual_fix の fix_id と
manual_fix_overwritten / decision。純粋関数と、BattleLogger が実際に書く行の両方を見る。

    python -m tests.test_stage0_logger
"""
from __future__ import annotations

import json
import shutil
import tempfile
import time
from pathlib import Path

import battle_logger as BL
from battle_logger import BattleLogger


def _mon(sid=None, ja=None, guess=False, hp=None, score=None, types=None, hp_ts=None, **kw):
    d = {"species_id": sid, "species_ja": ja, "species_guess": guess, "hp_percent": hp, "guess_score": score,
         "types": types or [], "hp_read_ts": hp_ts, "moves": [], "boosts": {}, "status": None}
    d.update(kw)
    return d


def _state(scene, seq=0, opp=None, opp_active=None, me=None, me_active=None, events=None, turn=1):
    return {"scene": scene, "outcome": None, "turn": turn, "events": events or [], "field": {"weather": None},
            "selection_picked": None, "mega_used": {}, "battle_seq": seq,
            "player": {"active_index": me_active, "remaining": None, "hazards": {}, "party": me or []},
            "opponent": {"active_index": opp_active, "remaining": None, "hazards": {}, "party": opp or []}}


def _records(path: Path) -> list:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ------------------------------------------------------------------ 純粋関数
def test_hp_stale_of():
    st = _state("command", me=[_mon("a", hp_ts=100.0)], me_active=0, opp=[_mon("b")], opp_active=0)
    assert BL.hp_stale_of(st, 103.5) == {"player": 3.5, "opponent": None}
    assert BL.hp_stale_of(_state("command"), 1.0) == {"player": None, "opponent": None}    # 場の個体が無い
    print("test_hp_stale_of OK")


def test_frames_row():
    start = {"received": 10, "processed": 8, "dropped": 2, "hidden": 0}
    end = {"received": 110, "processed": 78, "dropped": 32, "hidden": 25, "last_recv_ts": 1020.0}
    r = BL.frames_row(start, end, 1000.0)
    assert r["received"] == 100 and r["processed"] == 70 and r["dropped"] == 30 and r["hidden"] == 25
    assert r["hidden_ratio"] == 0.25 and r["span_sec"] == 20.0
    assert r["recv_fps"] == 5.0 and r["proc_fps"] == 3.5          # 対戦の時間の幅で割る (10 fps の仮定ではない)
    r0 = BL.frames_row(start, dict(start, last_recv_ts=900.0), 1000.0)   # 開いてから受信なし
    assert r0["received"] == 0 and r0["recv_fps"] is None and r0["hidden_ratio"] is None
    print("test_frames_row OK")


def test_label_opp_picks():
    slots = [{"slot": i, "species": s, "ja": s, "guess": False} for i, s in enumerate("abcdef")]
    lab = BL.label_opp_picks(slots, {"a", "b"})
    st = {r["species"]: r["pick_status"] for r in lab["slots"]}
    # 2 体しか場に出ていない: 残りは「選出外」ではなく不明 (選出されたが出なかった個体がありうる)
    assert st["a"] == st["b"] == BL.PICK_CONFIRMED and all(st[x] == BL.PICK_UNKNOWN for x in "cdef")
    assert lab["complete"] is False and lab["n_appeared"] == 2
    lab3 = BL.label_opp_picks(slots, {"a", "b", "c"})
    st3 = {r["species"]: r["pick_status"] for r in lab3["slots"]}
    assert lab3["complete"] and all(st3[x] == BL.PICK_UNPICKED for x in "def")
    assert [r["appeared"] for r in lab3["slots"]] == [True, True, True, False, False, False]
    # メガ形態で場に出た種は基本種の枠に数える。枠に無い種は slot=None の行で足す
    lab_m = BL.label_opp_picks(slots, {"amega", "b", "zz"})
    assert {r["species"]: r["pick_status"] for r in lab_m["slots"]}["a"] == BL.PICK_CONFIRMED
    assert lab_m["slots"][-1] == {"slot": None, "species": "zz", "ja": None, "guess": False, "appeared": True,
                                  "pick_status": BL.PICK_CONFIRMED}
    assert lab_m["complete"]
    # 誤読で 4 体出た: 揃ったとは言わない (残りは不明)
    lab4 = BL.label_opp_picks(slots, {"a", "b", "c", "d"})
    assert lab4["inconsistent"] and not lab4["complete"]
    assert {r["species"]: r["pick_status"] for r in lab4["slots"]}["e"] == BL.PICK_UNKNOWN
    print("test_label_opp_picks OK")


def test_roster_change_and_guess_verdict():
    prev = {0: {"species": "dragonite", "ja": "カイリュー", "guess": True, "score": 0.7},
            1: {"species": "garchomp", "ja": "ガブリアス", "guess": False, "score": None},
            2: {"species": None, "ja": None, "guess": False, "score": None}}
    cur = {0: {"species": "baxcalibur", "ja": "セグレイブ", "guess": False},
           1: {"species": "garchomp", "ja": "ガブリアス", "guess": False},
           2: {"species": "x", "ja": "X", "guess": True}}
    ch = BL.roster_changes(prev, cur)
    assert [c[0] for c in ch] == [0]                          # 種の無かった枠が埋まるのは置き換えではない
    assert BL.roster_change_basis(cur[0], 0, 0, "command", False) == "field"
    assert BL.roster_change_basis(cur[0], 0, 3, "command", False) == "name_read"
    assert BL.roster_change_basis(None, 0, 0, "command", False) == "cleared"
    assert BL.roster_change_basis({"species": "y", "guess": True}, 0, None, "selection", False) == "selection_guess"
    assert BL.roster_change_basis(cur[0], 0, 0, "command", True) == "manual"
    assert BL.guess_verdict("dragonite", "baxcalibur", {"baxcalibur"}) == ("baxcalibur", "mismatch")
    assert BL.guess_verdict("dragonite", None, {"dragonitemega"}) == ("dragonite", "match")
    assert BL.guess_verdict("dragonite", None, set()) == (None, "unrevealed")
    print("test_roster_change_and_guess_verdict OK")


def test_decision_and_manual_pure():
    assert BL.player_action_of(["hp_x", "move_player_surf"]) == {"kind": "move", "id": "surf"}
    assert BL.player_action_of(["switch_player"]) == {"kind": "switch", "id": None}
    assert BL.player_action_of(["move_opponent_surf"]) is None
    mv = {"kind": "move", "id": "surf"}
    assert BL.decision_match(mv, {"kind": "move", "id": "surf"}) is True
    assert BL.decision_match(mv, {"kind": "move", "id": "ice"}) is False
    assert BL.decision_match(mv, {"kind": "switch", "id": None}) is False
    assert BL.decision_match({"kind": "switch", "id": "a"}, {"kind": "switch", "id": None}) is None
    assert BL.decision_match({"kind": "switch", "id": "a"}, {"kind": "switch", "id": "a"}) is True
    assert BL.decision_match(None, mv) is None and BL.decision_match(mv, None) is None
    st = _state("command", me=[_mon("a", "A", boosts={"atk": 2})], me_active=0,
                opp=[_mon("b", "B", hp=40.0)], opp_active=0)
    ev = {"source": "manual", "event": "manual_fix", "text": "x",
          "detail": {"target": "mon", "field": "hp_percent", "label": "opponent:B:hp_percent", "after": "55"}}
    key = BL.manual_fix_key(ev, st)
    assert key == {"target": "mon", "side": "opponent", "index": 0, "field": "hp_percent"}   # 古い detail (index なし) は和名から
    assert BL.manual_value_from_detail(key, ev["detail"]) == (True, 55.0)
    assert BL.manual_current_value(st, key) == 40.0
    bkey = {"target": "mon", "side": "player", "index": 0, "field": "boost:atk"}
    assert BL.manual_current_value(st, bkey) == 2
    assert BL.manual_value_from_detail(bkey, {"after": "9"}) == (True, 6)
    sev = {"source": "manual", "event": "species_manual", "text": "相手のBを手動確定 (候補から選択)", "detail": {}}
    assert BL.manual_fix_key(sev, st) == {"target": "species", "side": "opponent", "index": 0, "field": "species"}
    assert BL.manual_fix_key({"source": "manual", "event": "species_manual_skip", "text": "無視", "detail": {}}, st) is None
    print("test_decision_and_manual_pure OK")


def test_frames_row_visibility():
    """2026-10-07 実機確認: visible / unknown を足し、hidden_ratio は既知 (hidden + visible) に対する比。古い server の件数でも落ちない"""
    start = {"received": 10, "processed": 8, "dropped": 2, "hidden": 0, "visible": 4, "unknown": 6}
    end = {"received": 110, "processed": 78, "dropped": 32, "hidden": 25, "visible": 54, "unknown": 31, "last_recv_ts": 1020.0}
    r = BL.frames_row(start, end, 1000.0)
    assert (r["hidden"], r["visible"], r["unknown"]) == (25, 50, 25)
    assert r["hidden_ratio"] == round(25 / 75, 3)                               # 受信 100 ではなく既知 75 に対する比
    for k in ("received", "processed", "dropped", "hidden", "hidden_ratio", "span_sec", "recv_fps", "proc_fps"):
        assert k in r, k                                                        # 既存の欄は残す
    # 通知が 1 度も無い (全部 unknown) → 既知 0 → hidden_ratio は None (0% = 前面とはしない)
    allu = BL.frames_row({"received": 0, "hidden": 0, "visible": 0, "unknown": 0},
                         {"received": 50, "hidden": 0, "visible": 0, "unknown": 50, "last_recv_ts": 1010.0}, 1000.0)
    assert allu["unknown"] == 50 and allu["visible"] == 0 and allu["hidden_ratio"] is None
    # 古い server (visible / unknown の欄が無い): visible / unknown は None、hidden_ratio は従来どおり受信に対する比
    old = BL.frames_row({"received": 0, "hidden": 0}, {"received": 40, "hidden": 8, "last_recv_ts": 1004.0}, 1000.0)
    assert old["visible"] is None and old["unknown"] is None and old["hidden_ratio"] == 0.2
    # 開いた時点の件数が古い形 (欄なし) で終わりが新しい形でも落ちない (開いた時点を 0 とみなす)
    mix = BL.frames_row({"received": 0, "hidden": 0}, {"received": 10, "hidden": 2, "visible": 3, "unknown": 5}, None)
    assert (mix["visible"], mix["unknown"], mix["hidden_ratio"]) == (3, 5, 0.4) and mix["recv_fps"] is None
    assert BL.frames_row(None, None, None)["received"] == 0
    print("test_frames_row_visibility OK")


# ------------------------------------------------------------------ ロガーが書く行
def test_logger_client_and_visibility_rows():
    """対戦ファイルを開いたとき version 行の直後に open_rows_source の行 (client / visibility) を書く。
    on_client_row は開いているファイルにだけ書く (ファイルを開かない)。frames 行に visible / unknown が載る"""
    from client_state import ClientRegistry
    tmp = Path(tempfile.mkdtemp())
    counts = {"received": 0, "processed": 0, "dropped": 0, "hidden": 0, "visible": 0, "unknown": 0, "last_recv_ts": 0.0}
    reg = ClientRegistry()
    try:
        lg = BattleLogger(log_dir=tmp, guess_prob_fn=None, frame_source=lambda: dict(counts))
        lg.open_rows_source = lambda: reg.open_rows("v2")
        assert not lg.file_open
        reg.on_connect("sid-old-page-1", 1.0)
        reg.on_hello("sid-new-page-2", {"html_version": "v1", "features": ["client_hello"], "visibility": "visible"}, 2.0)
        lg.on_client_row(reg.client_row("sid-new-page-2", "v2"))              # ファイルが無ければ書かない (開かない)
        lg.on_client_row(reg.on_visibility("sid-new-page-2", True, 3.0))
        assert not lg.file_open and not list(tmp.glob("*.jsonl"))
        lg.on_frame(_state("selection", seq=1), [])
        first = lg._file
        recs = _records(first)
        types = [r["type"] for r in recs]
        iv = types.index("version") if "version" in types else types.index("session")
        assert types[iv + 1: iv + 4] == ["client", "client", "visibility"], types
        cl = {r["sid"]: r for r in recs if r["type"] == "client"}
        assert cl["sid-old-"]["hello"] is False and cl["sid-old-"]["stale"] is None
        assert cl["sid-new-"]["stale"] is True and cl["sid-new-"]["served_version"] == "v2"
        vis = [r for r in recs if r["type"] == "visibility"]
        assert vis[0]["hidden"] is True and vis[0]["source"] == "page_visibility" and "t" in vis[0]   # 最新の状態を 1 行
        # 対戦中の通知はその場で書く
        lg.on_client_row(reg.on_visibility("sid-new-page-2", False, 5.0))
        lg.on_client_row(None)
        assert _records(first)[-1]["type"] == "visibility" and _records(first)[-1]["hidden"] is False
        counts.update(received=30, hidden=5, visible=20, unknown=5, last_recv_ts=time.time() + 3.0)
        lg._finalize(None)
        fr = [r for r in _records(first) if r["type"] == "frames"][-1]
        assert (fr["visible"], fr["unknown"], fr["hidden_ratio"]) == (20, 5, 0.2)
        assert not lg.file_open
    finally:
        shutil.rmtree(tmp)
    print("test_logger_client_and_visibility_rows OK")


def test_logger_frames_picks_and_guess_rows():
    tmp = Path(tempfile.mkdtemp())
    counts = {"received": 0, "processed": 0, "dropped": 0, "hidden": 0, "last_recv_ts": 0.0}
    try:
        lg = BattleLogger(log_dir=tmp, guess_prob_fn=lambda types, sid: 0.97 if sid == "garchomp" else 0.6,
                          frame_source=lambda: dict(counts))
        roster = [_mon("dragonite", "カイリュー", guess=True, score=0.71, types=["ドラゴン", "ひこう"]),
                  _mon("garchomp", "ガブリアス", guess=True, score=0.97),
                  _mon("rotomwash", "ウォッシュロトム"), _mon("kingambit", "ドドゲザン"),
                  _mon("gholdengo", "サーフゴー"), _mon(None, None, types=["フェアリー"])]
        lg.on_frame(_state("selection", opp=[dict(m) for m in roster]), [])
        first = lg._file
        counts.update(received=40, processed=30, dropped=10, hidden=8, last_recv_ts=time.time() + 4.0)
        # 場に出た: 枠 0 はセグレイブ (推定のカイリューは誤り)、ドドゲザン、サーフゴー
        b1 = [dict(m) for m in roster]
        b1[0] = _mon("baxcalibur", "セグレイブ", hp=100.0)
        lg.on_frame(_state("command", opp=b1, opp_active=0), [])
        b2 = [dict(m) for m in b1]
        b2[3] = _mon("kingambit", "ドドゲザン", hp=80.0)
        b2[4] = _mon("gholdengo", "サーフゴー", hp=50.0)
        lg.on_frame(_state("field", opp=b2, opp_active=4), [])
        lg._finalize(None)
        recs = _records(first)
        rc = [r for r in recs if r["type"] == "roster_change"]
        assert len(rc) == 1 and rc[0]["from"] == "dragonite" and rc[0]["to"] == "baxcalibur", rc
        assert rc[0]["basis"] == "field" and rc[0]["from_guess"] is True and rc[0]["from_prob"] == 0.6
        fr = [r for r in recs if r["type"] == "frames"][-1]
        assert fr["received"] == 40 and fr["hidden"] == 8 and fr["hidden_ratio"] == 0.2 and fr["recv_fps"] is not None
        op = [r for r in recs if r["type"] == "opp_picks"][-1]
        st = {s["species"]: s["pick_status"] for s in op["slots"]}
        assert op["complete"] is True and op["n_appeared"] == 3
        assert st["baxcalibur"] == st["kingambit"] == st["gholdengo"] == BL.PICK_CONFIRMED
        assert st["rotomwash"] == BL.PICK_UNPICKED and st["garchomp"] == BL.PICK_UNPICKED and st[None] == BL.PICK_UNPICKED
        gc = {r["species"]: r for r in recs if r["type"] == "guess_confirm"}
        assert gc["dragonite"]["verdict"] == "mismatch" and gc["dragonite"]["revealed"] == "baxcalibur"
        assert gc["dragonite"]["prob"] == 0.6 and gc["dragonite"]["auto_accept"] is False
        assert gc["garchomp"]["verdict"] == "unrevealed" and gc["garchomp"]["sure"] is True
        assert gc["garchomp"]["threshold_sure"] == BL.SELECTION_GUESS_SURE_PROB
        # 同じ内容なら書き直さない (close を呼んでも増えない)
        n_before = len(recs)
        lg.close()
        assert len(_records(first)) == n_before
        print("test_logger_frames_picks_and_guess_rows OK")
    finally:
        shutil.rmtree(tmp)


def test_logger_advice_fields_and_decision():
    tmp = Path(tempfile.mkdtemp())
    try:
        lg = BattleLogger(log_dir=tmp, guess_prob_fn=None)
        me = [_mon("archaludon", "ブリジュラス", hp=100.0, hp_ts=time.time() - 5.0), _mon("raichu", "ライチュウ")]
        opp = [_mon("garchomp", "ガブリアス", hp=100.0)]
        lg.on_frame(_state("command", me=me, me_active=0, opp=opp, opp_active=0), [])
        adv = {"ok": True, "best": {"kind": "move", "id": "dracometeor", "name": "りゅうせいぐん"},
               "actions": [{"kind": "move", "id": "dracometeor", "score": 80}]}
        aid = lg.on_advice(adv, "battle", _state("command", me=me, me_active=0, opp=opp, opp_active=0))
        sel = {"ok": True, "recommend": []}
        sid = lg.on_advice(sel, "selection", _state("selection"))
        # 選出の記録の欄は助言を送った後に別の行 (selection_record) で書く。advice 行・advice (表示) には入れない
        lg.on_selection_record(sid, {"candidates": {"rule": None}, "opp_pick_pred": {"combos": []}, "type": "x"})
        assert "candidates" not in sel
        lg.on_display(aid, time.time(), "battle", hidden=False)
        # 決定画面 → 解決側の場面 1 フレームだけ (誤分類) では決定にしない
        lg.on_frame(_state("battle_hud", me=me, me_active=0, opp=opp, opp_active=0), [])
        lg.on_frame(_state("command", me=me, me_active=0, opp=opp, opp_active=0), [])
        lg.on_frame(_state("field", me=me, me_active=0, opp=opp, opp_active=0), [])
        lg.on_frame(_state("field", me=me, me_active=0, opp=opp, opp_active=0), [])
        lg.on_frame(_state("field", me=me, me_active=0, opp=opp, opp_active=0), ["move_player_dracometeor"])
        # 手入力の訂正 → 後の推定で別の値に
        ev = {"source": "manual", "event": "manual_fix", "ts": time.time() + 1, "text": "手動修正",
              "detail": {"target": "mon", "field": "hp_percent", "label": "opponent:ガブリアス:hp_percent",
                         "after": 60, "side": "opponent", "index": 0}}
        opp60 = [_mon("garchomp", "ガブリアス", hp=60.0)]
        lg.on_frame(_state("field", me=me, me_active=0, opp=opp60, opp_active=0, events=[ev]), [])
        lg.on_frame(_state("field", me=me, me_active=0, opp=opp60, opp_active=0, events=[ev]), [])
        opp45 = [_mon("garchomp", "ガブリアス", hp=45.0)]
        lg.on_frame(_state("field", me=me, me_active=0, opp=opp45, opp_active=0, events=[ev], turn=2), [])
        recs = _records(lg._file)
        a = next(r for r in recs if r["type"] == "advice" and r["kind"] == "battle")
        assert a["hp_stale"]["player"] is not None and 4.0 <= a["hp_stale"]["player"] <= 7.0
        assert a["hp_stale"]["opponent"] is None
        s = next(r for r in recs if r["type"] == "advice" and r["kind"] == "selection")
        assert "candidates" not in s and "opp_pick_pred" not in s
        sr = [r for r in recs if r["type"] == "selection_record"]
        assert len(sr) == 1 and sr[0]["advice_id"] == sid and sr[0]["candidates"] == {"rule": None}
        assert sr[0]["opp_pick_pred"] == {"combos": []}                       # type などの予約の欄は上書きしない
        dec = [r for r in recs if r["type"] == "decision"]
        assert len(dec) == 1, dec
        d = dec[0]
        assert d["advice_id"] == aid and d["match"] is True and d["action"] == {"kind": "move", "id": "dracometeor"}
        assert d["scene_from"] == "command" and d["scene_to"] == "field" and d["t_shown"] is not None
        mf = next(r for r in recs if r["type"] == "manual_fix")
        assert mf["fix_id"] and mf["detail"]["after"] == 60
        ow = [r for r in recs if r["type"] == "manual_fix_overwritten"]
        assert len(ow) == 1 and ow[0]["fix_id"] == mf["fix_id"] and ow[0]["manual_value"] == 60.0
        assert ow[0]["new_value"] == 45.0 and ow[0]["overwritten_at"] and ow[0]["fix_turn"] == 1 and ow[0]["turn"] == 2
        print("test_logger_advice_fields_and_decision OK")
    finally:
        shutil.rmtree(tmp)


def test_decision_without_action_and_switch():
    tmp = Path(tempfile.mkdtemp())
    try:
        lg = BattleLogger(log_dir=tmp, guess_prob_fn=None)
        me = [_mon("archaludon", "ブリジュラス"), _mon("raichu", "ライチュウ")]
        lg.on_frame(_state("command", me=me, me_active=0), [])
        lg.on_advice({"ok": True, "best": {"kind": "switch", "id": "raichu"}}, "battle", _state("command", me=me, me_active=0))
        for _ in range(2):
            lg.on_frame(_state("field", me=me, me_active=0), [])
        lg.on_frame(_state("field", me=me, me_active=1), ["switch_player"])
        # 次の決定: 行動が読めないまま次の助言 → 行動 null で書く
        lg.on_frame(_state("move_select", me=me, me_active=1), [])
        lg.on_advice({"ok": True, "best": {"kind": "move", "id": "thunderbolt"}}, "battle",
                     _state("move_select", me=me, me_active=1))
        lg.on_frame(_state("battle_hud", me=me, me_active=1), [])
        lg.on_frame(_state("battle_hud", me=me, me_active=1), [])
        lg.on_advice({"ok": True, "provisional": True, "best": {"kind": "move", "id": "x"}}, "battle",
                     _state("command", me=me, me_active=1))       # 確定前の助言は決定に結ばない
        lg.on_advice({"ok": True, "best": {"kind": "move", "id": "surf"}}, "battle", _state("command", me=me, me_active=1))
        # 低 fps: 決定の確認 (解決側の場面 2 フレーム) より先に行動のイベントが来ても結ぶ
        lg.on_frame(_state("command", me=me, me_active=1), [])
        lg.on_frame(_state("field", me=me, me_active=1), ["move_player_surf"])
        lg.on_frame(_state("field", me=me, me_active=1), [])
        dec = [r for r in _records(lg._file) if r["type"] == "decision"]
        assert len(dec) == 3, dec
        assert dec[0]["action"] == {"kind": "switch", "id": "raichu"} and dec[0]["match"] is True
        assert dec[1]["action"] is None and dec[1]["match"] is None and dec[1]["best"]["id"] == "thunderbolt"
        assert dec[2]["action"] == {"kind": "move", "id": "surf"} and dec[2]["match"] is True
        print("test_decision_without_action_and_switch OK")
    finally:
        shutil.rmtree(tmp)


def test_selection_record_goes_to_advice_battle():
    """計算が終わる前に次の対戦へ切り替わっても、selection_record 行は助言を書いた対戦のファイルに書く"""
    tmp = Path(tempfile.mkdtemp())
    try:
        lg = BattleLogger(log_dir=tmp, guess_prob_fn=None)
        lg.on_frame(_state("selection", seq=1), [])
        sid = lg.on_advice({"ok": True, "recommend": []}, "selection", _state("selection", seq=1))
        first = lg._file
        lg.on_frame(_state("selection", seq=2), [])
        lg.on_frame(_state("selection", seq=2), [])
        assert lg._file != first
        lg.on_selection_record(sid, {"candidates": {"rule": None}})
        rows = [r for r in _records(first) if r["type"] == "selection_record"]
        assert len(rows) == 1 and rows[0]["attributed"] == "advice_battle"
        assert not [r for r in _records(lg._file) if r["type"] == "selection_record"]
        lg.on_selection_record("", {"candidates": {}})                       # id なしは書かない
        print("test_selection_record_goes_to_advice_battle OK")
    finally:
        shutil.rmtree(tmp)


def main():
    test_hp_stale_of()
    test_frames_row()
    test_frames_row_visibility()
    test_label_opp_picks()
    test_roster_change_and_guess_verdict()
    test_decision_and_manual_pure()
    test_logger_frames_picks_and_guess_rows()
    test_logger_advice_fields_and_decision()
    test_decision_without_action_and_switch()
    test_selection_record_goes_to_advice_battle()
    test_logger_client_and_visibility_rows()
    print("ALL OK")


if __name__ == "__main__":
    main()
