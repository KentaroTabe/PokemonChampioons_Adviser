"""固定入力の異常の伝え方のうち、実際の入口 (server._handle_one_frame) を動かす部分 (2026-10-31 レビュー E-inputs-3、ローカル専用)。

server は socketio / fastapi を import するので CI の最小依存では読めない (scripts/run_test.sh の LOCAL_ONLY_TESTS)。CI で回る部分
(engine・advice_key の判断・real_accept・advice_replay) は tests/test_inputs_pin_propagation。

- 採点 (engine の型推定の参照) が InputsPinError: 助言欄は停止の文言 (1 行目 config.INPUTS_PIN_STOP_TEXT) と error、advice 行は
  書かない、キャッシュに入らない、対戦ログに異常の行が 1 行 (2 フレーム目は重複して書かない、停止の表示も送り直さない)、
  認識処理 (pipeline.process・トラッカーの観測) は毎フレーム続き、状態の配信 (state_update) も止まらない。異常が解けたら通常の助言に戻る
- 型推定のトラッカーのフレームの観測 (on_frame) が InputsPinError: 同じく停止 (出どころ tracker)。採点はしない

差し替えるのは入出力だけ (tests.test_replay_path_server と同じ): フレーム処理・時計・送信・対戦ログ・トラッカー・候補の付加・
状態の保存先。助言の判断 (frame_advice_step・engine) は本物を通す。

    python -m tests.test_inputs_pin_propagation_server
"""
from __future__ import annotations

import asyncio
import copy
import os
import tempfile
from pathlib import Path

os.environ.setdefault("RL_BLEND_WEIGHT", "0")

import advice_key as K
from advisor import data_pin as DP
from champions_agent import config as C
from tests.test_inputs_pin_propagation import PIN_MSG
from tests.test_replay_path import _StaticVersions
from tests.test_replay_path_server import _Clock, _jpeg_data_url
from tests.test_scoring_transition import _Hint, _state_171, resolver


class _Log:
    """battle_log の代わり (書かずに記録する)"""

    def __init__(self):
        self.advice, self.pin = [], []

    def on_advice(self, advice, kind, state=None, evaluated=None, t_eval=None, ev_infer=None):
        self.advice.append(copy.deepcopy(advice))
        return f"test-{len(self.advice)}"

    def on_inputs_pin(self, row):
        self.pin.append(copy.deepcopy(row))

    def on_frame(self, *a, **k):
        pass

    def pop_revision(self):
        return None

    def outcome_info(self):
        return None

    def on_frame_burst(self, *a, **k):
        pass

    def on_selection_record(self, *a, **k):
        pass


class _Tracker:
    """server.spread_tracker の代わり。mode: None = 推定なし、"best_for" = 採点の参照で異常、"on_frame" = フレームの観測で異常"""

    def __init__(self):
        self.mode = None
        self.n_frames = 0

    def on_frame(self, *a, **k):
        self.n_frames += 1
        if self.mode == "on_frame":
            raise DP.InputsPinError(PIN_MSG)

    def best_for(self, sid):
        if self.mode == "best_for":
            raise DP.InputsPinError(PIN_MSG)
        return None

    def hypotheses_for(self, *a, **k):
        return []

    def reset(self):
        pass


class _Pipe:
    def __init__(self):
        self.n = 0
        self.rescue_stats = {"stashed": 0, "ocr": 0, "events": 0}
        self.resolver = resolver

    def process(self, img):
        self.n += 1
        st = _state_171()
        st.update(scene="command", battle_seq=3, events=[], turn=5)
        return st, []


