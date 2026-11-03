"""advisor-as-player の さいきのいのり の蘇生先の選択 (2026-10-16 E-holdout-1) のテスト。

holdout の測定 (logs/usefulness/p1_holdout_20261016_0124) で両条件に 1 件ずつ出たランダム退避の原因と修正を確かめる:
- Showdown の蘇生要求は {forceSwitch: [true], side} (active が無い) で、reviving の印は場の個体 (さいきのいのり の使い手) に付く。
  poke-env 0.10.0 は「印があって場にいない個体」を蘇生先にするので available_switches も available_moves も空になり、助言
  (ひんしを交代候補から外す) も only_legal_action (合法手の数 = 0) も行動を作れなかった。
- 修正: 要求の種類 (normal_switch / forced_switch / revive_select) を区別し、蘇生先は要求の合法なひんし個体から固定規則
  (1 体なら確定、複数なら自分の選出順で先) で選ぶ。受入監査は revive_select として数え、違反にしない。

poke-env の Battle は実物 (parse_request に要求 JSON を渡す。要求の形は 2026-10-16 の実対戦で保存したものと同じ) と、
tests/test_sim_legal_inputs の偽オブジェクト (通常交代・強制交代の回帰)。使用率 DB・RL・Showdown には依存しない。
実物の poke-env の Battle と make_advisor_player (poke-env の Player) を使う 5 本は tests/test_sim_revive_select_local
(ローカル専用、CI の最小依存に poke-env が無いため。2026-10-31)。ここに残るのは要求の種類・固定規則・受入監査・記録の欄。

    python -m tests.test_sim_revive_select
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS

os.environ.setdefault("RL_BLEND_WEIGHT", "0")

from champions_agent.env import advisor_player as AP
from champions_agent.env import sim_accept as SA
from tests.test_sim_legal_inputs import _battle
from tools.team_build.battle_log import BattleRecorder


def test_request_kind():
    """要求の種類: 通常 / 強制交代 / 蘇生。reviving の印だけ (forceSwitch なし) は蘇生にしない"""
    assert AP.request_kind(_battle()) == AP.REQ_NORMAL
    assert AP.request_kind(_battle(force_switch=True)) == AP.REQ_FORCED
    assert AP.request_kind(_battle(fainted=("Garchomp",), force_switch=True)) == AP.REQ_FORCED
    assert AP.request_kind(_battle(request=False)) == AP.REQ_NORMAL
    rv = _battle(force_switch=True)
    rv.last_request["side"]["pokemon"][0]["reviving"] = True
    assert AP.request_kind(rv) == AP.REQ_REVIVE
    only_mark = _battle()
    only_mark.last_request["side"]["pokemon"][0]["reviving"] = True
    assert AP.request_kind(only_mark) == AP.REQ_NORMAL
    assert AP.revive_candidates(_battle(fainted=("Primarina",), force_switch=True)) == []   # 強制交代は蘇生先なし
    print("test_request_kind OK")


def test_revive_target_rule():
    """固定規則: 1 体なら確定、複数なら選出順で先、選出順が分からなければ battle.team の並び"""
    a, b, c = NS(species="a"), NS(species="b"), NS(species="c")
    assert AP.revive_target([], ["p1: A"]) == (None, None)
    assert AP.revive_target([("p1: B", b)], None) == (b, "single")
    cands = [("p1: A", a), ("p1: C", c)]
    assert AP.revive_target(cands, ["p1: C", "p1: B", "p1: A"]) == (c, "pick_order")
    assert AP.revive_target(cands, ["p1a: A", "p1: C"]) == (a, "pick_order")          # ident の書き方の違いを揃える
    assert AP.revive_target(cands, None) == (a, "team_order")
    assert AP.revive_target(cands, ["p1: C"]) == (a, "team_order")                    # 候補が選出順に無い
    team = NS(team={f"p1: {n}": None for n in ("A", "B", "C", "D", "E", "F")})
    assert AP.pick_order_keys(team, "/team 315246") == ["p1: C", "p1: A", "p1: E", "p1: B", "p1: D", "p1: F"]
    assert AP.pick_order_keys(team, "/team 19") == ["p1: A"]
    assert AP.pick_order_keys(team, None) == []
    print("test_revive_target_rule OK")


def test_accept_revive_not_violation():
    """受入監査: revive_select は件数に残るが違反ではない (accept_violations・usefulness_verdict の受入検査)。表示はする"""
    f = SA.decision_flags(None, None, [], [], [], True, ["pawmot"], random_fallback=False, revive_select=True)
    assert f["revive_select"] and not f["random_fallback"] and not f["skip_unexplained"] and not f["skip_explained"]
    assert SA.raised(f) == {"revive_select": True}
    assert not SA.decision_flags(None, None, [], [], [], True, ["pawmot"], random_fallback=False)["revive_select"]
    stats: dict = {}
    SA.add_flags(stats, f, SA.STATS_PREFIX)
    s = SA.summary_from_stats(stats)
    assert s["revive_select"] == 1 and SA.accept_violations(s) == []
    tot = SA.sum_battle_accept([{"accept": {"decisions": 3, "revive_select": 1}}, {"accept": {"decisions": 2}}])
    assert tot["revive_select"] == 1 and SA.accept_violations(tot) == []
    from tools import usefulness_verdict as V
    good = dict({k: 0 for k in SA.COUNT_KEYS}, decisions=10, inputs_version=s["inputs_version"])
    recs = {"a": [{"accept": dict(good, revive_select=1)}] * 4, "b": [{"accept": dict(good)}] * 4}
    reasons, summ = V.accept_audit_check(recs, ["a", "b"], 4)
    assert reasons == [] and summ["per_condition"]["a"]["revive_select"] == 4
    # 旧来の記録 (revive_select の欄が無い) も読める
    old = {"a": [{"accept": {k: v for k, v in good.items() if k != "revive_select"}}] * 4}
    assert V.accept_audit_check(old, ["a"], 4)[0] == []
    # 表示 (render の受入監査の行)
    v = {"mode": V.MODE_INTERIM, "interim_reasons": ["x"], "invalid_reasons": [], "k": 1, "level": 0.95, "z": 1.96,
         "mde": 0.03, "n_used": 4, "comparisons": [], "per_condition": {}, "cost": {}, "opponent_mismatches": None,
         "notes": [], "accept_audit": summ}
    txt = V.render(v)
    assert "蘇生先の選択" in txt and "a 4 件" in txt and "b 0 件" in txt, txt
    print("test_accept_revive_not_violation OK")


def test_recorder_request_fields_optional():
    """決定の記録の request_kind / request は渡したときだけ (既存の読み手の互換)"""
    with tempfile.TemporaryDirectory() as d:
        rec = BattleRecorder(Path(d) / "b.jsonl", "c", "p")
        b = NS(battle_tag="battle-x", turn=1)
        rec.on_decision(b, {"turn": 1}, None, None, True)
        rec.on_decision(b, {"turn": 1}, None, None, True, request_kind="revive_select",
                        request={"kind": "revive_select", "revive_candidates": ["a"]})
        t0, t1 = rec._turns["battle-x"]
        assert "request_kind" not in t0 and "request" not in t0
        assert t1["request_kind"] == "revive_select" and t1["request"]["revive_candidates"] == ["a"]
        json.dumps(t1)
    print("test_recorder_request_fields_optional OK")


if __name__ == "__main__":
    test_request_kind()
    test_revive_target_rule()
    test_accept_revive_not_violation()
    test_recorder_request_fields_optional()
