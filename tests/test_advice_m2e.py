"""操縦提案の精度改善 M2e (正式比較の前の最後の修正、2026-10-09 23 時台のレビューの推奨) のテスト。

段 1 フォルム ID の対応 (シミュレータの id → 助言の図鑑の id の別名: advisor.dex.canonical_species_id)
段 2 評価不能 (ref_check.unbuilt > 0) の対戦があれば正式判定を止める (usefulness_verdict.own_correspondence_check)
段 3 くろいきり・ふきとばし・ほえる (共通の遷移、両陣営。未判明の個体を引き出した分岐は未評価)
段 4 採点比較専用の時間上限と開始条件、2 夜分割 (usefulness_p1 --part k/n、check_advisor_player の抽選の offset、verdict の結合)

使用率 DB・登録ファイル (config/my_team.json)・Showdown の対戦には依存しない。

    python -m tests.test_advice_m2e
"""
from __future__ import annotations

import io
import json
import os
import random
import tempfile
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("RL_BLEND_WEIGHT", "0")

from advisor import scoring_transition as ST
from advisor import transition as T
from advisor.dex import canonical_species_id, get_dex
from advisor.engine import build_mon_view
from advisor.transition import Action, SimSide, resolve_turn_branches
from tests.test_advice_m2c import _inp, _mv, _view
from tests.test_scoring_transition import resolver

ALIASES = {"indeedeef": "indeedeefemale", "basculegionf": "basculegionfemale", "mausholdfour": "mausholdfamilyoffour",
           "taurospaldeaaqua": "taurospaldeaaquabreed"}


# ==============================================================================
# 段 1: フォルム ID の対応
# ==============================================================================
def _mon_state(sid: str, **kw) -> dict:
    from advisor.infer import species_ja_name
    d = {"species_id": sid, "species_ja": species_ja_name(sid) or sid, "hp_percent": 100.0, "status": None, "boosts": {},
         "ability_id": None, "item_id": None, "is_mega": False, "moves": [], "revealed_moves": [], "is_active": True}
    d.update(kw)
    return d


def test_canonical_species_id_and_build():
    """レビューの 4 つの別名と vivillonfancy: 正規化した id で図鑑を引け、build_mon_view が None にならない (両陣営)。
    状態の species_id はシミュレータの id のまま (使用率 DB の名前と同じ)。名前は図鑑の id と同じ"""
    from advisor.infer import species_ja_name
    dex = get_dex()
    for sim, canon in ALIASES.items():
        assert canonical_species_id(sim) == canon and canonical_species_id(canon) == canon
        assert dex.species(sim) is not None and dex.species(sim) == dex.species(canon), sim
        for side in ("player", "opponent"):
            v = build_mon_view(_mon_state(sim), resolver, side=side)
            assert v is not None and v.base == dex.species(canon)["baseStats"] and v.types == dex.species(canon)["types"], sim
        assert species_ja_name(sim) == species_ja_name(canon) and species_ja_name(sim) != sim, (sim, species_ja_name(sim))
    # vivillonfancy: vivillon と種族値・タイプ・特性が同じで、champions mod の learnset は空 (基本種を引く) → 別名
    assert canonical_species_id("Vivillon-Fancy") == "vivillon" and dex.species("vivillonfancy") == dex.species("vivillon")
    assert build_mon_view(_mon_state("vivillonfancy"), resolver) is not None
    # 表記ゆれ (大文字・記号) も同じ変換。別名でない id はそのまま、図鑑に無い id は None のまま
    assert canonical_species_id("Indeedee-F") == "indeedeefemale" and canonical_species_id("garchomp") == "garchomp"
    assert canonical_species_id(None) == "" and dex.species("notindexmon") is None
    assert build_mon_view(_mon_state("notindexmon"), resolver) is None
    # メガ形態は別名にしない (石の表が Showdown の id を使う)
    from advisor.dex import species_aliases
    assert not any("mega" in k for k in species_aliases())
    print("test_canonical_species_id_and_build OK")


def test_reference_check_with_alias():
    """送った本文の名前 (Indeedee-F) と poke-env の id (indeedeef) の照合・シムの登録・参照した型の検査が同じ変換を使う:
    ref_unbuilt にならず、参照した型が送った型と一致する"""
    from champions_agent.env import advisor_player as AP
    text = ("Indeedee-F @ Psychic Seed\nAbility: Psychic Surge\nEVs: 32 HP / 32 Def / 2 SpD\nBold Nature\n"
            "- Follow Me\n- Psychic\n- Protect\n- Trick Room\n\n"
            "Basculegion-F @ Choice Scarf\nAbility: Adaptability\nEVs: 32 SpA / 32 Spe\nModest Nature\n- Hydro Pump\n- Shadow Ball")
    assert AP.team_matches_species(text, ["indeedeef", "basculegionf"])
    assert AP.team_matches_species(text, ["indeedeefemale"])                     # 図鑑の id 側からでも同じ個体
    assert AP.text_set_of(text, "indeedeef") == AP.text_set_of(text, "indeedeefemale") is not None
    AP._SIM_BUILDS.clear()
    AP._SIM_MOVES.clear()
    try:
        AP.register_team_text(text, resolver)
        corr: dict = {}
        for sid, item in (("indeedeef", "psychicseed"), ("basculegionf", "choicescarf")):
            assert AP.check_reference(corr, _mon_state(sid, item_id=item), text, resolver) is True, (sid, corr)
        assert corr.get("ref_unbuilt") is None and corr["ref_checked"] == 2 and "ref_mismatch" not in corr, corr
    finally:
        AP._SIM_BUILDS.clear()
        AP._SIM_MOVES.clear()
    print("test_reference_check_with_alias OK")


