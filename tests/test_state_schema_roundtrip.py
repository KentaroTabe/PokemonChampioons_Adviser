"""助言の行の簡約状態の版 2 (battle_logger.compact_state) と、その対の復元 (tools.advice_replay.compact_to_engine_state /
tools.real_accept._engine_state) の往復のテスト (2026-10-19 レビュー §1)。

- 純粋な部分: 版の判定、版 2 の欄、欠けた欄を確定値にしない復元、再生の now、照合できない項目、技の固定の照合用の欄
- 往復: 保存前の state と、保存 (JSON を通す) → 復元した state で、opp_pending・HP の範囲 (build_inputs の lo / hi)・合法手
  (build_inputs に渡す選べる技・選べない行動)・退避の理由・候補と順位 (得点) が legacy / transition の両方で一致する
  (合成: 倒される境界を跨ぐ HP の範囲・観測の古い HP・対応待ち・消費済み持ち物・アンコール・交代技の後の繰り出し待ち /
  実戦: tests/fixtures/state_schema/real_advice_rows.json = 本体の 10/15・10/19 の対戦ログの advice 行と、対応待ちの実例の
  10/09 の 2 行)。版 1 (旧版) で失われる場合に結果が変わることも確かめる (往復の比較が欠落を検出できること)
- 後から判明した情報を混ぜない: 復元はその advice 行だけから作る

比較の間はモデル・型・DB を固定する: RL は使わない (rl_blend_weight=0、policy_hint を固定)、相手の技は判明技だけ
(tests.test_scoring_transition._Hint)、登録チームは読まない (tests.test_advice_m1a._Patch)、使用率 DB は読まない
(advisor.sets の SetPredictor を存在しない DB に差し替える)。CI でも本体でも同じ条件で動く。

    python -m tests.test_state_schema_roundtrip
"""
from __future__ import annotations

import copy
import inspect
import json
import os
import tempfile
import time
from pathlib import Path

os.environ.setdefault("RL_BLEND_WEIGHT", "0")

import advisor.sets as SETS
from advisor import scoring_transition as ST
from advisor.engine import evaluate, opp_active_pending
from battle_logger import (MON_V2_EXTRA, MON_V2_FIELDS, STATE_SCHEMA_VERSION, STATE_V2_FIELDS, BattleLogger,
                           _compact_state, compact_state, data_version_of, move_locks_of, state_digest, state_schema_of)
from tools import real_accept as RA
from tools.advice_replay import (RESTORE_UNKNOWN_LABELS, RESTORE_V1_KEYS, compact_to_engine_state, move_locks_for_check,
                                 replay_now, restore_unknowns)
from tests.test_advice_m1a import _Patch
from tests.test_scoring_transition import _Hint, _mon, _state_171, resolver

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "state_schema" / "real_advice_rows.json"
MODES = ("legacy", "transition")
T0 = 1_792_000_000.0          # 合成局面の生成時刻 (= evaluate に渡す now)


# ------------------------------------------------------------------ 比較の条件の固定
class _Fixed:
    """RL・相手の技プール・登録チーム・使用率 DB を固定する (テストの間だけ)"""

    def __enter__(self):
        self.hint, self.patch = _Hint(), _Patch()
        self.hint.__enter__()
        self.patch.__enter__()
        self.pred = SETS._predictor
        SETS._predictor = SETS.SetPredictor(db_path=Path("/nonexistent/state_schema_test.sqlite3"))
        return self

    def __exit__(self, *a):
        SETS._predictor = self.pred
        self.patch.__exit__(*a)
        self.hint.__exit__(*a)


class _Spy:
    """build_inputs に渡した選べる技と、作った入力の HP の範囲 (lo / hi) / 退避の理由を取り出す"""

    def __enter__(self):
        self.orig = ST.build_inputs
        self.calls = []

        def spy(state, resolver_, **kw):
            inp, why = self.orig(state, resolver_, **kw)
            self.calls.append({"legal": list(kw.get("legal_move_ids") or []), "why": why,
                               "ranges": [(h["side"], h["slot"], h["lo"], h["hi"], h["source"]) for h in (inp.hp_ranges if inp else [])],
                               "hp_alt": bool(inp is not None and inp.hp_alt is not None)})
            return inp, why
        ST.build_inputs = spy
        return self

    def __exit__(self, *a):
        ST.build_inputs = self.orig


