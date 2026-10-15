"""advisor-as-player の選出の印 (未選出の個体を評価に含めていた不具合の修正、2026-10-15) と診断用の記録のテスト。

docs/USEFULNESS_P2_SCORING_20261013.md §5: battle_to_state が自分の全個体 (battle.team の 6 体) に is_picked=True を付け、
未選出の個体が交代候補・残存戦力 (transition の V の M)・終盤評価に入っていた。BSS のシミュレータはチームプレビュー後の要求
(request) の side.pokemon に選出した 3 体だけを入れるので、それで選出を決める (picked_mask_of)。

poke-env の Battle をダックタイピングの偽オブジェクトで与える。使用率 DB・RL・登録ファイル・Showdown には依存しない
(相手の技は判明技だけ、RL の加点は 0、自分の型はチームテキストで登録)。

    python -m tests.test_sim_picked_roster
"""
from __future__ import annotations

import copy
import json
import os
from types import SimpleNamespace as NS

os.environ.setdefault("RL_BLEND_WEIGHT", "0")

import advisor.endgame as EG
from advisor import scoring_transition as ST
from advisor.engine import evaluate
from champions_agent.env import advisor_player as AP
from tests.test_scoring_transition import _Hint, resolver
from tools.team_build.battle_log import BattleRecorder

_OWN = [  # (名前, 種族 id, 技)
    ("Garchomp", "garchomp", ("earthquake", "dragonclaw", "swordsdance", "stoneedge")),
    ("Primarina", "primarina", ("moonblast", "surf", "icebeam", "calmmind")),
    ("Corviknight", "corviknight", ("bravebird", "roost", "uturn", "bodypress")),
    ("Dragonite", "dragonite", ("extremespeed", "dragondance", "earthquake", "firepunch")),
    ("Kingambit", "kingambit", ("kowtowcleave", "suckerpunch", "ironhead", "swordsdance")),
    ("Gholdengo", "gholdengo", ("makeitrain", "shadowball", "nastyplot", "recover")),
]
_PICKED = ("Garchomp", "Primarina", "Corviknight")


def _move(mid, pp=10):
    return NS(id=mid, current_pp=pp, max_pp=pp)


def _pmon(species, moves, hp=1.0, fainted=False, item="leftovers", ability=None, max_hp=200):
    return NS(species=species, current_hp_fraction=0.0 if fainted else hp, fainted=fainted,
              moves={m: _move(m) for m in moves}, item=item, ability=ability, status=None, boosts={},
              max_hp=max_hp, current_hp=0 if fainted else int(max_hp * hp), types=[])


def _team_text() -> str:
    return "\n\n".join(f"{name} @ leftovers\nLevel: 50\nEVs: 32 HP / 32 Atk / 2 Spe\nAdamant Nature\n"
                       + "\n".join(f"- {m}" for m in moves) for name, _sid, moves in _OWN)


def _battle(picked=_PICKED, fainted=(), active="Garchomp", request=True, teampreview=False, ident_slot=False):
    """自分 6 体 (battle.team は 6 体のまま) と、最後の要求の side.pokemon (選出した個体だけ)"""
    mons = {f"p1: {name}": _pmon(sid, moves, fainted=name in fainted) for name, sid, moves in _OWN}
    act = mons[f"p1: {active}"]
    opp = _pmon("heatran", ("magmastorm", "earthpower"), hp=0.7, item="unknown_item")
    side = [{"ident": (f"p1a: {n}" if ident_slot else f"p1: {n}"), "details": n} for n in picked]
    req = ({"side": {"pokemon": side}, "teamPreview": teampreview} if request else {})
    switches = [m for k, m in mons.items() if k[4:] in picked and m is not act and not m.fainted]
    return NS(active_pokemon=act, opponent_active_pokemon=opp, team=mons, opponent_team={"p2: Heatran": opp},
              available_moves=[_move(m) for m in act.moves], available_switches=switches,
              can_mega_evolve=False, weather={}, fields={}, side_conditions={}, opponent_side_conditions={},
              turn=3, last_request=req, battle_tag="battle-test-1", force_switch=False, trapped=False)