def test_pool_unsupported_species():
    """構築プールの検査 (tools.scoring_pool): 別名で引ける種は対応済み、図鑑に無い種のチームは check_pool_doc が問題にする"""
    from tools import scoring_pool as SP
    ok_team = "Indeedee-F @ Psychic Seed\nEVs: 32 HP\nBold Nature\n- Psychic\n\nVivillon-Fancy @ Focus Sash\n- Hurricane"
    bad_team = "Notindexmon @ Leftovers\n- Tackle\n\nGarchomp @ Choice Scarf\n- Earthquake"
    assert SP.unsupported_species([ok_team]) == {}
    assert SP.unsupported_species([ok_team, bad_team]) == {1: ["notindexmon"]}
    doc = SP.pool_doc([ok_team, bad_team], ["a", "b"], snapshot_id=61, battle_format="f", seed=1, n_requested=2,
                      n_generated=2, excluded=[], db_path=None)
    errs = SP.check_pool_doc(doc)
    assert len(errs) == 1 and "図鑑で引けない種" in errs[0] and "notindexmon" in errs[0], errs
    assert SP.check_pool_doc(SP.pool_doc([ok_team], ["a"], snapshot_id=61, battle_format="f", seed=1, n_requested=1,
                                         n_generated=1, excluded=[], db_path=None)) == []
    print("test_pool_unsupported_species OK")


# ==============================================================================
# 段 2: 評価不能で判定を止める
# ==============================================================================
def test_unbuilt_blocks_final_verdict():
    from tools import usefulness_verdict as V
    from tests.test_usefulness_verdict import SC_BASE, SC_TR, _ids, _pair, _scoring_conditions

    def recs(unbuilt_at=()):
        out = []
        for i in range(600):
            rc = {"n": 3, "mismatch": 0, "unbuilt": 1 if i in unbuilt_at else 0}
            if i in unbuilt_at:
                rc["unbuilt_species"] = ["basculegionf"]
            out.append({"own_team_sha16": f"s{i}", "ref_check": rc})
        return out
    ok = {SC_BASE: recs(), SC_TR: recs()}
    reasons, summ = V.own_correspondence_check(ok, [SC_BASE, SC_TR], 600)
    assert reasons == [] and summ["unbuilt_battles"] == {} and summ["unbuilt_species"] == {}
    # 両条件で同じ評価不能でも止める (件数と種を報告)
    bad = {SC_BASE: recs((3, 9)), SC_TR: recs((3,))}
    reasons, summ = V.own_correspondence_check(bad, [SC_BASE, SC_TR], 600)
    assert len(reasons) == 1 and "評価できない" in reasons[0] and "basculegionf 3" in reasons[0], reasons
    assert summ["unbuilt_battles"] == {SC_BASE: 2, SC_TR: 1} and summ["unbuilt_species"] == {"basculegionf": 3}
    base, cond = _pair(166, 130, 150, 154)
    cdoc = dict(_scoring_conditions(), own_check={"required": True})
    ids = {SC_BASE: _ids(600), SC_TR: _ids(600)}
    v = V.build_verdict(cdoc, {SC_BASE: base, SC_TR: cond}, final_battles=600, opp_ids_by_cond=ids,
                        baseline_cond=SC_BASE, compare_conds=[SC_TR], own_records_by_cond=bad)
    assert v["mode"] == V.MODE_INVALID and v["comparisons"][0]["decision"] is None
    assert any("評価できない" in x for x in v["invalid_reasons"])
    v2 = V.build_verdict(cdoc, {SC_BASE: base, SC_TR: cond}, final_battles=600, opp_ids_by_cond=ids,
                         baseline_cond=SC_BASE, compare_conds=[SC_TR], own_records_by_cond=ok)
    assert v2["mode"] == V.MODE_FINAL
    print("test_unbuilt_blocks_final_verdict OK")


# ==============================================================================
# 段 3: くろいきり・ふきとばし・ほえる
# ==============================================================================
def _brs(me, opp, my_act, opp_act, reserves=None):
    return resolve_turn_branches(me, opp, my_act, opp_act, None, None, reserves=reserves or {})


def _p(brs, pred):
    return sum(p for p, r in brs if pred(r))