def _sig(state: dict, mode: str) -> dict:
    """比べる結果: opp_pending・退避の理由・使った方式・候補と順位 (得点)・選べない行動・技の固定の注記 (speed_note)・
    build_inputs の入力 (選べる技) と出力 (HP の範囲 / 退避の理由)"""
    with _Spy() as sp:
        a = evaluate(copy.deepcopy(state), resolver, rl_blend_weight=0, scoring_mode=mode, display_search=False)
    return {"ok": a.get("ok"), "opp_pending": a.get("opp_pending"), "fallback": a.get("scoring_fallback"),
            "used": a.get("scoring_used"), "best": [(a.get("best") or {}).get("kind"), (a.get("best") or {}).get("id")],
            "actions": [(x.get("kind"), x.get("id"), round(float(x.get("score") or 0.0), 6)) for x in a.get("actions") or []],
            "illegal": sorted(json.dumps(x, sort_keys=True, ensure_ascii=False, default=str) for x in a.get("illegal_actions") or []),
            "legality_unconfirmed": a.get("legality_unconfirmed"), "speed_note": a.get("speed_note"), "inputs": sp.calls}


def _row(state: dict, t_now=None, t_gen: float = T0, move_locks=None, compact=None) -> dict:
    """state から advice 行 (ログに書いて読んだ形 = JSON を通す) を作る"""
    c = compact_state(state) if compact is None else compact
    r = {"type": "advice", "kind": "battle", "t": t_gen + 0.05, "turn": state.get("turn"), "t_now": t_now,
         "advice": {"t_gen": t_gen}, "state": c}
    if move_locks:
        r["move_locks"] = move_locks
    return json.loads(json.dumps(r, ensure_ascii=False, default=str))


def _assert_same(a: dict, b: dict, label: str) -> None:
    for mode in MODES:
        sa, sb = _sig(a, mode), _sig(b, mode)
        assert sa == sb, (label, mode, {k: (sa[k], sb[k]) for k in sa if sa[k] != sb[k]})


# ------------------------------------------------------------------ 合成局面
def _base() -> dict:
    """battle_20261009_113830.jsonl:171 の局面 (tests.test_scoring_transition._state_171) に、生成時刻と観測時刻を足したもの"""
    st = _state_171()
    st["now"] = T0
    st["last_move"] = {}
    for side in ("player", "opponent"):
        for m in st[side]["party"]:
            m.setdefault("hp_read_ts", T0 - 2.0)
    return st


def _hp_bar(st: dict) -> dict:
    """自分の場の個体の HP がバー推定の 50% (範囲 45〜55%)。45% と 50% の間で ミミロップ が倒される境界を跨ぐ (下の確認)"""
    m = st["player"]["party"][0]
    m.update(hp_percent=50.0, hp_current=70, hp_max=140, hp_source="bar", hp_estimated=True, hp_uncertain=True,
             hp_read_ts=T0 - 1.0)
    for mv in m["moves"]:
        mv["effectiveness"] = "super" if mv["move_id"] == "closecombat" else None
        mv["max_pp"] = 8 if mv["move_id"] == "closecombat" else None
    return st


def _hp_stale(st: dict) -> dict:
    """相手の場の個体の HP の観測が 90 秒前 (古い。範囲の下限が下がる)"""
    st["opponent"]["party"][0].update(hp_percent=60.0, hp_read_ts=T0 - 90.0)
    return st


