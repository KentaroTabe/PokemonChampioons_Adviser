"""操縦提案の精度改善 M2c (600 戦の方式比較の前に直す不具合 2 件と、レビューの指摘) のテスト (2026-10-09)。

段 1 ダメージ計算のキャッシュの HP の衝突 / ダメージ乱数を平均 HP に潰さない (実際の 16 通り + 状態の併合)
段 2 同時全滅の勝敗 (TurnResult.winner、valuation.terminal_value)
段 3 HP の不確かさを範囲で扱う (悲観側で順位、楽観側は感度、範囲が作れなければ退避)
段 4 相手側の未対応の効果: アンコール・ちょうはつ・みがわり・こらえる を遷移に入れる、ほかは退避、退避率を決定単位で集計
段 5 判明情報との矛盾の除外 (消費済みのタスキ・相手の技の固定) と持ち物の感度

legacy の出力は変えない (tests.test_advice_variant の参照一致)。使用率 DB・登録ファイル (config/my_team.json) には依存しない
(相手の技は判明技だけに固定する tests.test_scoring_transition._Hint)。

Showdown の根拠 (ローカルの pokemon-showdown/):
- sim/battle.ts checkWin: 全陣営の残りが 0 なら this.win(faintData.target.side) (gen 5 以降) = faintQueue で最後に処理した個体の
  陣営の勝ち。faintMessages は faintQueue を先頭から処理する
- sim/battle-actions.ts useMoveInner: selfdestruct "always" は命中判定の前に this.battle.faint(pokemon) (使用者が先に並ぶ)。
  data/mods/champions/scripts.ts hitStepMoveHitLoop: 対象のひんしを faintMessages(false, false, !pokemon.hp) で処理した後に
  applyRecoilDamage (反動の使用者は後に並ぶ)
- data/moves.ts encore / taunt / substitute / endure、data/mods/champions/moves.ts encore、data/items.ts mentalherb

    python -m tests.test_advice_m2c
"""
from __future__ import annotations

import copy
import os
from dataclasses import replace

os.environ.setdefault("RL_BLEND_WEIGHT", "0")

from advisor import scoring_transition as ST
from advisor import transition as T
from advisor import valuation as VA
from advisor.damage import MonView, calc_damage
from advisor.dex import get_dex
from advisor.engine import evaluate
from advisor.transition import Action, SimSide
from champions_agent import config as C
from tests.test_scoring_transition import _Hint, _capture_inputs, _mon, _state, resolver


def _view(sid, ability=None, item=None, ev=None, **kw):
    sp = get_dex().species(sid)
    return MonView(species_id=sid, name_ja=sid, types=list(sp["types"]), base=dict(sp["baseStats"]),
                   ev=ev or {"atk": 252, "spa": 252, "spe": 252}, ability=ability, item=item, **kw)


def _mv(m):
    return Action("move", move_id=m, label=m)


def _p_where(brs, pred):
    return sum(p for p, r in brs if pred(r))


def _inp(me, opp, moves, responses, my_moves=None, opp_moves=None, my_unknown=0, opp_unknown=0):
    """TransitionInputs を直接組む (候補 = 技の列、応手は固定)"""
    dmg = ST.make_damage_fn({me.active.species_id}, None, None)
    vctx = VA.ValuationContext(my_moves=my_moves or {me.active.species_id: tuple(moves)},
                               opp_moves=opp_moves or {}, my_unknown=my_unknown, opp_unknown=opp_unknown, damage_fn=dmg)
    cands = [ST.Candidate("move", m, m, _mv(m), "move") for m in moves]
    return ST.TransitionInputs(me=me, opp=opp, my_field=None, opp_field=None, candidates=cands, responses=responses,
                               vctx=vctx, damage_fn=dmg)