def _run(mode_seq: list) -> dict:
    """mode_seq の各モードで 1 フレームずつ server._handle_one_frame を通す。戻り値は記録"""
    import advisor.ev_infer as EVI
    import server
    from advisor.service import Advisor
    tracker = _Tracker()
    emitted = []

    async def emit(event, data, room=None):
        emitted.append((event, copy.deepcopy(data)))

    keys = ("pipeline", "battle_log", "sio", "time", "spread_tracker", "_attach_candidates", "SNAPSHOT_PATH", "_advice_cache",
            "_data_versions", "_advice_recheck", "advisor", "_shadow", "_pin_monitor", "_pin_notice_shown",
            "_last_state_json")
    saved = {k: getattr(server, k) for k in keys}
    orig_get = EVI.get_tracker
    clock, log, pipe = _Clock(), _Log(), _Pipe()
    sio_stub = type("S", (), {"emit": staticmethod(emit)})()
    data = _jpeg_data_url()
    per_frame = []
    with tempfile.TemporaryDirectory() as td, _Hint():
        try:
            EVI.get_tracker = lambda: tracker        # engine は呼び出しのたびに読む
            server.pipeline, server.battle_log, server.sio, server.time = pipe, log, sio_stub, clock
            server.spread_tracker, server._attach_candidates = tracker, (lambda st: None)
            server.SNAPSHOT_PATH = Path(td) / "state_snapshot.json"
            server._advice_cache, server._data_versions, server._advice_recheck = K.AdviceCache(), _StaticVersions(), False
            server.advisor, server._shadow = Advisor(resolver=resolver), None
            server._pin_monitor, server._pin_notice_shown, server._last_state_json = K.InputsPinMonitor(), None, ""
            loop = asyncio.new_event_loop()
            for i, mode in enumerate(mode_seq):
                tracker.mode = mode
                clock.now = 1000.0 + i * 0.5
                n_emit = len(emitted)
                loop.run_until_complete(server._handle_one_frame("s", data))
                per_frame.append({"mode": mode, "emitted": emitted[n_emit:], "n_advice": len(log.advice), "n_pin": len(log.pin),
                                  "cache": server._advice_cache.result is not None})
            loop.close()
        finally:
            EVI.get_tracker = orig_get
            for k, v in saved.items():
                setattr(server, k, v)
    return {"frames": per_frame, "log": log, "pipe": pipe, "tracker": tracker}


def _advice_updates(fr):
    return [d for e, d in fr["emitted"] if e == "advice_update"]


def _check_stop(out: dict, source: str) -> None:
    f1, f2, f3 = out["frames"]
    # 1 フレーム目: 停止の表示、advice 行なし、キャッシュなし、異常の行 1 行
    up = _advice_updates(f1)
    assert len(up) == 1, f1["emitted"]
    assert up[0]["text"].split("\n")[0] == C.INPUTS_PIN_STOP_TEXT and PIN_MSG in up[0]["text"], up[0]
    assert up[0]["reason"] == C.INPUTS_PIN_REASON and not up[0].get("best") and not up[0].get("advice_id")
    assert f1["n_advice"] == 0 and not f1["cache"] and f1["n_pin"] == 1
    row = out["log"].pin[0]
    assert row["type"] == C.INPUTS_PIN_LOG_TYPE and row["source"] == source and row["error"] == PIN_MSG and row["battle_seq"] == 3
    # 2 フレーム目: 重複して記録しない・停止の表示も送り直さない・キャッシュなし
    assert f2["n_pin"] == 1 and f2["n_advice"] == 0 and not f2["cache"] and not _advice_updates(f2), f2["emitted"]
    # 認識処理は毎フレーム続く (状態の配信は状態が変わったときだけ = 1 フレーム目。異常で止まらない)
    assert any(e == "state_update" for e, _d in f1["emitted"]), f1["emitted"]
    assert out["pipe"].n == 3 and out["tracker"].n_frames == 3
    # 異常が解けたら通常の助言 (advice 行あり、キャッシュに入る)
    up3 = _advice_updates(f3)
    assert f3["n_advice"] == 1 and f3["cache"] and len(up3) == 1 and up3[0].get("ok") and up3[0].get("best"), f3["emitted"]
    assert not DP.is_pin_error_result(out["log"].advice[0]) and out["log"].advice[0].get("ok")


def test_server_stops_on_evaluate_pin_error():
    out = _run(["best_for", "best_for", None])
    _check_stop(out, "evaluate")
    print("test_server_stops_on_evaluate_pin_error OK")


def test_server_stops_on_tracker_pin_error():
    out = _run(["on_frame", "on_frame", None])
    _check_stop(out, "tracker")
    print("test_server_stops_on_tracker_pin_error OK")


def main():
    test_server_stops_on_evaluate_pin_error()
    test_server_stops_on_tracker_pin_error()
    print("ALL OK")


if __name__ == "__main__":
    main()
