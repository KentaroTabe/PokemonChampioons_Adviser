"""へんしん (メタモン・かわりもの) の入力修正のテスト (2026-10-21 レビュー、advisor/transform.py、計画書 §8.43)。

- 観測根拠 (発動のイベント ability_opponent_imposter / 写した後の技の使用) があれば transform が付き、両方式 (legacy・transition) の
  入力が写しになる (技は登録・判明の技 PP 5、特性・タイプ・種族値・能力ランクは写し、HP・持ち物・状態異常は相手のまま)
- 鍵が変わる・版 2 の記録と復元で往復する・交代で消える・advice に transform_applied (True / False / None)
- 根拠が無い・コピー元が 1 つに決まらない → 付かない (transition は従来どおり へんしん で退避)
- 近似を持ち込んでいない: 最大 HP は相手の種族の HP の種族値、実数値はこちらの登録の型 (無ければ自分の個体と同じ既定)、
  推定の持ち物を観測扱いしない (状態の item_id は空のまま、こだわりの技の固定もかからない)

使用率 DB・登録ファイル (config/my_team.json)・RL のモデルには依存しない (登録は合成、使用率の推定と RL は差し替え)。

    python -m tests.test_transform_input
"""
from __future__ import annotations

import copy
import os

os.environ.setdefault("RL_BLEND_WEIGHT", "0")

import advice_key as K
import advisor.engine as ENG
import advisor.rl_bridge as RB
from advisor import scoring_transition as ST
from advisor import transform as TF
from advisor.dex import get_dex
from advisor.engine import OFFENSIVE_EV, evaluate
from battle_logger import compact_state
from champions_agent import config as C
from tests.test_advice_m1a import _Patch
from tools.advice_replay import compact_to_engine_state
from vision.normalize import NameResolver
from vision.state import BattleStateV2, MoveSlot, PokemonState

resolver = NameResolver()
T0 = 1_800_000_000.0

ARCH_MOVES = [("てっていこうせん", "steelbeam", 5), ("10まんボルト", "thunderbolt", 15)]   # 画面で読めた 2 つ
ARCH_REG = ["てっていこうせん", "10まんボルト", "あくのはどう", "ステルスロック"]          # 登録の技 (残り 2 つを補う)
ARCH_BUILD = {"ev": {"hp": 16, "spa": 252, "spe": 252}, "nature": {"spa": 1.1, "atk": 0.9},
              "item_ja": "レッドカード", "ability_ja": "がんじょう"}
BUILDS = {"ブリジュラス": ARCH_BUILD}
MOVES = {"ブリジュラス": ARCH_REG}


def _mon(sid, ja, types, moves=(), hp=100.0, **kw):
    d = {"species_id": sid, "species_ja": ja, "types": list(types), "hp_percent": hp, "hp_current": None, "hp_max": None,
         "status": None, "boosts": {}, "ability_id": None, "item_id": None,
         "moves": [{"name_ja": a, "move_id": b, "pp": pp, "max_pp": None, "effectiveness": None} for a, b, pp in moves],
         "revealed_moves": [], "is_picked": True}
    d.update(kw)
    return d


def _state(my, opp, opp_rem=3):
    def side(party, rem):
        return {"active_index": 0, "remaining": rem, "tailwind": False,
                "hazards": {"stealth_rock": False, "spikes": 0, "toxic_spikes": 0, "sticky_web": False},
                "screens": {"reflect": False, "light_screen": False, "aurora_veil": False}, "party": party}
    return {"field": {"weather": None, "terrain": None, "trick_room": False}, "turn": 3,
            "mega_used": {"player": False, "opponent": False}, "ev_infer_override": {}, "now": T0,
            "player": side(my, 3), "opponent": side(opp, opp_rem)}


def _arch(**kw):
    kw = dict({"hp": 60.0, "ability_id": "sturdy", "item_id": "redcard"}, **kw)
    return _mon("archaludon", "ブリジュラス", ["はがね", "ドラゴン"], ARCH_MOVES, **kw)


