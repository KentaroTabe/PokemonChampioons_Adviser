"""共通の状態遷移 advisor/transition.py (2026-10-09 M1b、docs/ADVICE_ACCURACY_PLAN_1009.md §2.3 / 段 B-2 / §4.1) のテスト。

合成局面で、自滅・HP の消費・反動の発動順序、ひんし、繰り出し要求、行動前にひんしになった個体が行動しないことを確かめる。
根拠はローカルの Showdown (pokemon-showdown/、mod champions は該当の技・特性を上書きしていない):
- sim/battle-actions.ts useMoveInner: selfdestruct "always" は TryMove の後・命中判定 (まもる・タイプ無効) の前に faint。
  対象が居なければ (-fail / [notarget]) その前に失敗して自滅しない
- sim/battle-actions.ts applyRecoilDamage: recoil = clampIntRange(Math.round(与えた HP × 割合), 1)、
  mindBlownRecoil / chloroblastRecoil = Math.round(maxhp / 2)。data/moves.ts mindblown / steelbeam は onMoveFail でも同じ量を失う
- sim/battle.ts runAction (case 'move'): ひんしの個体は行動しない。繰り出しはターンの終わりの switch 要求
- data/abilities.ts damp (onAnyTryMove で explosion / mindblown / mistyexplosion / selfdestruct を止める)、
  rockhead (effect.id === 'recoil' を防ぐ)、magicguard (技以外のダメージを防ぐ)

    python -m tests.test_transition
"""
from __future__ import annotations

import math
import os
from dataclasses import replace

os.environ.setdefault("RL_BLEND_WEIGHT", "0")

from advisor import transition as T
from advisor.damage import MonView
from advisor.dex import get_dex
from advisor.search import simulate_turn
from advisor.transition import Action, SimSide, resolve_move, resolve_turn


def _view(sid, ability=None, item=None, ev=None):
    sp = get_dex().species(sid)
    return MonView(species_id=sid, name_ja=sid, types=list(sp["types"]), base=dict(sp["baseStats"]),
                   ev=ev or {"atk": 252, "spa": 252, "spe": 252}, ability=ability, item=item)


def _mv(m):
    return Action("move", move_id=m, label=m)


def _kinds(res, who=None):
    return [(w, k) for w, k, _d in res.events if who is None or w == who]


def _half(view):
    """Math.round(maxhp / 2) / maxhp (Showdown の mindBlownRecoil の量を独立に計算)"""
    mhp = view.max_hp()
    return math.floor(mhp / 2 + 0.5) / mhp


# ==============================================================================
# (a) 自滅技で相手を倒し、自分もひんし。両方に繰り出し要求、繰り出した個体は行動しない
# ==============================================================================
def test_selfko_kos_target_and_user():
    me = SimSide(_view("slurpuff"), 1.0, bench=[(_view("lopunny"), 1.0)])
    opp = SimSide(_view("ninetales"), 0.05, bench=[(_view("starmie"), 1.0)])
    res = resolve_turn(me, opp, _mv("mistyexplosion"), _mv("nastyplot"), None, None, "avg")
    # キュウコン (素早さ 100) が先に わるだくみ、ペロリーム (72) が ミストバースト
    assert _kinds(res) == [("opp", "move"), ("opp", "boost"),
                           ("me", "move"), ("me", "selfko"), ("opp", "damage"),
                           ("me", "faint"), ("opp", "faint"),
                           ("me", "replace"), ("opp", "replace")], res.events
    assert res.fainted == {"me": True, "opp": True}
    assert res.requires_switch == {"me": True, "opp": True}
    # 繰り出した個体は無傷で行動しない (ステルスロック無し)
    assert res.me.active.species_id == "lopunny" and res.me.active_hp == 1.0
    assert res.opp.active.species_id == "starmie" and res.opp.active_hp == 1.0
    assert dict((v.species_id, hp) for v, hp in res.me.bench) == {"slurpuff": 0.0}
    assert res.me.alive_count() == 1 and res.opp.alive_count() == 1
    # 控えが居なければ繰り出し要求は無く、陣営は残り 0
    r2 = resolve_turn(replace(me, bench=[]), opp, _mv("mistyexplosion"), _mv("nastyplot"), None, None, "avg")
    assert r2.fainted["me"] and not r2.requires_switch["me"] and r2.me.alive_count() == 0
    print("test_selfko_kos_target_and_user OK")