def test_haze_both_sides():
    """(a) くろいきり の後は両者のランクが 0 (遷移後の状態に残るので V の後続の対面にも効く)。自分側・相手側のどちらが使っても
    同じ (後から動いた側の積みは残る)。相手がひんしでも出る (場全体)。未対応の印は付かない"""
    from advisor import valuation as VA
    boosted = replace(_view("garchomp"), boosts={"atk": 2, "spe": 1})
    slow = replace(_view("toxapex"), boosts={"def": 1, "spd": -1})
    me, opp = SimSide(slow, 1.0), SimSide(boosted, 1.0)
    # 自分の くろいきり (相手の つるぎのまい が先: ガブリアスが速い) → 最後に くろいきり なので両者 0
    brs = _brs(me, opp, _mv("haze"), _mv("swordsdance"))
    assert len(brs) == 1
    r = brs[0][1]
    assert r.me.active.boosts == {} and r.opp.active.boosts == {}, (r.me.active.boosts, r.opp.active.boosts)
    assert not r.unsupported and not r.unsupported_by["me"] and ("me", "clear_all_boosts", "haze") in r.events, r.events
    # 相手の くろいきり (相手側が使う。ガブリアスが先に くろいきり、後から動いたドヒドイデの積みは残る)
    r2 = _brs(me, SimSide(replace(_view("garchomp"), boosts={"atk": 2}), 1.0), _mv("swordsdance"), _mv("haze"))[0][1]
    order = [(w, k) for w, k, _d in r2.events if k in ("boost", "clear_all_boosts")]
    assert order == [("opp", "clear_all_boosts"), ("me", "boost")], order
    assert r2.me.active.boosts == {"atk": 2} and r2.opp.active.boosts == {} and not r2.unsupported_by["opp"]
    # 相手がひんしでも出る (場全体)
    r3 = T.resolve_move(SimSide(slow, 1.0), SimSide(boosted, 0.0), "haze", None, full=True)
    assert r3.executed and r3.user.active.boosts == {} and r3.target.active.boosts == {} and not r3.unsupported
    # V に効く: くろいきり の後の状態の価値は、積まれたままの状態より自分に有利
    inp = _inp(me, opp, ["haze", "toxic"], [(1.0, _mv("swordsdance"))], my_moves={"toxapex": ("scald",)},
               opp_moves={"garchomp": ("earthquake",)})
    v_after = VA.value(r.me, r.opp, inp.vctx)
    v_boosted = VA.value(me, replace(opp, active=replace(boosted, boosts={"atk": 4, "spe": 1})), inp.vctx)
    assert v_after > v_boosted, (v_after, v_boosted)
    print("test_haze_both_sides OK")


def _bench(*sids, hp=1.0):
    return [(_view(s), hp) for s in sids]


def test_whirlwind_branches_and_failures():
    """(b) ふきとばし: 相手の控え 2 体への分岐 (1/2 ずつ。引っ込んだ個体のランクは 0、着地時のステルスロック)、最後の 1 体なら失敗、
    きゅうばん・ばんけん・ねをはる・おうごんのからだ・かぜのり (攻撃 +1) で失敗、かたやぶりはきゅうばんを無視、まもる・みがわりは貫通。
    マジックミラーは未対応の印。優先度 −6 (速くても後攻)"""
    user = SimSide(_view("skarmory"), 1.0)
    tgt = SimSide(replace(_view("garchomp"), boosts={"atk": 2}), 1.0, bench=_bench("heatran", "dragapult"), stealth_rock=True)
    brs = _brs(user, tgt, _mv("whirlwind"), _mv("swordsdance"))
    assert len(brs) == 2 and all(abs(p - 0.5) < 1e-9 for p, _r in brs), [(p, r.opp.active.species_id) for p, r in brs]
    ins = {r.opp.active.species_id: r for _p0, r in brs}
    assert set(ins) == {"heatran", "dragapult"}
    for sid, r in ins.items():
        assert not r.unsupported and not r.unevaluated and not r.unsupported_by["me"], r.unsupported
        out = next(v for v, _hp in r.opp.bench if v.species_id == "garchomp")
        assert out.boosts == {}, out.boosts                                        # 引っ込んだ個体のランクは 0
        assert ("opp", "dragged_in", sid) in r.events
    for sid in ("dragapult", "heatran"):                                          # 着地時のステルスロック
        sr = get_dex().effectiveness("Rock", _view(sid).types) * 0.125
        assert abs(ins[sid].opp.active_hp - (1.0 - sr)) < 1e-9, (sid, ins[sid].opp.active_hp, sr)
    # 優先度 −6: 使用者 (ドラパルト) が速くても、遅い相手 (カバルドン) の つるぎのまい の後に出る
    assert T.priority("whirlwind") == -6 and T.priority("roar") == -6
    fast = SimSide(_view("dragapult"), 1.0)
    slow_t = SimSide(_view("hippowdon"), 1.0, bench=_bench("heatran"))
    assert T.side_speed(fast, None) > T.side_speed(slow_t, None)
    ev = [(w, k) for w, k, _d in _brs(fast, slow_t, _mv("whirlwind"), _mv("swordsdance"))[0][1].events if k == "move"]
    assert ev == [("opp", "move"), ("me", "move")], ev

    def fails(target, why, act=_mv("whirlwind"), user_side=user):
        bs = _brs(user_side, target, act, _mv("protect"))
        assert len(bs) == 1, [r.events for _p0, r in bs]
        r = bs[0][1]
        assert r.opp.active.species_id == target.active.species_id and (("me", "fail", why) in r.events), r.events
        assert not r.unsupported, r.unsupported
        return r
    # 最後の 1 体 (控えも未判明の残りも無い)
    fails(SimSide(_view("garchomp"), 1.0), "force_switch_no_reserve")
    # 控えがひんしだけ
    fails(SimSide(_view("garchomp"), 1.0, bench=_bench("heatran", hp=0.0)), "force_switch_no_reserve")
    two = _bench("heatran", "dragapult")
    fails(SimSide(_view("octillery", ability="suctioncups"), 1.0, bench=two), "force_switch_blocked")
    fails(SimSide(_view("okidogi", ability="guarddog"), 1.0, bench=two), "force_switch_blocked")
    fails(SimSide(_view("garchomp"), 1.0, bench=two, volatiles=frozenset({"ingrain"})), "force_switch_blocked")
    fails(SimSide(_view("gholdengo", ability="goodasgold"), 1.0, bench=two), "status_move_immune")
    rw = fails(SimSide(_view("brambleghast", ability="windrider"), 1.0, bench=two), "wind_immune")
    assert rw.opp.active.boosts == {"atk": 1}
    # ほえる は wind ではない (かぜのり でも通る)、ぼうおん で失敗
    assert len(_brs(user, SimSide(_view("brambleghast", ability="windrider"), 1.0, bench=two), _mv("roar"), _mv("protect"))) == 2
    fails(SimSide(_view("garchomp", ability="soundproof"), 1.0, bench=two), "sound_immune", act=_mv("roar"))
    # かたやぶり は きゅうばん を無視して通る (ねをはる は特性ではないので無視しない)
    mold = SimSide(_view("excadrill", ability="moldbreaker"), 1.0)
    assert len(_brs(mold, SimSide(_view("octillery", ability="suctioncups"), 1.0, bench=two), _mv("roar"), _mv("protect"))) == 2
    fails(SimSide(_view("garchomp"), 1.0, bench=two, volatiles=frozenset({"ingrain"})), "force_switch_blocked",
          act=_mv("roar"), user_side=mold)
    # まもる・みがわりは貫通する (技の印に protect が無い / bypasssub)
    bs = _brs(user, SimSide(_view("garchomp"), 1.0, bench=two), _mv("whirlwind"), _mv("protect"))
    assert len(bs) == 2 and {r.opp.active.species_id for _p0, r in bs} == {"heatran", "dragapult"}
    bs = _brs(user, SimSide(_view("garchomp"), 1.0, bench=two, sub_hp=0.25), _mv("roar"), _mv("protect"))
    assert len(bs) == 2
    # マジックミラー: 跳ね返しは未対応の印 (自分の候補なら退避)
    bs = _brs(user, SimSide(_view("hatterene", ability="magicbounce"), 1.0, bench=two), _mv("whirlwind"), _mv("protect"))
    assert bs[0][1].unsupported_by["me"] == [("me", "magic_bounce", "whirlwind")], bs[0][1].unsupported_by
    print("test_whirlwind_branches_and_failures OK")