def _pending(st: dict) -> dict:
    """相手の場の個体が対応待ち (6 枠のどれか未定。party の 7 番目、候補の枠つき)"""
    opp = st["opponent"]
    paw = opp["party"][0]
    slots = [_mon(sid, ja, types, picked=False, hp=None, species_guess=True)
             for sid, ja, types in (("garchomp", "ガブリアス", ["ドラゴン", "じめん"]), ("raichu", "ライチュウ", ["でんき"]),
                                    ("gengar", "ゲンガー", ["ゴースト", "どく"]), ("corviknight", "アーマーガア", ["ひこう", "はがね"]),
                                    ("kingambit", "ドドゲザン", ["あく", "はがね"]), ("sneasler", "オオニューラ", ["かくとう", "どく"]))]
    slots[1].update(hp_percent=0.0, status="fainted", species_guess=False)
    pend = dict(paw, pending=True, t_first_seen=T0 - 30.0, species_candidates=["pawmot"],
                pending_hint=[{"slot": 5, "ja": "オオニューラ", "types": ["かくとう", "どく"], "source": "types"}])
    opp["party"] = slots + [pend]
    opp["active_index"] = 6
    return st


def _item_consumed(st: dict) -> dict:
    """相手の きあいのタスキ が消費済み"""
    st["opponent"]["party"][0].update(item_id="focussash", item_ja="きあいのタスキ", item_consumed=True)
    return st


def _encore(st: dict) -> dict:
    """自分の場の個体が アンコール中 (つるぎのまい に固定、残り 2 ターン)。相手は ちょうはつ を受けている"""
    st["player"]["party"][0].update(volatiles=["encore"], encore_move="swordsdance", volatile_turns={"encore": 2}, entry_turn=3)
    st["opponent"]["party"][0].update(volatiles=["taunt"], volatile_turns={"taunt": 1})
    st["last_move"] = {"player": "swordsdance", "opponent": "machpunch"}
    return st


def _pivot(st: dict) -> dict:
    """交代技の後の繰り出し待ち"""
    st["pending_pivot_switch"] = True
    st["last_move"] = {"player": "uturn"}
    return st


SYNTH = {"hp_bar": _hp_bar, "hp_stale": _hp_stale, "pending": _pending, "item_consumed": _item_consumed,
         "encore": _encore, "pivot": _pivot}


# ------------------------------------------------------------------ 純粋
def test_schema_detection():
    assert state_schema_of(None) == 1 and state_schema_of({}) == 1 and state_schema_of({"state_schema": 2}) == 2
    assert state_schema_of({"state_schema": "2"}) == 1 and state_schema_of({"state_schema": True}) == 1
    assert state_schema_of({"state_schema": 0}) == 1 and STATE_SCHEMA_VERSION == 2
    print("test_schema_detection OK")


def test_compact_v2_fields():
    """版 2 の欄: 版 1 の欄はそのまま、個体・全体・陣営の欄を足す。推定・対応待ちの印は真偽を必ず書く。scene 行と影の比較が使う
    _compact_state (版 1) は変えない"""
    st = _pending(_encore(_hp_bar(_base())))
    c = compact_state(st)
    v1 = _compact_state(st)
    assert c["state_schema"] == 2 and "state_schema" not in v1
    for k in STATE_V2_FIELDS:
        assert k in c, k
    assert c["turn"] == 5 and c["last_move"] == {"player": "swordsdance", "opponent": "machpunch"}
    for side in ("player", "opponent"):
        for row, row1 in zip(c[side]["party"], v1[side]["party"]):
            assert all(k in row for k in MON_V2_FIELDS + MON_V2_EXTRA), [k for k in MON_V2_FIELDS + MON_V2_EXTRA if k not in row]
            assert {k: row[k] for k in row1} == row1                       # 版 1 の欄は同じ値
    me = c["player"]["party"][0]
    assert (me["hp_source"], me["hp_estimated"], me["hp_uncertain"], me["hp_read_ts"]) == ("bar", True, True, T0 - 1.0)
    assert me["volatiles"] == ["encore"] and me["encore_move"] == "swordsdance" and me["volatile_turns"] == {"encore": 2}
    assert me["move_meta"] == [[8, "super"], [None, None], [None, None], [None, None]], me["move_meta"]
    pend = c["opponent"]["party"][6]
    assert pend["pending"] is True and pend["pending_hint"][0]["slot"] == 5 and pend["t_first_seen"] == T0 - 30.0
    # 推定・対応待ちの印は真偽を必ず書く。state に欄の無い個体は null (False にしない)
    assert c["opponent"]["party"][0]["guess"] is True and c["opponent"]["party"][1]["guess"] is False
    assert me["guess"] is None and me["pending"] is None and me["candidates"] is None
    assert c["opponent"]["party"][0]["candidates"] is None and pend["candidates"] == ["pawmot"]
    st_live = {"player": {"party": [{"species_id": "x", "species_guess": False, "pending": False, "species_candidates": []}]},
               "opponent": {"party": []}}
    live = compact_state(st_live)["player"]["party"][0]
    assert (live["guess"], live["pending"], live["candidates"]) == (False, False, [])
    # state を後で変えても簡約状態は変わらない (コピー)
    st["player"]["party"][0]["volatiles"].append("taunt")
    assert me["volatiles"] == ["encore"]
    print("test_compact_v2_fields OK")