# ==============================================================================
# (b) 自滅技が無効 (ゴーストへの だいばくはつ) でも、まもるで防がれても自滅
# ==============================================================================
def test_selfko_even_if_immune_or_protected():
    me = SimSide(_view("forretress"), 1.0, bench=[(_view("garchomp"), 1.0)])
    ghost = SimSide(_view("dragapult"), 1.0)
    res = resolve_turn(me, ghost, _mv("explosion"), _mv("dragondance"), None, None, "max")
    assert res.opp.active_hp == 1.0 and ("opp", "damage") not in _kinds(res)
    assert res.fainted["me"] and res.requires_switch["me"] and ("me", "selfko") in _kinds(res)
    assert res.me.active.species_id == "garchomp"
    # まもる (優先度 +4 で先に動く) で防がれても自滅する
    normal = SimSide(_view("garchomp"), 1.0)
    res2 = resolve_turn(me, normal, _mv("explosion"), _mv("protect"), None, None, "max")
    assert res2.opp.active_hp == 1.0 and ("opp", "protected") in _kinds(res2)
    assert res2.fainted["me"]
    # ほのおタイプへの ミストバースト は通常どおり (いまひとつでも与ダメージあり + 自滅)
    res3 = resolve_turn(SimSide(_view("slurpuff"), 1.0), SimSide(_view("ninetales"), 1.0),
                        _mv("mistyexplosion"), _mv("nastyplot"), None, None, "avg")
    assert 0.0 < res3.opp.active_hp < 1.0 and res3.fainted["me"]
    print("test_selfko_even_if_immune_or_protected OK")


# ==============================================================================
# (c) 行動前に倒されたら自滅しない (与ダメージ 0)。繰り出した個体は予定行動を引き継がない
# ==============================================================================
def test_ko_before_acting_no_selfko():
    me = SimSide(_view("slurpuff"), 0.05, bench=[(_view("lopunny"), 1.0)])
    opp = SimSide(_view("ninetales"), 1.0)
    res = resolve_turn(me, opp, _mv("mistyexplosion"), _mv("flamethrower"), None, None, "min")
    assert ("me", "skip") in _kinds(res) and ("me", "selfko") not in _kinds(res)
    assert not res.acted["me"] and res.acted["opp"]
    assert res.opp.active_hp == 1.0                      # 先に倒された分岐の与ダメージは 0
    assert res.fainted["me"] and res.requires_switch["me"]
    assert res.me.active.species_id == "lopunny" and res.me.active_hp == 1.0   # 繰り出したミミロップは行動も被弾もしない
    # 逆向き: 先に倒された相手の控え (ロトム) が倒れたガブリアスの じしん を撃たない。M1a までの simulate_turn は倒した直後に
    # 控えを出し、その控えが元の予定行動を実行していた (ドラパルトの HP が減っていた)。Showdown は runAction でひんしの個体の
    # 行動を飛ばし、繰り出しはターンの終わり
    fast = SimSide(_view("dragapult"), 1.0)        # 素早さ 142 > ガブリアス 102
    chomp = SimSide(_view("garchomp"), 0.05, bench=[(_view("rotomwash"), 1.0)])
    m2, o2 = simulate_turn(fast, chomp, _mv("dracometeor"), _mv("earthquake"), None, None, "avg")
    assert o2.active.species_id == "rotomwash" and o2.active_hp == 1.0
    assert m2.active_hp == 1.0, "倒れた個体の予定行動を繰り出した控えが実行している"
    print("test_ko_before_acting_no_selfko OK")


def test_no_target_after_foe_selfko():
    """相手が先に自滅して場に居ない: 相手を対象にする技は失敗 (自滅・HP の消費も起きない)。自分対象の技は出る"""
    boom = SimSide(_view("dragapult", ev={"hp": 0, "atk": 252, "spe": 252}), 1.0)
    tank = SimSide(_view("archaludon", ev={"hp": 252, "def": 252}), 1.0)
    res = resolve_turn(tank, boom, _mv("steelbeam"), _mv("explosion"), None, None, "min")
    assert res.fainted["opp"]
    assert ("me", "fail") in _kinds(res) and ("me", "hp_cost") not in _kinds(res)
    assert res.me.active_hp == 1.0 - [d for w, k, d in res.events if w == "me" and k == "damage"][0]
    res2 = resolve_turn(tank, boom, _mv("irondefense"), _mv("explosion"), None, None, "min")
    assert res2.me.active.boosts.get("def") == 2
    print("test_no_target_after_foe_selfko OK")