def test_force_switch_unknown_and_opponent_side():
    """未判明の個体: 相手の控え 1 体判明 + 未判明 1 体 → 判明した控えの分岐 1/2、未判明を引いた分岐 1/2 は未評価の印 (以後の行動は
    解決しない)。(c) 相手側が使う: 自分の控え 2 体への分岐。(d) unsupported から外れる (未対応の印が無い)"""
    user = SimSide(_view("skarmory"), 1.0)
    tgt = SimSide(_view("garchomp"), 1.0, bench=_bench("heatran"))
    brs = _brs(user, tgt, _mv("whirlwind"), _mv("swordsdance"), reserves={"opp": 1})
    assert len(brs) == 2
    une = [(p, r) for p, r in brs if r.unevaluated]
    ev = [(p, r) for p, r in brs if not r.unevaluated]
    assert len(une) == 1 and abs(une[0][0] - 0.5) < 1e-9 and une[0][1].unevaluated == [("opp", "force_switch_unknown", "whirlwind")]
    assert ev[0][1].opp.active.species_id == "heatran" and abs(ev[0][0] - 0.5) < 1e-9
    # 未判明だけ (判明した控えなし) → 全部が未評価の分岐
    brs = _brs(user, SimSide(_view("garchomp"), 1.0), _mv("roar"), _mv("protect"), reserves={"opp": 2})
    assert len(brs) == 1 and brs[0][1].unevaluated
    # 相手側が使う (自分の控え 2 体)
    me = SimSide(replace(_view("garchomp"), boosts={"atk": 2}), 1.0, bench=_bench("heatran", "dragapult"))
    brs = _brs(me, SimSide(_view("hippowdon"), 1.0), _mv("swordsdance"), _mv("whirlwind"))
    assert len(brs) == 2 and {r.me.active.species_id for _p0, r in brs} == {"heatran", "dragapult"}
    assert all(not r.unsupported and not r.unevaluated for _p0, r in brs)
    assert all(next(v for v, _h in r.me.bench if v.species_id == "garchomp").boosts == {} for _p0, r in brs)
    # まだ行動していない対象を引き出したら、その行動 (トリックルーム、優先度 −7) は出ない。引き出された個体も行動しない
    brs = _brs(user, SimSide(_view("garchomp"), 1.0, bench=_bench("heatran")), _mv("whirlwind"), _mv("trickroom"))
    assert len(brs) == 1
    r = brs[0][1]
    assert ("opp", "skip", "dragged_out") in r.events and r.opp.active.species_id == "heatran", r.events
    assert not r.unsupported and not any(w == "opp" and k == "move" for w, k, _d in r.events)
    # 未評価の分岐は併合しない (評価できる分岐と混ぜない)
    keys = {T._branch_key(T._Branch(1.0, r.me, r.opp, [], {"me": True, "opp": True}, dict(r.fainted), [],
                                    {"me": [], "opp": []}, {"me": False, "opp": False}, unevaluated=tuple(r.unevaluated)),
                          0.0, {}) for _p0, r in _brs(user, tgt, _mv("whirlwind"), _mv("swordsdance"), reserves={"opp": 1})}
    assert len(keys) == 2
    print("test_force_switch_unknown_and_opponent_side OK")