# ==============================================================================
# 段 1-1: ダメージ計算のキャッシュの HP の衝突 (ふんか)
# ==============================================================================
def test_cache_hp_dependent_moves():
    """レビューの例: バクフーンの ふんか を HP 90% で計算した後に 40% で計算すると、M2b のキャッシュは 90% の結果 (平均 34.9%) を
    返していた (キーの HP の区分が「満タン / 1/3 超 / 以下」だけ)。独立計算は 15.5%。修正後は順序によらず独立計算と一致する"""
    att, dfn = _view("typhlosion"), _view("garchomp")

    def indep(hp):
        return calc_damage(replace(att, hp_frac=hp), dfn, "eruption")

    assert indep(0.9)["avg"] == 34.9 and indep(0.4)["avg"] == 15.5       # 独立計算 (レビューの値)
    # 旧挙動の再現: M2b のキー (攻撃側の HP は 満タン / 1/3 超 の区分だけ) では 90% → 40% の順で 90% の結果が返る
    old_cache = {}

    def old_fn(hp):
        key = (hp >= 0.999, hp > 1 / 3)
        if key not in old_cache:
            old_cache[key] = indep(hp)
        return old_cache[key]

    old_fn(0.9)
    assert old_fn(0.4)["avg"] == 34.9
    # 修正後: 90% → 40% の順でも、40% → 90% の順でも独立計算と一致する
    for order in ((0.9, 0.4), (0.4, 0.9)):
        fn = ST.make_damage_fn(set(), None, None)
        for hp in order:
            got = fn(att, hp, dfn, 1.0, "eruption", None)
            assert got["avg"] == indep(hp)["avg"] and got["rolls"] == indep(hp)["rolls"], (order, hp, got["avg"])
    # 防御側の HP で変わる技 (しぼりとる) と、HP で変わらない技はキーに HP を入れない (同じ結果を使い回す)
    fn = ST.make_damage_fn(set(), None, None)
    a2 = _view("garchomp")
    assert fn(a2, 1.0, dfn, 0.9, "earthquake", None) is fn(a2, 0.3, dfn, 0.2, "earthquake", None)
    w1 = fn(_view("tentacruel"), 1.0, dfn, 0.9, "wringout", None)
    w2 = fn(_view("tentacruel"), 1.0, dfn, 0.3, "wringout", None)
    assert w1["avg"] > 0 and w2["avg"] < w1["avg"], (w1["avg"], w2["avg"])
    # 陣営の文脈 (fainted_allies): 同じ個体・同じ技でも陣営を渡せばその文脈で計算する (おはかまいり)
    fn2 = ST.make_damage_fn({"houndstone"}, {"fainted_allies": 0}, {"fainted_allies": 2})
    hs = _view("houndstone")
    mine = fn2(hs, 1.0, dfn, 1.0, "lastrespects", None, side="me")
    theirs = fn2(hs, 1.0, dfn, 1.0, "lastrespects", None, side="opp")
    assert mine["avg"] > 0 and theirs["avg"] > mine["avg"], (mine["avg"], theirs["avg"])
    print("test_cache_hp_dependent_moves OK")


def test_scoring_order_independent():
    """同じ局面を、候補と応手の順を入れ替え、キャッシュを別の HP で温めてから評価しても Q は一致する (HP で威力が変わる技を含む)"""
    me = SimSide(_view("typhlosion"), 0.62, bench=[(_view("garchomp"), 0.8)])
    opp = SimSide(_view("dragapult"), 0.9)
    responses = [(0.6, _mv("shadowball")), (0.4, _mv("dragonclaw"))]
    moves = ["eruption", "flamethrower", "focusblast"]
    opp_moves = {"dragapult": ("shadowball", "dragonclaw")}
    a = ST.score(_inp(me, opp, moves, responses, opp_moves=opp_moves), budget_ms=10_000)
    inp2 = _inp(me, opp, list(reversed(moves)), list(reversed(responses)), opp_moves=opp_moves)
    for hp in (1.0, 0.95, 0.3, 0.1):          # 別の HP の結果でキャッシュを温める
        inp2.damage_fn(me.active, hp, opp.active, 0.9, "eruption", None, side="me")
    inp2.candidates = list(reversed(inp2.candidates))
    b = ST.score(inp2, budget_ms=10_000)
    assert a["ok"] and b["ok"]
    qa = {x["id"]: x["q"] for x in a["actions"]}
    qb = {x["id"]: x["q"] for x in b["actions"]}
    assert qa == qb, (qa, qb)
    print("test_scoring_order_independent OK")


# ==============================================================================
# 段 1-2: 非 KO の乱数を平均 HP に潰さない (被弾を耐えてから てっていこうせん)
# ==============================================================================
def _steelbeam_case():
    """自分: ブリジュラス (最大 HP 165、HP 140.25/165、遅い)。相手: ドラパルト (速い) の ドラゴンクロー (乱数 52〜63 HP、命中 100)。
    相手が先に動き、耐えてから てっていこうせん (最大 HP の 1/2 = 83 HP を失う)。被弾 57 HP 以下なら生き残り、58 HP 以上ならひんし"""
    arch = _view("archaludon", ev={"spa": 252, "hp": 0, "spe": 0})
    pult = _view("dragapult")
    hp = 140.25 / arch.max_hp()
    return SimSide(arch, hp, bench=[(_view("garchomp"), 1.0)]), SimSide(pult, 1.0), arch, pult