def _ditto(**kw):
    return _mon("ditto", "メタモン", ["ノーマル"], hp=70.0, is_picked=False, **kw)


class _Env:
    """登録 (_Patch)・RL の方策・使用率の推定を合成に差し替える。相手の技の候補は、へんしん の入力 (transform_view) なら本物の
    engine.opponent_move_pool、そうでなければ使用率の代わりに pool_untransformed (メタモンの使用率 = へんしん だけ)"""

    def __init__(self, pool_untransformed=(("transform", 100.0),), items=(("choicescarf", 88.8),)):
        self.pool = list(pool_untransformed)
        self.items = list(items)
        self.calls = []

    def __enter__(self):
        self.patch = _Patch(BUILDS, MOVES).__enter__()
        self.orig = (RB.policy_hint, ENG.opponent_move_pool, ENG.get_predictor)
        env = self

        class _Pred:
            def predict(self, sid):
                env.calls.append(sid)
                return {"moves": [], "items": list(env.items), "abilities": [("imposter", 99.5)]}

        RB.policy_hint = lambda *a, **k: None
        ENG.get_predictor = lambda: _Pred()
        orig_pool = self.orig[1]
        ENG.opponent_move_pool = lambda p, v, r: orig_pool(p, v, r) if p.get("transform_view") else list(env.pool)
        return self

    def __exit__(self, *a):
        RB.policy_hint, ENG.opponent_move_pool, ENG.get_predictor = self.orig
        self.patch.__exit__(*a)


def _attach(opp_extra=None):
    """追跡で transform を付けた状態 (技の根拠: 対面が続いたまま あくのはどう を使った)"""
    my, opp = _arch(), _ditto(**(opp_extra or {}))
    fn = TF._default_copy_fn(resolver)
    tr, att = TF.tracker_step(None, 0, opp, 0, my, [], T0, fn)
    assert att is None and tr["pair"]["start_ok"] and tr["pair"]["stable"], tr
    tr, att = TF.tracker_step(tr, 0, opp, 0, my, ["move_opponent_darkpulse"], T0 + 5, fn)
    assert att is not None, tr
    st = _state([my], [opp])
    st["opponent"]["party"][0]["transform"] = att["transform"]
    st["opponent"]["party"][0]["boosts"] = att["boosts"]
    return st, att


def _capture(state, mode):
    cap = {}
    orig = ST.score

    def spy(inp, budget_ms=None):
        cap["inp"] = inp
        return orig(inp, budget_ms)

    ST.score = spy
    try:
        adv = evaluate(copy.deepcopy(state), resolver, rl_blend_weight=0, scoring_mode=mode)
    finally:
        ST.score = orig
    return adv, cap.get("inp")


# ==============================================================================
# 根拠あり: 付く・写しの中身
# ==============================================================================
def test_move_evidence_attaches_copy():
    """写した後の技の使用 (コピー元の登録の技) → transform が付く。技は判明技 + 登録の技 (PP 5)、特性・種族値・型は写し"""
    with _Env():
        st, att = _attach()
    tf = att["transform"]
    assert tf["source_species"] == "archaludon" and tf["source_index"] == 0 and tf["evidence"] == "event:move_opponent_darkpulse"
    cp = tf["copy"]
    assert [m["move_id"] for m in cp["moves"]] == ["steelbeam", "thunderbolt", "darkpulse", "stealthrock"], cp["moves"]
    assert all(m["pp"] == C.TRANSFORM_COPY_PP == 5 for m in cp["moves"])
    assert cp["species"] == "archaludon" and cp["ability"] == "sturdy" and set(cp["types"]) == {"はがね", "ドラゴン"}
    assert cp["base_stats"] == dict(get_dex().species("archaludon")["baseStats"])
    assert cp["ev"] == ARCH_BUILD["ev"] and cp["nature"] == ARCH_BUILD["nature"] and cp["boosts"] == {}
    # 状態の相手の個体は相手のまま (種族・HP・持ち物は transform の外)
    o = st["opponent"]["party"][0]
    assert o["species_id"] == "ditto" and o["hp_percent"] == 70.0 and o["item_id"] is None
    print("test_move_evidence_attaches_copy OK")


