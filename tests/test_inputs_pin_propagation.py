"""固定入力 (advisor.data_pin) の異常を、型推定 → 助言エンジン → サーバー / 測定 / 再生の入口まで伝える (2026-10-31 レビュー
E-inputs-3: engine が InputsPinError を握りつぶして ok=True と第一候補を返していた)。

- (a) engine の入口: SpreadTracker.best_for / hypotheses_for に InputsPinError を注入すると ok=False・reason=inputs_pin・第一候補なし
  (レビュワーの再現の形: tests.test_scoring_transition の _state_171 と _Hint、ADVICE_DISPLAY_SEARCH を False)。通常の例外
  (ValueError) の注入は従来どおり (注入なしと同じ第一候補で ok=True)
- (b) サーバーの判断の純粋な部分 (advice_key): 固定入力の異常の結果はキャッシュに入れず、持っていた結果・据え置きの履歴も捨てる。
  停止の表示 (inputs_pin_notice)・異常の行 (inputs_pin_row)・間引き (InputsPinMonitor)・BattleLogger.on_inputs_pin。
  既存の読み手は異常の行を読み飛ばす。実際の入口 (server._handle_one_frame) は tests/test_inputs_pin_propagation_server (ローカル専用)
- (c) real_accept の入口: 異常の行のある対戦を含む群は不成立 (判定不能)、同条件の根拠に理由。再生の異常は照合しない決定
- (d) advice_replay の入口: 再生中の異常で対戦の再生を止め、main は終了コード 1
- (e) 2026-10-31 追加の判断: engine の他の固定入力の読み手 (へんしん 中の get_predictor・タイプからの候補 get_inference) の注入も
  ok=False・reason=inputs_pin。シミュレータの測定 (advisor_player) は inputs_pin の結果を例外として stats["error"] に数え
  (以前の例外の経路と同じ振る舞い、採点の集計・助言どおりの決定には数えない。make_advisor_player が poke-env の Player を
  読むので tests/test_inputs_pin_propagation_local、ローカル専用)。real_accept の --compare-search /
  --compare-sensitivity は on / off のどちらか一方でも inputs_pin なら一致にせず照合できない決定に数える

    python -m tests.test_inputs_pin_propagation
"""
from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("RL_BLEND_WEIGHT", "0")

import advice_key as K
import advisor.engine as ENG
from advisor import data_pin as DP
from advisor.ev_infer import SpreadTracker
from champions_agent import config as C
from tests.test_scoring_transition import _Hint, _state_171, resolver

PIN_MSG = "test: 明示した使用率の snapshot が DB に無い"


def _eval(**kw):
    return ENG.evaluate(_state_171(), resolver, rl_blend_weight=0, scoring_mode="legacy", **kw)


# ------------------------------------------------------------------ (a) engine の入口
def test_engine_best_for_pin_error():
    """best_for の InputsPinError は ok=False・reason=inputs_pin・第一候補なし (evaluate と evaluate_common の両方)"""
    with _Hint(), patch.object(C, "ADVICE_DISPLAY_SEARCH", False):
        base = _eval()
        assert base["ok"] and base["best"], base.get("reason")
        with patch.object(SpreadTracker, "best_for", side_effect=DP.InputsPinError(PIN_MSG)) as m:
            r = _eval()
            common = ENG.evaluate_common(_state_171(), resolver, scoring_mode="transition")
        assert m.call_count >= 1
        for x in (r, common):
            assert x["ok"] is False and x["reason"] == C.INPUTS_PIN_REASON and x["error"] == PIN_MSG, x
            assert not x.get("best") and not x.get("actions"), x
        assert DP.is_pin_error_result(r) and ENG.finish_evaluation(common) is common
        # 通常の例外は従来どおり握りつぶす (推定なしと同じ第一候補)
        with patch.object(SpreadTracker, "best_for", side_effect=ValueError("ふつうの失敗")):
            r2 = _eval()
        assert r2["ok"] is True and r2["best"]["id"] == base["best"]["id"], r2.get("reason")
        assert not DP.is_pin_error_result(r2)
    print("test_engine_best_for_pin_error OK")