def test_rolls_not_collapsed_steelbeam():
    me, opp, arch, pult = _steelbeam_case()
    d = calc_damage(pult, arch, "dragonclaw")
    assert d["accuracy"] in (None, 100.0) and len(d["rolls"]) == 16
    # 独立した根拠: 16 通りの乱数を 1 つずつ解決する (相手の ドラゴンクロー → 自分の てっていこうせん)
    faints = 0
    for r in d["rolls"]:
        a1 = T.resolve_move(opp, me, "dragonclaw", None, full=True, hit=True, hit_damages=[r / 100.0])
        a2 = T.resolve_move(a1.target, a1.user, "steelbeam", None, full=True, hit=True)
        faints += 1 if a2.user.active_hp <= 0 else 0
    assert faints == 8, faints                                  # 16 通り中 8 通り = 50%
    # 旧挙動の再現: 最小〜最大 (0.1% に丸めた値) の等間隔 16 点を 倒せる / 倒せない の 2 群の平均に潰す (M2b の roll_split) と、
    # 倒せない群の平均ダメージ 1 点で解決するので 100%
    groups = T.roll_split(d["min"] / 100.0, d["max"] / 100.0, me.active_hp, 16)
    old = 0.0
    for p, dmg in groups:
        a1 = T.resolve_move(opp, me, "dragonclaw", None, full=True, hit=True, hit_damages=[dmg])
        a2 = T.resolve_move(a1.target, a1.user, "steelbeam", None, full=True, hit=True)
        old += p * (1.0 if a2.user.active_hp <= 0 else 0.0)
    assert old == 1.0, old
    # 変更後の遷移: 自分のひんし確率は独立解決と一致 (50%)。確率の合計は 1、同じ状態の分岐は併合されている
    brs = T.resolve_turn_branches(me, opp, _mv("steelbeam"), _mv("dragonclaw"), None, None)
    assert abs(sum(p for p, _r in brs) - 1.0) < 1e-12
    p_faint = _p_where(brs, lambda r: r.fainted["me"])
    assert abs(p_faint - 0.5) < 1e-12, p_faint
    assert all(next(w for w, k, _d in r.events if k == "move") == "opp" for _p, r in brs)
    keys = [(T.side_state_key(r.me), T.side_state_key(r.opp), r.fainted["me"], r.fainted["opp"]) for _p, r in brs]
    assert len(keys) == len(set(keys)), "同じ状態の分岐が併合されていない"
    # 倒せる乱数は 1 つに束ねる (roll_outcomes)
    ro = T.roll_outcomes([0.1, 0.2, 0.5, 0.6, 0.6], 0.5)
    assert ro[0] == (0.6, 0.5) and sum(p for p, _d in ro) == 1.0 and len(ro) == 3, ro
    print("test_rolls_not_collapsed_steelbeam OK")


def test_merge_granularity_config():
    """併合の粒度: TRANSITION_MERGE_HP_UNITS > 0 なら HP をその量に丸めて併合する (ひんしと生存は混ぜない、確率の合計は 1)"""
    me, opp, _a, _p = _steelbeam_case()
    exact = T.resolve_turn_branches(me, opp, _mv("steelbeam"), _mv("dragonclaw"), None, None, merge_hp_units=0)
    coarse = T.resolve_turn_branches(me, opp, _mv("steelbeam"), _mv("dragonclaw"), None, None, merge_hp_units=20)
    assert len(coarse) < len(exact)
    assert abs(sum(p for p, _r in coarse) - 1.0) < 1e-12
    assert abs(_p_where(coarse, lambda r: r.fainted["me"]) - 0.5) < 1e-12
    print("test_merge_granularity_config OK")


# ==============================================================================
# 段 2: 同時全滅の勝敗 (双方が最後の 1 体・残り HP 1)
# ==============================================================================
def _last_one_hp():
    me = SimSide(_view("slurpuff"), 0.0, bench=[])
    me = replace(me, active_hp=1.0 / me.active.max_hp())
    opp = SimSide(_view("garchomp"), 0.0, bench=[])
    opp = replace(opp, active_hp=1.0 / opp.active.max_hp())
    return me, opp


