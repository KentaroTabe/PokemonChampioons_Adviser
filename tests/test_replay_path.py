"""再生経路を本番と一致させる (2026-10-21 レビュー、計画書 §8.41) の結合テスト。

- (a) 本体の 10/20 の対戦ログ (tests/fixtures/replay_path/battle_20261020_085420_rows.jsonl = version 行と advice 行 12 件、
  版 2) の局面を順に、server と同じ手順 (frame_advice_step に AdviceCache・DataVersions・recheck を渡し、出した助言の recheck を
  次のフレームに渡す) と、録画再生 (tools.shadow_load_check.ReplayAdvice) に流し、(理由, 採点したか, 鍵が変わったか, 助言を
  出したか) の列と、出した助言の得点が同じになる。列には採点・キャッシュの両方と鍵の変化が含まれる
- (b) server._handle_one_frame が呼ぶ関数 (server.py の構文木で確かめる。server は CI の最小依存では import できない。
  実際に server の入口を動かす照合は tests/test_replay_path_server、ローカル専用) と、shadow_load_check の関数が
  advice_key.frame_advice_step と同一。旧い鍵の写し・10 秒の更新間隔・advise の直接呼び出しが残っていない
- (c) 本番と同じ手順で advice 行を書き (BattleLogger.on_advice に evaluated / t_eval / ev_infer)、tools.real_accept の再生
  (_engine_state_ex → replay_one) で読み直すと、now = t_eval (キャッシュを使った行も元の採点の時刻)、ev_infer が適用され、
  得点と第一候補が記録の advice と一致する (採点した行とキャッシュを使った行の両方)。型推定を入れないと得点が変わる
  (往復の比較が欠落を検出できること)
- (d) 退避 (scoring_fallback) の結果は短い有効期限 (ADVICE_RECALC_FALLBACK_TTL_SEC) を超えたら採点し直し、退避していない結果は
  通常の有効期限 (ADVICE_RECALC_TTL_SEC) までキャッシュを使う
- 純粋な部分: AdviceCache.recalc_reason、replay_now_source の順 (t_eval → t_now → t_gen → t)、replay_state、replay_clock、
  sensitivity_setting、step_counts、_Settings の復元

比較の間はモデル・型・DB を固定する (tests.test_state_schema_roundtrip._Fixed)。fixture は CI でも同じものを使う。

    python -m tests.test_replay_path
"""
from __future__ import annotations

import ast
import copy
import inspect
import json
import os
import tempfile
from pathlib import Path

os.environ.setdefault("RL_BLEND_WEIGHT", "0")

import advice_key as K
from advisor.service import Advisor
from battle_logger import BattleLogger, ev_infer_of
from champions_agent import config as C
from tools import real_accept as RA
from tools import shadow_load_check as SLC
from tools.advice_replay import NOW_SOURCES, replay_input_counts, replay_now, replay_now_source, replay_state
from tests.test_state_schema_roundtrip import _Fixed, resolver
from tests.test_version_split import GUESS, _doc, _FakeTracker, _Tracker, _Versions

REPO = Path(__file__).resolve().parent.parent
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "replay_path" / "battle_20261020_085420_rows.jsonl"
MODE = "transition"             # 10/20 の接続テストの採点方式 (version 行の scoring_mode)


class _StaticVersions:
    """DataVersions の代わり (データの版を固定する。server / 再生とも current() だけを読む)"""

    def current(self) -> dict:
        return {"team": "t", "dex": "d", "move_effects": "e", "usage_snapshot": None}


def _settings():
    """10/20 の接続テストの採点の設定 (方式 transition、感度 off、表示用の探索 off) を config に入れる (本番と同じく設定で渡す)"""
    return SLC._Settings(ADVICE_SCORING_MODE=MODE, TRANSITION_SENSITIVITY_ENABLED=False, ADVICE_DISPLAY_SEARCH="off")


def _fixture_rows() -> tuple:
    rows = [json.loads(x) for x in FIXTURE.read_text(encoding="utf-8").splitlines() if x.strip()]
    ver = next(r for r in rows if r["type"] == "version")
    adv = [r for r in rows if r["type"] == "advice" and r.get("kind") == "battle"]
    return ver, adv


