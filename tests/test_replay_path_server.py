"""再生経路の結合テストのうち、実際の入口 (server._handle_one_frame) を動かす部分 (2026-10-21 レビュー、ローカル専用)。

server は socketio / fastapi を import するので CI の最小依存 (requirements-ci.txt) では読めない (scripts/run_test.sh の
LOCAL_ONLY_TESTS)。CI で回る部分 (構文木での確認・録画再生の経路・記録 → 再生) は tests/test_replay_path。

- (b) server.frame_advice_step・tools.shadow_load_check.frame_advice_step・advice_key.frame_advice_step が同一オブジェクト
- (a) fixture (本体の 10/20 の対戦ログの advice 行 12 件) の局面を、フレーム処理 (pipeline.process) だけ差し替えた
  server._handle_one_frame に 1 フレームずつ通すと、対戦ログに書く助言 (battle_log.on_advice の evaluated / t_eval) と表示
  (advice_update) が、録画再生の経路 (shadow_load_check.ReplayAdvice) で出した助言・採点したか・採点の時刻と同じ

差し替えるのは入出力だけ: フレーム処理 (fixture の局面を返す)・時計 (fixture の now)・送信 (sio.emit)・対戦ログ
(battle_log)・型推定のトラッカー (固定の推定)・候補の付加 (_attach_candidates、記録の局面に既に入っている)・状態の保存先。
助言の判断 (鍵・キャッシュ・有効期限・通し直し) は本物を通す。

    python -m tests.test_replay_path_server
"""
from __future__ import annotations

import asyncio
import base64
import copy
import tempfile
import time as _time
from pathlib import Path

import tests.test_replay_path as T


class _Clock:
    """server の time (time.time だけ差し替え、他は本物)"""

    def __init__(self):
        self.now = 0.0

    def time(self):
        return self.now

    def __getattr__(self, name):
        return getattr(_time, name)


class _Log:
    """battle_log の代わり (書かずに記録する)"""

    def __init__(self):
        self.advice = []

    def on_advice(self, advice, kind, state=None, evaluated=None, t_eval=None, ev_infer=None):
        self.advice.append({"advice": copy.deepcopy(advice), "kind": kind, "evaluated": evaluated, "t_eval": t_eval,
                            "ev_infer": copy.deepcopy(ev_infer)})
        return f"test-{len(self.advice)}"

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


class _SpreadTracker(T._FakeTracker):
    """server.spread_tracker の代わり (固定の推定。フレームからは学ばない)"""

    def on_frame(self, *a, **k):
        pass

    def reset(self):
        pass


class _Pipe:
    def __init__(self):
        self.next = None
        self.rescue_stats = {"stashed": 0, "ocr": 0, "events": 0}
        self.resolver = T.resolver

    def process(self, img):
        st = copy.deepcopy(self.next)
        st.setdefault("events", [])          # VisionPipeline の state には常にある欄 (記録の局面には無い)
        return st, []


def _jpeg_data_url() -> str:
    import cv2
    import numpy as np
    ok, buf = cv2.imencode(".jpg", np.zeros((8, 8, 3), np.uint8))
    assert ok
    return "data:image/jpeg;base64," + base64.b64encode(buf).decode()


def test_server_entry_matches_replay():
    import advice_key as K
    import server
    from tools import shadow_load_check as SLC
    # (b) 同一オブジェクト
    assert server.frame_advice_step is K.frame_advice_step is SLC.frame_advice_step
    inputs = T._inputs()
    tracker = _SpreadTracker({sid: T.GUESS for sid in T._opp_species(inputs)})
    emitted = []

    async def emit(event, data, room=None):
        if event == "advice_update" and data.get("kind") != "selection":
            emitted.append(copy.deepcopy(data))

    saved = {k: getattr(server, k) for k in ("pipeline", "battle_log", "sio", "time", "spread_tracker", "_attach_candidates",
                                             "SNAPSHOT_PATH", "_advice_cache", "_data_versions", "_advice_recheck", "advisor",
                                             "_shadow")}
    clock, log, pipe = _Clock(), _Log(), _Pipe()
    sio_stub = type("S", (), {"emit": staticmethod(emit)})()
    data = _jpeg_data_url()
    n_emit_per_frame = []
    with tempfile.TemporaryDirectory() as td, T._Fixed(), T._settings(), T._Tracker(tracker):
        try:
            server.pipeline, server.battle_log, server.sio, server.time = pipe, log, sio_stub, clock
            server.spread_tracker, server._attach_candidates = tracker, (lambda st: None)
            server.SNAPSHOT_PATH = Path(td) / "state_snapshot.json"
            server._advice_cache, server._data_versions, server._advice_recheck = K.AdviceCache(), T._StaticVersions(), False
            server.advisor, server._shadow = T.Advisor(resolver=T.resolver), None
            loop = asyncio.new_event_loop()
            for st, now in inputs:
                pipe.next, clock.now = st, now
                before = len(log.advice)
                loop.run_until_complete(server._handle_one_frame("s", data))
                n_emit_per_frame.append(len(log.advice) - before)
            loop.close()
        finally:
            for k, v in saved.items():
                setattr(server, k, v)
        ra, shd = T._shadow(inputs, tracker)
    # 録画再生の経路で出した助言と、server の入口が書いた助言が 1 対 1 で同じ
    want = [r for r in shd if r["advice"] is not None]
    assert n_emit_per_frame == [1 if r["advice"] is not None else 0 for r in shd], n_emit_per_frame
    assert len(log.advice) == len(want) == len(emitted), (len(log.advice), len(want), len(emitted))
    for got, w, shown in zip(log.advice, want, emitted):
        assert got["kind"] == "battle"
        assert (got["evaluated"], got["t_eval"]) == (w["evaluated"], w["t_eval"]), (got["evaluated"], got["t_eval"], w["reason"])
        assert T._scores(got["advice"]) == T._scores(w["advice"]) and T._top(got["advice"]) == T._top(w["advice"])
        assert got["ev_infer"] == w["inputs"]
        assert T._scores(shown) == T._scores(w["advice"])
    cols = [(r["reason"], r["evaluated"]) for r in shd]
    print(f"  server の入口 {len(inputs)} フレーム → 助言 {len(log.advice)} 件 (採点 {sum(1 for g in log.advice if g['evaluated'])})、"
          f"録画再生の列 {cols}")
    print("test_server_entry_matches_replay OK")


def main():
    test_server_entry_matches_replay()
    print("ALL OK")


if __name__ == "__main__":
    main()