def test_simultaneous_wipe_winner():
    """レビュワーの 2 例 (Champions BSS Reg M-C で再現): ミストバースト = 使用側の負け (−1)、すてみタックル = 使用側の勝ち (+1)。
    Showdown の checkWin: 全陣営の残りが 0 なら faintQueue の最後の個体の陣営の勝ち。自滅技は使用者が先に並び、反動は後に並ぶ。
    実機との一致は未確認 (Showdown のコードとレビュワーの Showdown 上の再現だけが根拠)"""
    me, opp = _last_one_hp()
    sd = T.resolve_turn_branches(me, opp, _mv("mistyexplosion"), _mv("swordsdance"), None, None)
    for p, r in sd:
        assert r.faint_seq == ["me", "opp"] and r.winner == "opp" and r.end_reason == "last_faint", (r.faint_seq, r.events)
    rc = T.resolve_turn_branches(me, opp, _mv("doubleedge"), _mv("swordsdance"), None, None)
    for p, r in rc:
        if r.fainted["opp"]:
            assert r.faint_seq == ["opp", "me"] and r.winner == "me" and r.end_reason == "last_faint", (r.faint_seq, r.events)
    # 旧挙動の再現: 遷移の勝者を使わず両者の HP だけで判定すると 0 (M2b の terminal_value)
    ctx = VA.ValuationContext(my_moves={}, opp_moves={})
    r0 = sd[0][1]
    assert VA.terminal_value(r0.me, r0.opp, ctx) == 0.0
    assert VA.terminal_value(r0.me, r0.opp, ctx, r0.winner) == -1.0
    # 採点 (Q): ミストバースト −1、すてみタックル +1 (応手は固定 = つるぎのまい)
    res = ST.score(_inp(me, opp, ["mistyexplosion", "doubleedge"], [(1.0, _mv("swordsdance"))]), budget_ms=10_000)
    q = {a["id"]: a["q"] for a in res["actions"]}
    assert calc_damage(me.active, opp.active, "doubleedge")["accuracy"] == 100.0
    assert q["mistyexplosion"] == -1.0 and q["doubleedge"] == 1.0, q
    assert res["actions"][0]["id"] == "doubleedge" and res["actions"][0]["branches"]["p_win_now"] == 1.0
    # 片方だけ全滅 (相手に未判明の残りがあれば終わらない)
    rr = T.resolve_turn_branches(SimSide(_view("garchomp"), 1.0), opp, _mv("earthquake"), _mv("swordsdance"), None, None)
    assert all(r.winner == "me" and r.end_reason == "wipe" for _p, r in rr)
    rr2 = T.resolve_turn_branches(SimSide(_view("garchomp"), 1.0), opp, _mv("earthquake"), _mv("swordsdance"), None, None,
                                  reserves={"opp": 1})
    assert all(r.winner is None for _p, r in rr2)
    # いのちのたま (ダメージの後に使用者が倒れる) も使用側の勝ち
    lo_me = replace(me, active=replace(_view("garchomp"), item="lifeorb"), active_hp=1.0 / _view("garchomp").max_hp())
    rl = T.resolve_turn_branches(lo_me, opp, _mv("earthquake"), _mv("swordsdance"), None, None)
    assert all(r.winner == "me" for _p, r in rl if r.fainted["opp"]), [(r.faint_seq, r.winner) for _p, r in rl]
    print("test_simultaneous_wipe_winner OK")


# ==============================================================================
# 段 3: HP の不確かさ
# ==============================================================================
def test_hp_range_rules():
    """範囲の作り方 (config の HP_RANGE_*): バー推定は ±ERR_BAR、交代の見逃しは下限 1 HP、古い観測は経過時間で下限を下げる。
    自分の個体の最大 HP が分からない・直近値が無いと作れない。確かな個体は None"""
    base = {"species_id": "garchomp", "hp_percent": 60.0, "hp_max": 183}
    assert ST.hp_range_of(dict(base), side="me", is_active=False, now=100.0) is None
    bar = ST.hp_range_of(dict(base, hp_source="bar", hp_uncertain=True, hp_estimated=True), side="me", is_active=True,
                         now=100.0)
    assert bar["source"] == "bar" and abs(bar["lo"] - (0.6 - C.HP_RANGE_ERR_BAR)) < 1e-9 and \
        abs(bar["hi"] - (0.6 + C.HP_RANGE_ERR_BAR)) < 1e-9, bar
    unc = ST.hp_range_of(dict(base, hp_uncertain=True), side="me", is_active=False, now=100.0)
    assert unc["source"] == "uncertain" and unc["lo"] == round(1 / 183, 4) and unc["hi"] == 1.0, unc
    old = ST.hp_range_of(dict(base, hp_read_ts=100.0 - C.HP_RANGE_STALE_SEC - 60), side="opp", is_active=True, now=100.0)
    assert old["source"] == "stale" and abs(old["lo"] - (0.6 - 60 * C.HP_RANGE_STALE_DROP_PER_SEC)) < 1e-4 and old["hi"] == 0.6
    fresh = ST.hp_range_of(dict(base, hp_read_ts=95.0), side="opp", is_active=True, now=100.0)
    assert fresh is None
    assert ST.hp_range_of(dict(base, hp_uncertain=True, hp_max=None), side="me", is_active=False, now=1.0) == \
        {"error": "max_hp_unknown"}
    assert ST.hp_range_of({"species_id": "x", "hp_uncertain": True, "hp_percent": None, "hp_max": 100}, side="me",
                          is_active=False, now=1.0) == {"error": "no_recent_value"}
    print("test_hp_range_rules OK")