def test_engine_hypotheses_for_pin_error():
    """型の仮説 (hypotheses_for、BELIEF_K > 0 で表示用の探索を実行したとき) の InputsPinError も止める。ValueError は従来どおり"""
    with _Hint(), patch.object(ENG, "BELIEF_K", 3), patch.object(SpreadTracker, "best_for", return_value=None):
        base = _eval(display_search=True)
        assert base["ok"] and base["best"]
        with patch.object(SpreadTracker, "hypotheses_for", side_effect=DP.InputsPinError(PIN_MSG)) as m:
            r = _eval(display_search=True)
        assert m.call_count == 1, m.call_count
        assert r["ok"] is False and r["reason"] == C.INPUTS_PIN_REASON and not r.get("best"), r
        with patch.object(SpreadTracker, "hypotheses_for", side_effect=ValueError("ふつうの失敗")) as m2:
            r2 = _eval(display_search=True)
        assert m2.call_count == 1 and r2["ok"] is True and r2["best"]["id"] == base["best"]["id"]
        # 表示用の探索を省略すれば仮説は読まない (既定の経路は変わらない)
        with patch.object(SpreadTracker, "hypotheses_for", side_effect=DP.InputsPinError(PIN_MSG)) as m3:
            r3 = _eval(display_search=False)
        assert m3.call_count == 0 and r3["ok"] is True
    print("test_engine_hypotheses_for_pin_error OK")


def test_engine_transform_branch_pin_error():
    """へんしん 中 (transform_view) の元の種族の型推定の参照も同じ (評価用の写しは advisor.transform.eval_state を差し替えて作る)"""
    from advisor import transform as TF
    st = _state_171()

    def fake_eval_state(state):
        out = copy.deepcopy(state)
        opp = out["opponent"]["party"][out["opponent"]["active_index"]]
        opp["transform_view"] = {"original_species": "ditto", "source_species": "pawmot", "evidence": "test", "hp_base": 48}
        return out, True
    with _Hint(), patch.object(C, "ADVICE_DISPLAY_SEARCH", False), patch.object(TF, "eval_state", fake_eval_state):
        with patch.object(SpreadTracker, "best_for", side_effect=DP.InputsPinError(PIN_MSG)) as m:
            r = ENG.evaluate(st, resolver, rl_blend_weight=0, scoring_mode="legacy")
        with patch.object(SpreadTracker, "best_for", side_effect=ValueError("ふつうの失敗")):
            r2 = ENG.evaluate(st, resolver, rl_blend_weight=0, scoring_mode="legacy")
    assert m.call_args[0][-1] == "ditto", m.call_args          # 元の種族で読んだ
    assert r["ok"] is False and r["reason"] == C.INPUTS_PIN_REASON, r
    assert r2["ok"] is True and "へんしん" in (r2.get("opp_inference") or ""), r2.get("reason")
    print("test_engine_transform_branch_pin_error OK")


# ------------------------------------------------------------------ (b) サーバーの判断の純粋な部分
class _Adv:
    def __init__(self):
        self.resolver, self._last_best, self._stab_hist, self.last_common = None, None, None, None

    @staticmethod
    def suggest_check(state):
        return ""


class _StaticVersions:
    def current(self) -> dict:
        return {"team": "t", "dex": "d", "move_effects": "e", "usage_snapshot": None}


def _key_state(turn=1):
    return {"scene": "command", "turn": turn, "battle_seq": 7, "player": {"active_index": 0, "party": [{"species_id": "a"}]},
            "opponent": {"active_index": 0, "party": [{"species_id": "b"}]}}