def test_event_evidence_and_boosts():
    """発動のイベント (かわりもの) → 付く。能力ランクは へんしん した時点のこちらの値 (+ その後の相手の変化)。
    イベントが相手の場の個体の更新より先に来ても TRANSFORM_EVENT_WINDOW_SEC 内なら付く"""
    my = _arch(boosts={"atk": 1, "spe": 1})
    fn = TF._default_copy_fn(resolver)
    with _Env():
        # 同じフレーム: 交代 + かわりもの
        tr, att = TF.tracker_step(None, 0, _ditto(), 0, my, ["switch_opponent", "ability_opponent_imposter"], T0, fn)
        assert att and att["transform"]["evidence"] == "event:ability_opponent_imposter"
        assert att["transform"]["copy"]["boosts"] == {"atk": 1, "spe": 1} and att["boosts"] == {"atk": 1, "spe": 1}
        # イベントが先 (相手の場の個体はまだ前の個体) → 次のフレームで付く
        prev = _mon("sneasler", "オオニューラ", ["かくとう", "どく"], hp=0.0, status="fainted")
        tr, att = TF.tracker_step(None, 1, prev, 0, my, ["ability_opponent_imposter"], T0, fn)
        assert att is None and tr["pending_event"]
        tr, att = TF.tracker_step(tr, 0, _ditto(), 0, my, [], T0 + 3, fn)
        assert att and att["transform"]["evidence"] == "event:ability_opponent_imposter"
        # 期限切れ (TRANSFORM_EVENT_WINDOW_SEC 超) なら付かない
        tr, att = TF.tracker_step(None, 1, prev, 0, my, ["ability_opponent_imposter"], T0, fn)
        tr, att = TF.tracker_step(tr, 0, _ditto(), 0, my, [], T0 + C.TRANSFORM_EVENT_WINDOW_SEC + 1, fn)
        assert att is None
    # 能力ランク: 写し + (今の相手 − 対面の始まりの相手)
    assert TF.merge_boosts({"atk": 1, "spe": 1}, {"def": -1}, {}) == {"atk": 1, "spe": 1, "def": -1}
    assert TF.merge_boosts({"atk": 6}, {"atk": 2}, {}) == {"atk": 6}
    print("test_event_evidence_and_boosts OK")


# ==============================================================================
# 両方式の入力が写しになる (近似なし)
# ==============================================================================
def test_both_modes_use_copy():
    with _Env() as env:
        st, _att = _attach()
        leg = evaluate(copy.deepcopy(st), resolver, rl_blend_weight=0, scoring_mode="legacy")
        adv, inp = _capture(st, "transition")
    # legacy: 相手の技の脅威は写しの技だけ (へんしん は入らない)、注記
    assert leg["ok"], leg.get("reason")
    assert leg["threats"] and {t["move_id"] for t in leg["threats"]} <= {"steelbeam", "thunderbolt", "darkpulse"}, leg["threats"]
    assert all(t["revealed"] for t in leg["threats"]) and "へんしん 中" in leg["opp_inference"]
    # transition: 退避しない。入力の相手は写し
    assert adv["scoring_used"] == "transition", adv.get("scoring_fallback")
    assert adv["scoring_fallback"] is None
    o = inp.opp.active
    assert o.species_id == "archaludon" and set(o.types) == {"Steel", "Dragon"} and o.ability == "sturdy"
    assert sorted(m for m, _w in inp.pool) == ["darkpulse", "stealthrock", "steelbeam", "thunderbolt"]
    assert all(w == 100.0 for _m, w in inp.pool) and not inp.opp_estimated
    # 種族値: HP 以外は写し、HP は相手の種族 (メタモン) の値。努力値は登録の型 (HP は相手の既定の仮定)、性格も登録
    base_c, base_o = get_dex().species("archaludon")["baseStats"], get_dex().species("ditto")["baseStats"]
    assert o.base["hp"] == base_o["hp"] and {k: o.base[k] for k in ("atk", "def", "spa", "spd", "spe")} == \
        {k: base_c[k] for k in ("atk", "def", "spa", "spd", "spe")}
    assert o.ev == {"spa": 252, "spe": 252, "hp": OFFENSIVE_EV.get("hp", 0)} and o.nature == ARCH_BUILD["nature"]
    assert abs(inp.opp.active_hp - 0.70) < 1e-9
    # 持ち物: 相手の種族 (メタモン) の使用率の推定 (写した種族の推定ではない)。観測扱いにしない = こだわりの固定がかからない
    assert o.item == "choicescarf" and "ditto" in env.calls and "archaludon" not in env.calls
    assert st["opponent"]["party"][0]["item_id"] is None and len(inp.pool) == 4
    # メガシンカの見込みは無い (石は相手の持ち物ではない)
    assert not inp.opp.mega_forms
    print("test_both_modes_use_copy OK")