# ==============================================================================
# (d) てっていこうせん: 最大 HP の 1/2 を失う。残り 1/2 以下ならひんし。まもるでも失う
# ==============================================================================
def test_half_hp_cost():
    user = _view("archaludon")
    half = _half(user)
    foe = SimSide(_view("garchomp"), 1.0)
    r = resolve_move(SimSide(user, 1.0), foe, "steelbeam", None, "avg")
    assert abs(r.user.active_hp - (1.0 - half)) < 1e-9 and r.dealt > 0
    assert [k for _w, k, _d in r.events] == ["damage", "hp_cost"]          # ダメージの後に HP を失う
    r2 = resolve_move(SimSide(user, 0.5), foe, "steelbeam", None, "avg")
    assert r2.user.active_hp == 0.0 and r2.faint_order[-1] == "user"
    r3 = resolve_move(SimSide(user, 1.0), foe, "steelbeam", None, "avg", target_protected=True)
    assert r3.target.active_hp == 1.0 and abs(r3.user.active_hp - (1.0 - half)) < 1e-9
    # マジックガードは防ぐ。いしあたまは防がない (効果は 'recoil' ではない)
    assert resolve_move(SimSide(replace(user, ability="magicguard"), 1.0), foe, "steelbeam", None).user.active_hp == 1.0
    assert resolve_move(SimSide(replace(user, ability="rockhead"), 1.0), foe, "steelbeam", None).user.active_hp < 1.0
    # しめりけ: ビックリヘッド (mindblown) と自滅技は不発 (ダメージ・HP の消費・自滅なし)。てっていこうせん は Showdown の
    # damp の一覧に無いので通常どおり。かたやぶりは相手のしめりけを無視
    damp = SimSide(_view("swampert", ability="damp"), 1.0)
    rd = resolve_move(SimSide(user, 1.0), damp, "mindblown", None)
    assert not rd.executed and rd.user.active_hp == 1.0 and rd.target.active_hp == 1.0
    rs = resolve_move(SimSide(user, 1.0), damp, "steelbeam", None)
    assert rs.executed and rs.target.active_hp < 1.0 and abs(rs.user.active_hp - (1.0 - half)) < 1e-9
    # クロロブラスト: 与ダメージがあったときだけ 1/2 (onMoveFail なし)。いしあたまで防げる (効果は 'recoil')
    rc = resolve_move(SimSide(user, 1.0), foe, "chloroblast", None, target_protected=True)
    assert rc.user.active_hp == 1.0
    rc2 = resolve_move(SimSide(user, 1.0), foe, "chloroblast", None)
    assert rc2.dealt > 0 and abs(rc2.user.active_hp - (1.0 - half)) < 1e-9
    assert resolve_move(SimSide(replace(user, ability="rockhead"), 1.0), foe, "chloroblast", None).user.active_hp == 1.0
    rx = resolve_move(SimSide(_view("forretress"), 1.0), damp, "explosion", None)
    assert not rx.executed and rx.user.active_hp == 1.0
    rm = resolve_move(SimSide(_view("forretress", ability="moldbreaker"), 1.0), damp, "explosion", None)
    assert rm.executed and rm.user.active_hp == 0.0
    # 遷移のターン全体でも同じ (相手が先に まもる → それでも失う)
    res = resolve_turn(SimSide(user, 1.0), foe, _mv("steelbeam"), _mv("protect"), None, None, "avg")
    assert res.opp.active_hp == 1.0 and abs(res.me.active_hp - (1.0 - half)) < 1e-9
    print("test_half_hp_cost OK")


# ==============================================================================
# (e) 反動で倒れる (与えた HP × 割合)。いしあたまは防ぐ
# ==============================================================================
def test_recoil():
    bird = _view("staraptor")
    foe_view = _view("garchomp", ev={"hp": 252})
    r = resolve_move(SimSide(bird, 1.0), SimSide(foe_view, 1.0), "bravebird", None, "avg")
    dealt_hp = r.dealt * foe_view.max_hp()
    expect = max(1, math.floor(dealt_hp * 0.33 + 0.5)) / bird.max_hp()
    assert abs((1.0 - r.user.active_hp) - expect) < 1e-9, (r.user.active_hp, expect)
    assert [k for _w, k, _d in r.events] == ["damage", "recoil"]
    # 相手の残り HP が少ないと、反動は実際に与えた HP (残り HP) から計算する
    r_low = resolve_move(SimSide(bird, 1.0), SimSide(foe_view, 0.1), "bravebird", None, "avg")
    assert abs(r_low.dealt - 0.1) < 1e-9
    expect_low = max(1, math.floor(0.1 * foe_view.max_hp() * 0.33 + 0.5)) / bird.max_hp()
    assert abs((1.0 - r_low.user.active_hp) - expect_low) < 1e-9
    # 反動で倒れる: 使用者の HP は反動の半分。相手を倒した上で自分もひんし (相手 → 自分の順に HP 0)
    res = resolve_turn(SimSide(bird, expect_low / 2, bench=[(_view("lopunny"), 1.0)]), SimSide(foe_view, 0.1),
                       _mv("bravebird"), _mv("swordsdance"), None, None, "avg")
    assert res.fainted == {"me": True, "opp": True}
    assert [w for w, k, _d in res.events if k == "faint"] == ["opp", "me"]
    assert res.requires_switch["me"] and not res.requires_switch["opp"]
    # いしあたま
    rh = resolve_move(SimSide(replace(bird, ability="rockhead"), 0.05), SimSide(foe_view, 1.0), "bravebird", None)
    assert rh.user.active_hp == 0.05 and ("user", "recoil_blocked", "rockhead") in rh.events
    # 与ダメージ 0 (無効) なら反動なし
    rg = resolve_move(SimSide(_view("lopunny"), 1.0), SimSide(_view("dragapult"), 1.0), "doubleedge", None)
    assert rg.dealt == 0.0 and rg.user.active_hp == 1.0
    print("test_recoil OK")