def test_restore_pairs_and_missing_none():
    """版 2 の欄はすべて戻る。版 1 (欄が無い) は None のまま (確定値 False / [] にしない)。版 1 でも推定・対応待ちの印は戻す"""
    st = _item_consumed(_pending(_encore(_hp_bar(_base()))))
    r2 = compact_to_engine_state(_row(st)["state"], resolver)
    for side in ("player", "opponent"):
        for a, b in zip(st[side]["party"], r2[side]["party"]):
            for k in MON_V2_FIELDS:
                assert b[k] == (a.get(k) if not isinstance(a.get(k), tuple) else list(a[k])), (side, k, a.get(k), b[k])
            assert b["species_guess"] == a.get("species_guess") and b["pending"] == a.get("pending")
            assert b["species_candidates"] == a.get("species_candidates")
            assert b["pending_hint"] == a.get("pending_hint")
            assert [(m["max_pp"], m["effectiveness"]) for m in b["moves"]] == \
                [(m.get("max_pp"), m.get("effectiveness")) for m in a["moves"] if m.get("move_id")]
    assert r2["turn"] == 5 and r2["last_move"] == st["last_move"] and r2["pending_pivot_switch"] is None
    assert opp_active_pending(r2["opponent"])
    # 版 1: 印のある個体は戻し、印の無い個体と版 2 の欄は None
    r1 = compact_to_engine_state(json.loads(json.dumps(_compact_state(st))), resolver)
    me, opp = r1["player"]["party"][0], r1["opponent"]["party"][6]
    for k in MON_V2_FIELDS:
        assert me[k] is None and opp[k] is None, k
    assert opp["pending"] is True and opp_active_pending(r1["opponent"])          # 版 1 にもある印 (以前は復元で消えていた)
    assert r1["opponent"]["party"][0]["species_guess"] is True and me["species_guess"] is None and me["pending"] is None
    assert me["species_candidates"] is None and opp["species_candidates"] == ["pawmot"] and opp["pending_hint"] is None
    assert "turn" not in r1 and "pending_pivot_switch" not in r1
    # 版 1 + move_locks: 場の個体の技の固定と直前の技は戻る
    r1l = compact_to_engine_state(_compact_state(st), resolver, move_locks_of(st))
    assert r1l["player"]["party"][0]["encore_move"] == "swordsdance" and r1l["last_move"]["player"] == "swordsdance"
    assert r1l["player"]["party"][1]["volatiles"] is None
    # 版 2 で欄が欠けた個体も None (確定値にしない)
    c = _row(st)["state"]
    del c["player"]["party"][0]["hp_uncertain"], c["player"]["party"][0]["guess"]
    rr = compact_to_engine_state(c, resolver)["player"]["party"][0]
    assert rr["hp_uncertain"] is None and rr["species_guess"] is None
    # 復元した state を変えても行の簡約状態は変わらない
    row = _row(st)
    rs = compact_to_engine_state(row["state"], resolver)
    rs["player"]["party"][0]["volatiles"].append("taunt")
    rs["opponent"]["party"][6]["pending_hint"][0]["slot"] = 0
    assert row["state"]["player"]["party"][0]["volatiles"] == ["encore"]
    assert row["state"]["opponent"]["party"][6]["pending_hint"][0]["slot"] == 5
    print("test_restore_pairs_and_missing_none OK")