def test_frame_step_does_not_cache_pin_error():
    """固定入力の異常の結果はキャッシュ・据え置きに使わない。異常の前の結果も再利用しない。次のフレームは採点し直す"""
    ok_res = {"ok": True, "actions": [{"kind": "move", "id": "surf", "score": 1.0}], "best": {"kind": "move", "id": "surf", "score": 1.0}}
    mode = {"pin": False}
    calls = []

    def fake(state, resolver=None, **kw):
        calls.append(state["now"])
        return DP.pin_error_result(DP.InputsPinError(PIN_MSG)) if mode["pin"] else copy.deepcopy(ok_res)
    adv, cache, vers = _Adv(), K.AdviceCache(), _StaticVersions()
    with patch.object(ENG, "evaluate", fake):
        r1 = K.frame_advice_step(adv, _key_state(), 1.0, cache, vers)
        assert r1["advice"]["ok"] and cache.result is not None and adv._last_best is not None and adv._stab_hist is not None
        mode["pin"] = True
        r2 = K.frame_advice_step(adv, _key_state(turn=2), 2.0, cache, vers)
        assert DP.is_pin_error_result(r2["advice"]) and r2["evaluated"] and not r2["advice"].get("best"), r2
        assert cache.result is None and cache.key is None, "固定入力の異常の結果をキャッシュに入れた"
        assert adv._last_best is None and adv._stab_hist is None and adv.last_common is None
        # 同じ局面の次のフレームもキャッシュを使わずに採点し直す (異常が続く間は毎回止める)
        r3 = K.frame_advice_step(adv, _key_state(turn=2), 2.1, cache, vers)
        assert r3["reason"] == "first" and DP.is_pin_error_result(r3["advice"]) and calls == [1.0, 2.0, 2.1]
        # 前の局面に戻っても異常の前の結果は再利用しない
        r4 = K.frame_advice_step(adv, _key_state(turn=1), 2.2, cache, vers)
        assert r4["reason"] == "first" and DP.is_pin_error_result(r4["advice"]) and len(calls) == 4
        # 異常が解けたら通常どおり採点してキャッシュに入れる
        mode["pin"] = False
        r5 = K.frame_advice_step(adv, _key_state(turn=1), 3.0, cache, vers)
        assert r5["advice"]["ok"] and cache.result is not None
        # 通常の評価エラー (例外) は従来どおり短い有効期限でキャッシュする (固定入力の異常とは分ける)
        cache2 = K.AdviceCache()
        with patch.object(ENG, "evaluate", side_effect=RuntimeError("ふつうの失敗")):
            r6 = K.frame_advice_step(_Adv(), _key_state(), 4.0, cache2, vers)
        assert r6["advice"]["ok"] is False and not DP.is_pin_error_result(r6["advice"]) and cache2.result is not None and cache2.short
    print("test_frame_step_does_not_cache_pin_error OK")


def test_notice_row_and_monitor():
    """停止の表示 (1 行目が停止の文言、2 行目が error)・異常の行・間引き (種類ごと、対戦ごと、間隔)"""
    res = DP.pin_error_result(DP.InputsPinError(PIN_MSG))
    n = K.inputs_pin_notice(res)
    assert n["ok"] is False and n["reason"] == C.INPUTS_PIN_REASON and "best" not in n and "advice_id" not in n
    lines = n["text"].split("\n")
    assert lines[0] == C.INPUTS_PIN_STOP_TEXT and PIN_MSG in lines[1], n["text"]
    row = K.inputs_pin_row("evaluate", PIN_MSG, _key_state(), 3)
    assert row["type"] == C.INPUTS_PIN_LOG_TYPE and row["source"] == "evaluate" and row["error"] == PIN_MSG
    assert row["battle_seq"] == 7 and row["n_suppressed"] == 3 and row["scene"] == "command"
    m = K.InputsPinMonitor(interval_sec=10.0)
    assert m.check("evaluate", PIN_MSG, 7, 0.0) == 0
    assert m.check("evaluate", PIN_MSG, 7, 1.0) is None and m.check("evaluate", PIN_MSG, 7, 9.0) is None
    assert m.check("tracker", PIN_MSG, 7, 1.0) == 0              # 出どころが違えば別の種類
    assert m.check("evaluate", "別の文言", 7, 1.0) == 0
    assert m.check("evaluate", PIN_MSG, 8, 2.0) == 0             # 次の対戦 (ファイル) にも 1 行
    assert m.check("evaluate", PIN_MSG, 7, 10.5) == 2            # 間隔を超えたら間に書かなかった件数つきで 1 行
    assert m.check("evaluate", PIN_MSG, 7, 11.0) is None
    assert K.InputsPinMonitor().interval == float(C.INPUTS_PIN_LOG_INTERVAL_SEC)
    print("test_notice_row_and_monitor OK")


