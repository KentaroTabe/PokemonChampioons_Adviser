"""自分の選出の 3 値の判定 (advisor.party.pick_state) と、交代候補・残存戦力・探索・RL の合法手・終盤判定への適用のテスト
(2026-10-20 レビュー・開発者承認、docs/REAL_ACCEPT_TRANSITION_20261020.md §3・§6)。

選出の印 (is_picked) が足りないと全員を交代先として許可していたため、10/20 の接続テスト 2 戦目の対戦冒頭 (印なし) に、実際に
選出されていないブリジュラスへの交代を第一候補に出した (助言 20261020_085420-0052)。その局面は本体の対戦ログの advice 行を
tests/fixtures/pick_state/advice_0052.json に写した (ログはコミットしない)。CI には登録チーム (config/my_team.json)・使用率 DB・
RL のモデルが無いので、場の個体の技は状態に入れ、相手の技は判明技だけ、RL の分布は使わない (rl_bridge.policy_hint を差し替える)。

    python -m tests.test_pick_state
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

os.environ.setdefault("RL_BLEND_WEIGHT", "0")

import advisor.endgame as EG
import advisor.engine as ENG
import advisor.rl_bridge as RB
from advisor import scoring_transition as ST
from advisor import valuation as VA
from advisor.engine import evaluate, my_unknown_alive, sim_bench
from advisor.party import (battle_party_indices, has_appearance_record, is_switchable, pick_state, species_certain,
                           unknown_alive_count, usable_indices)
from advisor.rl_bridge import _legal_actions
from advisor.service import pick_state_field
from advisor.transition import SimSide
from tools.advice_replay import compact_to_engine_state, replay_now
from vision.normalize import NameResolver

resolver = NameResolver()
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "pick_state" / "advice_0052.json"

_ORIG_HINT = RB.policy_hint
_ORIG_POOL = ENG.opponent_move_pool


def _revealed_only_pool(opp_p, opp_view, resolver):
    out = []
    for ja in (opp_p or {}).get("revealed_moves") or []:
        r = resolver.resolve(ja, "moves", cutoff=0.7) if resolver is not None else None
        if r and r[1] not in {m for m, _w in out}:
            out.append((r[1], 100.0))
    return out


class _Env:
    """RL の分布を使わず、相手の技プールを判明技だけにする (テストの間だけ。使用率 DB・RL のモデルに依存しない)"""

    def __enter__(self):
        RB.policy_hint = lambda *a, **k: None
        ENG.opponent_move_pool = _revealed_only_pool
        return self

    def __exit__(self, *a):
        RB.policy_hint = _ORIG_HINT
        ENG.opponent_move_pool = _ORIG_POOL


# ------------------------------------------------------------------ 合成の局面
MOVES = {
    "blastoise": [("なみのり", "surf"), ("れいとうビーム", "icebeam")],
    "indeedee": [("サイコキネシス", "psychic")],
    "sneasler": [("インファイト", "closecombat")],
    "kingambit": [("ドゲザン", "kowtowcleave")],
    "salamence": [("だいもんじ", "fireblast")],
    "archaludon": [("てっていこうせん", "steelbeam"), ("ラスターカノン", "flashcannon")],
}
ROSTER = [("blastoise", "カメックス", ["みず"]), ("indeedee", "イエッサン", ["エスパー", "ノーマル"]),
          ("sneasler", "オオニューラ", ["かくとう", "どく"]), ("kingambit", "ドドゲザン", ["あく", "はがね"]),
          ("salamence", "ボーマンダ", ["ドラゴン", "ひこう"]), ("archaludon", "ブリジュラス", ["はがね", "ドラゴン"])]


def _mon(sid, ja, types, picked=False, hp=None, active=False, **kw):
    d = {"species_id": sid, "species_ja": ja, "types": list(types), "hp_percent": hp, "hp_current": None, "hp_max": None,
         "status": None, "boosts": {}, "ability_id": None, "item_id": None, "is_active": active, "is_picked": picked,
         "moves": [{"name_ja": a, "move_id": b, "pp": 5, "max_pp": None, "effectiveness": None} for a, b in MOVES[sid]],
         "revealed_moves": []}
    d.update(kw)
    return d


def _my_state(active=0, picked=(), appeared=(), remaining=3, active_hp=100.0):
    """自分の 6 体 (ROSTER の順)。picked = 選出の印、appeared = HP を観測済み (確かな登場記録)"""
    party = []
    for i, (sid, ja, types) in enumerate(ROSTER):
        hp = active_hp if i == active else (100.0 if i in appeared else None)
        party.append(_mon(sid, ja, types, picked=i in picked, hp=hp, active=(i == active)))
    return {"active_index": active, "remaining": remaining, "tailwind": False,
            "hazards": {"stealth_rock": False, "spikes": 0, "toxic_spikes": 0, "sticky_web": False},
            "screens": {"reflect": False, "light_screen": False, "aurora_veil": False}, "party": party}


def _state(my_state, turn=3):
    opp = {"species_id": "garchomp", "species_ja": "ガブリアス", "types": ["ドラゴン", "じめん"], "hp_percent": 100.0,
           "hp_current": None, "hp_max": None, "status": None, "boosts": {}, "ability_id": None, "item_id": None,
           "moves": [], "revealed_moves": ["じしん"], "is_picked": False, "is_active": True}
    return {"field": {"weather": None, "terrain": None, "trick_room": False}, "turn": turn, "scene": "command",
            "mega_used": {"player": False, "opponent": False}, "player": my_state,
            "opponent": {"active_index": 0, "remaining": 3, "tailwind": False,
                         "hazards": {"stealth_rock": False, "spikes": 0, "toxic_spikes": 0, "sticky_web": False},
                         "screens": {"reflect": False, "light_screen": False, "aurora_veil": False}, "party": [opp]}}


def _switch_ids(adv) -> set:
    return {a["id"] for a in adv.get("actions") or [] if a.get("kind") == "switch"}


def _fixture_state():
    row = json.loads(FIXTURE.read_text(encoding="utf-8"))
    st = compact_to_engine_state(row["state"], resolver, row.get("move_locks"))
    st["scene"] = row["state"].get("scene")
    st["now"] = replay_now(row)
    return row, st


# ------------------------------------------------------------------ 純粋な判定
def test_pure_judgement():
    # 種族の同定が確かでない個体 (選出画面の推定・形態の候補・対応待ち) は登場記録に数えない
    assert species_certain({"species_id": "blastoise"})
    assert not species_certain({"species_id": "blastoise", "species_guess": True})
    assert not species_certain({"species_id": "slowking", "species_candidates": ["slowking", "slowkinggalar"]})
    assert not species_certain({"species_id": "blastoise", "pending": True})
    assert not species_certain({})
    for rec in ({"hp_percent": 100.0}, {"hp_read_ts": 1.0}, {"is_active": True}, {"pick_order": 1}, {"entry_turn": 0},
                {"status": "fainted"}):
        assert has_appearance_record(dict(rec, species_id="blastoise")), rec
        assert not has_appearance_record(dict(rec, species_id="blastoise", species_guess=True)), rec
    assert not has_appearance_record({"species_id": "blastoise", "hp_percent": None, "is_active": False})

    # 印なし・場の 1 体だけ: picked = 場の個体、残りは unknown (全員許可にしない)
    ps = pick_state(_my_state(active=1))
    assert ps.picked == {1} and ps.unknown == {0, 2, 3, 4, 5} and not ps.not_picked and ps.unknown_count == 2, ps
    assert battle_party_indices(_my_state(active=1)) == {1} and not is_switchable(_my_state(active=1), 5)
    # 2/3 判明 (印 2 体)
    ps = pick_state(_my_state(active=0, picked=(0, 2)))
    assert ps.picked == {0, 2} and ps.unknown_count == 1 and not ps.not_picked, ps
    # 3 体確定 (印): 残りは not_picked。印が 3 体分あれば登場記録 (HP) のある印の無い個体も入れない (従来どおり)
    ps = pick_state(_my_state(active=0, picked=(0, 1, 2), appeared=(5,)))
    assert ps.picked == {0, 1, 2} and ps.not_picked == {3, 4, 5} and not ps.unknown and ps.unknown_count == 0, ps
    # 印 1 + 登場記録 2 で 3 体: 確定
    ps = pick_state(_my_state(active=0, picked=(0,), appeared=(3, 4)))
    assert ps.picked == {0, 3, 4} and ps.not_picked == {1, 2, 5} and ps.unknown_count == 0, ps
    # 7 体目以降 (誤読で生えた枠) は数えない
    st = _my_state(active=0)
    st["party"].append(dict(st["party"][5], species_id="raichu", species_ja="ライチュウ", hp_percent=50.0))
    assert 6 not in usable_indices(st)
    # party が 6 体そろわない合成の状態: 状態に無い個体は unknown_count に数えない
    st = _my_state(active=0)
    st["party"] = st["party"][:1]
    assert pick_state(st).unknown_count == 0
    # advice の欄
    f = pick_state_field(_state(_my_state(active=0, picked=(0, 2))))
    assert f == {"picked": ["blastoise", "sneasler"], "unknown_count": 1, "not_picked": []}, f
    print("test_pure_judgement OK")


# ------------------------------------------------------------------ (a) 0052 の局面
def test_a_0052_no_unpicked_switch():
    """助言 0052 (印なし・場はイエッサン、HP を観測済みは場の 1 体だけ): 当時は第一候補がブリジュラスへの交代で、交代の候補に
    5 体が並んだ。修正後は第一候補も候補もブリジュラスへの交代を含まない (選出が未判明の個体への交代を出さない)"""
    row, st = _fixture_state()
    assert row["recorded_best"]["id"] == "archaludon" and "archaludon" in row["recorded_switches"]
    my = st["player"]
    assert not any(p.get("is_picked") for p in my["party"])
    assert my["party"][my["active_index"]]["species_id"] == "indeedee"
    # CI には登録チームが無いので、場の個体の技を状態に入れる (技が無いと行動の候補が交代だけになる)
    act = my["party"][my["active_index"]]
    act["moves"] = [{"name_ja": "サイコキネシス", "move_id": "psychic", "pp": 10, "max_pp": None, "effectiveness": None}]
    ps = pick_state(my)
    assert ps.picked == {my["active_index"]} and ps.unknown_count == 2 and not ps.not_picked, ps
    assert pick_state_field(st) == {"picked": ["indeedee"], "unknown_count": 2, "not_picked": []}
    with _Env():
        for mode in ("legacy", "transition"):
            adv = evaluate(copy.deepcopy(st), resolver, rl_blend_weight=0, scoring_mode=mode)
            assert adv["ok"], (mode, adv.get("reason"))
            assert adv["actions"], mode
            assert (adv["best"]["kind"], adv["best"]["id"]) != ("switch", "archaludon"), (mode, adv["best"])
            assert "archaludon" not in _switch_ids(adv), (mode, _switch_ids(adv))
            assert not _switch_ids(adv), (mode, _switch_ids(adv))      # 選出が判明した控えが無い
    print("test_a_0052_no_unpicked_switch OK")


# ------------------------------------------------------------------ (b) (c) (d) 交代の候補
def test_b_two_of_three_known():
    """2/3 判明 (カメックス・オオニューラに印): 印の無いイエッサン等への交代を候補に出さない"""
    st = _state(_my_state(active=0, picked=(0, 2)))
    with _Env():
        for mode in ("legacy", "transition"):
            adv = evaluate(copy.deepcopy(st), resolver, rl_blend_weight=0, scoring_mode=mode)
            assert adv["ok"], (mode, adv.get("reason"))
            assert _switch_ids(adv) == {"sneasler"}, (mode, _switch_ids(adv))
    print("test_b_two_of_three_known OK")


def test_c_three_known_unchanged():
    """3 体確定 (印) なら従来と同じ: 印の 3 体だけ (登場記録のある印の無い個体も入れない)"""
    st = _state(_my_state(active=0, picked=(0, 1, 2), appeared=(5,)))
    assert battle_party_indices(st["player"]) == {0, 1, 2}
    with _Env():
        for mode in ("legacy", "transition"):
            adv = evaluate(copy.deepcopy(st), resolver, rl_blend_weight=0, scoring_mode=mode)
            assert _switch_ids(adv) == {"indeedee", "sneasler"}, (mode, _switch_ids(adv))
    print("test_c_three_known_unchanged OK")


def test_d_appearance_record():
    """確かな登場記録 (HP 観測済み) の個体は候補に入る。種族が推定 (species_guess / 形態の候補) の個体は入らない"""
    my = _my_state(active=0, picked=(0,), appeared=(3, 5))
    my["party"][5]["species_guess"] = True          # ブリジュラスは選出画面の推定
    st = _state(my)
    with _Env():
        adv = evaluate(copy.deepcopy(st), resolver, rl_blend_weight=0, scoring_mode="legacy")
    assert _switch_ids(adv) == {"kingambit"}, _switch_ids(adv)
    my["party"][5]["species_guess"] = False
    my["party"][5]["species_candidates"] = ["archaludon", "duraludon"]
    assert usable_indices(my) == {0, 3}
    my["party"][5]["species_candidates"] = []
    assert usable_indices(my) == {0, 3, 5}
    print("test_d_appearance_record OK")


# ------------------------------------------------------------------ (e) 残存戦力・探索・RL の合法手
def _capture_inputs(st, mode="transition"):
    box = {}
    orig = ST.build_inputs

    def wrap(*a, **k):
        out = orig(*a, **k)
        box["inp"] = out[0]
        return out
    ST.build_inputs = wrap
    try:
        with _Env():
            adv = evaluate(copy.deepcopy(st), resolver, rl_blend_weight=0, scoring_mode=mode)
    finally:
        ST.build_inputs = orig
    return adv, box.get("inp")


def test_e_material_search_rl_exclude_unknown():
    st = _state(_my_state(active=0, picked=(0, 2)))
    my = st["player"]
    # 残存戦力 (transition の控え) と交代の候補: 判明した選出の控え (オオニューラ) だけ。未判明の 1 体は数だけ my_unknown に残る
    adv, inp = _capture_inputs(st)
    assert inp is not None, adv.get("scoring_fallback")
    assert [v.species_id for v, _hp in inp.me.bench] == ["sneasler"], inp.me.bench
    assert inp.vctx.my_unknown == 1, inp.vctx.my_unknown
    assert {c.id for c in inp.candidates if c.kind == "switch"} == {"sneasler"}
    # 探索 (_run_search) と自分の損失の控え
    assert [v.species_id for v, _hp in sim_bench(my, "player", resolver)] == ["sneasler"]
    # RL の合法手 (交代の mask)
    labels = sorted(lbl for _j, lbl, k in _legal_actions(st) if k == "switch")
    assert labels == ["交代:オオニューラ"], labels
    # 印なし (0052 と同じ形): どれにも入らない
    st0 = _state(_my_state(active=1))
    assert sim_bench(st0["player"], "player", resolver) == []
    assert [lbl for _j, lbl, k in _legal_actions(st0) if k == "switch"] == []
    _adv, inp0 = _capture_inputs(st0)
    assert inp0 is not None and inp0.me.bench == [] and inp0.vctx.my_unknown == 2, inp0
    print("test_e_material_search_rl_exclude_unknown OK")


# ------------------------------------------------------------------ (f) 終盤判定
def test_f_endgame_counts_unknown():
    """未判明の選出を 0 体扱いにしない: 残数が無くても未判明の数を状態に無い残りとして数え、全滅・控えなし・負け筋と判定しない"""
    # 残数 (HUD) が無いとき: 未判明の数 (2) を my_unknown にする (以前は 0)
    st = _state(_my_state(active=1, remaining=None))
    _adv, inp = _capture_inputs(st)
    assert inp is not None and inp.vctx.my_unknown == 2, inp
    assert unknown_alive_count(st["player"], 1) == 2 and my_unknown_alive(st["player"]) == 2
    # 残数があれば従来の式 (残数 − 分かっている生存数)
    assert unknown_alive_count(_my_state(active=1, remaining=2), 1) == 1
    assert my_unknown_alive(_my_state(active=1, remaining=2)) == 1
    assert my_unknown_alive(_my_state(active=0, picked=(0, 1, 2))) == 0
    # 局面評価: 場の個体がひんし・判明した控えなしでも、未判明の残りがあれば終了 (−1) にしない
    me = SimSide(active=inp.me.active, active_hp=0.0, bench=[])
    vctx = VA.ValuationContext(my_moves={}, opp_moves={}, my_unknown=2, opp_unknown=0)
    assert VA.terminal_value(me, inp.opp, vctx) is None
    assert VA.terminal_value(me, inp.opp, VA.ValuationContext(my_moves={}, opp_moves={})) == -1.0
    assert VA.material(me, inp.opp, vctx) > VA.material(me, inp.opp, VA.ValuationContext(my_moves={}, opp_moves={}))

    # 自分の損失: てっていこうせん (HP 1/2) で自分がひんしでも、未判明の生存の個体が残るなら「控えなし」にしない
    def steelbeam_cost(my):
        with _Env():
            adv = evaluate(copy.deepcopy(_state(my)), resolver, rl_blend_weight=0, scoring_mode="legacy")
        return next(a for a in adv["actions"] if a.get("id") == "steelbeam")["self_cost"]
    sc = steelbeam_cost(_my_state(active=5, active_hp=40.0))
    assert sc["faints"] and sc["requires_switch"], sc
    my3 = _my_state(active=5, picked=(5, 0, 1), active_hp=40.0)
    for i in (0, 1):
        my3["party"][i].update(hp_percent=0.0, status="fainted")
    my3["remaining"] = 1
    sc = steelbeam_cost(my3)          # 3 体確定・控えはひんし: 従来どおり控えなし
    assert sc["faints"] and not sc["requires_switch"], sc

    # 負け筋 (「こちらの残り全員が不利」): 未判明の生存の個体が残る間は出さない。3 体確定なら従来どおり出す
    orig = EG.matchup_matrix
    EG.matchup_matrix = lambda my_mons, opp_mons: {"matrix": {}, "win_conditions": [], "lose_threats": ["ガブリアス"],
                                                    "unsupported": []}
    try:
        st_u = _state(_my_state(active=0, picked=(0, 2)))
        note = ENG._run_endgame(st_u["player"], st_u["opponent"], resolver)
        assert "負け筋" not in note, note
        st_k = _state(_my_state(active=0, picked=(0, 1, 2)))
        note = ENG._run_endgame(st_k["player"], st_k["opponent"], resolver)
        assert "負け筋" in note, note
    finally:
        EG.matchup_matrix = orig
    print("test_f_endgame_counts_unknown OK")


def test_g_advice_field_on_both_entries():
    """advice の欄 pick_state は助言の入口 (Advisor.advise と、実機の advise_cached) の両方で付く (evaluate の戻り値の鍵は変えない)"""
    import advice_key as K
    from advisor.service import Advisor
    st = _state(_my_state(active=0, picked=(0, 2)))
    want = {"picked": ["blastoise", "sneasler"], "unknown_count": 1, "not_picked": []}
    with _Env():
        adv = Advisor(resolver)
        assert "pick_state" not in evaluate(copy.deepcopy(st), resolver, rl_blend_weight=0)
        assert adv.advise(copy.deepcopy(st))["pick_state"] == want
        cache = K.AdviceCache()
        a1, ev1 = K.advise_cached(adv, copy.deepcopy(st), cache, "k", 100.0)
        a2, ev2 = K.advise_cached(adv, copy.deepcopy(st), cache, "k", 100.5)     # キャッシュの採点結果を使う回
        assert ev1 and not ev2
        assert a1["pick_state"] == want and a2["pick_state"] == want, (a1.get("pick_state"), a2.get("pick_state"))
    print("test_g_advice_field_on_both_entries OK")


def main():
    test_pure_judgement()
    test_a_0052_no_unpicked_switch()
    test_b_two_of_three_known()
    test_c_three_known_unchanged()
    test_d_appearance_record()
    test_e_material_search_rl_exclude_unknown()
    test_f_endgame_counts_unknown()
    test_g_advice_field_on_both_entries()
    print("\nALL OK")


if __name__ == "__main__":
    main()