# ==============================================================================
# (f) 連続技: 従来どおり期待回数の合計を 1 回で与える (回数の分岐は段 B-1)。未対応の印が残る
# ==============================================================================
def test_multihit_unchanged():
    me_v, foe_v = _view("breloom"), _view("garchomp")
    r = resolve_move(SimSide(me_v, 1.0), SimSide(foe_v, 1.0), "bulletseed", None, "avg")
    assert abs(r.dealt - T.dmg_frac(me_v, foe_v, 1.0, "bulletseed", None, "avg")) < 1e-9
    assert [k for _w, k, _d in r.events] == ["damage"] and r.user.active_hp == 1.0
    assert ("user", "multihit_expected", "bulletseed") in r.unsupported
    print("test_multihit_unchanged OK")


def test_unsupported_marks_and_purity():
    """未対応の効果は結果に印を残す (成功扱いで効果を捏造しない)。入力は書き換えない。simulate_turn は resolve_turn と同じ状態"""
    me = SimSide(_view("conkeldurr", item="lifeorb"), 0.5, bench=[(_view("clefable"), 1.0)])
    opp = SimSide(_view("garchomp", item="rockyhelmet"), 1.0)
    snap = (me.active_hp, list(me.bench), opp.active_hp, dict(me.active.boosts))
    res = resolve_turn(me, opp, _mv("drainpunch"), _mv("lightscreen"), None, None, "avg")
    marks = {(w, e) for w, e, _s in res.unsupported}
    assert {("me", "drain"), ("me", "self_damage_item"), ("opp", "contact_damage"),
            ("opp", "effect_unmodeled")} <= marks, marks
    assert res.me.active_hp == 0.5                               # 吸収・いのちのたま・ゴツゴツメットは HP を変えない (未対応)
    assert (me.active_hp, list(me.bench), opp.active_hp, dict(me.active.boosts)) == snap
    m2, o2 = simulate_turn(me, opp, _mv("drainpunch"), _mv("lightscreen"), None, None, "avg")
    assert (m2, o2) == (res.me, res.opp)
    print("test_unsupported_marks_and_purity OK")


# ==============================================================================
# 段 3: 直接の採点への受け渡し (self_cost の欄と理由。スコアは変えない)
# ==============================================================================
def _mon(sid, ja, types, moves=(), hp=100.0, picked=True):
    return {"species_id": sid, "species_ja": ja, "types": list(types), "hp_percent": hp, "hp_current": None,
            "hp_max": None, "status": None, "boosts": {}, "ability_id": None, "item_id": None,
            "moves": [{"name_ja": ja_m, "move_id": mid, "pp": 5, "max_pp": None, "effectiveness": None}
                      for ja_m, mid in moves],
            "revealed_moves": [], "is_picked": picked}


def _state(my_party, opp_party):
    side = lambda party: {"active_index": 0, "remaining": 3, "tailwind": False,
                          "hazards": {"stealth_rock": False, "spikes": 0, "toxic_spikes": 0, "sticky_web": False},
                          "screens": {"reflect": False, "light_screen": False, "aurora_veil": False},
                          "party": party}
    return {"field": {"weather": None, "terrain": None, "trick_room": False},
            "mega_used": {"player": False, "opponent": False},
            "player": side(my_party), "opponent": side(opp_party)}


SLURPUFF_MOVES = (("ミストバースト", "mistyexplosion"), ("ひかりのかべ", "lightscreen"),
                  ("ねばねばネット", "stickyweb"), ("あくび", "yawn"))


def _evaluate_with_and_without_self_cost(state):
    """同じ局面を、遷移の自分の損失あり (M1b) と、自分の損失を解決しない場合 (= M1a までの採点) で評価する"""
    from advisor.engine import evaluate
    from vision.normalize import NameResolver
    resolver = NameResolver()
    with_sc = evaluate(state, resolver, rl_blend_weight=0)
    orig = T.self_cost_kind
    T.self_cost_kind = lambda m: None
    try:
        without = evaluate(state, resolver, rl_blend_weight=0)
    finally:
        T.self_cost_kind = orig
    return with_sc, without