def test_logger_row_and_readers_ignore():
    """BattleLogger.on_inputs_pin は異常の行を 1 行書く。既存の読み手 (advice 行・決定の数え方) は読み飛ばす"""
    import battle_logger as BL
    from tools import real_accept as RA
    with tempfile.TemporaryDirectory() as td:
        lg = BL.BattleLogger(log_dir=Path(td), guess_prob_fn=None)
        lg.on_inputs_pin(K.inputs_pin_row("evaluate", PIN_MSG, _key_state(), 0))
        files = list(Path(td).glob("battle_*.jsonl"))
        assert len(files) == 1
        recs = [json.loads(x) for x in files[0].read_text(encoding="utf-8").splitlines()]
    pins = [r for r in recs if r.get("type") == C.INPUTS_PIN_LOG_TYPE]
    assert len(pins) == 1 and pins[0]["error"] == PIN_MSG and pins[0]["t"] > 0
    assert RA.battle_advice_rows(recs) == [] and BL.evaluation_counts(recs)["n_advice"] == 0
    assert RA.record_check_of([{"name": "x", "records": recs}])["n_logs"] == 1
    print("test_logger_row_and_readers_ignore OK")


# ------------------------------------------------------------------ (c) real_accept の入口
def _records(pin: bool, via_advice: bool = False) -> list:
    out = [{"t": 1.0, "type": "version", "version_id": "v1", "scoring_mode": "legacy", "served_commit": "abc"}]
    out.append({"t": 2.0, "type": "advice", "kind": "battle", "advice": {"ok": True, "best": {"kind": "move", "id": "surf"}},
                "advice_id": "x-1", "policy": {"rl_loaded": True}, "scoring_mode": "legacy", "fallback_reason": None})
    if pin and via_advice:
        out.append({"t": 3.0, "type": "advice", "kind": "battle", "advice": DP.pin_error_result(DP.InputsPinError(PIN_MSG)),
                    "advice_id": "x-2", "policy": {"rl_loaded": True}})
    elif pin:
        out.append(dict(K.inputs_pin_row("tracker", PIN_MSG, _key_state(), 4), t=3.0))
    return out


def test_real_accept_marks_group_unknown():
    """異常の行のある対戦を含む群は不成立 (判定不能)。理由は同条件の根拠に出る。違反 (満たさない) はそのまま"""
    from tools import real_accept as RA
    from tests.test_real_accept import _dec, _res
    bad, good = _records(True), _records(False)
    info = RA.inputs_pin_rows(bad)
    assert info["n"] == 1 and info["n_rows"] == 1 and info["n_suppressed"] == 4 and info["sources"] == {"tracker": 1}
    assert info["errors"] == [PIN_MSG]
    adv_info = RA.inputs_pin_rows(_records(True, via_advice=True))
    assert adv_info["n"] == 1 and adv_info["n_advice"] == 1 and adv_info["sources"] == {"advice": 1}
    assert RA.inputs_pin_rows(good)["n"] == 0
    ib, ig = RA.version_identity(bad), RA.version_identity(good)
    assert ib["inputs_pin"]["n"] == 1 and ig["inputs_pin"]["n"] == 0
    g_ok = RA.group_consistency([{"name": "g1", "ident": ig}, {"name": "g2", "ident": RA.version_identity(_records(False))}])
    assert g_ok["ok"] is True and g_ok["inputs_pin"] == {}, g_ok["notes"]
    g = RA.group_consistency([{"name": "g1", "ident": ig}, {"name": "b1", "ident": ib}])
    assert g["ok"] is False and "b1" in g["inputs_pin"] and any("不成立" in x and PIN_MSG in x for x in g["notes"]), g["notes"]
    # 受入条件: 満たす / PARTIAL は 判定不能、違反の 満たさない はそのまま、異常の行を足す
    s = RA.summarize([_dec(legacy=_res(), transition=_res(viol=["switch_self"]))])
    plain = RA.acceptance(s, {}, {"n_logs": 2, "n_logs_ok": 2})
    clean = RA.acceptance(s, {}, {"n_logs": 2, "n_logs_ok": 2}, inputs_pin={"g1": RA.inputs_pin_rows(good)})
    dirty = RA.acceptance(s, {}, {"n_logs": 2, "n_logs_ok": 2}, inputs_pin={"g1": RA.inputs_pin_rows(good), "b1": info})
    item = "入力: 実機の固定入力 (data_pin) の異常の記録 (対戦ログ)"
    assert [r["status"] for r in clean if r["item"] != item] == [r["status"] for r in plain]
    assert {r["item"]: r for r in clean}[item]["status"] == RA.OK
    prow = {r["item"]: r for r in dirty}[item]
    assert prow["status"] == RA.UNKNOWN and prow["value"] == "1 / 2 ログ" and "b1" in prow["note"] and PIN_MSG in prow["note"]
    by = {(r["item"], r["mode"]): r for r in dirty}
    for r in plain:
        got = by[(r["item"], r["mode"])]
        if r["status"] in (RA.OK, RA.PARTIAL):
            assert got["status"] == RA.UNKNOWN and got["note"].startswith("不成立"), got
        else:
            assert got["status"] == r["status"], (r, got)
    assert any(r["status"] == RA.NG for r in dirty)                       # 違反は隠さない
    # report の同条件の根拠
    md = "\n".join(RA.inputs_pin_lines({"g1": RA.inputs_pin_rows(good), "b1": info}))
    assert "同条件の根拠 (実機の固定入力の異常)" in md and "不成立 (判定不能)" in md and "b1" in md and PIN_MSG in md and "g1" not in md
    assert "異常の記録なし (1 ログ)" in "\n".join(RA.inputs_pin_lines({"g1": RA.inputs_pin_rows(good)}))
    assert RA.inputs_pin_lines(None) == []
    print("test_real_accept_marks_group_unknown OK")