def _inputs() -> list:
    """fixture の advice 行 → [(局面, now)] (real_accept の再生の入力。now = t_eval → t_now → t_gen → t)"""
    _ver, adv = _fixture_rows()
    out = []
    for r in adv:
        st, _info = RA._engine_state_ex(r, resolver)
        out.append((st, st["now"]))
    return out


def _opp_species(inputs) -> list:
    out = []
    for st, _now in inputs:
        sd = st["opponent"]
        m = sd["party"][sd["active_index"]]
        if m.get("species_id") not in out:
            out.append(m.get("species_id"))
    return out


def _scores(adv: dict) -> dict:
    return {f"{a.get('kind')}:{a.get('id')}": round(float(a.get("score") or 0.0), 6) for a in (adv or {}).get("actions") or []}


def _top(adv: dict):
    acts = (adv or {}).get("actions") or []
    if not acts:
        return None
    a = max(acts, key=lambda x: float(x.get("score") or 0.0))
    return a.get("kind"), a.get("id")


def _server_like(inputs, tracker, versions=None) -> list:
    """server._handle_one_frame の助言の部分と同じ手順 (frame_advice_step に server と同じ引数。出した助言の recheck を次へ渡す)"""
    advisor = Advisor(resolver=resolver)
    cache, vers, recheck = K.AdviceCache(), versions or _StaticVersions(), False
    out = []
    for st, now in inputs:
        state = copy.deepcopy(st)
        res = K.frame_advice_step(advisor, state, now, cache, vers, recheck, keep_common=False,
                                  inputs_fn=lambda: ev_infer_of(state, tracker.best_for))
        if res["advice"] is not None:
            recheck = res["recheck"]
        out.append((state, res))
    return out


def _shadow(inputs, tracker) -> tuple:
    """録画再生の経路 (tools.shadow_load_check.ReplayAdvice。simulate が executor で呼ぶのと同じ step)"""
    ra = SLC.ReplayAdvice(Advisor(resolver=resolver), keep_common=False, tracker=tracker, data_versions=_StaticVersions())
    res = [ra.step(copy.deepcopy(st), now) for st, now in inputs]
    return ra, res


def _cols(results) -> list:
    return [(r["reason"], r["evaluated"], r["key_changed"], r["advice"] is not None) for r in results]


# ------------------------------------------------------------------ 純粋な部分
def test_recalc_reason():
    c = K.AdviceCache(ttl=10.0, fallback_ttl=2.0)
    assert c.recalc_reason("k", 0.0) == "first" and c.needs_recalc("k", 0.0)
    c.store("k", {"ok": True}, 100.0)
    assert c.recalc_reason("k", 109.9) is None and not c.needs_recalc("k", 109.9)
    assert c.recalc_reason("k2", 100.1) == "key_changed"
    assert c.recalc_reason("k", 110.1) == "ttl"
    c.store("k", {"ok": True, "scoring_fallback": "opp_fainted"}, 100.0)
    assert c.recalc_reason("k", 101.9) is None and c.recalc_reason("k", 102.1) == "fallback_ttl"
    c.store("k", {"ok": False}, 100.0, error=True)
    assert c.recalc_reason("k", 102.1) == "fallback_ttl"
    assert set(K.FRAME_STEP_REASONS) >= {"first", "key_changed", "ttl", "fallback_ttl", "recheck", "cached_skip", "no_key"}
    print("test_recalc_reason OK")