def test_replay_now_and_unknowns():
    """now = t_now → t_gen → t。版 1 の行は照合できない項目を全部数え、版 2 は欠けがあれば restore_v2_missing"""
    assert replay_now({"t_now": 5.0, "advice": {"t_gen": 6.0}, "t": 7.0}) == 5.0
    assert replay_now({"t_now": None, "advice": {"t_gen": 6.0}, "t": 7.0}) == 6.0
    assert replay_now({"advice": {}, "t": 7.0}) == 7.0 and replay_now({}) is None
    assert replay_now({"t_now": True, "t": 7.0}) == 7.0
    st = _hp_bar(_base())
    assert restore_unknowns(_row(st)) == []
    v1 = {"state": _compact_state(st)}
    assert restore_unknowns(v1) == list(RESTORE_V1_KEYS) and all(k in RESTORE_UNKNOWN_LABELS for k in RESTORE_V1_KEYS)
    bad = _row(st)
    del bad["state"]["opponent"]["party"][0]["item_consumed"]
    assert restore_unknowns(bad) == ["restore_v2_missing"]
    # real_accept: 版 1 は state_restore = unknown、照合できない項目は UNCONFIRMED_LABELS にある
    assert RA.restore_check(v1) == (list(RESTORE_V1_KEYS), "unknown") and RA.restore_check(_row(st)) == ([], "ok")
    assert all(k in RA.UNCONFIRMED_LABELS for k in RESTORE_UNKNOWN_LABELS) and "state_restore" in RA.CHECK_LABELS
    assert RA.state_schema_counts([v1, _row(st), _row(st)]) == {"v1": 1, "v2": 2}
    print("test_replay_now_and_unknowns OK")


def test_unknown_restore_not_ok():
    """版 1 の行だけの再生は、違反 0 でも 正しさ = 判定不能 (照合できた範囲で違反 0)。項目名が注記に出る"""
    ru, rst = RA.restore_check({"state": _compact_state(_base())})
    res = {"ok": True, "best": {"kind": "move", "id": "closecombat"}, "elapsed_ms": 1.0, "violations": [], "unconfirmed": ru,
           "checks": {k: None for k in RA.CHECK_LABELS} | {"best": "ok", "state_restore": rst}}
    s = RA.summarize([{"log": "x", "advice_id": "a", "stale": [], "modes": {m: dict(res) for m in RA.MODES}}])
    rows = {r["item"] + "|" + r["mode"]: r for r in RA.acceptance(s, {}, {"n_logs": 1, "n_logs_ok": 1})}
    r = rows["正しさ: 違反 (有効な決定、再生)|transition"]
    assert r["status"] == RA.PARTIAL and "状態の復元" in r["note"], r
    assert s["modes"]["transition"]["valid"]["unconfirmed"]["restore_v1_hp"] == 1
    print("test_unknown_restore_not_ok OK")