def test_scoring_unevaluated_branch_interval():
    """採点: 未判明の個体を引き出す分岐は V を評価せず、候補ごとの未評価の質量 δ として区間 [(1 − δ) q − δ, (1 − δ) q + δ] に
    入れる (q は評価した分岐だけで正規化)。第一候補の下限が他の上限を上回らなければ退避 "unevaluated_ambiguous"。
    分岐の未評価が無ければ M2d と同じ (区間の欄なし)"""
    me = SimSide(_view("skarmory"), 1.0)
    opp = SimSide(replace(_view("garchomp"), boosts={"atk": 6}), 1.0, bench=_bench("heatran"))
    # 判明した控えだけ → 未評価なし (区間の欄も付かない)
    r0 = ST.score(_inp(me, opp, ["whirlwind", "bravebird"], [(1.0, _mv("earthquake"))]), budget_ms=10_000)
    assert r0["ok"] and r0["unevaluated"] is None and all("q_lo" not in a for a in r0["actions"]), r0
    # 未判明の残り 1 体 → ふきとばし の候補の半分が未評価 (δ = 0.5、区間の幅 1.0)。ブレイブバード (δ = 0) と順位が決まらず退避
    r = ST.score(_inp(me, opp, ["whirlwind", "bravebird"], [(1.0, _mv("earthquake"))], opp_unknown=1), budget_ms=10_000)
    assert not r["ok"] and r["fallback"] == "unevaluated_ambiguous", r
    u = r["unevaluated"]
    assert u["eps"] == 0.0 and u["moves"] == ["whirlwind"] and u["effects"] == ["force_switch_unknown:whirlwind"], u
    assert u["best_lo"] <= u["other_hi"]
    # 候補が 1 つなら比較は要らない (続行): 区間・未評価の質量・分岐の未評価の割合が付く
    one = ST.score(_inp(me, opp, ["whirlwind"], [(1.0, _mv("earthquake"))], opp_unknown=1), budget_ms=10_000)
    assert one["ok"], one
    a = one["actions"][0]
    assert a["unevaluated_mass"] == 0.5 and a["branches"]["p_unevaluated"] == 0.5 and a["eps_unevaluated"] == 0.0, a
    assert abs(a["q_lo"] - (0.5 * a["q"] - 0.5)) < 1e-3 and abs(a["q_hi"] - (0.5 * a["q"] + 0.5)) < 1e-3, a
    assert "未評価 50%" in a["reason"], a["reason"]
    # q は評価した分岐 (ヒードランを引き出した分岐) だけで正規化した期待値
    from advisor import valuation as VA
    inp1 = _inp(me, opp, ["whirlwind"], [(1.0, _mv("earthquake"))], opp_unknown=1)
    brs = resolve_turn_branches(me, opp, _mv("whirlwind"), _mv("earthquake"), None, None, damage_fn=inp1.damage_fn,
                                reserves={"me": 0, "opp": 1})
    ev = [(p, VA.value(r.me, r.opp, inp1.vctx, r.winner)) for p, r in brs if not r.unevaluated]
    assert abs(sum(p for p, _v in ev) - 0.5) < 1e-9
    assert abs(sum(p * v for p, v in ev) / 0.5 - a["q"]) < 1e-3, (ev, a["q"])
    # 全部の分岐が未評価 (判明した控えが無い) の候補 → 退避
    for moves in (["whirlwind", "bravebird"], ["whirlwind"]):
        allu = ST.score(_inp(me, SimSide(_view("garchomp"), 1.0), moves, [(1.0, _mv("earthquake"))], opp_unknown=2),
                        budget_ms=10_000)
        assert not allu["ok"] and allu["fallback"] == "unevaluated_ambiguous", allu
    print("test_scoring_unevaluated_branch_interval OK")


def test_legacy_resolve_turn_unchanged():
    """探索 (legacy) が使う単一分岐の resolve_turn と resolve_move (full=False) は変えていない: くろいきり・ふきとばしは従来どおり
    表に無い変化技 (effect_unmodeled)"""
    me, opp = SimSide(_view("skarmory"), 1.0), SimSide(_view("garchomp"), 1.0, bench=_bench("heatran"))
    for m in ("haze", "whirlwind", "roar"):
        r = T.resolve_move(me, opp, m, None)
        assert r.unsupported == [("user", "effect_unmodeled", m)] and r.target.active.species_id == "garchomp", (m, r.unsupported)
    print("test_legacy_resolve_turn_unchanged OK")