def _uncertain_bench_state(uncertain: bool, hp_max=200):
    """自分: ガブリアス 50% (場)、控えに ミミロップ 100% (交代の見逃しで HP が不確か) と ブリジュラス 60%。相手: ドラパルト"""
    gar = _mon("garchomp", "ガブリアス", ["ドラゴン", "じめん"], [("じしん", "earthquake"), ("ドラゴンクロー", "dragonclaw")],
               hp=50.0, hp_max=183)
    lop = _mon("lopunny", "ミミロップ", ["ノーマル"], [("インファイト", "closecombat"), ("おんがえし", "return")],
               hp=100.0, hp_max=hp_max, hp_uncertain=uncertain)
    arch = _mon("archaludon", "ブリジュラス", ["はがね", "ドラゴン"], [("ラスターカノン", "flashcannon")], hp=60.0, hp_max=167)
    pult = _mon("dragapult", "ドラパルト", ["ドラゴン", "ゴースト"], picked=False, revealed_moves=["ドラゴンアロー", "シャドーボール"])
    return _state([gar, lop, arch], [pult], my_rem=3, opp_rem=3)


def test_hp_uncertain_switch_not_promoted():
    """不確かな控えへの交代は、確定 HP のときより上位にならない (悲観側 = 下限 1 HP で T と V の両方を評価)。
    楽観側の仮説の順位は感度として残る。最大 HP が分からなければ範囲が作れず legacy に退避 (scoring_fallback = "hp_range")"""
    with _Hint():
        certain, inp_c = _capture_inputs(_uncertain_bench_state(False))
        unc, inp_u = _capture_inputs(_uncertain_bench_state(True))
        nomax = evaluate(copy.deepcopy(_uncertain_bench_state(True, hp_max=None)), resolver, rl_blend_weight=0,
                         scoring_mode="transition")
    assert certain["scoring_used"] == "transition" and unc["scoring_used"] == "transition", unc.get("scoring_fallback")

    def rank(adv):
        return next(i for i, a in enumerate(adv["actions"]) if a["kind"] == "switch" and a["id"] == "lopunny")

    sw_c = certain["actions"][rank(certain)]
    sw_u = unc["actions"][rank(unc)]
    assert rank(unc) >= rank(certain) and sw_u["q"] <= sw_c["q"], (rank(unc), rank(certain), sw_u["q"], sw_c["q"])
    # T と V に同じ仮説の値: 入力の控えの HP が下限 (1 HP) になっている (交代の遷移も V もこの値を読む)
    j = next(j for j, (v, _hp) in enumerate(inp_u.me.bench) if v.species_id == "lopunny")
    assert abs(inp_u.me.bench[j][1] - 1 / 200) < 1e-9 and inp_u.hp_alt[0].bench[j][1] == 1.0
    assert sw_u["hp_range"] == [0.005, 1.0] and "HP は範囲" in sw_u["reason"]
    hr = unc["transition_stats"]["hp_range"]
    assert hr["individuals"][0]["source"] == "uncertain" and "optimistic" in hr, hr
    assert "q_hp_opt" in sw_u and sw_u["q_hp_opt"] >= sw_u["q"]
    # legacy の −15 は重ねない (transition の点は Q の線形変換だけ)
    assert abs(sw_u["score"] - (50 + 50 * sw_u["q"])) <= 0.06
    assert nomax["scoring_fallback"] == "hp_range" and nomax["scoring_used"] == "legacy", nomax.get("scoring_fallback")
    import json
    json.dumps(unc, ensure_ascii=False)                         # advice 行にそのまま書ける (hp_range の欄を含む)
    print("test_hp_uncertain_switch_not_promoted OK")


