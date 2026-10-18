"""transition の感度を実機で切る (2026-10-18 レビュー、計画書 §8.34) のテスト。

    python -m tests.test_sensitivity_off

- 同値性 (固定局面の回帰): 不確かな HP の控え / 最大 HP が分からない (退避) / 消費済みのタスキ / 判明したスカーフ (こだわりの固定) /
  持ち物が未判明 (感度の仮説あり) / 合成局面 (tests.test_advice_variant) で、感度 off でも本採点の候補・得点・Q・順位・合法性の欄・
  退避の理由・HP の悲観側の入力・表示の文言が感度 on と同じ
- 記録: battle_logger.sensitivity_fields_of (disabled / computed / partial / omitted / failed / none / null と changed)、
  version 行の transition_sensitivity / _source / _env、advice 行 (kind = battle) の sensitivity_status / sensitivity_changed
- tools.real_accept --compare-sensitivity の純粋な部分 (q_signature / compare_sensitivity_pair / summarize_sensitivity_compare / 表)
予算超過の退避は実行時間で決まるので、計算予算は時間に依存しない値に広げて比べる (予算の扱いは real_accept --compare-sensitivity で見る)。
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path

import battle_logger as BL
from advisor.engine import evaluate
from champions_agent import config as C
from tools import real_accept as RA
from tests.test_advice_m2c import _uncertain_bench_state
from tests.test_scoring_transition import _Hint, _capture_inputs, _mon, _state, resolver
from vision.scenes import SCENE_COMMAND


class _Cfg:
    """感度の有無と計算予算を一時的に切り替える"""

    def __init__(self, sens: bool, budget: float = 60_000.0):
        self.sens, self.budget = sens, budget

    def __enter__(self):
        self.orig = (C.TRANSITION_SENSITIVITY_ENABLED, C.TRANSITION_BUDGET_MS)
        C.TRANSITION_SENSITIVITY_ENABLED, C.TRANSITION_BUDGET_MS = self.sens, self.budget
        return self

    def __exit__(self, *a):
        C.TRANSITION_SENSITIVITY_ENABLED, C.TRANSITION_BUDGET_MS = self.orig


def _gar():
    return _mon("garchomp", "ガブリアス", ["ドラゴン", "じめん"], [("じしん", "earthquake"), ("ドラゴンクロー", "dragonclaw")])


def _cases() -> list:
    """(名前, 状態) の固定局面 (test_advice_m2c / m2d の回帰と同じ作り)"""
    sash = _mon("heatran", "ヒードラン", ["ほのお", "はがね"], picked=False, item_id="focussash", item_consumed=True,
                revealed_moves=["マグマストーム", "だいちのちから"])
    scarf = _mon("heatran", "ヒードラン", ["ほのお", "はがね"], picked=False, item_id="choicescarf",
                 revealed_moves=["マグマストーム", "だいちのちから"])
    st_scarf = _state([_gar()], [scarf], my_rem=3, opp_rem=3)
    st_scarf["last_move"] = {"opponent": "magmastorm"}
    unknown = _mon("heatran", "ヒードラン", ["ほのお", "はがね"], picked=False, revealed_moves=["マグマストーム"])
    return [("hp_uncertain", _uncertain_bench_state(True)), ("hp_certain", _uncertain_bench_state(False)),
            ("hp_no_max", _uncertain_bench_state(True, hp_max=None)),
            ("consumed_sash", _state([_gar()], [sash], my_rem=3, opp_rem=3)), ("known_scarf", st_scarf),
            ("item_unknown", _state([_gar()], [unknown], my_rem=3, opp_rem=3))]


def _main_view(adv: dict) -> dict:
    """本採点の結果として一致すべき部分 (感度の欄 q_hp_opt・transition_stats は除く)"""
    return {"sig": RA.rank_signature(adv), "q": RA.q_signature(adv),
            "reasons": [(a.get("kind"), a.get("id"), a.get("reason")) for a in (adv.get("actions") or [])],
            "legality": {k: adv.get(k) for k in ("illegal_actions", "unevaluated_actions", "legality_unconfirmed")},
            "fallback": (adv.get("scoring_used"), adv.get("scoring_fallback")),
            "notes": ((adv.get("transition_stats") or {}).get("notes"))}


def _pess(inp) -> list:
    """入力の HP (悲観側の仮説を T と V に渡す値) と持ち物"""
    if inp is None:
        return None
    return [round(inp.me.active_hp, 9), [round(hp, 9) for _v, hp in inp.me.bench], round(inp.opp.active_hp, 9),
            inp.opp.active.item, [h.get("lo") for h in inp.hp_ranges]]


def test_regression_positions_same_main_scoring():
    from advisor.service import Advisor
    fmt = Advisor().format_advice
    seen = {}
    for name, st in _cases():
        out = {}
        for on in (True, False):
            with _Hint(), _Cfg(on):
                adv, inp = _capture_inputs(copy.deepcopy(st))
            out[on] = (adv, inp)
        (a_on, i_on), (a_off, i_off) = out[True], out[False]
        assert _main_view(a_on) == _main_view(a_off), (name, _main_view(a_on), _main_view(a_off))
        assert _pess(i_on) == _pess(i_off), (name, _pess(i_on), _pess(i_off))
        # 表示の文言も同じ (感度は表示に出ない。disabled を「仮説を変えても安全」と読める文言にしない)
        t_on, t_off = fmt(dict(a_on, provisional=False)), fmt(dict(a_off, provisional=False))
        assert t_on == t_off and "推奨" in t_on, (name, t_on, t_off)
        assert all("q_hp_opt" not in a for a in a_off.get("actions") or [])
        f_on, f_off = BL.sensitivity_fields_of(a_on), BL.sensitivity_fields_of(a_off)
        seen[name] = (f_on["sensitivity_status"], f_off["sensitivity_status"], a_on.get("scoring_fallback"))
        if a_on.get("scoring_used") == "transition":
            assert f_off == {"sensitivity_status": "disabled", "sensitivity_changed": None}, (name, f_off)
        else:
            assert f_on["sensitivity_status"] is None and f_off["sensitivity_status"] is None, (name, f_on, f_off)
    # 固定局面ごとの期待: 不確かな HP は悲観側 (1 HP) のまま、楽観側の仮説は on でだけ計算。最大 HP なしは退避 (hp_range)。
    # 消費済みタスキ・判明したスカーフは持ち物の仮説が該当しない
    assert seen["hp_no_max"] == (None, None, "hp_range"), seen
    assert seen["hp_uncertain"][0] in ("computed", "partial"), seen
    assert seen["known_scarf"][0] == "none" and seen["consumed_sash"][0] == "none", seen
    assert seen["item_unknown"][0] in ("computed", "partial"), seen
    print("test_regression_positions_same_main_scoring OK", seen)


def test_synthetic_fixtures_same_main_scoring():
    from tests.test_advice_variant import _FakePolicy, _fixtures
    for name, st in _fixtures():
        res = {}
        for on in (True, False):
            with _FakePolicy(), _Cfg(on):
                res[on] = evaluate(copy.deepcopy(st), scoring_mode="transition")
        assert _main_view(res[True]) == _main_view(res[False]), name
        assert res[True]["transition_stats"]["sens"]["enabled"] is True
        assert BL.sensitivity_fields_of(res[False])["sensitivity_status"] == "disabled"
    print("test_synthetic_fixtures_same_main_scoring OK")


# ------------------------------------------------------------------ 記録
def _adv_with(sens: dict, sensitivity=None, hp_opt=None) -> dict:
    ts = {"sens": sens, "sensitivity": sensitivity, "hp_range": ({"optimistic": hp_opt} if hp_opt is not None else None)}
    return {"ok": True, "scoring_mode": "transition", "scoring_used": "transition", "transition_stats": ts}


def test_sensitivity_fields_pure():
    f = BL.sensitivity_fields_of
    off = {"enabled": False, "status": "disabled", "run": [], "omitted": {}}
    assert f(_adv_with(off)) == {"sensitivity_status": "disabled", "sensitivity_changed": None}
    full = {"enabled": True, "status": "run", "run": ["scarf", "sash"], "omitted": {}}
    assert f(_adv_with(full, {"scarf": {"changes": False}, "sash": {"changes": False}})) == \
        {"sensitivity_status": "computed", "sensitivity_changed": False}
    assert f(_adv_with(full, {"scarf": {"changes": False}, "sash": {"changes": True}}))["sensitivity_changed"] is True
    hp = {"enabled": True, "status": "run", "run": ["hp_optimistic"], "omitted": {"scarf": "item_known", "sash": "item_known"}}
    assert f(_adv_with(hp, {"scarf": {"omitted": "item_known"}}, {"best": "x", "changes": True})) == \
        {"sensitivity_status": "computed", "sensitivity_changed": True}
    part = {"enabled": True, "status": "partial", "run": ["scarf"], "omitted": {"sash": "budget"}}
    assert f(_adv_with(part, {"scarf": {"changes": False}, "sash": {"omitted": "budget"}})) == \
        {"sensitivity_status": "partial", "sensitivity_changed": False}
    om = {"enabled": True, "status": "omitted", "run": [], "omitted": {"scarf": "budget", "sash": "budget"}}
    assert f(_adv_with(om)) == {"sensitivity_status": "omitted", "sensitivity_changed": None}
    # 既存の status "omitted" でも、該当しない理由だけなら none (予算での省略と区別する)
    na = {"enabled": True, "status": "omitted", "run": [], "omitted": {"scarf": "item_known", "sash": "not_full_hp"}}
    assert f(_adv_with(na)) == {"sensitivity_status": "none", "sensitivity_changed": None}
    assert f(_adv_with({"enabled": True, "status": "none", "run": [], "omitted": {}}))["sensitivity_status"] == "none"
    fl = {"enabled": True, "status": "omitted", "run": [], "omitted": {"scarf": "opp_unsupported:x:y"}}
    assert f(_adv_with(fl))["sensitivity_status"] == "failed"
    fl2 = {"enabled": True, "status": "partial", "run": ["sash"], "omitted": {"scarf": "opp_unsupported:x:y"}}
    assert f(_adv_with(fl2, {"sash": {"changes": False}}))["sensitivity_status"] == "partial"
    # transition で評価していない (legacy・退避・評価できない)
    for a in ({"ok": True, "scoring_mode": "legacy"}, {"ok": True, "scoring_mode": "transition", "scoring_used": "legacy",
                                                       "transition_stats": {"fallback": "budget", "main": {}}},
              {"ok": False}, None):
        assert f(a) == {"sensitivity_status": None, "sensitivity_changed": None}, a
    print("test_sensitivity_fields_pure OK")


class _Env:
    def __init__(self, **kv):
        self.kv = kv

    def __enter__(self):
        self.orig = {k: os.environ.get(k) for k in self.kv}
        for k, v in self.kv.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def __exit__(self, *a):
        for k, v in self.orig.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_logger_version_and_advice_rows():
    st = {"scene": SCENE_COMMAND, "turn": 2, "field": {}, "player": {"active_index": 0, "party": [{"species_id": "scizor"}]},
          "opponent": {"active_index": 0, "party": []}}
    for sens, env, src, want_src in ((False, "0", "default", "default"), (True, "1", "file", "file"), (True, None, None, None)):
        with tempfile.TemporaryDirectory() as td, _Cfg(sens), \
                _Env(TRANSITION_SENSITIVITY=env, TRANSITION_SENSITIVITY_SOURCE=src):
            lg = BL.BattleLogger(log_dir=Path(td))
            lg.on_advice(_adv_with({"enabled": False, "status": "disabled", "run": [], "omitted": {}}), "battle", st)
            lg.on_advice(_adv_with({"enabled": True, "status": "omitted", "run": [], "omitted": {"scarf": "budget"}}),
                         "battle", st)
            lg.on_advice({"ok": True, "scoring_mode": "legacy", "best": None, "actions": []}, "battle", st)
            lg.on_advice({"ok": True, "recommend": []}, "selection", st)
            rows = [json.loads(x) for x in lg._file.read_text(encoding="utf-8").splitlines()]
        ver = [r for r in rows if r["type"] == "version"][0]
        assert ver["transition_sensitivity"] is sens and ver["transition_sensitivity_source"] == want_src, ver
        assert ver["transition_sensitivity_env"] == env, ver
        advs = [r for r in rows if r["type"] == "advice"]
        assert [r.get("sensitivity_status", "なし") for r in advs[:3]] == ["disabled", "omitted", None], advs
        assert all("sensitivity_changed" in r for r in advs[:3]) and "sensitivity_status" not in advs[3]
    print("test_logger_version_and_advice_rows OK")


# ------------------------------------------------------------------ real_accept --compare-sensitivity (純粋)
def _x(best="a", q=0.1, fb=None, err=None, viol=(), status="computed", ms=10.0):
    sig = None if err else {"ok": True, "actions": [("move", best, best, 55.0), ("move", "z", "z", 40.0)],
                            "best": ("move", best), "scoring_used": "legacy" if fb else "transition", "fallback": fb}
    return {"elapsed_ms": ms, "error": err, "sig": sig, "q": None if (err or fb) else [("move", best, q, -0.1)],
            "violations": list(viol), "checks": {}, "sens_status": status}


def test_compare_pair_and_summary_pure():
    p = RA.compare_sensitivity_pair
    assert p(_x(), _x(status="disabled")) == []
    assert p(_x(q=0.1), _x(q=0.2)) == ["q"]
    assert "best" in p(_x(best="a"), _x(best="b"))
    assert "fallback" in p(_x(), _x(fb="budget"))
    assert p(_x(err="E"), _x()) == ["error"] and p(_x(err="E"), _x(err="E")) == []
    assert "legality" in p(_x(), _x(viol=("move_pp0",)))
    adv = {"ok": True, "scoring_used": "transition", "actions": [{"kind": "move", "id": "a", "q": 0.1, "q_worst": -0.2}]}
    assert RA.q_signature(adv) == [("move", "a", 0.1, -0.2)]
    assert RA.q_signature(dict(adv, scoring_used="legacy")) is None and RA.q_signature(None) is None
    rows = [{"log": "a.jsonl", "advice_id": f"a-{i}", "turn": i, "on": _x(ms=20.0), "off": _x(status="disabled", ms=8.0)}
            for i in range(4)]
    for r in rows:
        r["diffs"] = p(r["on"], r["off"])
    s = RA.summarize_sensitivity_compare(rows)
    assert s["status"] == RA.OK and s["n"] == 4 and s["n_mismatch"] == 0
    assert s["elapsed_delta_ms"]["p50"] == -12.0 and s["sens_status"]["off"] == {"disabled": 4}
    rows[1]["off"] = _x(q=0.3, status="disabled")
    rows[1]["diffs"] = p(rows[1]["on"], rows[1]["off"])
    s2 = RA.summarize_sensitivity_compare(rows)
    assert s2["status"] == RA.NG and s2["by_kind"]["q"] == 1 and s2["examples"][0]["advice_id"] == "a-1"
    assert RA.summarize_sensitivity_compare([])["status"] == RA.UNKNOWN
    cond = {"at": "2026-10-18 12:00", "code": {"commit": "abc", "dirty": False, "engine": "/x/engine.py"}, "mode": "transition",
            "transition_budget_ms": 300.0, "transition_sensitivity": True, "display_search": "off", "gap_sec": 8.0,
            "target_fixed": True, "logs": [{"name": "a.jsonl", "n_decisions": 4}]}
    md = RA.render_sensitivity_compare_markdown({"conditions": cond, "summary": s2})
    assert "| 4 | 1 | 満たさない |" in md and "## 不一致の例" in md and "a-1" in md, md
    md_ok = RA.render_sensitivity_compare_markdown({"conditions": cond, "summary": s})
    assert "| 4 | 0 | 満たす | - |" in md_ok and "不一致の例" not in md_ok and "disabled 4" in md_ok
    print("test_compare_pair_and_summary_pure OK")


def main() -> None:
    test_regression_positions_same_main_scoring()
    test_synthetic_fixtures_same_main_scoring()
    test_sensitivity_fields_pure()
    test_logger_version_and_advice_rows()
    test_compare_pair_and_summary_pure()
    print("\nALL OK")


if __name__ == "__main__":
    main()