def test_picked_mask_from_last_request():
    b = _battle()
    assert AP.picked_mask_of(b) == [True, True, True, False, False, False]
    # "p1a: 名前" の形の ident も同じ個体
    assert AP.picked_mask_of(_battle(ident_slot=True)) == [True, True, True, False, False, False]
    # 要求が無い・プレビュー中・当たらない → None (全個体を選出扱い = 従来どおり)
    assert AP.picked_mask_of(_battle(request=False)) is None
    assert AP.picked_mask_of(_battle(teampreview=True)) is None
    assert AP.picked_mask_of(_battle(picked=("Nobody",))) is None
    print("test_picked_mask_from_last_request OK")


def test_battle_to_state_marks_only_picked():
    st = AP.battle_to_state(_battle(fainted=("Primarina",)), resolver)
    me = st["player"]
    assert [p["species_id"] for p in me["party"]] == [sid for _n, sid, _m in _OWN]    # party には 6 体を残す
    assert [p["is_picked"] for p in me["party"]] == [True, True, True, False, False, False]
    assert me["remaining"] == 2                         # 選出 3 体のうち生存 (アシレーヌがひんし)。以前は 5
    # 要求が無ければ従来どおり全個体を選出扱い
    st0 = AP.battle_to_state(_battle(request=False, fainted=("Primarina",)), resolver)
    assert all(p["is_picked"] for p in st0["player"]["party"]) and st0["player"]["remaining"] == 5
    # 状態の交代先 (選出・生存・場にいない) = poke-env の available_switches
    chk = AP.switchable_check(_battle(fainted=("Primarina",)), st)
    assert chk == {"ok": True, "state": ["corviknight"], "poke_env": ["corviknight"]}, chk
    assert AP.switchable_check(_battle(fainted=("Primarina",)), st0)["ok"] is False
    print("test_battle_to_state_marks_only_picked OK")


def _eval_both(st):
    """legacy / transition の助言、transition の入力、終盤評価の自分側の個体を返す"""
    cap = {}
    orig_score, orig_mm = ST.score, EG.matchup_matrix

    def spy_score(inp, budget_ms=None, sensitivity=None):
        cap["inp"] = inp
        return orig_score(inp, budget_ms=10_000, sensitivity=False)

    def spy_mm(my_mons, opp_mons):
        cap.setdefault("endgame_my", []).append([m[1].species_id for m in my_mons])
        return orig_mm(my_mons, opp_mons)

    ST.score, EG.matchup_matrix = spy_score, spy_mm
    try:
        leg = evaluate(copy.deepcopy(st), resolver, rl_blend_weight=0, scoring_mode="legacy")
        tr = evaluate(copy.deepcopy(st), resolver, rl_blend_weight=0, scoring_mode="transition")
    finally:
        ST.score, EG.matchup_matrix = orig_score, orig_mm
    return leg, tr, cap


def _switch_ids(adv):
    return {a["id"] for a in adv.get("actions") or [] if a.get("kind") == "switch"}


def test_engine_counts_only_picked():
    """選出 3 体だけが交代候補・transition の控えと残数・終盤評価に入る (修正前の状態では未選出の 3 体も入る)"""
    AP.register_team_text(_team_text(), resolver)
    try:
        with _Hint():
            st = AP.battle_to_state(_battle(), resolver)
            leg, tr, cap = _eval_both(st)
            assert leg["ok"] and tr["ok"]
            assert _switch_ids(leg) == {"primarina", "corviknight"}, _switch_ids(leg)
            assert _switch_ids(tr) == {"primarina", "corviknight"}, _switch_ids(tr)
            inp = cap["inp"]
            assert sorted(v.species_id for v, _hp in inp.me.bench) == ["corviknight", "primarina"]
            assert inp.vctx.my_unknown == 0
            for mons in cap["endgame_my"]:
                assert set(mons) <= {"garchomp", "primarina", "corviknight"}, mons
            # 修正前の状態 (全個体に選出の印): 未選出の個体が入っていた (このテストが不具合を検出できることの確認)
            st_old = copy.deepcopy(st)
            for p in st_old["player"]["party"]:
                p["is_picked"] = True
            st_old["player"]["remaining"] = 6
            leg0, tr0, cap0 = _eval_both(st_old)
            assert {"dragonite", "kingambit", "gholdengo"} <= _switch_ids(leg0)
            assert len(cap0["inp"].me.bench) == 5
            assert any("gholdengo" in m for m in cap0["endgame_my"])
    finally:
        AP._SIM_BUILDS.clear()
        AP._SIM_MOVES.clear()
    print("test_engine_counts_only_picked OK")