# ==============================================================================
# 段 4: 時間上限と開始条件、2 夜分割
# ==============================================================================
def test_time_limit_and_start_rule():
    from champions_agent.config import (P1_ABORT_SEC_PER_BATTLE, P1_SCORING_START_MARGIN_SEC, P1_SCORING_TIME_LIMIT_SEC,
                                        P1_TIME_LIMIT_SEC)
    from tools import usefulness_p1 as P
    assert P1_SCORING_TIME_LIMIT_SEC == 10800 and P1_TIME_LIMIT_SEC == 7200 and P1_ABORT_SEC_PER_BATTLE == 12.0
    assert P.resolve_time_limit(None, "rl") == 7200 and P.resolve_time_limit(None, "scoring") == 10800
    assert P.resolve_time_limit(None, "scoring", 300) == 5400 and P.resolve_time_limit(None, "scoring", 200) == 3600
    assert P.resolve_time_limit(None, "scoring", 301) == 5418 and P.resolve_time_limit(999, "scoring", 300) == 999
    conds = ["scoring:legacy", "scoring:transition"]
    ok0 = {c: 0 for c in conds}
    rule = {"time_limit_sec": P1_SCORING_TIME_LIMIT_SEC, "margin_sec": P1_SCORING_START_MARGIN_SEC}
    # 予備確認の実測 (transition 15.4 秒/戦): 15.4 × 600 + 1080 = 10320 ≤ 10800 → 開始可 (RL の基準 12 秒/戦なら不可)
    a = P.prelim_advice(50 * 15.4, {c: 50 for c in conds}, ok0, False, 50, conds=conds, **rule)
    assert a["start_final"] and a["threshold"] is None and abs(a["max_sec_per_battle"] - (10800 - 1080) / 600) < 1e-9, a
    assert "margin" in a["criterion"]
    b = P.prelim_advice(50 * 15.4, {c: 50 for c in conds}, ok0, False, 50, conds=conds)
    assert not b["start_final"] and b["threshold"] == P1_ABORT_SEC_PER_BATTLE
    # 16.3 秒/戦: 9780 + 1080 = 10860 > 10800 → 不可 (理由に式の値)
    c = P.prelim_advice(50 * 16.3, {cc: 50 for cc in conds}, ok0, False, 50, conds=conds, **rule)
    assert not c["start_final"] and any("10860" in x and "10800" in x for x in c["reasons"]), c["reasons"]
    # 到達・終了の条件は従来どおり
    d = P.prelim_advice(50 * 10.0, {"scoring:legacy": 50, "scoring:transition": 40}, ok0, False, 50, conds=conds, **rule)
    assert not d["start_final"]
    print("test_time_limit_and_start_rule OK")


def test_part_ranges_and_pool_rng():
    from tools import usefulness_p1 as P
    from tools.check_advisor_player import pool_rng
    assert P.parse_part(None) is None and P.parse_part("1/2") == (1, 2) and P.parse_part("2/2") == (2, 2)
    for bad in ("0/2", "3/2", "x", "1-2"):
        try:
            P.parse_part(bad)
            raise AssertionError(bad)
        except ValueError:
            pass
    assert P.part_range(1, 2) == (0, 300) and P.part_range(2, 2) == (300, 600)
    assert [P.part_range(k, 3) for k in (1, 2, 3)] == [(0, 200), (200, 400), (400, 600)]
    assert [P.part_range(k, 7, 600) for k in range(1, 8)][-1][1] == 600
    rs = [P.part_range(k, 7, 600) for k in range(1, 8)]
    assert all(rs[i][1] == rs[i + 1][0] for i in range(6)) and sum(e - s for s, e in rs) == 600     # 重複も抜けも無い
    rec = P.part_record(2, 2)
    assert rec["start"] == 300 and rec["end"] == 600 and rec["battles"] == 300 and rec["opp_offset"] == 300
    # 自チームの抽選: 同じ seed で最初から引いた列の skip 番目以降と同じ
    texts = [f"team{i}" for i in range(200)]
    full = random.Random(42)
    seq = [full.choice(texts) for _ in range(600)]
    r2 = pool_rng(42, texts, 300)
    assert [r2.choice(texts) for _ in range(300)] == seq[300:]
    r1 = pool_rng(42, texts, 0)
    assert [r1.choice(texts) for _ in range(300)] == seq[:300]
    # 相手列も同じ (opponent_sequence は先頭が安定)
    from tools.team_build.opponents import opponent_sequence
    ids = [f"t{i}" for i in range(33)]
    assert opponent_sequence(ids, 600, 7)[:300] == opponent_sequence(ids, 300, 7)
    print("test_part_ranges_and_pool_rng OK")


def _dry_json(text: str) -> dict:
    """dry-run の標準出力から条件表の JSON を取り出す (本体では登録チームの補完の注記が JSON の前に出るので、最初の '{' の行から
    '# コマンド列' の前まで。2026-10-09: 本体の CI で先頭から解析して失敗した。期待は変えていない)"""
    head = text[:text.index("\n# コマンド列")]
    return json.loads(head[head.index("\n{") + 1:] if "\n{" in head else head)