def test_no_registration_uses_own_default():
    """登録の型が無ければ ev / nature は None。評価は自分の個体を評価するときと同じ既定 (OFFENSIVE_EV、性格なし)。
    使用率の配分 (写した種族の推定) では代用しない"""
    with _Patch({}, MOVES):
        reg = ENG.registered_moves_of_mon(_arch(), resolver)
        cp = TF.make_copy(_arch(), reg, ENG.my_build_of(_arch()))
    assert cp["ev"] is None and cp["nature"] is None
    base, ev, nature = TF.view_stats({"hp_base": 48, "base": cp["base_stats"], "ev": None, "nature": None}, dict(OFFENSIVE_EV))
    assert ev == dict(OFFENSIVE_EV, hp=0) and nature == {} and base["hp"] == 48
    # 技が 1 つも分からなければ写しを作らない (付けない)
    assert TF.make_copy(_mon("archaludon", "ブリジュラス", ["はがね", "ドラゴン"]), [], None) is None
    print("test_no_registration_uses_own_default OK")


# ==============================================================================
# 根拠なし・コピー元が決まらない → 付かない (transition は従来どおり退避)
# ==============================================================================
def test_no_evidence_keeps_fallback():
    my, opp = _arch(), _ditto()
    fn = TF._default_copy_fn(resolver)
    with _Env():
        tr, att = TF.tracker_step(None, 0, opp, 0, my, [], T0, fn)
        tr, att = TF.tracker_step(tr, 0, opp, 0, my, ["super_effective"], T0 + 2, fn)
        assert att is None
        st = _state([my], [opp])
        adv = evaluate(copy.deepcopy(st), resolver, rl_blend_weight=0, scoring_mode="transition")
    assert adv["scoring_used"] == "legacy", adv.get("scoring_used")
    assert TF.transform_applied_of(st) is False
    assert adv["scoring_fallback"] == "opp_unsupported:effect_unmodeled:transform", adv["scoring_fallback"]
    print("test_no_evidence_keeps_fallback OK")