def test_move_locks_for_check():
    st = _encore(_base())
    assert move_locks_for_check({"state": _compact_state(st)}) is None                       # 版 1・move_locks なし = 照合できない
    ml = move_locks_of(st)
    assert move_locks_for_check({"state": _compact_state(st), "move_locks": ml}) == ml
    got = move_locks_for_check(_row(st))
    assert got["player"]["encore_move"] == "swordsdance" and got["opponent"]["volatiles"] == ["taunt"]
    assert got["last_move"] == {"player": "swordsdance", "opponent": "machpunch"} and got["turn"] == 5
    # 固定の無い版 2 の行は「固定なし」と確定 (None にしない)
    plain = move_locks_for_check(_row(_base()))
    assert plain == {"turn": 5, "last_move": {}}
    # check_best に渡すと、版 2 の行は技の固定・かなしばりを照合できる (版 1・move_locks なしは unknown)
    adv = {"ok": True, "best": {"kind": "move", "id": "swordsdance"}, "actions": []}
    c2 = _row(st)["state"]
    ck = RA.check_best(adv, c2, move_locks_for_check(_row(st)), detail=True)["checks"]
    assert ck["move_lock"] == "ok" and ck["move_disable"] == "ok", ck
    ck1 = RA.check_best(adv, _compact_state(st), None, detail=True)["checks"]
    assert ck1["move_lock"] == "unknown"
    bad = RA.check_best({"ok": True, "best": {"kind": "move", "id": "closecombat"}, "actions": []}, c2,
                        move_locks_for_check(_row(st)), detail=True)
    assert "move_locked" in bad["violations"]
    print("test_move_locks_for_check OK")


def test_logger_advice_row():
    """advice 行: state は版 2、state_id はその digest、t_now と data_version を必ず書く。読んだ行から同じ欄が戻る"""
    st = _pending(_encore(_hp_bar(_base())))
    with tempfile.TemporaryDirectory() as td:
        lg = BattleLogger(log_dir=Path(td))
        lg.on_advice({"ok": True, "best": {"kind": "move", "id": "swordsdance"}, "actions": []}, "battle", st)
        st2 = dict(st)
        st2.pop("now")
        lg.on_advice({"ok": True, "best": {"kind": "move", "id": "swordsdance"}, "actions": []}, "battle", st2)
        rows = [json.loads(x) for x in lg._file.read_text(encoding="utf-8").splitlines()]
    adv = [r for r in rows if r["type"] == "advice"]
    a, b = adv
    assert a["state"]["state_schema"] == 2 and a["state_id"] == state_digest(a["state"]) and a["t_now"] == T0
    assert b["t_now"] is None and "t_now" in b
    assert set(a["data_version"]) == {"usage_snapshot", "dex_sha256", "effects_sha256"}
    ver = next(r for r in rows if r["type"] == "version")
    assert a["data_version"]["dex_sha256"] == (ver.get("rules") or {}).get("dex_sha256")
    assert a["move_locks"] == move_locks_of(st)                              # 版 1 の読み手のため従来どおり書く
    # 版 1 の digest とは別 (HP の不確かさの有無で state_id が変わる。以前は同じだった)
    clear = copy.deepcopy(st)
    for k in ("hp_uncertain", "hp_estimated", "hp_source"):
        clear["player"]["party"][0][k] = None
    assert state_digest(compact_state(clear)) != a["state_id"]
    assert state_digest(_compact_state(clear)) == state_digest(_compact_state(st))
    r = RA._engine_state(a, resolver)
    assert r["now"] == T0 and r["player"]["party"][0]["hp_source"] == "bar" and opp_active_pending(r["opponent"])
    assert RA._engine_state(b, resolver)["now"] == b["advice"]["t_gen"]
    print("test_logger_advice_row OK")


def test_data_version_of():
    v = {"rules": {"dex_sha256": "d", "effects_sha256": "e"}}
    pred = SETS._predictor
    try:
        SETS._predictor = None
        assert data_version_of(v) == {"usage_snapshot": None, "dex_sha256": "d", "effects_sha256": "e"}
        p = SETS.SetPredictor(db_path=Path("/nonexistent/x.sqlite3"))
        p._snapshot_id = 71
        SETS._predictor = p
        assert data_version_of(None) == {"usage_snapshot": 71, "dex_sha256": None, "effects_sha256": None}
    finally:
        SETS._predictor = pred
    print("test_data_version_of OK")