def test_part_dry_run():
    """--part 2/2 の dry-run: 両条件のコマンドが --battles 300 --opp-offset 300、条件表に part と範囲、時間上限は按分 (5400)。
    --part は --prelim / --compare rl / --team general-pinned と併用できない"""
    from tests.test_usefulness_p1 import _facts
    from tools import usefulness_p1 as P
    pool_team = {"source": "pool", "path": "/pool.json", "sha16": "c7a32f8897d71375", "n_teams": 200, "snapshot_id": 61,
                 "battle_format": "gen9championsbssregmc", "seed": 20261010}
    orig = (P.collect_facts, P.make_pin, P.launch)

    def _no(*a, **k):
        raise AssertionError("呼ばれてはいけない")
    try:
        P.make_pin, P.launch = _no, _no
        P.collect_facts = lambda *a, **k: _facts(team=pool_team)
        with tempfile.TemporaryDirectory() as tmp:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = P.main(["--compare", "scoring", "--part", "2/2", "--dry-run", "--own-pool", "/pool.json",
                             "--out", str(Path(tmp) / "x")])
            text = buf.getvalue()
            assert rc == 0, text
            doc = _dry_json(text)
            assert doc["part"]["k"] == 2 and doc["part"]["start"] == 300 and doc["part"]["end"] == 600, doc.get("part")
            assert doc["opponents"]["offset"] == 300 and doc["battles"] == 300 and doc["time_limit_sec"] == 5400
            assert doc["scoring_time"]["time_limit_total_sec"] == 10800 and doc["scoring_time"]["start_margin_sec"] == 1080
            for c, cmd in doc["commands"].items():
                s = " ".join(cmd)
                assert "--battles 300" in s and "--opp-offset 300" in s and "--own-pool" in s, s
            for argv in (["--compare", "scoring", "--part", "1/2", "--prelim", "--dry-run"],
                         ["--compare", "rl", "--part", "1/2", "--dry-run"],
                         ["--compare", "scoring", "--team", "general-pinned", "--part", "1/2", "--dry-run"],
                         ["--compare", "scoring", "--part", "1/2", "--battles", "250", "--dry-run"],
                         ["--compare", "scoring", "--part", "3/2", "--dry-run"]):
                with redirect_stdout(io.StringIO()):
                    assert P.main(argv + ["--out", str(Path(tmp) / "y")]) == 2, argv
            # part なしの採点比較の時間上限は 10800、RL の比較は 7200 のまま
            buf = io.StringIO()
            with redirect_stdout(buf):
                P.main(["--compare", "scoring", "--dry-run", "--own-pool", "/pool.json", "--out", str(Path(tmp) / "z")])
            t2 = buf.getvalue()
            d2 = _dry_json(t2)
            assert d2["time_limit_sec"] == 10800 and "part" not in d2 and d2["opponents"]["offset"] == 0
            P.collect_facts = lambda *a, **k: _facts()
            buf = io.StringIO()
            with redirect_stdout(buf):
                P.main(["--dry-run", "--out", str(Path(tmp) / "w")])
            t3 = buf.getvalue()
            d3 = _dry_json(t3)
            assert d3["time_limit_sec"] == 7200 and "scoring_time" not in d3 and "part" not in d3
    finally:
        P.collect_facts, P.make_pin, P.launch = orig
    print("test_part_dry_run OK")


def _write_part(root: Path, k: int, n: int, outs: dict, *, seed=20261009, offset=None, drop_cond=None, incomplete=False,
                elapsed=4000.0, unbuilt_at=()):
    from tools import usefulness_p1 as P
    from tools import usefulness_verdict as V
    from tests.test_usefulness_verdict import _scoring_conditions
    rec = P.part_record(k, n)
    d = root / f"part{k}"
    d.mkdir(parents=True, exist_ok=True)
    cdoc = dict(_scoring_conditions(incomplete=incomplete), own_check={"required": True}, part=rec, elapsed_sec=elapsed,
                opponents={"seed": seed, "tier": "search", "fold": 1, "split_sha16": "ss",
                           "offset": rec["opp_offset"] if offset is None else offset},
                scoring_time={"time_limit_total_sec": 10800}, git={"commit": "g"}, showdown={"commit": "sd"})
    cdoc["team"] = {"source": "pool", "sha16": "c7a32f8897d71375"}
    (d / "conditions.json").write_text(json.dumps(cdoc), encoding="utf-8")
    for c, o in outs.items():
        if c == drop_cond:
            continue
        f = V.cond_file(c)
        seg = o[rec["start"]:rec["end"]]
        (d / f"{f}.json").write_text(json.dumps({"outcomes": seg, "elapsed_s": elapsed, "stats": {}}), encoding="utf-8")
        with (d / f"{f}.battles.jsonl").open("w", encoding="utf-8") as fh:
            for j, w in enumerate(seg):
                i = rec["start"] + j
                rc = {"n": 3, "mismatch": 0, "unbuilt": 1 if i in unbuilt_at else 0}
                fh.write(json.dumps({"won": bool(w), "opponent_team_id": f"t{i % 33}", "own_team_sha16": f"s{i}",
                                     "ref_check": rc}) + "\n")
    return d