# ==============================================================================
# 段 4: アンコール・ちょうはつ・みがわり・こらえる、相手側の未対応の退避、決定単位の集計
# ==============================================================================
def test_encore_taunt_substitute_endure():
    # アンコール: 先に動いた相手の アンコール で、自分の インファイト は直前の技 (つるぎのまい) に替わる (チャンピオンズの encore)
    me = SimSide(_view("lopunny", ev={"atk": 252, "spe": 0}), 1.0, last_move="swordsdance")
    opp = SimSide(_view("whimsicott"), 1.0)
    brs = T.resolve_turn_branches(me, opp, _mv("closecombat"), _mv("encore"), None, None)
    for p, r in brs:
        assert ("me", "encore_override", "swordsdance") in r.events and "encore:swordsdance" in r.me.volatiles, r.events
        assert r.opp.active_hp == 1.0 and r.me.active.boosts.get("atk") == 2
        assert not r.unsupported
    # 直前の技が無ければ失敗、メンタルハーブはすぐ治して消費する
    r2 = T.resolve_move(opp, replace(me, last_move=None), "encore", None, full=True)
    assert ("user", "fail", "encore_no_last_move") in r2.events
    r3 = T.resolve_move(opp, replace(me, active=replace(me.active, item="mentalherb")), "encore", None, full=True)
    assert r3.target.active.item is None and not any(str(v).startswith("encore") for v in r3.target.volatiles)
    # V: アンコール中の側は固定技だけで撃ち合う
    assert VA.usable_moves(SimSide(me.active, 1.0, volatiles=frozenset({"encore:swordsdance"})), ("closecombat",)) == \
        ["swordsdance"]
    # ちょうはつ: 先に動いた相手の ちょうはつ で、自分の変化技は失敗する
    tb = T.resolve_turn_branches(me, opp, _mv("swordsdance"), _mv("taunt"), None, None)
    for p, r in tb:
        assert ("me", "fail", "taunt") in r.events and not r.me.active.boosts.get("atk"), r.events
    # みがわり: 最大 HP の 1/4 を失い、次の攻撃を身代わりが受ける (超えた分は通らない)。反動は身代わりへのダメージで掛かる
    sub_user = SimSide(_view("garchomp"), 1.0)
    rs = T.resolve_move(sub_user, opp, "substitute", None, full=True)
    q = T.floor_hp_frac(sub_user.active, 1, 4)
    assert abs(rs.user.sub_hp - q) < 1e-12 and abs(rs.user.active_hp - (1.0 - q)) < 1e-12
    hit = T.resolve_move(SimSide(_view("tauros"), 1.0), rs.user, "doubleedge", None, full=True, hit=True,
                         hit_damages=[0.9])
    assert hit.target.active_hp == rs.user.active_hp and hit.target.sub_hp == 0.0
    assert any(k == "recoil" for _w, k, _d in hit.events)
    # 状態異常の技は身代わりで失敗、音技は貫通する
    st = T.resolve_move(opp, rs.user, "willowisp", None, full=True, hit=True)
    assert st.target.active.status is None and ("target", "substitute_block", "willowisp") in st.events
    hv = T.resolve_move(SimSide(_view("sylveon"), 1.0), rs.user, "hypervoice", None, full=True, hit=True,
                        hit_damages=[0.1])
    assert hv.target.sub_hp == rs.user.sub_hp and hv.target.active_hp < rs.user.active_hp
    # HP が最大 HP の 1/4 以下なら失敗 (source.hp <= source.maxhp / 4)
    weak = T.resolve_move(replace(sub_user, active_hp=0.25), opp, "substitute", None, full=True)
    assert weak.user.sub_hp == 0.0 and weak.user.active_hp == 0.25 and ("user", "fail", "substitute") in weak.events
    # こらえる: このターンの攻撃を HP 1 で耐える。後に行動する者が居なければ失敗
    en = T.resolve_turn_branches(SimSide(_view("garchomp"), 0.3), SimSide(_view("dragapult"), 1.0), _mv("endure"),
                                 _mv("dragondarts"), None, None)
    for p, r in en:
        assert r.me.active_hp > 0 and "endure" not in r.me.volatiles, r.events
    # 同じ優先度 (+4) の まもる を先に使われると、こらえる は最後の行動になり失敗する
    last = T.resolve_turn_branches(SimSide(_view("garchomp", ev={"atk": 252, "spe": 0}), 0.3),
                                   SimSide(_view("dragapult"), 1.0), _mv("endure"), _mv("protect"), None, None)
    assert all(("me", "fail", "endure_last") in r.events for _p, r in last), [r.events for _p, r in last]
    print("test_encore_taunt_substitute_endure OK")


