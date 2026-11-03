"""表示用の探索 (advisor.engine._run_search) の切り替えのテスト (2026-10-16 レビュー、計画書 §8.29)。

    python -m tests.test_display_search_gate

- 設定の解決 (純粋): config.display_search_decision / resolve_display_search (既定 off、探索の重み > 0 なら省略を認めない)
- evaluate: off で _run_search を呼ばない・gtheory = None・search_skipped = True、on で呼ぶ、SEARCH_BLEND > 0 なら off でも呼ぶ
- 表示 (advisor.service.Advisor.format_advice): 探索を省略した助言に「択評価」「起点注意」の行が出ない
- battle_logger: battle の advice 行に search_skipped、version 行に display_search
- 同じ入力で探索 on / off の候補・得点・順位・第一候補の合法性が一致する (合成局面 + 実戦ログの決定点)。
  実戦ログは logs/battles (環境変数 REAL_BATTLE_LOG_DIR で差し替え) の 2026-10-15 のログ。無ければその部分だけ SKIP (CI)
- tools.real_accept --compare-search の純粋な部分 (rank_signature / compare_search_pair / summarize_search_compare / 表)
RL のモデルは合成局面では使わない (tests.test_advice_variant の固定の分布)。transition の計算予算は時間に依存しない値に広げて比べる
(予算超過の退避は実行時間で決まり、探索の有無と関係なく揺れるため。予算の扱いの比較は tools.real_accept --compare-search で見る)。
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path

import advisor.engine as engine
from champions_agent import config as C
from tools import real_accept as RA
from tests.test_advice_variant import _FakePolicy, _fixtures
from vision.scenes import SCENE_COMMAND

REPO = Path(__file__).resolve().parent.parent
REAL_LOG_GLOB = "battle_20261015_*.jsonl"
REAL_MIN_DECISIONS = 5


class _CountSearch:
    """engine._run_search を数える (中身は元の関数を呼ぶ)"""

    def __enter__(self):
        self.n = 0
        self.orig = engine._run_search

        def counted(*a, **kw):
            self.n += 1
            return self.orig(*a, **kw)

        engine._run_search = counted
        return self

    def __exit__(self, *exc):
        engine._run_search = self.orig
        return False


class _Setting:
    """config.ADVICE_DISPLAY_SEARCH と engine.SEARCH_BLEND を一時的に変える"""

    def __init__(self, display=None, blend=None):
        self.display, self.blend = display, blend

    def __enter__(self):
        self.saved = (C.ADVICE_DISPLAY_SEARCH, engine.SEARCH_BLEND, C._DISPLAY_SEARCH_FORCED_WARNED)
        if self.display is not None:
            C.ADVICE_DISPLAY_SEARCH = self.display
        if self.blend is not None:
            engine.SEARCH_BLEND = self.blend
        return self

    def __exit__(self, *exc):
        C.ADVICE_DISPLAY_SEARCH, engine.SEARCH_BLEND, C._DISPLAY_SEARCH_FORCED_WARNED = self.saved
        return False


class _BigBudget:
    """transition の計算予算を時間に依存しない値にする (予算超過の退避を起こさない)"""

    def __enter__(self):
        self.saved = C.TRANSITION_BUDGET_MS
        C.TRANSITION_BUDGET_MS = C.DIAG_SCENES_BIG_BUDGET_MS
        return self

    def __exit__(self, *exc):
        C.TRANSITION_BUDGET_MS = self.saved
        return False


def _state():
    return dict(_fixtures())["pivot_fast"]


# ------------------------------------------------------------------ 設定の解決 (純粋)
def test_decision_pure():
    f = C.display_search_decision
    assert f("off", None, 0.0) == (False, False)
    assert f("on", None, 0.0) == (True, False)
    assert f("off", True, 0.0) == (True, False)
    assert f("on", False, 0.0) == (False, False)
    # 探索を採点に混ぜる設定では省略を認めない (指定にかかわらず実行、強制の印)
    assert f("off", None, 0.5) == (True, True)
    assert f("on", False, 0.5) == (True, True)
    assert f("on", None, 0.5) == (True, False)
    assert f("", None, 0.0) == (False, False)
    print("test_decision_pure OK")


def test_config_defaults():
    """既定は off、探索の重みは 0.0 (engine.py から移しただけで値は同じ)。読めない値は既定"""
    assert C.ADVICE_DISPLAY_SEARCH_DEFAULT == "off" and C.ADVICE_SEARCH_BLEND == 0.0
    assert engine.SEARCH_BLEND == C.ADVICE_SEARCH_BLEND
    prev = os.environ.get(C.ADVICE_DISPLAY_SEARCH_ENV)
    try:
        for raw, want in ((None, "off"), ("", "off"), ("on", "on"), ("ON", "on"), ("off", "off"), ("yes", "off")):
            if raw is None:
                os.environ.pop(C.ADVICE_DISPLAY_SEARCH_ENV, None)
            else:
                os.environ[C.ADVICE_DISPLAY_SEARCH_ENV] = raw
            assert C._advice_display_search_from_env() == want, (raw, want)
    finally:
        if prev is None:
            os.environ.pop(C.ADVICE_DISPLAY_SEARCH_ENV, None)
        else:
            os.environ[C.ADVICE_DISPLAY_SEARCH_ENV] = prev
    with _Setting(display="off", blend=0.0):
        assert C.resolve_display_search() is False and engine.resolve_display_search() is False
        assert engine.resolve_display_search(True) is True
    with _Setting(display="on", blend=0.0):
        assert engine.resolve_display_search() is True and engine.resolve_display_search(False) is False
    with _Setting(display="off", blend=0.3):
        C._DISPLAY_SEARCH_FORCED_WARNED = False
        assert engine.resolve_display_search() is True and C._DISPLAY_SEARCH_FORCED_WARNED is True
        assert engine.resolve_display_search(False) is True
    print("test_config_defaults OK")


# ------------------------------------------------------------------ evaluate の切り替え
def test_off_skips_search():
    with _FakePolicy(), _Setting(display="off", blend=0.0), _CountSearch() as cs:
        adv = engine.evaluate(copy.deepcopy(_state()))
        assert cs.n == 0, cs.n
        assert adv["ok"] and adv["gtheory"] is None and adv["search_skipped"] is True, adv.get("search_skipped")
        common = engine.evaluate_common(copy.deepcopy(_state()))
        assert cs.n == 0 and common["gtheory"] is None and common["search_skipped"] is True
        adv2 = engine.evaluate(copy.deepcopy(_state()), display_search=False)
        assert cs.n == 0 and adv2["search_skipped"] is True
    print("test_off_skips_search OK")


def test_on_runs_search():
    with _FakePolicy(), _Setting(display="off", blend=0.0), _CountSearch() as cs:
        adv = engine.evaluate(copy.deepcopy(_state()), display_search=True)
        assert cs.n == 1, cs.n
        assert adv["search_skipped"] is False and adv["gtheory"] and adv["gtheory"].get("summary_lines")
    with _FakePolicy(), _Setting(display="on", blend=0.0), _CountSearch() as cs:
        adv = engine.evaluate(copy.deepcopy(_state()))
        assert cs.n == 1 and adv["search_skipped"] is False and adv["gtheory"]
    print("test_on_runs_search OK")


def test_blend_forces_search():
    """探索を採点に混ぜる設定 (SEARCH_BLEND > 0) では、off でも display_search=False でも探索する"""
    with _FakePolicy(), _Setting(display="off", blend=0.5), _CountSearch() as cs:
        adv = engine.evaluate(copy.deepcopy(_state()))
        assert cs.n == 1 and adv["search_skipped"] is False and adv["gtheory"]
        adv = engine.evaluate(copy.deepcopy(_state()), display_search=False)
        assert cs.n == 2 and adv["search_skipped"] is False
    print("test_blend_forces_search OK")


# ------------------------------------------------------------------ 表示
def test_service_text_without_search():
    from advisor.service import Advisor
    a = Advisor()
    with _FakePolicy(), _Setting(display="off", blend=0.0):
        adv = a.advise(copy.deepcopy(_state()))
        assert adv.get("ok") and adv["search_skipped"] is True and adv["gtheory"] is None
        adv = dict(adv, provisional=False)          # 安定化ゲートの確定前の表示ではなく、確定した助言の表示を見る
        txt = a.format_advice(adv)
        assert "◎ 推奨" in txt and "択評価" not in txt and "起点" not in txt, txt
    # 対照: 探索の結果 (起点の印つき) があれば両方の行が出る (既存の分岐)
    adv2 = dict(adv, gtheory={"summary_lines": ["とんぼがえり: 期待+0.10 保証-0.20 (最悪応手: つるぎのまい)"],
                              "setup_bait": [{"my": "とんぼがえり", "opp": "つるぎのまい"}], "actions": []},
                search_skipped=False)
    txt2 = a.format_advice(adv2)
    assert "択評価" in txt2 and "起点注意" in txt2, txt2
    print("test_service_text_without_search OK")


# ------------------------------------------------------------------ 記録
def test_logger_search_skipped():
    import battle_logger as BL
    assert BL.search_skipped_of({"ok": True, "search_skipped": True}) is True
    assert BL.search_skipped_of({"ok": True, "search_skipped": False}) is False
    assert BL.search_skipped_of({"ok": False, "reason": "x"}) is None
    assert BL.search_skipped_of(None) is None
    with tempfile.TemporaryDirectory() as td, _Setting(display="off"):
        lg = BL.BattleLogger(log_dir=Path(td))
        st = {"scene": SCENE_COMMAND, "turn": 2, "field": {}, "player": {"active_index": 0, "party": [{"species_id": "scizor"}]},
              "opponent": {"active_index": 0, "party": []}}
        lg.on_advice({"ok": True, "scoring_mode": "legacy", "search_skipped": True, "gtheory": None,
                      "best": {"kind": "move", "id": "uturn"}, "actions": []}, "battle", st)
        lg.on_advice({"ok": True, "scoring_mode": "legacy", "search_skipped": False, "gtheory": {"actions": []},
                      "best": {"kind": "move", "id": "uturn"}, "actions": []}, "battle", st)
        lg.on_advice({"ok": False, "reason": "x"}, "battle", st)
        lg.on_advice({"ok": True, "recommend": []}, "selection", st)
        rows = [json.loads(x) for x in lg._file.read_text(encoding="utf-8").splitlines()]
        ver = [r for r in rows if r["type"] == "version"][0]
        assert ver["display_search"] == "off", ver
        advs = [r for r in rows if r["type"] == "advice"]
        assert [r.get("search_skipped", "なし") for r in advs[:3]] == [True, False, None], advs
        assert all("search_skipped" in r for r in advs[:3])          # battle の行には必ず書く
        assert "search_skipped" not in advs[3]                       # 選出の助言には書かない
    print("test_logger_search_skipped OK")


# ------------------------------------------------------------------ on / off の一致
def _sig_pair(state, resolver, mode, ck=None, compact=None, move_locks=None, reg=()):
    out = {}
    for st in RA.SEARCH_STATES:
        adv = engine.evaluate(copy.deepcopy(state), resolver, scoring_mode=mode, display_search=(st == "on"))
        x = {"sig": RA.rank_signature(adv), "violations": [], "checks": {}, "error": None,
             "search_skipped": adv.get("search_skipped")}
        if ck is not None and adv.get("ok"):
            chk = RA.check_best(adv, compact, move_locks, reg, detail=True, **ck)
            x["violations"], x["checks"] = chk["violations"], chk["checks"]
        out[st] = x
    return out


def test_on_off_same_ranking_synthetic():
    from vision.normalize import NameResolver
    res = NameResolver()
    n = 0
    with _FakePolicy(), _Setting(blend=0.0), _BigBudget():
        for name, st in _fixtures():
            for mode in RA.MODES:
                p = _sig_pair(st, res, mode)
                assert p["off"]["search_skipped"] is True and p["on"]["search_skipped"] is False, name
                assert p["on"]["sig"]["actions"], (name, mode)
                assert RA.compare_search_pair(p["on"], p["off"]) == [], (name, mode, p)
                assert p["on"]["sig"] == p["off"]["sig"], (name, mode)
                n += 1
    print(f"test_on_off_same_ranking_synthetic OK ({n} 組)")


def _real_logs() -> list:
    d = Path(os.environ.get("REAL_BATTLE_LOG_DIR") or (REPO / "logs" / "battles"))
    return sorted(d.glob(REAL_LOG_GLOB)) if d.is_dir() else []


def test_on_off_same_ranking_real_logs():
    """本体の 2026-10-15 の実戦ログの決定点 (tools.real_accept と同じ間引き・復元) で、両方式とも on / off が一致する"""
    paths = _real_logs()
    if not paths:
        print("test_on_off_same_ranking_real_logs SKIP (2026-10-15 の実戦ログなし)")
        return
    from vision.normalize import NameResolver
    res = NameResolver()
    ck = RA._checker_kwargs()
    n_dec = 0
    with _Setting(blend=0.0), _BigBudget():
        for path in paths:
            rows = RA.thin_by_gap(RA.battle_advice_rows(RA.load_records(path)), RA.DECISION_GAP_SEC)
            for row in rows:
                try:
                    st = RA._engine_state(row, res)
                except Exception:
                    continue
                reg = RA._registered_ids(st, res)
                for mode in RA.MODES:
                    p = _sig_pair(st, res, mode, ck, row["state"], row.get("move_locks"), reg)
                    assert p["off"]["search_skipped"] is True, (path.name, row.get("advice_id"))
                    diffs = RA.compare_search_pair(p["on"], p["off"])
                    assert diffs == [], (path.name, row.get("advice_id"), mode, diffs,
                                         p["on"]["sig"]["actions"][:3], p["off"]["sig"]["actions"][:3])
                n_dec += 1
    assert n_dec >= REAL_MIN_DECISIONS, n_dec
    print(f"test_on_off_same_ranking_real_logs OK ({len(paths)} 本、{n_dec} 決定点 × {len(RA.MODES)} 方式)")


# ------------------------------------------------------------------ real_accept --compare-search の純粋な部分
def _x(acts, best=None, ms=100.0, skipped=None, fb=None, viol=(), checks=None, err=None, ok=True, used="legacy"):
    sig = None if err else {"ok": ok, "actions": [tuple(a) for a in acts], "best": best or ((acts[0][0], acts[0][1]) if acts else None),
                             "scoring_used": used, "fallback": fb}
    return {"elapsed_ms": ms, "search_skipped": skipped, "sig": sig, "violations": list(viol), "checks": checks or {},
            "error": err}


def test_compare_pair_pure():
    A = [("move", "uturn", "とんぼがえり", 50.0), ("switch", "garchomp", "ガブリアス", 45.0)]
    assert RA.rank_signature(None) is None
    sig = RA.rank_signature({"ok": True, "scoring_mode": "legacy", "best": {"kind": "move", "id": "uturn"},
                             "actions": [{"kind": "move", "id": "uturn", "name": "とんぼがえり", "score": 50.0, "reason": "r"}]})
    assert sig == {"ok": True, "actions": [("move", "uturn", "とんぼがえり", 50.0)], "best": ("move", "uturn"),
                   "scoring_used": "legacy", "fallback": None}
    f = RA.compare_search_pair
    assert f(_x(A), _x(A)) == []
    assert f(_x(A), _x([A[0], ("switch", "garchomp", "ガブリアス", 45.1)])) == ["scores"]
    assert f(_x(A), _x([A[1], A[0]], best=("move", "uturn"))) == ["order"]
    assert f(_x(A), _x([A[1], A[0]])) == ["order", "best"]
    assert f(_x(A), _x(A[:1])) == ["candidates", "order"]
    assert f(_x(A), _x(A, viol=["move_pp0"])) == ["legality"]
    assert f(_x(A, checks={"best": "ok"}), _x(A, checks={"best": "unknown"})) == ["legality"]
    assert f(_x(A, used="transition"), _x(A, used="legacy", fb="budget")) == ["fallback"]
    assert f(_x(A), _x(A, ok=False)) == ["ok"]
    assert f(_x(A), _x(A, err="E")) == ["error"]
    assert f(_x(A, err="E"), _x(A, err="E")) == []
    print("test_compare_pair_pure OK")


def test_summarize_and_render_pure():
    A = [("move", "uturn", "とんぼがえり", 50.0), ("switch", "garchomp", "ガブリアス", 45.0)]
    B = [A[1], A[0]]

    def row(i, tr_off_acts=A, tr_off_fb=None):
        m_l = {"on": _x(A, ms=500.0, skipped=False), "off": _x(A, ms=10.0, skipped=True)}
        m_t = {"on": _x(A, ms=600.0, skipped=False, used="transition"),
               "off": _x(tr_off_acts, ms=100.0, skipped=True, used="legacy" if tr_off_fb else "transition", fb=tr_off_fb)}
        for m in (m_l, m_t):
            m["diffs"] = RA.compare_search_pair(m["on"], m["off"])
        return {"log": "a.jsonl", "advice_id": f"a-{i}", "turn": i, "stale": [], "modes": {"legacy": m_l, "transition": m_t}}

    ok_rows = [row(i) for i in range(4)]
    s = RA.summarize_search_compare(ok_rows)
    assert s["status"] == RA.OK and s["n_mismatch"] == 0 and s["n"] == 4
    lg = s["modes"]["legacy"]
    assert lg["status"] == RA.OK and lg["n"] == 4 and lg["n_search_skipped"] == {"on": 0, "off": 4}
    assert lg["elapsed_ms"]["on"]["p50"] == 500.0 and lg["elapsed_ms"]["off"]["p95"] == 10.0
    assert lg["elapsed_delta_ms"]["p50"] == -490.0 and lg["fallback"] is None
    tf = s["modes"]["transition"]["fallback"]
    assert tf["on"] == {"n": 4, "n_fallback": 0, "n_budget": 0, "rate": 0.0, "budget_rate": 0.0}
    bad = ok_rows + [row(9, tr_off_acts=B, tr_off_fb="budget")]
    s2 = RA.summarize_search_compare(bad)
    assert s2["status"] == RA.NG and s2["n_mismatch"] == 1 and s2["modes"]["legacy"]["status"] == RA.OK
    t2 = s2["modes"]["transition"]
    assert t2["status"] == RA.NG and t2["by_kind"]["order"] == 1 and t2["by_kind"]["best"] == 1 and t2["by_kind"]["fallback"] == 1
    assert t2["fallback"]["off"]["n_budget"] == 1 and t2["fallback"]["off"]["rate"] == 0.2 and t2["fallback"]["on"]["rate"] == 0.0
    assert t2["examples"][0]["advice_id"] == "a-9" and t2["examples"][0]["off"]["fallback"] == "budget"
    assert RA.summarize_search_compare([])["status"] == RA.UNKNOWN
    cond = {"at": "2026-10-16 12:00", "code": {"commit": "abc", "dirty": True, "engine": "/x/engine.py"}, "modes": list(RA.MODES),
            "rl_blend_weight": 25.0, "transition_budget_ms": 300.0, "transition_sensitivity": True, "search_blend": 0.0,
            "display_search": "off", "gap_sec": 8.0, "target_fixed": True, "logs": [{"name": "a.jsonl", "n_decisions": 5}]}
    md = RA.render_search_compare_markdown({"conditions": cond, "summary": s2})
    assert "| transition | 5 | 1 | 満たさない |" in md and "| 総合 | 5 | 1 | 満たさない | |" in md, md
    assert "## 不一致の例" in md and "a-9" in md and "択評価" in md and "ADVICE_DISPLAY_SEARCH = off" in md
    md_ok = RA.render_search_compare_markdown({"conditions": cond, "summary": s})
    assert "不一致の例" not in md_ok and "| 総合 | 4 | 0 | 満たす | |" in md_ok
    print("test_summarize_and_render_pure OK")


def main() -> None:
    test_decision_pure()
    test_config_defaults()
    test_off_skips_search()
    test_on_runs_search()
    test_blend_forces_search()
    test_service_text_without_search()
    test_logger_search_skipped()
    test_compare_pair_pure()
    test_summarize_and_render_pure()
    test_on_off_same_ranking_synthetic()
    test_on_off_same_ranking_real_logs()
    print("\nALL OK")


if __name__ == "__main__":
    main()