def test_real_accept_replay_pin_is_unconfirmed():
    """再生の evaluate が固定入力の異常を返した方式は照合しない (照合できない決定。正しさは 満たす にならない)"""
    from tools import real_accept as RA
    from tests.test_real_accept import _dec, _res
    pr = RA.replay_pin_result(DP.pin_error_result(DP.InputsPinError(PIN_MSG)), 12.0)
    assert pr["best"] is None and pr["unconfirmed"] == ["data_restore"] and pr["checks"] == {"data_restore": "unknown"}
    assert PIN_MSG in pr["data_reasons"][0]
    s = RA.summarize([_dec(legacy=_res(), transition=_res()), _dec(legacy=pr, transition=pr)])
    v = s["modes"]["legacy"]["valid"]
    assert v["n_unknown_decisions"] == 1 and v["unconfirmed"]["data_restore"] == 1
    corr = [r for r in RA.acceptance(s, {}, {"n_logs": 1, "n_logs_ok": 1}) if r["item"].startswith("正しさ")]
    assert all(r["status"] == RA.PARTIAL for r in corr), corr
    print("test_real_accept_replay_pin_is_unconfirmed OK")


# ------------------------------------------------------------------ (d) advice_replay の入口
class _DS:
    """tools.inputs_pin.DataSwitch の代わり (全決定を当時の版で復元できたことにする)"""

    def __init__(self, mode="restore"):
        self.mode = mode

    def plan(self, *a, **k):
        return {"ok": True}

    def summary(self):
        return {"n_decisions": 0, "n_unrestored_decisions": 0}

    def close(self):
        pass


def _replay_log(d: Path, name: str, n: int = 2) -> Path:
    from tests.test_advice_replay import _compact
    from tools.advice_replay import DECISION_GAP_SEC
    p = d / name
    rows = [{"t": 100.0 + i * (DECISION_GAP_SEC + 1), "type": "scene", "scene": "command", "turn": i + 1, "state": _compact()}
            for i in range(n)]
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    return p


def test_advice_replay_stops_on_pin_error():
    """再生中の固定入力の異常 (結果の形・例外の両方) は、その対戦の再生を止めて結果に残す。main は終了コード 1"""
    import tools.advice_replay as AR
    import tools.inputs_pin as IP
    calls = []

    def pin_result(state, resolver=None, **kw):
        calls.append("r")
        return DP.pin_error_result(DP.InputsPinError(PIN_MSG))

    def pin_raise(state, resolver=None, **kw):
        calls.append("x")
        raise DP.InputsPinError(PIN_MSG)
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        log = _replay_log(d, "battle_20261031_000001.jsonl")
        for fn in (pin_result, pin_raise):
            calls.clear()
            with patch.object(IP, "DataSwitch", _DS), patch.object(ENG, "evaluate", fn):
                res = AR.run([log], d / f"out_{fn.__name__}.json")
            assert len(calls) == 1, calls                           # 2 つ目の決定は再生しない
            assert res["inputs_pin_failures"] == [{"log": log.name, "t": 100.0, "error": PIN_MSG}], res["inputs_pin_failures"]
            saved = json.loads((d / f"out_{fn.__name__}.json").read_text(encoding="utf-8"))
            assert saved["inputs_pin_failures"][0]["error"] == PIN_MSG
        # 通常の失敗は従来どおり baseline_fail に数えて続ける (失敗にしない)
        calls.clear()

        def plain_fail(state, resolver=None, **kw):
            calls.append("v")
            raise ValueError("ふつうの失敗")
        with patch.object(IP, "DataSwitch", _DS), patch.object(ENG, "evaluate", plain_fail):
            res = AR.run([log], d / "out_plain.json")
        assert len(calls) == 2 and res["baseline_fail"] == 2 and res["inputs_pin_failures"] == []
        # main: 非 0 で終わる
        argv = ["advice_replay", "--logs", "5", "--out", str(d / "out_main.json")]
        with patch.object(IP, "DataSwitch", _DS), patch.object(ENG, "evaluate", pin_result), patch.object(AR, "LOG_DIR", d), \
                patch.object(sys, "argv", argv):
            try:
                AR.main()
                code = 0
            except SystemExit as e:
                code = e.code
        assert code == 1, code
    print("test_advice_replay_stops_on_pin_error OK")


