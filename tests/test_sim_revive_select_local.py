"""tests/test_sim_revive_select のうち poke-env が要る部分 (ローカル専用: scripts/run_test.sh の LOCAL_ONLY_TESTS。CI の最小依存 requirements-ci.txt に poke-env が無いため
scripts/ci_tests.sh から外した。2026-10-31)。

実物の poke-env の Battle (parse_request に要求 JSON を渡す。要求の形は 2026-10-16 の実対戦で保存したものと同じ) での蘇生要求の確認、
holdout の 2 局面の再現、make_advisor_player (poke_env.player.Player) の choose_move を通す回帰 (通常交代・強制交代、request の記録)。
要求の種類・固定規則・受入監査・記録の欄は tests/test_sim_revive_select (CI)。使用率 DB・RL・Showdown には依存しない。

    python -m tests.test_sim_revive_select_local
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS

os.environ.setdefault("RL_BLEND_WEIGHT", "0")

from advisor.engine import evaluate
from champions_agent.env import advisor_player as AP
from tests.test_scoring_transition import _Hint, resolver
from tests.test_sim_legal_inputs import _Team, _battle
from tests.test_sim_picked_roster import _team_text
from tools.team_build.battle_log import BattleRecorder

_LOG = logging.getLogger("test_sim_revive_select")


# ------------------------------------------------------------------ 実物の poke-env Battle と要求 JSON
def _mon_req(name, details=None, cond="150/150", active=False, reviving=False, moves=("protect",), item="leftovers"):
    """要求の side.pokemon の 1 体 (2026-10-16 の実対戦の要求と同じ欄)"""
    return {"ident": f"p1: {name}", "details": details or f"{name}, L50, M", "condition": cond, "active": active,
            "stats": {"atk": 100, "def": 100, "spa": 100, "spd": 100, "spe": 100}, "moves": list(moves),
            "baseAbility": "pressure", "item": item, "pokeball": "pokeball", "ability": "pressure",
            "commanding": False, "reviving": reviving}


def _preview_req(names):
    """チームプレビューの要求 (6 体)"""
    return {"teamPreview": True, "maxChosenTeamSize": 3,
            "side": {"name": "me", "id": "p1", "pokemon": [_mon_req(n) for n in names]}, "rqid": 1}


def _revive_req(side):
    """蘇生要求 (実対戦で保存した形: forceSwitch・noCancel・rqid・side。active は無い)"""
    return {"forceSwitch": [True], "side": {"name": "me", "id": "p1", "pokemon": side}, "noCancel": True, "rqid": 16}


def _real_battle(team_names, tag="battle-revive-1"):
    from poke_env.battle import Battle
    b = Battle(tag, "me", _LOG, gen=9)
    b.parse_request(_preview_req(team_names))
    return b


# 2026-10-16 の実対戦 (シナリオ B: 選出 ninetales, gardevoir, pawmot) の蘇生要求の side.pokemon
_LIVE_B_SIDE = [_mon_req("Pawmot", "Pawmot, L50, M", "147/147", True, True, ("revivalblessing", "doubleshock", "closecombat", "protect")),
                _mon_req("Gardevoir", "Gardevoir, L50, F", "0 fnt", moves=("memento", "moonblast", "psychic", "protect")),
                _mon_req("Ninetales", "Ninetales, L50, F", "0 fnt", moves=("memento", "flamethrower", "protect", "nastyplot"))]


_LIVE_TEAM = ["Ninetales", "Gardevoir", "Pawmot", "Garchomp", "Corviknight", "Kingambit"]


def test_poke_env_revive_request_is_empty():
    """原因の確認: 実物の poke-env (0.10.0) は蘇生要求で available_switches / available_moves が空 (only_legal_action の
    旧来の数え方では 0 個 → None → ランダム退避)。修正後は要求から蘇生先を数える"""
    b = _real_battle(_LIVE_TEAM)
    b.parse_request(_revive_req(_LIVE_B_SIDE))
    assert b.reviving is True and b.force_switch is True
    assert list(b.available_switches) == [] and list(b.available_moves) == []
    assert AP.request_kind(b) == AP.REQ_REVIVE
    cands = AP.revive_candidates(b)
    assert [k for k, _p in cands] == ["p1: Ninetales", "p1: Gardevoir"]          # battle.team の並び
    moves, switches = AP.legal_options(b)
    assert moves == [] and [p.species for p in switches] == ["ninetales", "gardevoir"]
    assert AP.only_legal_action(b) is None                                       # 2 体: 唯一の合法手ではない
    # 1 体なら only_legal_action でも決まる (旧来は available_switches が空で決まらなかった)
    side1 = [_LIVE_B_SIDE[0], dict(_LIVE_B_SIDE[1], condition="145/145"), _LIVE_B_SIDE[2]]
    b1 = _real_battle(_LIVE_TEAM, "battle-revive-2")
    b1.parse_request(_revive_req(side1))
    assert list(b1.available_switches) == [] and AP.only_legal_action(b1)["pokemon"].species == "ninetales"
    print("test_poke_env_revive_request_is_empty OK")


def _player(rec, stats, order, text):
    player = AP.make_advisor_player(team_source=NS(last_text=text), stats=stats, recorder=rec,
                                    start_listening=False, battle_format="gen9championsbssregmc")
    player.create_order = lambda x, mega=False: ("order", getattr(x, "id", None) or getattr(x, "species", None))
    player.choose_random_move = lambda b: ("random",)
    player._pick = lambda b: order
    return player


def _holdout_case(team_names, order, side, tag):
    """holdout の局面の再現: 実物の Battle にプレビュー → 選出 (order) → 蘇生要求を渡し、修正後の choose_move で決める。
    戻り値 (実行した order, stats, 決定の記録)"""
    text = "\n\n".join(f"{n} @ leftovers\nLevel: 50\nEVs: 32 HP / 32 Atk / 2 Spe\nAdamant Nature\n- protect" for n in team_names)
    with tempfile.TemporaryDirectory() as d:
        rec = BattleRecorder(Path(d) / "b.jsonl", "c", "p")
        stats: dict = {}
        player = _player(rec, stats, order, text)
        b = _real_battle(team_names, tag)
        assert player.teampreview(b) == order
        b.parse_request(_revive_req(side))
        out = player.choose_move(b)
        turns = rec._turns[tag]
        AP._SIM_BUILDS.clear()
        AP._SIM_MOVES.clear()
        return out, stats, turns


def test_holdout_legacy_two_candidates():
    """legacy の 52 件目 (battle-…-46567606、13 ターン目): 選出 pawmot, skeledirge, salamence、ひんし = salamence (メガ後)・
    skeledirge。蘇生要求の side は Showdown の交代の入れ替えで [pawmot, salamence, skeledirge] (Showdown の既定 = 先頭のひんし =
    salamence)。固定規則は選出順で先の skeledirge"""
    team = ["Basculegion", "Metagross", "Mimikyu", "Pawmot", "Salamence", "Skeledirge"]
    side = [_mon_req("Pawmot", cond="1/147", active=True, reviving=True, moves=("revivalblessing", "doubleshock")),
            _mon_req("Salamence", "Salamence-Mega, L50, M", "0 fnt"),
            _mon_req("Skeledirge", cond="0 fnt")]
    out, stats, turns = _holdout_case(team, "/team 465123", side, "battle-gen9championsbssregmc-46567606")
    assert out == ("order", "skeledirge"), out
    assert stats["revive_select"] == 1 and stats["accept:revive_select"] == 1 and stats.get("fallback", 0) == 0
    assert stats.get("accept:random_fallback", 0) == 0 and stats.get("decide", 0) == 0
    t = turns[-1]
    assert t["request_kind"] == AP.REQ_REVIVE and t["accept"] == {"revive_select": True}
    assert t["request"] == {"kind": "revive_select", "available_moves": [], "available_switches": [],
                            "revive_candidates": ["salamencemega", "skeledirge"],
                            "executed": {"kind": "switch", "id": "skeledirge"}, "revive_rule": "pick_order"}, t["request"]
    print("test_holdout_legacy_two_candidates OK")


def test_holdout_transition_single_candidate():
    """transition の 91 件目 (battle-…-46567718、8 ターン目): 選出 salamence, pawmot, gyarados、ひんし = salamence (メガ後) だけ。
    蘇生先 1 体で確定"""
    team = ["Archaludon", "Basculegion", "Gyarados", "Hippowdon", "Pawmot", "Salamence"]
    side = [_mon_req("Pawmot", cond="26/147", active=True, reviving=True, moves=("revivalblessing", "doubleshock")),
            _mon_req("Salamence", "Salamence-Mega, L50, M", "0 fnt"),
            _mon_req("Gyarados", cond="170/170")]
    out, stats, turns = _holdout_case(team, "/team 653124", side, "battle-gen9championsbssregmc-46567718")
    assert out == ("order", "salamencemega"), out
    assert stats["revive_select"] == 1 and stats.get("fallback", 0) == 0 and stats.get("accept:random_fallback", 0) == 0
    req = turns[-1]["request"]
    assert req["revive_candidates"] == ["salamencemega"] and req["revive_rule"] == "single"
    assert req["available_switches"] == [] and req["available_moves"] == []
    print("test_holdout_transition_single_candidate OK")


def test_regression_normal_and_forced_switch():
    """回帰: 通常の決定・強制交代 (ひんし後) の候補と実行行動は修正前の経路 (battle_to_state → evaluate → choose_with_trace)
    と同じで、ひんしの個体は候補に入らない。決定の記録は request_kind だけ (request は付けない)"""
    with tempfile.TemporaryDirectory() as d, _Team(), _Hint():
        rec = BattleRecorder(Path(d) / "b.jsonl", "c", "p")
        stats: dict = {}
        player = _player(rec, stats, "/team 123456", _team_text())
        cases = [(_battle(), AP.REQ_NORMAL),
                 (_battle(fainted=("Garchomp",), force_switch=True), AP.REQ_FORCED),
                 (_battle(fainted=("Garchomp", "Primarina"), force_switch=True), AP.REQ_FORCED)]
        for b, kind in cases:
            # 修正前の経路 (要求の種類を見ない): 状態 → 助言 (player と同じ引数) → choose_with_trace
            adv = evaluate(AP.battle_to_state(b, resolver), resolver, rl_blend_weight=None)
            dd = AP.choose_with_trace(b, adv)[0]
            exp = ("order", dd["move"].id if dd["kind"] == "move" else dd["pokemon"].species)
            assert player.choose_move(b) == exp, (kind, exp)
            t = rec._turns["battle-legal-1"][-1]
            assert t["request_kind"] == kind and "request" not in t, t
            assert [a["id"] for a in t["advisor_recommendation"]["actions"]] == [a["id"] for a in adv["actions"][:4]]
            if kind == AP.REQ_FORCED:
                fainted = {p.species for p in b.team.values() if p.fainted}
                assert exp[0] == "order" and exp[1] not in fainted
                assert not fainted & {a["id"] for a in adv["actions"] if a["kind"] == "switch"}, adv["actions"]
        assert stats.get("revive_select", 0) == 0 and stats.get("accept:revive_select", 0) == 0
        assert stats.get("fallback", 0) == 0 and stats["decide"] == 3
    print("test_regression_normal_and_forced_switch OK")


def test_request_recorded_on_forced_single_and_fallback():
    """助言から行動を作れなかった決定 (唯一の合法手・ランダム退避) は request を残す"""
    with tempfile.TemporaryDirectory() as d, _Team(), _Hint():
        rec = BattleRecorder(Path(d) / "b.jsonl", "c", "p")
        stats: dict = {}
        one = _battle(avail_moves=["struggle"], req_moves=[("struggle", False)], trapped=True)
        import advisor.engine as eng
        orig = eng.evaluate
        try:
            # 助言が候補を返さない (ok だが actions 空 = holdout の記録と同じ形) とき
            eng.evaluate = lambda *a, **k: {"ok": True, "best": {}, "actions": []}
            player2 = _player(rec, stats, "/team 123456", _team_text())
            assert player2.choose_move(one) == ("order", "struggle")
            t = rec._turns["battle-legal-1"][-1]
            assert t["request"]["kind"] == AP.REQ_NORMAL and t["request"]["available_moves"] == ["struggle"]
            assert t["request"]["executed"] == {"kind": "move", "id": "struggle"} and t["accept"]["forced_single"]
            two = _battle()
            assert player2.choose_move(two) == ("random",)
            t2 = rec._turns["battle-legal-1"][-1]
            assert t2["request"]["executed"] is None and t2["accept"]["random_fallback"]
            assert t2["request"]["revive_candidates"] == [] and "revive_rule" not in t2["request"]
        finally:
            eng.evaluate = orig
    print("test_request_recorded_on_forced_single_and_fallback OK")


def main():
    test_poke_env_revive_request_is_empty()
    test_holdout_legacy_two_candidates()
    test_holdout_transition_single_candidate()
    test_regression_normal_and_forced_switch()
    test_request_recorded_on_forced_single_and_fallback()
    print("ALL OK")


if __name__ == "__main__":
    main()