def _ranking(adv):
    return [(a["kind"], a.get("id"), a["score"]) for a in adv["actions"]]


def test_engine_self_cost_112839():
    """battle_20261009_112839.jsonl 39 行目の局面 (ペロリーム HP 不明 vs キュウコン 100%、選出はペロリーム・ミミロップ。
    簡約状態の値を写して合成。技は当時の advice 行の 4 技)。ミストバーストの候補に self_cost が付き、理由に「自分はひんし」。
    スコアと順位は self_cost の有無で同じ (順位の変更は段 B の方式比較で行う。計画書 §3.0)"""
    my = [_mon("slurpuff", "ペロリーム", ["フェアリー"], SLURPUFF_MOVES, hp=None),
          _mon("lopunny", "ミミロップ", ["ノーマル"], hp=None),
          _mon("archaludon", "ブリジュラス", ["はがね", "ドラゴン"], hp=None, picked=False)]
    opp = [_mon("ninetales", "キュウコン", ["ほのお"], hp=100.0, picked=False)]
    adv, base = _evaluate_with_and_without_self_cost(_state(my, opp))
    me_x = next(a for a in adv["actions"] if a.get("id") == "mistyexplosion")
    assert me_x["self_cost"] == {"kind": "selfko", "faints": True, "hp_loss_frac": 1.0, "requires_switch": True,
                                 "before_ko_risk": False}, me_x
    assert "自分はひんし (控えを出す)" in me_x["reason"], me_x["reason"]
    assert not any("self_cost" in a for a in adv["actions"] if a.get("id") != "mistyexplosion")
    assert _ranking(adv) == _ranking(base), (_ranking(adv), _ranking(base))
    print("test_engine_self_cost_112839 OK")


def test_engine_self_cost_before_ko_risk():
    """行動前に倒される見込み (遅いペロリーム HP 5% vs キュウコン): 自滅技は「撃てれば」の分岐の損失として残し、
    既存の ×0.25 の割引 (act_discount) はそのまま (スコアは self_cost の有無で同じ)"""
    my = [_mon("slurpuff", "ペロリーム", ["フェアリー"], SLURPUFF_MOVES, hp=5.0),
          _mon("lopunny", "ミミロップ", ["ノーマル"], hp=100.0)]
    opp = [_mon("ninetales", "キュウコン", ["ほのお"], hp=100.0, picked=False)]
    opp[0]["revealed_moves"] = ["かえんほうしゃ"]     # 使用率データが無い環境 (CI) でも同じ脅威になるように
    adv, base = _evaluate_with_and_without_self_cost(_state(my, opp))
    me_x = next(a for a in adv["actions"] if a.get("id") == "mistyexplosion")
    assert me_x["self_cost"]["before_ko_risk"] is True and me_x["self_cost"]["faints"], me_x
    assert "行動前に倒される見込み" in me_x["reason"] and "先に倒されれば不発" in me_x["reason"], me_x["reason"]
    assert _ranking(adv) == _ranking(base)
    print("test_engine_self_cost_before_ko_risk OK")


def test_engine_self_cost_half_hp_and_recoil():
    """てっていこうせん (HP 1/2) と反動技は自分の HP の損失を、控えが居なければ繰り出し要求なしで残す"""
    my = [_mon("archaludon", "ブリジュラス", ["はがね", "ドラゴン"],
               (("てっていこうせん", "steelbeam"), ("すてみタックル", "doubleedge")), hp=40.0)]
    opp = [_mon("garchomp", "ガブリアス", ["ドラゴン", "じめん"], hp=100.0, picked=False)]
    adv, base = _evaluate_with_and_without_self_cost(_state(my, opp))
    sb = next(a for a in adv["actions"] if a.get("id") == "steelbeam")["self_cost"]
    assert sb["kind"] == "half_hp" and sb["faints"] and not sb["requires_switch"] and sb["hp_loss_frac"] == 0.4, sb
    de = next(a for a in adv["actions"] if a.get("id") == "doubleedge")
    assert de["self_cost"]["kind"] == "recoil" and 0 < de["self_cost"]["hp_loss_frac"] < 0.4, de
    assert "反動 −" in de["reason"], de["reason"]
    assert _ranking(adv) == _ranking(base)
    print("test_engine_self_cost_half_hp_and_recoil OK")