def test_verdict_merge_parts():
    """verdict の結合: 2 part (各 300 戦、両条件) → n = 600 で 1 回だけ最終判定。part 1 だけ → 中間確認。範囲の重複・
    条件の欠け・seed の違い・offset の違い → 不成立 (invalid)。所要の合計が全体の上限を超える → 未完了。評価不能 → invalid"""
    from tools import usefulness_verdict as V
    from tests.test_usefulness_verdict import SC_BASE, SC_TR, _pair
    base, cond = _pair(166, 130, 150, 154)
    outs = {SC_BASE: cond, SC_TR: base}                                     # transition − legacy = +0.06 → 採用候補
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        p1, p2 = _write_part(root / "a", 1, 2, outs), _write_part(root / "a", 2, 2, outs)
        with redirect_stdout(io.StringIO()):
            v = V.run_merged([p2, p1], root / "merged")                    # 渡す順によらず範囲の順に連結する
        assert v["mode"] == V.MODE_FINAL and v["n_used"] == 600 and v["comparisons"][0]["decision"] == V.ADOPT, v
        assert v["own_correspondence"]["unbuilt_battles"] == {} and [r["k"] for r in v["parts"]] == [1, 2]
        assert (root / "merged" / "verdict.md").exists() and (root / "merged" / "conditions_merged.json").exists()
        # 1 本で通した場合と同じ差・区間
        full = V.build_verdict({"factor": "scoring", "final_battles": 600}, outs, final_battles=600,
                               baseline_cond=SC_BASE, compare_conds=[SC_TR])
        assert abs(full["comparisons"][0]["mean_diff"] - v["comparisons"][0]["mean_diff"]) < 1e-12
        # part 1 だけ (part の数が足りない) → 中間確認
        with redirect_stdout(io.StringIO()):
            v1 = V.run_merged([p1], root / "m1")
        assert v1["mode"] == V.MODE_INTERIM and v1["n_used"] == 300
        # 単独の run は part の注記を付けて中間確認
        with redirect_stdout(io.StringIO()):
            vs = V.run(p1)
        assert vs["mode"] == V.MODE_INTERIM and any("part 1/2" in x for x in vs["notes"])
        # 範囲の重複 (同じ part を 2 回) → invalid
        p1b = _write_part(root / "b", 1, 2, outs)
        with redirect_stdout(io.StringIO()):
            vd = V.run_merged([p1, p1b], root / "m2")
        assert vd["mode"] == V.MODE_INVALID and any("重複" in x for x in vd["invalid_reasons"]), vd["invalid_reasons"]
        # 条件の欠け
        p2c = _write_part(root / "c", 2, 2, outs, drop_cond=SC_TR)
        with redirect_stdout(io.StringIO()):
            vc = V.run_merged([p1, p2c], root / "m3")
        assert vc["mode"] == V.MODE_INVALID and any("条件の結果が無い" in x for x in vc["invalid_reasons"])
        # seed の違い / offset の違い
        p2s = _write_part(root / "d", 2, 2, outs, seed=1)
        p2o = _write_part(root / "e", 2, 2, outs, offset=0)
        with redirect_stdout(io.StringIO()):
            vseed = V.run_merged([p1, p2s], root / "m4")
            voff = V.run_merged([p1, p2o], root / "m5")
        assert vseed["mode"] == V.MODE_INVALID and any("opponents.seed" in x for x in vseed["invalid_reasons"])
        assert voff["mode"] == V.MODE_INVALID and any("offset" in x for x in voff["invalid_reasons"])
        # 所要の合計が全体の上限 (10800) を超える → 未完了 (中間確認)
        p2t = _write_part(root / "f", 2, 2, outs, elapsed=7000.0)
        with redirect_stdout(io.StringIO()):
            vt = V.run_merged([p1, p2t], root / "m6")
        assert vt["mode"] == V.MODE_INTERIM and any("incomplete" in x for x in vt["interim_reasons"])
        # part の未完了 → 中間確認
        p2i = _write_part(root / "g", 2, 2, outs, incomplete=True)
        with redirect_stdout(io.StringIO()):
            vi = V.run_merged([p1, p2i], root / "m7")
        assert vi["mode"] == V.MODE_INTERIM
        # 評価不能の個体を含む対戦 → invalid
        p2u = _write_part(root / "h", 2, 2, outs, unbuilt_at=(450,))
        with redirect_stdout(io.StringIO()):
            vu = V.run_merged([p1, p2u], root / "m8")
        assert vu["mode"] == V.MODE_INVALID and any("評価できない" in x for x in vu["invalid_reasons"])
    print("test_verdict_merge_parts OK")


def main():
    test_canonical_species_id_and_build()
    test_reference_check_with_alias()
    test_pool_unsupported_species()
    test_unbuilt_blocks_final_verdict()
    test_haze_both_sides()
    test_whirlwind_branches_and_failures()
    test_force_switch_unknown_and_opponent_side()
    test_scoring_unevaluated_branch_interval()
    test_legacy_resolve_turn_unchanged()
    test_time_limit_and_start_rule()
    test_part_ranges_and_pool_rng()
    test_part_dry_run()
    test_verdict_merge_parts()
    print("test_advice_m2e: all OK")


if __name__ == "__main__":
    main()