def test_choose_with_trace_reasons():
    b = _battle(fainted=("Primarina",))
    adv = {"ok": True, "best": {"kind": "switch", "id": "gholdengo"},
           "actions": [{"kind": "switch", "id": "gholdengo"}, {"kind": "move", "id": "outrage"},
                       {"kind": "switch", "id": "primarina"}, {"kind": "move", "id": "earthquake"}]}
    d, skipped = AP.choose_with_trace(b, adv)
    assert d["kind"] == "move" and d["move"].id == "earthquake"
    assert skipped == [{"kind": "switch", "id": "gholdengo", "why": "switch_not_picked"},
                       {"kind": "move", "id": "outrage", "why": "move_unavailable"},
                       {"kind": "switch", "id": "primarina", "why": "switch_fainted"}], skipped
    assert AP.choose_from_advice(b, adv)["move"].id == "earthquake"      # 行動は choose_from_advice と同じ
    assert AP.choose_with_trace(b, {"ok": False}) == (None, [])
    print("test_choose_with_trace_reasons OK")


def test_diag_record_replay_reproduces():
    """保存した診断の記録 (JSON に書いて読み戻した state) を evaluate に渡し直すと、同じ順位と点になる (legacy / transition)"""
    AP.register_team_text(_team_text(), resolver)
    try:
        with _Hint():
            b = _battle()
            for mode in ("legacy", "transition"):
                st = AP.battle_to_state(b, resolver)
                snap = AP._jsonable(st)
                adv = evaluate(st, resolver, rl_blend_weight=0, scoring_mode=mode)
                d, skipped = AP.choose_with_trace(b, adv)
                ex = {"kind": d["kind"], "id": d["move"].id if d["kind"] == "move" else d["pokemon"].species}
                other = evaluate(AP._jsonable(snap), resolver, rl_blend_weight=0,
                                 scoring_mode="legacy" if mode == "transition" else "transition")
                rec = AP.diag_record(snap, adv, b, ex, skipped, True, 0, other=other)
                rec = json.loads(json.dumps(rec, ensure_ascii=False))       # ファイルに書いて読み戻すのと同じ
                assert rec["scoring_mode"] == mode and rec["advice"]["scoring_mode"] == mode
                assert rec["other_mode"]["scoring_mode"] != mode
                assert rec["legal"]["moves"] == list(b.active_pokemon.moves) and rec["executed"] == ex
                assert rec["switchable_check"]["ok"] is True
                again = AP.replay_diag(rec, resolver)
                assert AP.ranking_of(again) == rec["advice"]["actions"], (mode, AP.ranking_of(again), rec["advice"]["actions"])
                assert len(rec["advice"]["actions"]) >= 4
                if mode == "transition":
                    assert rec["advice"]["scoring_fallback"] == again.get("scoring_fallback")
    finally:
        AP._SIM_BUILDS.clear()
        AP._SIM_MOVES.clear()
    print("test_diag_record_replay_reproduces OK")


def test_recorder_diag_field_optional(tmp=None):
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        rec = BattleRecorder(Path(d) / "b.jsonl", "c", "p")
        b = _battle()
        rec.on_decision(b, {"turn": 3}, {"ok": True, "best": {"kind": "move", "id": "earthquake"}, "actions": []},
                        {"kind": "move", "id": "earthquake"}, True)
        rec.on_decision(b, {"turn": 3}, None, None, True, diag={"state": {"x": 1}})
        turns = rec._turns["battle-test-1"]
        assert "diag" not in turns[0] and turns[1]["diag"] == {"state": {"x": 1}}
    # 既定はオフ (環境変数が無ければ無効、引数が優先)
    old = os.environ.pop("ADVICE_DIAG_LOG", None)
    try:
        assert AP.diag_log_enabled() is False and AP.diag_log_enabled(True) is True
        os.environ["ADVICE_DIAG_LOG"] = "1"
        assert AP.diag_log_enabled() is True and AP.diag_log_enabled(False) is False
    finally:
        os.environ.pop("ADVICE_DIAG_LOG", None)
        if old is not None:
            os.environ["ADVICE_DIAG_LOG"] = old
    print("test_recorder_diag_field_optional OK")


if __name__ == "__main__":
    test_picked_mask_from_last_request()
    test_battle_to_state_marks_only_picked()
    test_engine_counts_only_picked()
    test_choose_with_trace_reasons()
    test_diag_record_replay_reproduces()
    test_recorder_diag_field_optional()