# ==============================================================================
# M2b 段 1: 分岐つきの解決 (resolve_turn_branches / move_outcomes) と耐える効果 (2026-10-09)
# 根拠 (ローカルの Showdown): sim/battle.ts (同速は乱数)、data/mods/champions/conditions.ts par (1/8 で行動不能)、
# data/items.ts focussash・data/abilities.ts sturdy / disguise (onDamage)、data/moves.ts highjumpkick (onMoveFail: baseMaxhp / 2)・
# steelbeam (onMoveFail でも Math.round(maxhp / 2))、sim/battle.ts spreadDamage (clampIntRange = 切り捨て・最低 1)
# ==============================================================================
def _br_sum(brs):
    return sum(p for p, _r in brs)


def _p_where(brs, pred):
    return sum(p for p, r in brs if pred(r))


def _has(r, who, kind):
    return any(w == who and k == kind for w, k, _d in r.events)


def test_branches_speed_tie_and_purity():
    """同速 (同じ個体・同じ配分): 両方の順が 0.5 ずつ。確率の合計は 1。入力の状態は書き換えない"""
    me = SimSide(_view("garchomp"), 1.0, bench=[(_view("clefable"), 1.0)])
    opp = SimSide(_view("garchomp"), 1.0)
    snap = (me.active_hp, list(me.bench), opp.active_hp, dict(me.active.boosts))
    brs = T.resolve_turn_branches(me, opp, _mv("dragonclaw"), _mv("dragonclaw"), None, None)
    assert abs(_br_sum(brs) - 1.0) < 1e-9
    p_me_first = _p_where(brs, lambda r: next(w for w, k, _d in r.events if k == "move") == "me")
    assert abs(p_me_first - 0.5) < 1e-9, p_me_first
    assert (me.active_hp, list(me.bench), opp.active_hp, dict(me.active.boosts)) == snap
    # 素早さが違えば分岐しない (旧 resolve_turn の「同速は自分が先」は残したまま)
    fast = SimSide(_view("garchomp", ev={"atk": 252, "spa": 252, "spe": 252}), 1.0)
    slow = SimSide(_view("garchomp", ev={"atk": 252, "spa": 252, "spe": 0}), 1.0)
    brs2 = T.resolve_turn_branches(fast, slow, _mv("dragonclaw"), _mv("dragonclaw"), None, None)
    assert all(next(w for w, k, _d in r.events if k == "move") == "me" for _p, r in brs2)
    print("test_branches_speed_tie_and_purity OK")


def test_branches_paralysis_full_para():
    """まひ: 1/8 で行動しない分岐 (チャンピオンズ)。行動しなかった分岐では与ダメージ 0"""
    me = SimSide(replace(_view("garchomp"), status="paralysis"), 1.0)
    opp = SimSide(_view("clefable"), 1.0)
    brs = T.resolve_turn_branches(me, opp, _mv("earthquake"), _mv("calmmind"), None, None)
    p_skip = _p_where(brs, lambda r: any(w == "me" and k == "skip" and d == "paralysis" for w, k, d in r.events))
    assert abs(p_skip - 1 / 8) < 1e-9, p_skip
    assert all(r.opp.active_hp == 1.0 for p, r in brs if not r.acted["me"])
    assert abs(_br_sum(brs) - 1.0) < 1e-9
    print("test_branches_paralysis_full_para OK")