def test_replay_now_source_order():
    full = {"t_eval": 1.0, "t_now": 2.0, "advice": {"t_gen": 3.0}, "t": 4.0}
    assert replay_now_source(full) == (1.0, "t_eval") and replay_now(full) == 1.0
    assert replay_now_source(dict(full, t_eval=None)) == (2.0, "t_now")
    assert replay_now_source({"t_now": None, "advice": {"t_gen": 3.0}, "t": 4.0}) == (3.0, "t_gen")
    assert replay_now_source({"t": 4.0}) == (4.0, "t") and replay_now_source({}) == (None, None)
    assert replay_now_source({"t_eval": True, "t": 4.0}) == (4.0, "t")
    assert NOW_SOURCES == ("t_eval", "t_now", "t_gen", "t")
    counts = replay_input_counts([{"now_source": "t_eval", "ev_infer_applied": True}, {"now_source": "t_now", "ev_infer_applied": False},
                                  {"now_source": "t_eval", "ev_infer_applied": True}])
    assert counts == {"n": 3, "now_source": {"t_eval": 2, "t_now": 1}, "ev_infer_applied": 2}, counts
    assert "t_eval 2" in RA.replay_inputs_line(counts) and "適用 2" in RA.replay_inputs_line(counts)
    assert RA.replay_inputs_line(None) == "- 再生の入力: 記録なし"
    print("test_replay_now_source_order OK")


def test_replay_state_applies_ev_infer():
    """replay_state: now = t_eval (無ければ t_now)、行に ev_infer があれば ev_infer_override。real_accept._engine_state も同じ"""
    _ver, adv = _fixture_rows()
    row = dict(adv[0], t_eval=adv[0]["t_now"] - 0.5, ev_infer={"mimikyu": {"nature": "jolly"}})
    st, info = replay_state(row, resolver)
    assert st["now"] == row["t_eval"] and info == {"now_source": "t_eval", "ev_infer_applied": True}
    assert st["ev_infer_override"] == {"mimikyu": {"nature": "jolly"}}
    st2, info2 = RA._engine_state_ex(adv[0], resolver)          # 10/20 08 時台の行 (t_eval / ev_infer の欄が無い)
    assert st2["now"] == adv[0]["t_now"] and info2 == {"now_source": "t_now", "ev_infer_applied": False}
    assert "ev_infer_override" not in st2 and st2["scene"] == adv[0]["state"]["scene"]
    assert RA._engine_state(row, resolver)["ev_infer_override"] == row["ev_infer"]
    print("test_replay_state_applies_ev_infer OK")


def test_load_check_pure():
    assert SLC.replay_clock(1000.0, 5.0, 5.4, 9e9) == 1000.4
    assert SLC.replay_clock(1000.0, 5.0, 4.0, 9e9) == 1000.0
    assert SLC.replay_clock(None, 5.0, 5.4, 123.0) == 123.0
    assert SLC.sensitivity_setting(None, None) is False and SLC.SENSITIVITY_DEFAULT == "off"
    assert SLC.sensitivity_setting("on", "0") is True and SLC.sensitivity_setting("off", "1") is False
    assert SLC.sensitivity_setting(None, "1") is True and SLC.sensitivity_setting(None, "0") is False
    assert SLC.sensitivity_setting(None, "  ") is False
    steps = [{"reason": "first", "evaluated": True, "emitted": True}, {"reason": "cached_skip", "evaluated": False, "emitted": False},
             {"reason": "recheck", "evaluated": False, "emitted": True}]
    assert SLC.step_counts(steps) == {"n_steps": 3, "n_emitted": 2, "n_evaluated": 1, "n_cached": 1,
                                      "reasons": {"first": 1, "cached_skip": 1, "recheck": 1}}
    s = SLC.run_summary([1.0], [1.0], [1.0], 2, 0, 3.0, steps=SLC.step_counts(steps))
    assert s["steps"]["n_evaluated"] == 1
    orig = (C.ADVICE_SCORING_MODE, C.TRANSITION_SENSITIVITY_ENABLED, C.ADVICE_DISPLAY_SEARCH)
    with SLC._Settings(ADVICE_SCORING_MODE="transition", TRANSITION_SENSITIVITY_ENABLED=False):
        assert C.ADVICE_SCORING_MODE == "transition" and C.TRANSITION_SENSITIVITY_ENABLED is False
        assert K.scoring_settings()["scoring_mode"] == "transition"
    assert (C.ADVICE_SCORING_MODE, C.TRANSITION_SENSITIVITY_ENABLED, C.ADVICE_DISPLAY_SEARCH) == orig
    print("test_load_check_pure OK")


# ------------------------------------------------------------------ (b) 同じ関数
def _func_node(tree, name):
    return next(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)


def _called_names(node) -> set:
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            out.add(f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None)
    return out