def test_ambiguous_source_not_attached():
    fn = TF._default_copy_fn(resolver)
    kg = _mon("kingambit", "ドドゲザン", ["あく", "はがね"], [("ふいうち", "suckerpunch", 5), ("あくのはどう", "darkpulse", 10)])
    with _Env():
        # こちらが交代した後の技 (コピー元が 1 つに決まらない)
        tr, _ = TF.tracker_step(None, 0, _ditto(), 0, _arch(), [], T0, fn)
        tr, att = TF.tracker_step(tr, 0, _ditto(), 1, kg, ["move_opponent_darkpulse"], T0 + 5, fn)
        assert att is None and tr["pair"]["stable"] is False
        # こちらの能力ランクが変わった後の技
        tr, _ = TF.tracker_step(None, 0, _ditto(), 0, _arch(), [], T0, fn)
        tr, att = TF.tracker_step(tr, 0, _ditto(), 0, _arch(boosts={"spa": 1}), ["move_opponent_darkpulse"], T0 + 5, fn)
        assert att is None
        # コピー元の技に無い技 / へんしん そのもの / 対面が始まったフレームの技 (前の個体の技の取り違え) / 交代と同じフレーム
        for fired, dt in ((["move_opponent_flamethrower"], 5), (["move_opponent_transform"], 5),
                          (["move_opponent_darkpulse"], 0), (["switch_opponent", "move_opponent_darkpulse"], 5)):
            tr, _ = TF.tracker_step(None, 0, _ditto(), 0, _arch(), [], T0, fn)
            tr, att = TF.tracker_step(tr, 0, _ditto(), 0, _arch(), fired, T0 + dt, fn)
            assert att is None, fired
        # 対面の始まりにこちらの場の個体が ひんし (かわりもの の相手が決まらない)
        tr, att = TF.tracker_step(None, 0, _ditto(), 0, _arch(hp=0.0, status="fainted"),
                                  ["ability_opponent_imposter"], T0, fn)
        assert att is None and not tr["pair"]["start_ok"]
        # 対象の種でない相手 (技の根拠は TRANSFORM_SPECIES だけ)
        tr, _ = TF.tracker_step(None, 0, kg, 0, _arch(), [], T0, fn)
        tr, att = TF.tracker_step(tr, 0, kg, 0, _arch(), ["move_opponent_darkpulse"], T0 + 5, fn)
        assert att is None and tr["pair"] is None
    print("test_ambiguous_source_not_attached OK")


# ==============================================================================
# 鍵・記録と復元・交代・advice の欄
# ==============================================================================
def test_key_changes():
    with _Env():
        st, att = _attach()
    plain = copy.deepcopy(st)
    plain["opponent"]["party"][0].pop("transform")
    plain["opponent"]["party"][0]["boosts"] = att["boosts"]
    s, v = {"scoring_mode": "legacy"}, {"team": "a"}
    k1, k0 = K.advice_input_key(st, T0, s, v), K.advice_input_key(plain, T0, s, v)
    assert k1 and k0 and k1 != k0
    other = copy.deepcopy(st)
    other["opponent"]["party"][0]["transform"]["source_index"] = 3
    other["opponent"]["party"][0]["transform"]["source_species"] = "kingambit"
    assert K.advice_input_key(other, T0, s, v) != k1
    # 観測根拠・時刻は採点に使わないので鍵に入らない
    ev = copy.deepcopy(st)
    ev["opponent"]["party"][0]["transform"].update(evidence="event:ability_opponent_imposter", t=T0 + 99)
    assert K.advice_input_key(ev, T0, s, v) == k1
    print("test_key_changes OK")