def test_opp_encore_not_fallback_and_unsupported_fallback():
    """相手の アンコール を含む局面は退避しない (対応済み)。未対応の相手の技 (トリックルーム) は退避する
    (scoring_fallback = "opp_unsupported:<効果>:<技>")。自分の アンコール も退避しない (M2b では unsupported で退避していた)"""
    lop = _mon("lopunny", "ミミロップ", ["ノーマル"], [("インファイト", "closecombat"), ("つるぎのまい", "swordsdance")])
    wh = _mon("whimsicott", "エルフーン", ["くさ", "フェアリー"], picked=False, revealed_moves=["アンコール", "ムーンフォース"])
    st = _state([lop, _mon("garchomp", "ガブリアス", ["ドラゴン", "じめん"], [("じしん", "earthquake")])], [wh],
                my_rem=3, opp_rem=3)
    st["last_move"] = {"player": "swordsdance"}
    with _Hint():
        adv, inp = _capture_inputs(st)
    assert adv["scoring_used"] == "transition" and adv["scoring_fallback"] is None, adv.get("scoring_fallback")
    assert any(a.move_id == "encore" for _p, a in inp.responses)
    assert inp.me.last_move == "swordsdance"
    hz = _mon("hatterene", "ブリムオン", ["エスパー", "フェアリー"], picked=False, revealed_moves=["トリックルーム", "サイコキネシス"])
    st2 = _state([copy.deepcopy(lop)], [hz], my_rem=3, opp_rem=3)
    with _Hint():
        tr = evaluate(st2, resolver, rl_blend_weight=0, scoring_mode="transition")
    assert tr["scoring_used"] == "legacy" and tr["scoring_fallback"] == "opp_unsupported:effect_unmodeled:trickroom", \
        tr.get("scoring_fallback")
    # 自分の アンコール (M2b では "unsupported:effect_unmodeled:encore" で退避していた) は遷移で解決する
    ind = _mon("indeedee", "イエッサン", ["エスパー", "ノーマル"],
               [("マジカルフレイム", "mysticalfire"), ("アンコール", "encore"), ("ワイドフォース", "expandingforce")])
    gar = _mon("garchomp", "ガブリアス", ["ドラゴン", "じめん"], picked=False, revealed_moves=["じしん"])
    st3 = _state([ind], [gar], my_rem=3, opp_rem=3)
    st3["last_move"] = {"opponent": "earthquake"}
    with _Hint():
        tr3 = evaluate(st3, resolver, rl_blend_weight=0, scoring_mode="transition")
    assert tr3["scoring_used"] == "transition", tr3.get("scoring_fallback")
    print("test_opp_encore_not_fallback_and_unsupported_fallback OK")


def test_multiple_fixed_responses():
    """固定した複数の応手: Q は応手ごとの分岐の期待値の確率加重和 (分岐の影響を検証する)。応手の確率の合計は 1"""
    me = SimSide(_view("garchomp"), 0.7, bench=[(_view("lopunny"), 1.0)])
    opp = SimSide(_view("dragapult"), 0.8)
    responses = [(0.5, _mv("dragondarts")), (0.3, _mv("shadowball")), (0.2, _mv("uturn"))]
    inp = _inp(me, opp, ["earthquake", "dragonclaw"], responses,
               opp_moves={"dragapult": ("dragondarts", "shadowball", "uturn")})
    res = ST.score(inp, budget_ms=10_000)
    for a in res["actions"]:
        q = 0.0
        for p_o, oa in responses:
            brs = T.resolve_turn_branches(me, opp, _mv(a["id"]), oa, None, None, damage_fn=inp.damage_fn)
            assert abs(sum(p for p, _r in brs) - 1.0) < 1e-12
            q += p_o * sum(p * VA.value(r.me, r.opp, inp.vctx, r.winner) for p, r in brs)
        assert abs(q - a["q"]) < 1e-4, (a["id"], q, a["q"])
    print("test_multiple_fixed_responses OK")


def test_summarize_fallbacks_decision_unit():
    """退避率を決定単位で集計する: decision 行の advice_id の助言を代表にする / 渡さなければ同じ局面が続く行を 1 つに数える"""
    def adv(aid, fb, pk):
        return {"type": "advice", "advice_id": aid, "turn": 1,
                "advice": {"scoring_mode": "transition", "scoring_fallback": fb, "position_key": pk}}

    rows = [adv("a1", "opp_unsupported:effect_unmodeled:bellydrum", "P1"),
            adv("a2", "opp_unsupported:effect_unmodeled:bellydrum", "P1"),
            adv("a3", "opp_unsupported:effect_unmodeled:bellydrum", "P1"),
            adv("a4", None, "P2"), adv("a5", "budget", "P3")]
    s = ST.summarize_fallbacks(rows)
    assert s["n"] == 5 and s["n_fallback"] == 4                              # 行の単位 (従来)
    assert s["decisions"]["n"] == 3 and s["decisions"]["n_fallback"] == 2 and s["decisions"]["unit"] == "position"
    assert s["decisions"]["opp_unsupported"] == {"n": 1, "rate": 1 / 3, "by_effect": {"effect_unmodeled": 1}}
    dec = [{"type": "decision", "advice_id": "a3"}, {"type": "decision", "advice_id": "a4"},
           {"type": "decision", "advice_id": "zz"}]
    s2 = ST.summarize_fallbacks(rows, decisions=dec)
    assert s2["decisions"]["n"] == 2 and s2["decisions"]["n_fallback"] == 1 and s2["decisions"]["unit"] == "decision"
    assert s2["decisions"]["by_reason"] == {"opp_unsupported:effect_unmodeled:bellydrum": 1}
    # advisor-as-player の stats も相手側の未対応を効果と技まで分ける
    from champions_agent.env.advisor_player import _count_scoring
    stats: dict = {}
    _count_scoring(stats, rows[0]["advice"])
    assert stats == {"scoring_transition": 1, "scoring_fallback": 1,
                     "scoring_fallback:opp_unsupported:effect_unmodeled:bellydrum": 1}, stats
    print("test_summarize_fallbacks_decision_unit OK")