# ------------------------------------------------------------------ (e) 追加の判断 (2026-10-31)
def test_engine_other_pinned_readers():
    """へんしん 中の型予測 (get_predictor) とタイプからの候補 (get_inference) の InputsPinError も止める。ValueError は従来どおり"""
    from advisor import infer as INF
    from advisor import transform as TF
    from tests.test_scoring_transition import _ORIG_POOL

    class _Bad:
        def __init__(self, exc):
            self.exc = exc

        def predict(self, *a, **k):
            raise self.exc

        def candidates(self, *a, **k):
            raise self.exc

    def fake_eval_state(state):
        out = copy.deepcopy(state)
        opp = out["opponent"]["party"][out["opponent"]["active_index"]]
        opp["transform_view"] = {"original_species": "ditto", "source_species": "pawmot", "evidence": "test", "hp_base": 48,
                                 "moves": ["machpunch"]}
        return out, True
    # (1) へんしん 中の型予測 (opponent_move_pool の本物を通す)
    for exc, want_pin in ((DP.InputsPinError(PIN_MSG), True), (ValueError("ふつうの失敗"), False)):
        with _Hint(), patch.object(C, "ADVICE_DISPLAY_SEARCH", False), patch.object(TF, "eval_state", fake_eval_state), \
                patch.object(ENG, "opponent_move_pool", _ORIG_POOL), patch.object(ENG, "get_predictor", lambda: _Bad(exc)), \
                patch.object(SpreadTracker, "best_for", return_value=None):
            r = _eval()
        if want_pin:
            assert r["ok"] is False and r["reason"] == C.INPUTS_PIN_REASON and r["error"] == PIN_MSG and not r.get("best"), r
        else:
            assert r["ok"] is True and r["best"], r.get("reason")
    # (2) タイプからの候補 (相手の種族が未判明)
    st = _state_171()
    opp = st["opponent"]["party"][0]
    opp.update(species_id=None, species_ja=None)
    for exc, want_pin in ((DP.InputsPinError(PIN_MSG), True), (ValueError("ふつうの失敗"), False)):
        with _Hint(), patch.object(C, "ADVICE_DISPLAY_SEARCH", False), patch.object(INF, "get_inference", lambda: _Bad(exc)), \
                patch.object(SpreadTracker, "best_for", return_value=None):
            r = ENG.evaluate(copy.deepcopy(st), resolver, rl_blend_weight=0, scoring_mode="legacy")
        if want_pin:
            assert r["ok"] is False and r["reason"] == C.INPUTS_PIN_REASON and not r.get("best"), r
        else:
            assert r.get("reason") != C.INPUTS_PIN_REASON, r       # 候補なしで従来どおり (相手を特定できないまま評価)
    print("test_engine_other_pinned_readers OK")