def test_roundtrip_and_switch_clears():
    """BattleStateV2 で付ける (observe_frame) → to_dict → 版 2 の記録 (compact_state) → 復元で transform が戻る。
    付けた時点で相手の能力ランクは写しの値。交代で消える。transform の無い個体の記録は null、復元しても欄なし"""
    s = BattleStateV2()
    s.scene, s.turn, s.battle_active = "command", 3, True
    me = PokemonState(species_ja="ブリジュラス", species_id="archaludon", types=["はがね", "ドラゴン"], hp_percent=60.0,
                      ability_id="sturdy", item_id="redcard", is_active=True, is_picked=True,
                      moves=[MoveSlot(name_ja=a, move_id=b, pp=pp) for a, b, pp in ARCH_MOVES])
    me.boosts["spe"] = 1
    op = PokemonState(species_ja="メタモン", species_id="ditto", types=["ノーマル"], hp_percent=70.0, is_active=True)
    other = PokemonState(species_ja="オオニューラ", species_id="sneasler", types=["かくとう", "どく"])
    s.player.party, s.player.active_index = [me], 0
    s.opponent.party, s.opponent.active_index = [op, other], 0
    with _Env():
        assert TF.observe_frame(s, [], T0, resolver) is None
        tf = TF.observe_frame(s, ["ability_opponent_imposter"], T0 + 1, resolver)
    assert tf and op.transform is tf and op.boosts["spe"] == 1 and op.species_id == "ditto"
    # 注記は event_id なし (battle_logger の events 行の texts と発火の対応をずらさない)
    notes = [e for e in s.events if "へんしん 中" in (e.get("text") or "")]
    assert len(notes) == 1 and not notes[0].get("event"), s.events
    d = s.to_dict()
    row = compact_state(d)
    assert row["opponent"]["party"][0]["transform"] == tf and row["opponent"]["party"][1]["transform"] is None
    back = compact_to_engine_state(row, resolver)
    assert back["opponent"]["party"][0]["transform"] == tf and "transform" not in back["opponent"]["party"][1]
    assert back["opponent"]["party"][0]["boosts"].get("spe") == 1
    # サーバー再起動の復元 (restore_from_dict) も戻す
    s2 = BattleStateV2()
    s2.restore_from_dict(d)
    assert s2.opponent.party[0].transform == tf
    # 交代で消える (追跡も対面が切れる)
    s.opponent.switch_to(1)
    assert op.transform is None
    with _Env():
        TF.observe_frame(s, [], T0 + 2, resolver)
    assert s.transform_tracker["pair"] is None
    print("test_roundtrip_and_switch_clears OK")


def test_transform_applied_field():
    """advice の欄 transform_applied: Advisor.advise とキャッシュの経路 (advice_key.advise_cached、server・録画再生) の両方で付く。
    True = 写しで評価 / False = 相手の場の個体に transform なし / None = 相手の場の個体が分からない。
    evaluate の戻り値の鍵は変えない (共通部分の内部の欄 transform_applied と同じ値)"""
    import advisor.service as SV
    with _Env():
        st, _ = _attach()
        plain = _state([_arch()], [_mon("sneasler", "オオニューラ", ["かくとう", "どく"], is_picked=False,
                                        revealed_moves=["インファイト"])])
        none = copy.deepcopy(plain)
        none["opponent"]["active_index"] = None
        got = {}
        for name, s0 in (("true", st), ("false", plain), ("none", none)):
            a = SV.Advisor(resolver).advise(copy.deepcopy(s0), rl_blend_weight=0, scoring_mode="transition")
            b, _ev = K.advise_cached(SV.Advisor(resolver), copy.deepcopy(s0), K.AdviceCache(), f"k-{name}", T0)
            common = ENG.evaluate_common(copy.deepcopy(s0), resolver, scoring_mode="legacy")
            assert a.get("ok") and b.get("ok"), (name, a.get("reason"))
            assert a["transform_applied"] == b["transform_applied"] == common["transform_applied"], name
            assert "transform_applied" not in evaluate(copy.deepcopy(s0), resolver, rl_blend_weight=0)
            got[name] = a["transform_applied"]
    assert got == {"true": True, "false": False, "none": None}, got
    # 評価の入力は元の state を書き換えない (eval_state は写し)
    st2, applied = TF.eval_state(st)
    assert applied is True and st2 is not st and st["opponent"]["party"][0]["species_id"] == "ditto"
    assert st2["opponent"]["party"][0]["species_id"] == "archaludon"
    assert "spe_est" not in TF.eval_view(dict(st["opponent"]["party"][0], spe_est=123.0))
    print("test_transform_applied_field OK")


if __name__ == "__main__":
    test_move_evidence_attaches_copy()
    test_event_evidence_and_boosts()
    test_both_modes_use_copy()
    test_no_registration_uses_own_default()
    test_no_evidence_keeps_fallback()
    test_ambiguous_source_not_attached()
    test_key_changes()
    test_roundtrip_and_switch_clears()
    test_transform_applied_field()
    print("ALL OK")
