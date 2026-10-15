"""advisor-as-player の入力・合法性の修正 (2026-10-15 M3a) と測定の受入監査のテスト。

docs/incidents/reports/2026-09-04-sim-unpicked-members-in-evaluation.md §9 の残る不一致 2 種:
(1) 交代技の後の繰り出し (poke-env の要求 forceSwitch) がシミュレータの状態に無く、交代しか選べない場面で技を推奨していた、
(2) 技の固定・とらわれ: battle_to_state が選べる技に絞った後、登録の技の補完で選べない技が戻り、とらわれでも交代を推奨していた。
と、レビューの確認 3 点 (選出不明の入力不成立、switchable_check の未取得と既知の空集合、RL の控え) と、本番で常に残す軽い
受入監査 (第一候補の合法性・未選出の候補・説明できない飛ばし・ランダム退避) と、その集計 (usefulness_verdict / racing /
arc_remeasure_summary) を確かめる。

poke-env の Battle はダックタイピングの偽オブジェクト (tests/test_sim_picked_roster と同じ作り)。使用率 DB・RL・Showdown には
依存しない。

    python -m tests.test_sim_legal_inputs
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace as NS

os.environ.setdefault("RL_BLEND_WEIGHT", "0")

from advisor import scoring_transition as ST
from advisor.engine import SIM_SWITCH_BLOCKED_REASON, evaluate, legal_move_slots
from champions_agent.config import SIM_INPUTS_VERSION
from champions_agent.env import advisor_player as AP
from champions_agent.env import sim_accept as SA
from tests.test_scoring_transition import _Hint, resolver
from tests.test_sim_picked_roster import _OWN, _PICKED, _move, _pmon, _team_text

_UTURN_OWN = [("Garchomp", "garchomp", ("earthquake", "dragonclaw", "swordsdance", "outrage")),
              ("Primarina", "primarina", ("moonblast", "surf", "icebeam", "calmmind")),
              ("Corviknight", "corviknight", ("bravebird", "roost", "uturn", "bodypress"))] + list(_OWN[3:])


def _obs(events):
    return NS(events=[["", *e] for e in events])


def _battle(active="Garchomp", own=None, picked=_PICKED, fainted=(), avail_moves=None, req_moves=None,
            force_switch=False, trapped=False, maybe_trapped=False, switches=None, request=True, events=None,
            role="p1", opp_hp=0.7):
    """自分 6 体 (battle.team は 6 体のまま)、最後の要求 (選出 3 体 + 場の個体の技の要求)。
    avail_moves: available_moves の id (None なら場の個体の全技、forceSwitch なら空)。req_moves: 要求の active[0].moves
    [(id, disabled)] (None なら avail_moves を disabled なしで)。switches: available_switches の名前 (None なら選出・生存・控え、
    trapped なら空)"""
    own = own or _OWN
    mons = {f"p1: {name}": _pmon(sid, moves, fainted=name in fainted) for name, sid, moves in own}
    act = mons[f"p1: {active}"]
    opp = _pmon("heatran", ("magmastorm", "earthpower"), hp=opp_hp, item="unknown_item")
    if avail_moves is None:
        avail_moves = [] if force_switch else list(act.moves)
    if req_moves is None:
        req_moves = [(m, False) for m in avail_moves]
    if switches is None:
        switches = [] if trapped else [k[4:] for k, m in mons.items() if k[4:] in picked and m is not act and not m.fainted]
    req = {}
    if request:
        req = {"side": {"pokemon": [{"ident": f"p1: {n}", "details": n} for n in picked]}}
        if force_switch:
            req["forceSwitch"] = [True]
        else:
            a0 = {"moves": [{"move": m, "id": m, "disabled": d} for m, d in req_moves]}
            if trapped:
                a0["trapped"] = True
            if maybe_trapped:
                a0["maybeTrapped"] = True
            req["active"] = [a0]
    return NS(active_pokemon=act, opponent_active_pokemon=opp, team=mons, opponent_team={"p2: Heatran": opp},
              available_moves=[_move(m) for m in avail_moves], available_switches=[mons[f"p1: {n}"] for n in switches],
              can_mega_evolve=False, weather={}, fields={}, side_conditions={}, opponent_side_conditions={},
              turn=5, last_request=req, battle_tag="battle-legal-1", force_switch=force_switch, trapped=trapped,
              player_role=role, current_observation=_obs(events or []))


def _eval(st, mode):
    cap = {}
    orig = ST.score

    def spy(inp, budget_ms=None, sensitivity=None):
        cap["inp"] = inp
        return orig(inp, budget_ms=10_000, sensitivity=False)

    ST.score = spy
    try:
        adv = evaluate(copy.deepcopy(st), resolver, rl_blend_weight=0, scoring_mode=mode)
    finally:
        ST.score = orig
    return adv, cap.get("inp")


def _kinds(adv):
    return {(a["kind"], a["id"]) for a in adv.get("actions") or []}


class _Team:
    """自分の型をチームテキストで登録する (テストの間だけ)"""

    def __init__(self, own=None):
        self.own = own

    def __enter__(self):
        own = self.own or _OWN
        text = "\n\n".join(f"{name} @ leftovers\nLevel: 50\nEVs: 32 HP / 32 Atk / 2 Spe\nAdamant Nature\n"
                           + "\n".join(f"- {m}" for m in moves) for name, _sid, moves in own) if self.own else _team_text()
        AP.register_team_text(text, resolver)
        return self

    def __exit__(self, *a):
        AP._SIM_BUILDS.clear()
        AP._SIM_MOVES.clear()


# ------------------------------------------------------------------ 段 1: 交代技の後の繰り出し
def test_force_switch_pivot_only_switches():
    """forceSwitch (交代技の後、場の個体は生存): 状態に pending_pivot_switch、候補は交代だけ (legacy / transition)。
    相手がこのターンに行動済みなら transition は相手の技を受けない (応手なし)、未行動なら技を受ける"""
    with _Team(_UTURN_OWN), _Hint():
        acted = [("move", "p1a: Corviknight", "U-turn", "p2a: Heatran"), ("move", "p2a: Heatran", "Magma Storm", "p1a: X")]
        b = _battle(active="Corviknight", own=_UTURN_OWN, force_switch=True, events=acted)
        st = AP.battle_to_state(b, resolver)
        assert st["pending_pivot_switch"] is True and st["pivot_opp_acted"] is True, st
        assert st["player"]["party"][2]["legal_move_ids"] == []
        for mode in ("legacy", "transition"):
            adv, inp = _eval(st, mode)
            assert adv["ok"] and _kinds(adv) == {("switch", "garchomp"), ("switch", "primarina")}, (mode, _kinds(adv))
            assert adv["best"]["kind"] == "switch"
        _adv, inp = _eval(st, "transition")
        assert inp.responses is None and inp.response_mode is None          # 既に行動した相手の技を受け直さない
        # 相手が未行動 (自分の交代技が先): 繰り出した個体が相手の技を受ける
        b2 = _battle(active="Corviknight", own=_UTURN_OWN, force_switch=True, events=acted[:1])
        st2 = AP.battle_to_state(b2, resolver)
        assert st2["pivot_opp_acted"] is False
        _adv2, inp2 = _eval(st2, "transition")
        assert inp2.response_mode == "no_switch" and inp2.responses
        # 記録が読めない (自分の交代技がまだ記録に無い) → 印を入れない (素早さの見込みに戻る)
        st3 = AP.battle_to_state(_battle(active="Corviknight", own=_UTURN_OWN, force_switch=True, events=[]), resolver)
        assert st3["pending_pivot_switch"] is True and "pivot_opp_acted" not in st3
        # 交代先を選ぶ場面の決定は交代だけで、実行も交代 (choose_with_trace が技に落ちない)
        adv, _ = _eval(st, "legacy")
        d, skipped = AP.choose_with_trace(b, adv)
        assert d["kind"] == "switch" and skipped == []
        # 要求が無い (従来の偽 Battle) なら印を付けない
        assert "pending_pivot_switch" not in AP.battle_to_state(_battle(request=False), resolver)
    print("test_force_switch_pivot_only_switches OK")


def test_force_switch_after_faint_is_replace():
    """ひんし後の繰り出し: 場の個体は fainted、pending_pivot_switch は付けない (engine の my_fainted が交代だけにする)"""
    with _Team(), _Hint():
        b = _battle(fainted=("Garchomp",), force_switch=True)
        st = AP.battle_to_state(b, resolver)
        assert "pending_pivot_switch" not in st
        adv, inp = _eval(st, "transition")
        assert _kinds(adv) == {("switch", "primarina"), ("switch", "corviknight")}
        assert inp.responses is None and all(c.mode == "replace" for c in inp.candidates)
    print("test_force_switch_after_faint_is_replace OK")


# ------------------------------------------------------------------ 段 2: 技の固定・とらわれ
def test_locked_move_only_candidate_no_registration_revival():
    """げきりん等の固定 (available_moves が 1 つ、要求の技も 1 つ): 候補はその技 (と交代) だけ。登録の技の補完で他の技を戻さない。
    選べない技は illegal_actions に simulator の理由で残る"""
    with _Team(_UTURN_OWN), _Hint():
        b = _battle(active="Garchomp", own=_UTURN_OWN, avail_moves=["outrage"], trapped=True)
        st = AP.battle_to_state(b, resolver)
        act = st["player"]["party"][0]
        assert [m["move_id"] for m in act["moves"]] == ["earthquake", "dragonclaw", "swordsdance", "outrage"]
        assert act["legal_move_ids"] == ["outrage"]
        assert act["move_block_reasons"]["earthquake"] == AP.SIM_REASON_LOCKED
        for mode in ("legacy", "transition"):
            adv, _ = _eval(st, mode)
            assert _kinds(adv) == {("move", "outrage")}, (mode, _kinds(adv))
            ill = {(a["kind"], a["id"]): a["reason"] for a in adv["illegal_actions"]}
            assert ill[("move", "earthquake")] == AP.SIM_REASON_LOCKED
            assert ill[("switch", "primarina")] == AP.SIM_REASON_TRAPPED
        # 修正前の作り (選べる技に絞って渡す = 要求が無い経路): 技の枠が 1 つになり、登録の技の補完で他の技が戻る
        b0 = _battle(active="Garchomp", own=_UTURN_OWN, avail_moves=["outrage"], request=False)
        adv0, _ = _eval(AP.battle_to_state(b0, resolver), "legacy")
        assert {"earthquake", "dragonclaw"} <= {i for k, i in _kinds(adv0) if k == "move"}, _kinds(adv0)
    print("test_locked_move_only_candidate_no_registration_revival OK")


def test_disabled_moves_and_slots():
    """要求で disabled の技 (こだわり・PP 0 等) は選べない (理由 simulator: disabled)。シミュレータが選べると言う技は、
    エンジンの推定 (初手限定等) より優先して選べる。状態に無い選べる技 (わるあがき) は枠を足す"""
    with _Team(), _Hint():
        mv = ["earthquake", "dragonclaw", "swordsdance", "stoneedge"]
        b = _battle(avail_moves=["earthquake"], req_moves=[(m, m != "earthquake") for m in mv])
        st = AP.battle_to_state(b, resolver)
        act = st["player"]["party"][0]
        assert act["move_block_reasons"] == {m: AP.SIM_REASON_DISABLED for m in mv[1:]}
        leg = legal_move_slots(st, act, registered=[("じしん", "earthquake"), ("インファイト", "closecombat")])
        assert leg.legal_move_ids == ["earthquake"] and all(s.get("move_id") != "closecombat" for s in leg.slots)
        # わるあがき: 選べる技が状態の技に無ければ枠を足す
        b2 = _battle(avail_moves=["struggle"], req_moves=[("struggle", False)])
        act2 = AP.battle_to_state(b2, resolver)["player"]["party"][0]
        assert act2["moves"][-1]["move_id"] == "struggle" and act2["moves"][-1]["from_simulator"]
        assert act2["legal_move_ids"] == ["struggle"]
        # 実機の状態 (legal_move_ids が無い) は従来どおり: 登録の補完あり
        p = {"moves": [{"move_id": "earthquake", "name_ja": "じしん", "pp": 5}]}
        assert [s["move_id"] for s in legal_move_slots({}, p, registered=[("インファイト", "closecombat")]).slots] == \
            ["earthquake", "closecombat"]
    print("test_disabled_moves_and_slots OK")


def test_trapped_no_switch_candidates_and_rl():
    """とらわれ: 交代候補を出さない (legacy / transition / RL の合法手)。選出した控えは残存戦力 (transition の控え) に残る"""
    from advisor.rl_bridge import _legal_actions
    with _Team(), _Hint():
        b = _battle(trapped=True)
        st = AP.battle_to_state(b, resolver)
        me = st["player"]
        assert me["legal_switch_indices"] == [] and me["switch_block_reason"] == AP.SIM_REASON_TRAPPED
        for mode in ("legacy", "transition"):
            adv, inp = _eval(st, mode)
            assert all(k == "move" for k, _i in _kinds(adv)) and _kinds(adv), (mode, _kinds(adv))
        _adv, inp = _eval(st, "transition")
        assert sorted(v.species_id for v, _hp in inp.me.bench) == ["corviknight", "primarina"]   # 残存戦力は選出 2 体
        assert all(c.kind == "move" for c in inp.candidates)
        assert all(k != "switch" for _j, _l, k in _legal_actions(st))
        # maybeTrapped (相手の特性が分からないだけ) は交代を出す
        stm = AP.battle_to_state(_battle(maybe_trapped=True), resolver)
        assert "legal_switch_indices" not in stm["player"]
        assert {("switch", "primarina"), ("switch", "corviknight")} <= _kinds(_eval(stm, "legacy")[0])
        # 交代先が選出・生存の控えと同じなら欄を足さない、違えば (選出の印の誤り等) 実行環境の交代先で絞る
        stx = AP.battle_to_state(_battle(switches=["Primarina"]), resolver)
        assert stx["player"]["legal_switch_indices"] == [1]
        assert "switch_block_reason" not in stx["player"]
        advx, _ = _eval(stx, "legacy")
        assert ("switch", "corviknight") not in _kinds(advx)
        assert {"kind": "switch", "id": "corviknight"}.items() <= next(
            a for a in advx["illegal_actions"] if a["id"] == "corviknight").items()
        assert next(a for a in advx["illegal_actions"] if a["id"] == "corviknight")["reason"] == SIM_SWITCH_BLOCKED_REASON
    print("test_trapped_no_switch_candidates_and_rl OK")


# ------------------------------------------------------------------ 段 3: レビューの確認
def test_switchable_check_unknown_vs_known_empty():
    """available_switches が空: 要求が無ければ未取得 (照合しない)、要求があれば既知の空集合 (状態の交代先も空か検査)"""
    st = AP.battle_to_state(_battle(trapped=True), resolver)
    chk = AP.switchable_check(_battle(trapped=True), st)
    assert chk["status"] == "known_empty" and chk["ok"] is True and chk["state"] == []
    # 状態に交代先の制限が無い (修正前の状態) なら既知の空集合と食い違う
    st_old = copy.deepcopy(st)
    st_old["player"].pop("legal_switch_indices")
    bad = AP.switchable_check(_battle(trapped=True), st_old)
    assert bad["status"] == "known_empty" and bad["ok"] is False and bad["state"] == ["corviknight", "primarina"]
    # 要求が無い: 未取得
    st0 = AP.battle_to_state(_battle(request=False, switches=[]), resolver)
    unk = AP.switchable_check(_battle(request=False, switches=[]), st0)
    assert unk["status"] == "unknown" and unk["ok"] is None
    # 空でなければ従来どおり (欄も同じ)
    full = AP.switchable_check(_battle(), AP.battle_to_state(_battle(), resolver))
    assert full == {"ok": True, "state": ["corviknight", "primarina"], "poke_env": ["corviknight", "primarina"]}
    assert AP.switchable_check(_battle(), None) is None
    print("test_switchable_check_unknown_vs_known_empty OK")


def test_sim_legality_and_opp_acted():
    leg = AP.sim_legality(_battle(avail_moves=["earthquake"], req_moves=[("earthquake", False), ("stoneedge", True)],
                                  trapped=True))
    assert leg["known"] and leg["trapped"] and not leg["force_switch"] and leg["moves"] == ["earthquake"]
    assert leg["request_moves"] == {"earthquake": False, "stoneedge": True} and leg["switches"] == []
    assert AP.sim_legality(_battle(request=False))["known"] is False
    assert AP.sim_legality(_battle(force_switch=True))["force_switch"] is True
    # 相手の行動: 技・行動不能・交代。自分の技の記録が無ければ None。p2 側でも同じ
    me = ("move", "p1a: Corviknight", "U-turn", "p2a: Heatran")
    assert AP.opp_acted_this_turn(_battle(events=[me])) is False
    assert AP.opp_acted_this_turn(_battle(events=[("cant", "p2a: Heatran", "par"), me])) is True
    assert AP.opp_acted_this_turn(_battle(events=[("switch", "p2a: Gholdengo", "Gholdengo, L50", "100/100"), me])) is True
    assert AP.opp_acted_this_turn(_battle(events=[("move", "p2a: Heatran", "Magma Storm", "p1a: X")])) is None
    b = _battle(role="p2", events=[("move", "p2a: Corviknight", "U-turn", "p1a: X"), ("move", "p1a: Heatran", "Lava Plume")])
    assert AP.opp_acted_this_turn(b) is True
    assert AP.opp_acted_this_turn(NS(player_role="p1")) is None
    print("test_sim_legality_and_opp_acted OK")


def test_only_legal_action():
    one = _battle(avail_moves=["struggle"], trapped=True)
    d = AP.only_legal_action(one)
    assert d["kind"] == "move" and d["move"].id == "struggle" and d["mega"] is False
    sw = _battle(force_switch=True, switches=["Primarina"])
    assert AP.only_legal_action(sw)["pokemon"].species == "primarina"
    assert AP.only_legal_action(_battle()) is None
    print("test_only_legal_action OK")


# ------------------------------------------------------------------ 段 4: 受入監査
def test_decision_flags():
    adv = {"ok": True, "best": {"kind": "switch", "id": "gholdengo"},
           "actions": [{"kind": "switch", "id": "gholdengo"}, {"kind": "move", "id": "earthquake"}]}
    picked = ["garchomp", "primarina", "corviknight"]
    f = SA.decision_flags(adv, adv, [{"kind": "switch", "id": "gholdengo", "why": "switch_not_picked"}],
                          ["earthquake"], ["primarina"], True, picked, random_fallback=False)
    assert f["best_illegal"] and f["unpicked_candidate"] and f["skip_unexplained"] and not f["skip_explained"]
    assert not f["picked_unknown"] and not f["random_fallback"] and f["decisions"]
    # 要求が無い (状態にシミュレータの合法手が無い) ときの飛ばしは説明できる
    f2 = SA.decision_flags(adv, adv, [{"kind": "switch", "id": "gholdengo"}], ["earthquake"], [], False, None,
                           random_fallback=False)
    assert f2["skip_explained"] and not f2["skip_unexplained"] and f2["picked_unknown"] and not f2["unpicked_candidate"]
    ok = {"ok": True, "best": {"kind": "move", "id": "earthquake"}, "actions": [{"kind": "move", "id": "earthquake"},
                                                                            {"kind": "switch", "id": "primarina"}]}
    f3 = SA.decision_flags(ok, ok, [], ["earthquake"], ["primarina"], True, picked, random_fallback=False)
    assert SA.raised(f3) == {}
    # 行動ノイズ (2 位を実行) は飛ばしではない。助言なし → ランダム退避 / 合法手 1 つの実行
    noisy = dict(ok, best=ok["actions"][1])
    assert not SA.decision_flags(ok, noisy, [], ["earthquake"], ["primarina"], True, picked, False)["skip_unexplained"]
    f4 = SA.decision_flags(None, None, [], [], [], True, picked, random_fallback=True)
    assert f4["random_fallback"] and not f4["best_illegal"]
    assert SA.decision_flags(None, None, [], ["struggle"], [], True, picked, False, forced=True)["forced_single"]
    stats, per = {}, {}
    for fl in (f, f3, f4):
        SA.add_flags(stats, fl, SA.STATS_PREFIX)
        SA.add_flags(per, fl)
    s = SA.summary_from_stats(stats)
    assert s["decisions"] == 3 and s["best_illegal"] == 1 and s["random_fallback"] == 1 and s["inputs_version"] == SIM_INPUTS_VERSION
    assert per["unpicked_candidate"] == 1 and "accept:decisions" in stats
    v = SA.accept_violations(s)
    assert len(v) == 4 and any("第一候補が不合法 1 件" in x for x in v), v     # 不合法・未選出・飛ばし・ランダム
    assert SA.accept_violations(SA.summary_from_stats({})) == []
    assert SA.accept_violations(dict(s), limits={k: 5 for k in SA.ACCEPT_KEYS}) == []
    assert "受入監査の記録が無い" in SA.accept_violations(None)[0]
    tot = SA.sum_battle_accept([{"accept": {"best_illegal": 1, "decisions": 4}}, {"accept": {"decisions": 2}}, {"won": True}])
    assert tot["best_illegal"] == 1 and tot["decisions"] == 6 and tot["battles"] == 3 and tot["battles_missing"] == 1
    assert SA.version_warnings({"a": SIM_INPUTS_VERSION, "b": SIM_INPUTS_VERSION}) == []
    w = SA.version_warnings({"a": SIM_INPUTS_VERSION, "b": None})
    assert len(w) == 2 and "混ざっている" in w[0] and "b" in w[1]
    print("test_decision_flags OK")


def test_player_counts_accept_and_records(tmp=None):
    """make_advisor_player の choose_move を偽 Battle で直接呼び、stats と対戦記録に受入監査が残ることを確かめる
    (選出不明は picked_unknown と決定の印、合法手 1 つは forced_single、通常の決定は印なし)"""
    from tools.team_build.battle_log import BattleRecorder
    with tempfile.TemporaryDirectory() as d, _Team(), _Hint():
        rec = BattleRecorder(Path(d) / "b.jsonl", "c", "p")
        stats: dict = {}
        player = AP.make_advisor_player(team_source=NS(last_text=_team_text()), stats=stats, recorder=rec,
                                        start_listening=False, battle_format="gen9championsbssregmc")
        player.create_order = lambda x, mega=False: ("order", getattr(x, "id", None) or getattr(x, "species", None))
        player.choose_random_move = lambda b: ("random",)
        b = _battle()
        assert player.choose_move(b)[0] == "order"
        nb = _battle(request=False)                         # 選出が分からない (要求なし)
        player.choose_move(nb)
        one = _battle(avail_moves=["struggle"], req_moves=[("struggle", False)], trapped=True)
        player.choose_move(one)
        assert stats["picked_unknown"] == 1 and stats["accept:picked_unknown"] == 1 and stats["accept:decisions"] == 3
        assert stats.get("accept:best_illegal", 0) == 0 and stats.get("accept:unpicked_candidate", 0) == 0
        assert stats.get("accept:skip_unexplained", 0) == 0 and stats.get("accept:random_fallback", 0) == 0, stats
        turns = rec._turns["battle-legal-1"]
        assert "accept" not in turns[0] and turns[1]["accept"]["picked_unknown"] is True
        acc = SA.summary_from_stats(stats)
        assert SA.accept_violations(acc) and any("選出が分からない" in x for x in SA.accept_violations(acc))
    print("test_player_counts_accept_and_records OK")


def _rec(i, acc=None, sha="s1"):
    r = {"won": bool(i % 2), "opponent_team_id": f"t{i % 33}", "own_team_sha16": sha,
         "ref_check": {"n": 3, "mismatch": 0, "unbuilt": 0}}
    if acc is not None:
        r["accept"] = acc
    return r


def test_verdict_accept_invalid():
    from tools import usefulness_verdict as V
    SC_BASE, SC_TR = "scoring:legacy", "scoring:transition"
    good = dict({k: 0 for k in SA.COUNT_KEYS}, decisions=10, inputs_version=SIM_INPUTS_VERSION)
    recs = {c: [_rec(i, dict(good)) for i in range(600)] for c in (SC_BASE, SC_TR)}
    vers = {SC_BASE: SIM_INPUTS_VERSION, SC_TR: SIM_INPUTS_VERSION}
    reasons, summ = V.accept_audit_check(recs, [SC_BASE, SC_TR], 600, vers)
    assert reasons == [] and summ["per_condition"][SC_TR]["decisions"] == 6000
    bad = copy.deepcopy(recs)
    bad[SC_TR][7]["accept"]["random_fallback"] = 1
    bad[SC_BASE][3]["accept"].pop("best_illegal")
    del bad[SC_BASE][9]["accept"]
    r2, _ = V.accept_audit_check(bad, [SC_BASE, SC_TR], 600, vers)
    assert any("ランダム行動への退避 1 件" in x and SC_TR in x for x in r2) and any("accept) の無い対戦 1 件" in x for x in r2), r2
    r3, _ = V.accept_audit_check(recs, [SC_BASE, SC_TR], 600, {SC_BASE: SIM_INPUTS_VERSION, SC_TR: None})
    assert any("混ざっている" in x for x in r3)
    # build_verdict: 受入を満たさなければ invalid (採否を出さない)
    outs = {c: [1 if i % 3 else 0 for i in range(600)] for c in (SC_BASE, SC_TR)}
    ids = {c: [f"t{i % 33}" for i in range(600)] for c in outs}
    kw = dict(final_battles=600, mde=0.05, alpha=0.05, opp_ids_by_cond=ids, baseline_cond=SC_BASE, compare_conds=[SC_TR])
    v_ok = V.build_verdict({}, outs, accept_records_by_cond=recs, inputs_versions=vers, **kw)
    assert v_ok["mode"] == V.MODE_FINAL and v_ok["accept_audit"]["n"] == 600, v_ok["invalid_reasons"]
    v_bad = V.build_verdict({}, outs, accept_records_by_cond=bad, inputs_versions=vers, **kw)
    assert v_bad["mode"] == V.MODE_INVALID and v_bad["comparisons"][0]["decision"] is None
    # own_check: 選出不明 (入力不成立) を含む対戦があれば不成立
    pu = copy.deepcopy(recs)
    pu[SC_BASE][0]["accept"]["picked_unknown"] = 2
    own_reasons, own_summ = V.own_correspondence_check(pu, [SC_BASE, SC_TR], 600)
    assert any("入力不成立" in x for x in own_reasons) and own_summ["picked_unknown_battles"] == {SC_BASE: 1}
    assert V.own_correspondence_check(recs, [SC_BASE, SC_TR], 600)[0] == []
    # run: 条件表が accept_check を指定していればファイルから確かめる。無ければ注記だけ
    with tempfile.TemporaryDirectory() as d:
        out = Path(d)
        cond = {"experiment": "P1", "factor": "scoring", "baseline": SC_BASE, "k": 1, "final_battles": 600, "mde": 0.05,
                "alpha": 0.05, "incomplete": False, "accept_check": {"required": True}}
        (out / "conditions.json").write_text(json.dumps(cond), encoding="utf-8")
        for c in (SC_BASE, SC_TR):
            f = V.cond_file(c)
            (out / f"{f}.json").write_text(json.dumps({"outcomes": outs[c], "inputs_version": SIM_INPUTS_VERSION}),
                                           encoding="utf-8")
            (out / f"{f}.battles.jsonl").write_text("".join(json.dumps(r) + "\n" for r in bad[c]), encoding="utf-8")
        assert V.run(out)["mode"] == V.MODE_INVALID
        for c in (SC_BASE, SC_TR):
            (out / f"{V.cond_file(c)}.battles.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs[c]),
                                                                 encoding="utf-8")
        assert V.run(out)["mode"] == V.MODE_FINAL
        (out / f"{V.cond_file(SC_TR)}.json").write_text(json.dumps({"outcomes": outs[SC_TR]}), encoding="utf-8")
        assert V.run(out)["mode"] == V.MODE_INVALID                       # 版の無い (修正前の) 結果が混ざる
        cond.pop("accept_check")
        (out / "conditions.json").write_text(json.dumps(cond), encoding="utf-8")
        v = V.run(out)
        assert v["mode"] == V.MODE_FINAL and any("accept_check" in n for n in v["notes"])
    print("test_verdict_accept_invalid OK")


def test_p1_conditions_require_accept():
    from tools import usefulness_p1 as P
    assert P.ACCEPT_CHECK["required"] is True and P.ACCEPT_CHECK["inputs_version"] == SIM_INPUTS_VERSION
    print("test_p1_conditions_require_accept OK")


def test_racing_withholds_and_warns():
    import importlib
    from tools.team_build import racing as R
    R = importlib.reload(R)
    good = dict({k: 0 for k in SA.COUNT_KEYS}, inputs_version=SIM_INPUTS_VERSION)

    def stub(bad_arm=None, version=SIM_INPUTS_VERSION):
        def _measure(arms, n, offset, seed, split_file, tier, fold, out_dir, stage, parallel=1, timeout=0,
                     pick_policy="advisor"):
            for arm in arms:
                arm.outcomes.extend([1 if (i + len(arm.arm_id)) % 2 else 0 for i in range(n)])
                arm.n_done = len(arm.outcomes)
                acc = dict(good, random_fallback=1) if arm.arm_id == bad_arm else dict(good)
                arm.history.append({"offset": offset, "n": n, "accept": acc,
                                    "inputs_version": version if arm.arm_id == "c1" else SIM_INPUTS_VERSION})
        return _measure
    with tempfile.TemporaryDirectory() as d:
        R.measure_round = stub(bad_arm="c1")
        try:
            R.race([R.Arm("c1", Path("c1.txt"))], R.Arm("ref", Path("r.txt")), Path("s.json"), "search", 1, Path(d),
                   stage="t", steps=(100, 300), max_battles=300, log=lambda m: None)
            raise AssertionError("受入を満たさない round で順位判定を出した")
        except R.RacingAcceptError:
            pass
        res = json.loads((Path(d) / "t.json").read_text(encoding="utf-8"))
        assert res["ranking_withheld"] and R.contenders(res) == [] and res["accept"]["problems"]
        assert res["arms"][0]["n_done"] == 100                                 # 次の round に進まない
        R.measure_round = stub(version=None)
        res2 = R.race([R.Arm("c1", Path("c1.txt"))], R.Arm("ref", Path("r.txt")), Path("s.json"), "search", 1, Path(d),
                      stage="t2", steps=(100,), max_battles=100, log=lambda m: None)
        assert not res2["ranking_withheld"] and res2["inputs_version"] == SIM_INPUTS_VERSION
        assert any("混ざっている" in w for w in res2["warnings"]), res2["warnings"]
        assert R.contenders(res2) == R.contenders(dict(res2, ranking_withheld=False))
    importlib.reload(R)
    print("test_racing_withholds_and_warns OK")


def test_arc_summary_accept():
    from tools import arc_remeasure_summary as S
    good = dict({k: 0 for k in SA.COUNT_KEYS}, inputs_version=SIM_INPUTS_VERSION)

    def doc(outs, acc=None, ver=SIM_INPUTS_VERSION):
        d = {"n_battles": len(outs), "wins": sum(outs), "outcomes": list(outs), "stats": {}, "search_blend": 0.0,
             "inputs_version": ver}
        if acc is not None:
            d["accept"] = acc
        return d
    a, b = [1, 0] * 150, [1, 1] * 75 + [1, 0] * 75
    old = {"p9_600": {"A": doc(a), "B": doc(a)}}
    ok = S.build_summary({"p9_600": {"A": doc(a, good), "B": doc(b, good)}}, old, smoke=False)["verdicts"][0]
    assert ok["new"]["verdict"] == S.ADOPT and ok["accept_problems"] == [] and ok["accept_audited"]
    bad = S.build_summary({"p9_600": {"A": doc(a, good), "B": doc(b, dict(good, best_illegal=3))}}, old,
                          smoke=False)
    v = bad["verdicts"][0]
    assert v["new"]["verdict"] == S.ACCEPT_FAILED and v["changed"] is None and "B:" in v["accept_problems"][0]
    assert "受入不成立" in S.render(bad)
    mixed = S.build_summary({"p9_600": {"A": doc(a, good), "B": doc(b, good, ver="old")}}, old, smoke=False)
    assert mixed["verdicts"][0]["version_warnings"] and "警告" in S.render(mixed)
    print("test_arc_summary_accept OK")


if __name__ == "__main__":
    test_force_switch_pivot_only_switches()
    test_force_switch_after_faint_is_replace()
    test_locked_move_only_candidate_no_registration_revival()
    test_disabled_moves_and_slots()
    test_trapped_no_switch_candidates_and_rl()
    test_switchable_check_unknown_vs_known_empty()
    test_sim_legality_and_opp_acted()
    test_only_legal_action()
    test_decision_flags()
    test_player_counts_accept_and_records()
    test_verdict_accept_invalid()
    test_p1_conditions_require_accept()
    test_racing_withholds_and_warns()
    test_arc_summary_accept()