def test_branches_miss_crash_and_steelbeam():
    """命中: とびひざげり (90) は 0.1 で外れ、外れた分岐は与ダメージ 0 で使用者が最大 HP の 1/2 (切り捨て) を失う。
    てっていこうせん (95) は外れても HP の 1/2 (四捨五入) を失う。当たった分岐で反動 crash は無い"""
    user_v, foe_v = _view("lopunny"), _view("garchomp")
    outs = T.move_outcomes(SimSide(user_v, 1.0), SimSide(foe_v, 1.0), "highjumpkick", None)
    miss = [(p, r) for p, r in outs if any(k == "miss" for _w, k, _d in r.events)]
    assert len(miss) == 1 and abs(miss[0][0] - 0.1) < 1e-9, outs
    mr = miss[0][1]
    mhp = user_v.max_hp()
    assert mr.dealt == 0.0 and mr.target.active_hp == 1.0
    assert abs(mr.user.active_hp - (1.0 - (mhp // 2) / mhp)) < 1e-9, mr.user.active_hp
    assert all(not any(k == "crash" for _w, k, _d in r.events) and r.user.active_hp == 1.0
               for p, r in outs if r.dealt > 0)
    assert abs(sum(p for p, _r in outs) - 1.0) < 1e-9
    arch = _view("archaludon")
    outs2 = T.move_outcomes(SimSide(arch, 1.0), SimSide(_view("garchomp"), 1.0), "steelbeam", None)
    miss2 = [r for p, r in outs2 if any(k == "miss" for _w, k, _d in r.events)]
    assert len(miss2) == 1 and miss2[0].dealt == 0.0
    assert abs(miss2[0].user.active_hp - (1.0 - _half(arch))) < 1e-9          # 外れても失う
    assert abs(sum(p for p, r in outs2 if r.dealt == 0.0) - 0.05) < 1e-9
    print("test_branches_miss_crash_and_steelbeam OK")


def test_focus_sash_and_sturdy():
    """きあいのタスキ: HP 満タンからの一撃は HP 1 で耐えて消費。連続技は 2 発目で倒れる。がんじょうは満タンのときだけ"""
    foe = _view("heatran", item="focussash")
    outs = T.move_outcomes(SimSide(_view("garchomp"), 1.0), SimSide(foe, 1.0), "earthquake", None)
    one = 1.0 / foe.max_hp()
    for p, r in outs:
        assert abs(r.target.active_hp - one) < 1e-9 and r.target.active.item is None, (r.target.active_hp, r.events)
        assert ("target", "survive", "focussash") in r.events and not r.faint_order
    # 連続技 (ロックブラスト 2〜5 回、攻撃 +6 で 1 発が最大 HP を超える): 1 発目は HP 1 で耐え、2 発目で倒れる
    strong = replace(_view("garchomp"), boosts={"atk": 6})
    rb = T.move_outcomes(SimSide(strong, 1.0), SimSide(replace(_view("charizard"), item="focussash"), 1.0),
                         "rockblast", None)
    assert any(r.dealt > 0 for _p, r in rb)
    for p, r in rb:
        if r.dealt > 0:
            assert ("target", "survive", "focussash") in r.events and r.target.active_hp == 0.0, r.events
            assert "target" in r.faint_order and ("target", "hits", 2) in r.events, r.events
    # 1 発では倒れない量 (攻撃 ±0、1 発 約 52%) ならタスキは発動せず、2 発の合計で倒れる
    rb0 = T.move_outcomes(SimSide(_view("garchomp"), 1.0), SimSide(replace(_view("charizard"), item="focussash"), 1.0),
                          "rockblast", None)
    assert all(not any(k == "survive" for _w, k, _d in r.events) for _p, r in rb0)
    # がんじょう: 満タンなら耐える、HP 99% なら倒れる
    st = _view("aggron", ability="sturdy")
    full = T.move_outcomes(SimSide(_view("garchomp"), 1.0), SimSide(st, 1.0), "earthquake", None)
    assert all(abs(r.target.active_hp - 1.0 / st.max_hp()) < 1e-9 for p, r in full)
    hurt = T.move_outcomes(SimSide(_view("garchomp"), 1.0), SimSide(st, 0.99), "earthquake", None)
    assert all(r.target.active_hp == 0.0 for p, r in hurt)
    # かたやぶりは がんじょう を無視する (タスキは無視しない)
    mb = T.move_outcomes(SimSide(_view("garchomp", ability="moldbreaker"), 1.0), SimSide(st, 1.0), "earthquake", None)
    assert all(r.target.active_hp == 0.0 for p, r in mb)
    print("test_focus_sash_and_sturdy OK")


def test_disguise():
    """ばけのかわ: 初撃は 0 で ばけたすがた になり、最大 HP の 1/8 (切り捨て) を失う。連続技の 2 発目以降は通る"""
    mim = _view("mimikyu", ability="disguise")
    loss = (mim.max_hp() // 8) / mim.max_hp()
    outs = T.move_outcomes(SimSide(_view("garchomp"), 1.0), SimSide(mim, 1.0), "earthquake", None)
    for p, r in outs:
        assert r.dealt == 0.0 and r.target.active.species_id == "mimikyubusted"
        assert abs(r.target.active_hp - (1.0 - loss)) < 1e-9, r.target.active_hp
    # 2 発目以降 (ロックブラスト) はダメージが入る
    rb = T.move_outcomes(SimSide(_view("garchomp"), 1.0), SimSide(mim, 1.0), "rockblast", None)
    assert all(r.dealt > 0 for p, r in rb if any(k == "disguise" for _w, k, _d in r.events))
    # かたやぶりは無視する
    mb = T.move_outcomes(SimSide(_view("garchomp", ability="moldbreaker"), 1.0), SimSide(mim, 1.0), "earthquake", None)
    assert all(r.dealt > 0 and r.target.active.species_id == "mimikyu" for p, r in mb)
    print("test_disguise OK")


def test_branches_fainted_before_acting():
    """(c) 先に倒された分岐では、攻撃も HP の消費も発生しない: 遅い ペロリーム (HP 5%) の ミストバースト・
    遅い ブリジュラス (HP 10%) の てっていこうせん は、先に倒されたら自滅も HP の消費も無く、相手は無傷"""
    me = SimSide(_view("slurpuff"), 0.05, bench=[(_view("lopunny"), 1.0)])
    opp = SimSide(_view("ninetales"), 1.0)
    brs = T.resolve_turn_branches(me, opp, _mv("mistyexplosion"), _mv("flamethrower"), None, None)
    assert abs(_br_sum(brs) - 1.0) < 1e-9
    for p, r in brs:
        assert not _has(r, "me", "selfko") and r.opp.active_hp == 1.0, r.events
        assert ("me", "skip", "fainted_before_acting") in r.events and r.acted["me"] is False
        assert r.me.active.species_id == "lopunny" and r.requires_switch["me"]
    me2 = SimSide(_view("archaludon"), 0.10, bench=[(_view("lopunny"), 1.0)])
    brs2 = T.resolve_turn_branches(me2, SimSide(_view("garchomp"), 1.0), _mv("steelbeam"), _mv("earthquake"), None, None)
    for p, r in brs2:
        assert not _has(r, "me", "hp_cost") and r.opp.active_hp == 1.0, r.events
    print("test_branches_fainted_before_acting OK")


def test_branches_pivot_and_fakeout():
    """交代技: 先に動いた とんぼがえり が当たったら、その場で控え (best_bench) を出し、後から動く相手の技はその個体が受ける。
    ねこだまし (ひるみ 100%): 先に当たれば相手は行動しない"""
    me = SimSide(_view("weavile"), 1.0, bench=[(_view("corviknight"), 1.0)])
    opp = SimSide(_view("snorlax"), 1.0)
    brs = T.resolve_turn_branches(me, opp, _mv("uturn"), _mv("bodyslam"), None, None)
    for p, r in brs:
        assert r.me.active.species_id == "corviknight" and r.me.active_hp < 1.0, (r.me.active.species_id, r.events)
        assert dict((v.species_id, hp) for v, hp in r.me.bench)["weavile"] == 1.0
    fo = T.resolve_turn_branches(SimSide(_view("weavile"), 1.0), SimSide(_view("snorlax"), 1.0),
                                 _mv("fakeout"), _mv("bodyslam"), None, None)
    for p, r in fo:
        assert r.me.active_hp == 1.0 and ("opp", "skip", "flinch") in r.events, r.events
    print("test_branches_pivot_and_fakeout OK")


def test_full_status_effects():
    """段 C の前倒し (1 ターンの状態変化): ひかりのかべ (陣営の screens、以降の特殊技が半減)、ねばねばネット (着地時 素早さ -1)、
    あくび (ねむけ。即ねむりにしない)、スイートベールはねむけを防ぐ"""
    me = SimSide(_view("slurpuff"), 1.0, screens={})
    opp = SimSide(_view("ninetales"), 1.0, screens={}, bench=[(_view("garchomp"), 1.0)])
    r = T.resolve_move(me, opp, "lightscreen", None, full=True)
    assert r.user.screens == {"light_screen": True} and r.executed
    f = T.field_against(None, r.user)
    assert f.light_screen and not f.reflect
    r2 = T.resolve_move(me, opp, "stickyweb", None, full=True)
    sw = T.apply_switch(r2.target, 0)
    assert r2.target.sticky_web and sw.active.boosts.get("spe") == -1
    r3 = T.resolve_move(me, opp, "yawn", None, full=True)
    assert "yawn" in r3.target.volatiles and r3.target.active.status is None
    r4 = T.resolve_move(SimSide(_view("ninetales"), 1.0), SimSide(_view("slurpuff", ability="sweetveil"), 1.0),
                        "yawn", None, full=True)
    assert "yawn" not in r4.target.volatiles
    # 旧 resolve_move (full=False) の あくび は従来どおり即ねむり (探索の結果を変えない)
    assert T.resolve_move(me, opp, "yawn", None).target.active.status == "sleep"
    print("test_full_status_effects OK")


if __name__ == "__main__":
    test_selfko_kos_target_and_user()
    test_selfko_even_if_immune_or_protected()
    test_ko_before_acting_no_selfko()
    test_no_target_after_foe_selfko()
    test_half_hp_cost()
    test_recoil()
    test_multihit_unchanged()
    test_unsupported_marks_and_purity()
    test_engine_self_cost_112839()
    test_engine_self_cost_before_ko_risk()
    test_engine_self_cost_half_hp_and_recoil()
    test_branches_speed_tie_and_purity()
    test_branches_paralysis_full_para()
    test_branches_miss_crash_and_steelbeam()
    test_focus_sash_and_sturdy()
    test_disguise()
    test_branches_fainted_before_acting()
    test_branches_pivot_and_fakeout()
    test_full_status_effects()
    print("ALL OK")