# ==============================================================================
# 段 5: 判明情報との矛盾の除外と感度
# ==============================================================================
def test_consumed_sash_and_opp_locks():
    """消費済みのタスキ (item_consumed) は耐えない。相手の技の固定 (観測したこだわり・ちょうはつ・かなしばり) は応手に反映。
    感度の欄 (スカーフ / タスキの有無) が出る"""
    gar = _mon("garchomp", "ガブリアス", ["ドラゴン", "じめん"], [("じしん", "earthquake"), ("ドラゴンクロー", "dragonclaw")])
    hea = _mon("heatran", "ヒードラン", ["ほのお", "はがね"], picked=False, item_id="focussash", item_consumed=True,
               revealed_moves=["マグマストーム", "だいちのちから"])
    st = _state([gar], [hea], my_rem=3, opp_rem=3)
    with _Hint():
        adv, inp = _capture_inputs(st)
    assert inp.opp.active.item is None, inp.opp.active.item
    assert VA.ko_prob(inp.me, inp.opp, "earthquake", None, inp.vctx, side="me") == 1.0
    # 旧挙動の再現: 消費済みの印を見ずに タスキ を残すと、HP 満タンの単発では倒せない
    assert VA.ko_prob(inp.me, replace(inp.opp, active=replace(inp.opp.active, item="focussash")), "earthquake", None,
                      VA.ValuationContext(my_moves={}, opp_moves={}, damage_fn=inp.damage_fn), side="me") == 0.0
    assert any("消費済み" in n for n in inp.notes)
    # 相手の観測したこだわり (直前の技 = マグマストーム) → 応手の技はそれだけ
    hea2 = _mon("heatran", "ヒードラン", ["ほのお", "はがね"], picked=False, item_id="choicescarf",
                revealed_moves=["マグマストーム", "だいちのちから"])
    st2 = _state([copy.deepcopy(gar)], [hea2], my_rem=3, opp_rem=3)
    st2["last_move"] = {"opponent": "magmastorm"}
    with _Hint():
        adv2, inp2 = _capture_inputs(st2)
    assert {a.move_id for _p, a in inp2.responses if a.kind == "move"} == {"magmastorm"}, inp2.responses
    sens = adv2["transition_stats"]["sensitivity"]
    assert sens["scarf"] == {"omitted": "item_known"} and sens["sash"] == {"omitted": "item_known"}, sens
    # ちょうはつ中は変化技を除く、かなしばりの技を除く (純粋な関数)
    pool = [("magmastorm", 100.0), ("stealthrock", 100.0), ("earthpower", 100.0)]
    p1, _n = ST.restrict_opp_pool(pool, {"volatiles": ["taunt", "disable_earthpower"]}, {}, None)
    assert p1 == [("magmastorm", 100.0)], p1
    # 持ち物が観測で確定していなければ、スカーフ / タスキの有無を入れ替えた評価の結果が出る
    hea3 = _mon("heatran", "ヒードラン", ["ほのお", "はがね"], picked=False, revealed_moves=["マグマストーム"])
    st3 = _state([copy.deepcopy(gar)], [hea3], my_rem=3, opp_rem=3)
    with _Hint():
        adv3 = evaluate(st3, resolver, rl_blend_weight=0, scoring_mode="transition")
    s3 = adv3["transition_stats"]["sensitivity"]
    for k in ("scarf", "sash"):
        assert "changes" in s3[k] or "omitted" in s3[k], s3
    assert s3["scarf"].get("flip") == "add" and isinstance(s3["scarf"].get("changes"), bool), s3
    import json
    json.dumps(adv3, ensure_ascii=False)                        # advice 行にそのまま書ける (sensitivity の欄を含む)
    print("test_consumed_sash_and_opp_locks OK")


if __name__ == "__main__":
    test_cache_hp_dependent_moves()
    test_scoring_order_independent()
    test_rolls_not_collapsed_steelbeam()
    test_merge_granularity_config()
    test_simultaneous_wipe_winner()
    test_hp_range_rules()
    test_hp_uncertain_switch_not_promoted()
    test_encore_taunt_substitute_endure()
    test_opp_encore_not_fallback_and_unsupported_fallback()
    test_multiple_fixed_responses()
    test_summarize_fallbacks_decision_unit()
    test_consumed_sash_and_opp_locks()
    print("ALL OK")