# ------------------------------------------------------------------ 往復 (合成)
def test_roundtrip_synthetic():
    """合成の 6 局面で、保存前と保存 → 復元後の結果が両方式で一致する。版 1 で失われる情報は結果を変える (往復の比較が欠落を
    検出できる) ことも確かめる"""
    with _Fixed():
        for name, fn in SYNTH.items():
            st = fn(_base())
            for t_now in (T0, None):                       # 実機の server は now を渡さない (t_now = null → t_gen)
                row = _row(st, t_now=t_now)
                back = RA._engine_state(row, resolver)
                assert back["now"] == T0, (name, back["now"])
                _assert_same(st, back, f"{name} (t_now={t_now})")
        # 倒される境界: 50% (範囲 45〜55%) と 45% (下限) で得点が違い、範囲を失うと 50% の確定値として採点される
        st = _hp_bar(_base())
        lo = copy.deepcopy(st)
        lo["player"]["party"][0].update(hp_percent=45.0, hp_current=63, hp_source=None, hp_estimated=False, hp_uncertain=False)
        plain50 = copy.deepcopy(st)
        plain50["player"]["party"][0].update(hp_source=None, hp_estimated=False, hp_uncertain=False)
        s_unc, s_lo, s_50 = _sig(st, "transition"), _sig(lo, "transition"), _sig(plain50, "transition")
        assert s_unc["inputs"][0]["ranges"] == [("me", "active", 0.45, 0.55, "bar")], s_unc["inputs"]
        assert s_lo["actions"][0][2] != s_50["actions"][0][2], (s_lo["actions"][:2], s_50["actions"][:2])
        assert s_unc["actions"][0][2] == s_lo["actions"][0][2]           # 悲観側 (下限 45%) で採点
        v1 = RA._engine_state(_row(st, t_now=T0, compact=_compact_state(st)), resolver)
        assert _sig(v1, "transition")["actions"] == s_50["actions"] != s_unc["actions"]
        # 観測の古い HP: 生成時刻を now にすれば一致、現在の時計を now にすると範囲が変わる (以前の再生の挙動)
        st = _hp_stale(_base())
        back = RA._engine_state(_row(st), resolver)
        assert _sig(back, "transition")["inputs"][0]["ranges"] == _sig(st, "transition")["inputs"][0]["ranges"] != []
        wall = dict(back, now=time.time())
        assert _sig(wall, "transition")["inputs"][0]["ranges"] != _sig(st, "transition")["inputs"][0]["ranges"]
        # 対応待ち: 版 2 も版 1 も印が戻り、transition は opp_pending で退避
        st = _pending(_base())
        for compact in (None, _compact_state(st)):
            r = RA._engine_state(_row(st, t_now=T0, compact=compact), resolver)
            s = _sig(r, "transition")
            assert s["opp_pending"] is True and s["fallback"] == "opp_pending", s["fallback"]
        # アンコール: 版 2 は選べる技が つるぎのまい だけ。版 1 (move_locks なし) は固定が消えて結果が変わる
        st = _encore(_base())
        s = _sig(RA._engine_state(_row(st), resolver), "transition")
        assert s["inputs"][0]["legal"] == ["swordsdance"] and "アンコール中" in (s["speed_note"] or ""), s["inputs"]
        v1 = RA._engine_state(_row(st, t_now=T0, compact=_compact_state(st)), resolver)
        assert _sig(v1, "transition")["inputs"][0]["legal"] != ["swordsdance"]
        v1l = RA._engine_state(_row(st, t_now=T0, compact=_compact_state(st), move_locks=move_locks_of(st)), resolver)
        assert _sig(v1l, "transition")["inputs"][0]["legal"] == ["swordsdance"]
        # 消費済みの持ち物: 版 2 は戻り、版 1 は None (不明)
        st = _item_consumed(_base())
        assert RA._engine_state(_row(st), resolver)["opponent"]["party"][0]["item_consumed"] is True
        assert RA._engine_state(_row(st, t_now=T0, compact=_compact_state(st)), resolver)["opponent"]["party"][0]["item_consumed"] is None
    print("test_roundtrip_synthetic OK")