def test_same_function_server_and_replay():
    """(b) server._handle_one_frame と録画再生が advice_key.frame_advice_step を呼び、旧い経路 (鍵の写し・10 秒・advise の直接呼び出し)
    が残っていない"""
    tree = ast.parse((REPO / "server.py").read_text(encoding="utf-8"))
    imported = {(n.module, a.name) for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    assert ("advice_key", "frame_advice_step") in imported, sorted(x for x in imported if x[0] == "advice_key")
    names = _called_names(_func_node(tree, "_handle_one_frame"))
    assert "frame_advice_step" in names
    assert not names & {"advise_cached_ex", "advise_cached", "needs_recalc", "advice_input_key", "advise", "_advice_key"}, names
    # 録画再生 (shadow_load_check) も同じオブジェクト。旧い鍵の写しと更新間隔は無い
    assert SLC.frame_advice_step is K.frame_advice_step
    assert not hasattr(SLC, "advice_key") and not hasattr(SLC, "ADVICE_REFRESH_SEC")
    src = inspect.getsource(SLC)
    assert ".advise(" not in src and "scoring_mode=scoring_mode" not in src
    assert "frame_advice_step(" in inspect.getsource(SLC.ReplayAdvice.step)
    assert "ra.step" in inspect.getsource(SLC.simulate)
    print("test_same_function_server_and_replay OK")


# ------------------------------------------------------------------ (a) 同じ列
def test_fixture_columns_match():
    """(a) fixture の局面を server と同じ手順と録画再生に流すと、(理由, 採点したか, 鍵が変わったか, 出したか) の列と得点が同じ"""
    inputs = _inputs()
    tracker = _FakeTracker({sid: GUESS for sid in _opp_species(inputs)})
    with _Fixed(), _settings(), _Tracker(tracker):
        srv = _server_like(inputs, tracker)
        ra, shd = _shadow(inputs, tracker)
    a, b = _cols([r for _s, r in srv]), _cols(shd)
    print("  列 (理由, 採点, 鍵の変化, 出した):", a)
    assert a == b, list(zip(a, b))
    assert [x["reason"] for x in ra.steps] == [c[0] for c in a]
    for (_s, r1), r2 in zip(srv, shd):
        assert _scores(r1["advice"]) == _scores(r2["advice"]) and r1["t_eval"] == r2["t_eval"]
    # 列に採点・キャッシュの両方と鍵の変化が含まれる (検査の対象として足りる)
    assert any(c[1] for c in a), a
    assert any((c[3] and not c[1]) or c[0] == "cached_skip" for c in a), a
    assert any(c[2] for c in a), a
    assert any(c[0] == "fallback_ttl" for c in a), a          # 0117 → 0118 (2.1 秒、退避の結果) は短い有効期限で採点し直す
    assert SLC.step_counts(ra.steps)["n_evaluated"] == sum(1 for c in a if c[1])
    print("test_fixture_columns_match OK")


# ------------------------------------------------------------------ (c) 記録 → 再生
def _record(inputs, tracker, td: Path) -> list:
    """本番と同じ手順で助言を出し、BattleLogger.on_advice (server と同じ引数) で advice 行を書いて読み直す"""
    with _Versions(_doc(True), [True]):
        lg = BattleLogger(log_dir=td)
        for state, res in _server_like(inputs, tracker):
            if res["advice"] is None:
                continue
            lg.on_advice(res["advice"], "battle", state, evaluated=res["evaluated"], t_eval=res["t_eval"], ev_infer=res["inputs"])
        rows = [json.loads(x) for x in lg._file.read_text(encoding="utf-8").splitlines()]
    return [r for r in rows if r["type"] == "advice" and r.get("kind") == "battle"]


def test_record_replay_matches():
    """(c) 記録した行を real_accept の再生で読み直すと now = t_eval・ev_infer 適用・得点と第一候補が記録と一致する"""
    inputs = _inputs()
    # 同じ局面を続けて送る (本番の「同じ画面が続く」): 0.3 秒後の同じ局面はキャッシュを使う (確定前なら通し直す)
    doubled = []
    for st, now in inputs:
        doubled += [(st, now), (st, now + 0.3)]
    live = _FakeTracker({sid: GUESS for sid in _opp_species(inputs)})
    settings = {"transition_sensitivity": False, "display_search": False}
    with tempfile.TemporaryDirectory() as td, _Fixed(), _settings():
        with _Tracker(live):
            rows = _record(doubled, live, Path(td))
        n_eval = sum(1 for r in rows if r["evaluated"])
        n_cache = sum(1 for r in rows if r["evaluated"] is False)
        assert n_eval >= 5 and n_cache >= 1, (n_eval, n_cache)
        infos, lost_diff = [], 0
        with _Tracker(_FakeTracker({})):             # 再生はトラッカーを読まない (記録の ev_infer だけ)
            for r in rows:
                st, info = RA._engine_state_ex(r, resolver)
                infos.append(info)
                assert info == {"now_source": "t_eval", "ev_infer_applied": True}, info
                assert st["now"] == r["t_eval"] and st["ev_infer_override"] == r["ev_infer"]
                if not r["evaluated"]:
                    assert r["t_eval"] < r["t_now"], (r["t_eval"], r["t_now"])     # キャッシュの行は元の採点の時刻
                got = RA.replay_one(st, resolver, (MODE,), settings)[MODE]
                assert got[2] is None, got[2]
                want = r["advice"]
                assert _scores(got[0]) == _scores(want), (r["advice_id"], r["evaluated"], _scores(got[0]), _scores(want))
                assert _top(got[0]) == _top(want), (r["advice_id"], _top(got[0]), _top(want))
                # 型推定を入れない再生 (以前の real_accept) と比べる
                bare = copy.deepcopy(st)
                bare.pop("ev_infer_override", None)
                lost = RA.replay_one(bare, resolver, (MODE,), settings)[MODE][0]
                lost_diff += 1 if _scores(lost) != _scores(want) else 0
    counts = replay_input_counts(infos)
    print(f"  記録 {len(rows)} 行 (採点 {n_eval} / キャッシュ {n_cache})、再生の入力 {counts}、型推定なしで得点が変わる行 {lost_diff}")
    assert counts["now_source"] == {"t_eval": len(rows)} and counts["ev_infer_applied"] == len(rows)
    assert lost_diff >= 1, "型推定の有無で得点が変わらない (テストの局面が検出に使えない)"
    print("test_record_replay_matches OK")


# ------------------------------------------------------------------ (d) 退避の有効期限
class _Grain:
    """HP の経過時間の粒度を大きくする (有効期限だけを見るため、経過時間で鍵が変わらないようにする)"""

    def __enter__(self):
        self.orig = C.ADVICE_KEY_HP_AGE_GRAIN_SEC
        C.ADVICE_KEY_HP_AGE_GRAIN_SEC = 1e9
        return self

    def __exit__(self, *a):
        C.ADVICE_KEY_HP_AGE_GRAIN_SEC = self.orig


def _ttl_run(st, now0: float, offsets: tuple) -> list:
    advisor = Advisor(resolver=resolver)
    cache, vers, recheck = K.AdviceCache(), _StaticVersions(), False
    out = []
    for dt in offsets:
        res = K.frame_advice_step(advisor, copy.deepcopy(st), now0 + dt, cache, vers, recheck)
        if res["advice"] is not None:
            recheck = res["recheck"]
        out.append(res)
    return out


def test_fallback_short_ttl():
    """(d) 退避した結果は ADVICE_RECALC_FALLBACK_TTL_SEC を超えたら採点し直す。退避していない結果は ADVICE_RECALC_TTL_SEC まで使う"""
    fb_ttl, ttl = C.ADVICE_RECALC_FALLBACK_TTL_SEC, C.ADVICE_RECALC_TTL_SEC
    assert fb_ttl < ttl
    inputs = _inputs()
    with _Fixed(), _settings(), _Grain(), _Tracker(_FakeTracker({})):
        fb = nf = None                                         # 局面を探す: 退避した局面・していない局面
        for st, now in inputs:
            r = _ttl_run(st, now, (0.0,))[0]["advice"]
            if r.get("scoring_fallback") and fb is None:
                fb = (st, now, r.get("scoring_fallback"))
            if r.get("ok") and not r.get("scoring_fallback") and nf is None:
                nf = (st, now)
        assert fb is not None and nf is not None, (fb, nf)
        st, now, why = fb
        seq = _ttl_run(st, now, (0.0, fb_ttl - 0.1, fb_ttl + 0.1))
        assert seq[0]["evaluated"] and seq[0]["advice"].get("scoring_fallback") == why
        assert not seq[1]["evaluated"] and seq[1]["reason"] in ("cached_skip", "recheck"), seq[1]["reason"]
        assert seq[2]["evaluated"] and seq[2]["reason"] == "fallback_ttl" and seq[2]["t_eval"] == now + fb_ttl + 0.1
        st, now = nf
        seq = _ttl_run(st, now, (0.0, fb_ttl + 0.1, ttl - 0.1, ttl + 0.1))
        assert seq[0]["evaluated"] and not seq[0]["advice"].get("scoring_fallback")
        assert [s["evaluated"] for s in seq[1:3]] == [False, False], [s["reason"] for s in seq]
        assert seq[3]["evaluated"] and seq[3]["reason"] == "ttl"
    print(f"  退避の局面 ({why}) は {fb_ttl:g} 秒、退避していない局面は {ttl:g} 秒で採点し直す")
    print("test_fallback_short_ttl OK")


def test_no_key_and_skip():
    """自分の場の個体が無い局面は助言を出さない (no_key)。キャッシュが使えて通し直しも要らなければ出さない (cached_skip)"""
    calls = []

    class _Adv:
        resolver, _last_best, _stab_hist, last_common = None, None, None, None

        @staticmethod
        def suggest_check(state):
            return ""

    import advisor.engine as E
    orig = E.evaluate

    def fake(state, resolver=None, **kw):
        calls.append(state["now"])
        return {"ok": True, "actions": [{"kind": "move", "id": "surf", "score": 1.0}], "best": {"kind": "move", "id": "surf", "score": 1.0}}
    E.evaluate = fake
    try:
        cache, vers = K.AdviceCache(), _StaticVersions()
        st = {"scene": "command", "turn": 1, "player": {"active_index": 0, "party": [{"species_id": "a"}]},
              "opponent": {"active_index": 0, "party": [{"species_id": "b"}]}}
        r0 = K.frame_advice_step(_Adv(), {"player": {"active_index": None, "party": []}}, 1.0, cache, vers)
        assert r0["reason"] == "no_key" and r0["advice"] is None and not calls
        r1 = K.frame_advice_step(_Adv(), copy.deepcopy(st), 1.0, cache, vers)
        assert r1["reason"] == "first" and r1["evaluated"] and calls == [1.0]
        r2 = K.frame_advice_step(_Adv(), copy.deepcopy(st), 1.5, cache, vers, recheck=r1["recheck"])
        assert r2["evaluated"] is False and r2["reason"] in ("recheck", "cached_skip")
        r3 = K.frame_advice_step(_Adv(), copy.deepcopy(st), 2.0, cache, vers, recheck=False)
        assert r3["reason"] == "cached_skip" and r3["advice"] is None and r3["t_eval"] is None
        r4 = K.frame_advice_step(_Adv(), copy.deepcopy(st), 2.5, cache, vers, recheck=True)
        assert r4["reason"] == "recheck" and r4["advice"] is not None and r4["t_eval"] == 1.0 and not r4["evaluated"]
        st2 = dict(copy.deepcopy(st), turn=2)
        r5 = K.frame_advice_step(_Adv(), st2, 3.0, cache, vers)
        assert r5["reason"] == "key_changed" and r5["key_changed"] and st2["now"] == 3.0 and calls == [1.0, 3.0]
    finally:
        E.evaluate = orig
    print("test_no_key_and_skip OK")


def main():
    test_recalc_reason()
    test_replay_now_source_order()
    test_replay_state_applies_ev_infer()
    test_load_check_pure()
    test_same_function_server_and_replay()
    test_no_key_and_skip()
    test_fixture_columns_match()
    test_record_replay_matches()
    test_fallback_short_ttl()
    print("ALL OK")


if __name__ == "__main__":
    main()