def test_real_accept_compare_pin_unconfirmed():
    """--compare-search / --compare-sensitivity: on / off のどちらか一方でも inputs_pin なら一致にせず照合できない決定 (判定不能)"""
    from tools import real_accept as RA
    pin = DP.pin_error_result(DP.InputsPinError(PIN_MSG))
    ok = {"ok": True, "actions": [{"kind": "move", "id": "surf", "name": "なみのり", "score": 1.0}],
          "best": {"kind": "move", "id": "surf"}, "scoring_used": "legacy"}
    assert RA.pin_note(pin) == PIN_MSG and RA.pin_note(ok) is None and RA.pin_note(None) is None
    assert RA.pin_note({"ok": False, "reason": "評価エラー: x"}) is None

    def res(adv):
        return {"elapsed_ms": 1.0, "error": None, "sig": None if RA.pin_note(adv) else RA.rank_signature(adv),
                "violations": [], "checks": {}, "search_skipped": True, "q": None, "sens_status": None,
                "inputs_pin": RA.pin_note(adv)}
    assert RA.pair_unconfirmed(res(pin), res(ok)) == C.INPUTS_PIN_REASON
    assert RA.pair_unconfirmed(res(ok), res(pin)) == C.INPUTS_PIN_REASON
    assert RA.pair_unconfirmed(res(pin), res(pin)) == C.INPUTS_PIN_REASON      # 両方とも異常でも一致にしない
    assert RA.pair_unconfirmed(res(ok), res(ok)) is None

    def row(on, off):
        m = {"on": res(on), "off": res(off)}
        m["unconfirmed"] = RA.pair_unconfirmed(m["on"], m["off"])
        m["diffs"] = [] if m["unconfirmed"] else RA.compare_search_pair(m["on"], m["off"])
        return m
    # --compare-search
    rows = [{"log": "a", "advice_id": f"a-{i}", "turn": i, "modes": {mode: row(*pair) for mode in RA.MODES}}
            for i, pair in enumerate(((ok, ok), (pin, ok)))]
    sm = RA.summarize_search_compare(rows)
    for mode in RA.MODES:
        mo = sm["modes"][mode]
        assert mo["n"] == 1 and mo["n_unconfirmed"] == 1 and mo["n_mismatch"] == 0 and mo["status"] == RA.UNKNOWN, mo
    assert sm["status"] == RA.UNKNOWN and sm["n_unconfirmed"] == 2
    ok_only = RA.summarize_search_compare(rows[:1])
    assert ok_only["status"] == RA.OK and ok_only["modes"]["legacy"]["n_unconfirmed"] == 0
    cond = {"at": "t", "code": {"commit": "c", "engine": "e"}, "modes": list(RA.MODES), "rl_blend_weight": 0,
            "transition_budget_ms": 1, "transition_sensitivity": False, "search_blend": 0, "display_search": "off", "gap_sec": 8,
            "replay_inputs": None, "logs": [{"name": "a", "n_decisions": 2}], "data_versions": None}
    md = RA.render_search_compare_markdown({"conditions": cond, "summary": sm})
    assert "照合できない決定 (固定入力の異常" in md and "判定不能" in md, md
    assert "照合できない決定" not in RA.render_search_compare_markdown({"conditions": cond, "summary": ok_only})
    # --compare-sensitivity
    srows = []
    for i, (on, off) in enumerate(((ok, ok), (ok, pin))):
        d = {"log": "a", "advice_id": f"a-{i}", "turn": i, "on": res(on), "off": res(off)}
        d["unconfirmed"] = RA.pair_unconfirmed(d["on"], d["off"])
        d["diffs"] = [] if d["unconfirmed"] else RA.compare_sensitivity_pair(d["on"], d["off"])
        srows.append(d)
    ss = RA.summarize_sensitivity_compare(srows)
    assert ss["n"] == 1 and ss["n_unconfirmed"] == 1 and ss["n_mismatch"] == 0 and ss["status"] == RA.UNKNOWN, ss
    assert RA.summarize_sensitivity_compare(srows[:1])["status"] == RA.OK
    md2 = RA.render_sensitivity_compare_markdown({"conditions": dict(cond, mode="transition"), "summary": ss})
    assert "照合できない決定 (固定入力の異常" in md2 and "| 1 | 0 | 判定不能 |" in md2, md2
    print("test_real_accept_compare_pin_unconfirmed OK")


def main():
    test_engine_best_for_pin_error()
    test_engine_hypotheses_for_pin_error()
    test_engine_transform_branch_pin_error()
    test_frame_step_does_not_cache_pin_error()
    test_notice_row_and_monitor()
    test_logger_row_and_readers_ignore()
    test_real_accept_marks_group_unknown()
    test_real_accept_replay_pin_is_unconfirmed()
    test_advice_replay_stops_on_pin_error()
    test_engine_other_pinned_readers()
    test_real_accept_compare_pin_unconfirmed()
    print("ALL OK")


if __name__ == "__main__":
    main()