# ------------------------------------------------------------------ 往復 (実戦の状態)
def test_roundtrip_real_rows():
    """実戦の advice 行 (版 1) を復元した state → 版 2 で保存 → 復元で、結果が両方式で一致する。対応待ちの行は
    ログの opp_pending=true が復元後も true (以前は全部 false になっていた)"""
    rows = json.loads(FIXTURE.read_text(encoding="utf-8"))["rows"]
    assert len(rows) >= 12 and any((r["advice"] or {}).get("opp_pending") for r in rows)
    n_pending = 0
    with _Fixed():
        for r in rows:
            assert state_schema_of(r["state"]) == 1 and restore_unknowns(r) == list(RESTORE_V1_KEYS)
            s0 = RA._engine_state(r, resolver)
            assert s0["now"] == r["advice"]["t_gen"]
            if (r["advice"] or {}).get("opp_pending"):
                n_pending += 1
                assert opp_active_pending(s0["opponent"]), r["source"]
            back = RA._engine_state(_row(s0, t_now=s0["now"], t_gen=r["advice"]["t_gen"]), resolver)
            _assert_same(s0, back, r["source"])
            # 版 2 の行は照合できない項目なし (版 1 の不明は None のまま運ばれる。確定値にはならない)
            assert restore_unknowns(_row(s0)) == []
            assert all(m["hp_uncertain"] is None for m in back["player"]["party"])
    assert n_pending == 2
    print("test_roundtrip_real_rows OK")


# ------------------------------------------------------------------ 後から判明した情報を混ぜない
def test_no_later_information():
    """復元は advice 行 1 行だけから作る: 同じ対戦の後の行 (持ち物が判明・対応待ちが解消・HP を読み直し) や scene 行を
    混ぜない。再生の入力を作る関数は行と resolver しか受け取らない"""
    assert list(inspect.signature(RA._engine_state).parameters) == ["row", "resolver"]
    assert list(inspect.signature(compact_to_engine_state).parameters) == ["c", "resolver", "move_locks"]
    early = _pending(_hp_bar(_base()))
    later = copy.deepcopy(early)
    later["now"] = T0 + 60.0
    later["opponent"]["party"][6].update(item_id="choicescarf", pending=False)
    later["player"]["party"][0].update(hp_percent=20.0, hp_current=28, hp_source=None, hp_estimated=False, hp_uncertain=False,
                                       hp_read_ts=T0 + 59.0)
    r1 = _row(early, t_now=T0)
    r2 = _row(later, t_now=T0 + 60.0, t_gen=T0 + 60.0)
    scene = {"type": "scene", "t": T0 + 61.0, "scene": "command", "state": _compact_state(later)}
    records = [r1, scene, r2]
    before = RA._engine_state(r1, resolver)
    rows = RA.battle_advice_rows(records)
    assert [x["t"] for x in rows] == [r1["t"], r2["t"]]
    got = RA._engine_state(rows[0], resolver)
    assert got == before
    assert got["now"] == T0 and got["opponent"]["party"][6]["item_id"] is None and got["opponent"]["party"][6]["pending"] is True
    me = got["player"]["party"][0]
    assert (me["hp_percent"], me["hp_source"], me["hp_read_ts"]) == (50.0, "bar", T0 - 1.0)
    # 評価で state を変えられても行は変わらない (検査 check_best は行の state を読む)
    snap = json.dumps(r1, sort_keys=True)
    with _Fixed():
        evaluate(got, resolver, rl_blend_weight=0, scoring_mode="transition", display_search=False)
    assert json.dumps(r1, sort_keys=True) == snap
    print("test_no_later_information OK")


def main() -> None:
    test_schema_detection()
    test_compact_v2_fields()
    test_restore_pairs_and_missing_none()
    test_replay_now_and_unknowns()
    test_unknown_restore_not_ok()
    test_move_locks_for_check()
    test_logger_advice_row()
    test_data_version_of()
    test_roundtrip_synthetic()
    test_roundtrip_real_rows()
    test_no_later_information()
    print("\nALL OK")


if __name__ == "__main__":
    main()
