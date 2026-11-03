"""transition 方式の採点 (advisor/scoring_transition.py) と engine・service への接続のテスト (2026-10-09 M2b 段 3・段 4、
docs/ADVICE_ACCURACY_PLAN_1009.md §8.4 のレビュー 5 の受入)。

実ログ (battle_20261009_113830.jsonl:171 / battle_20261009_112839.jsonl:39) の局面は、ログの簡約状態の値をここに写して合成する
(ログはコミットしない)。CI には使用率 DB が無いので、相手の技は判明技として与える (113830:171 は当時の判明技 マッハパンチ に、
使用率の推定の上位 でんこうそうげき を判明技として足した)。登録チーム (config/my_team.json) は読まない。RL のモデルは使わない
(rl_bridge.policy_hint を固定の分布に差し替える)。

    python -m tests.test_scoring_transition
"""
from __future__ import annotations

import copy
import os
import subprocess
import sys

os.environ.setdefault("RL_BLEND_WEIGHT", "0")

import advisor.engine as ENG
import advisor.rl_bridge as RB
from advisor import scoring_transition as ST
from advisor import valuation as VA
from advisor.engine import evaluate, evaluate_common, finish_evaluation
from advisor.transition import resolve_turn_branches
from champions_agent import config as C
from vision.normalize import NameResolver

resolver = NameResolver()
_ORIG_HINT = RB.policy_hint


_ORIG_POOL = ENG.opponent_move_pool


def _revealed_only_pool(opp_p, opp_view, resolver):
    """相手の技プールを判明技 (revealed_moves) だけ・重み 100 に固定する。本体には使用率 DB (champions.sqlite3) があり、
    推定技 (あくのはどう のひるみ等) が応手に入って分岐の確率が環境で変わるため (2026-10-09: 本体の CI で p_faint が 0.9963)。
    テストの局面は判明技だけで応手が決まる前提で書いてある"""
    out = []
    for ja in (opp_p or {}).get("revealed_moves") or []:
        r = resolver.resolve(ja, "moves", cutoff=0.7) if resolver is not None else None
        if r and r[1] not in {m for m, _w in out}:
            out.append((r[1], 100.0))
    return out


class _Hint:
    """rl_bridge.policy_hint を固定の分布に差し替え、相手の技プールを判明技だけに固定する (テストの間だけ。使用率 DB に依存しない)"""

    def __init__(self, top=None):
        self.top = top

    def __enter__(self):
        top = self.top
        RB.policy_hint = (lambda *a, **k: {"top": top, "style": "test", "value": 0.0}) if top else (lambda *a, **k: None)
        ENG.opponent_move_pool = _revealed_only_pool
        return self

    def __exit__(self, *a):
        RB.policy_hint = _ORIG_HINT
        ENG.opponent_move_pool = _ORIG_POOL


def _mon(sid, ja, types, moves=(), hp=100.0, picked=True, **kw):
    d = {"species_id": sid, "species_ja": ja, "types": list(types), "hp_percent": hp, "hp_current": None,
         "hp_max": None, "status": None, "boosts": {}, "ability_id": None, "item_id": None,
         "moves": [{"name_ja": a, "move_id": b, "pp": 5, "max_pp": None, "effectiveness": None} for a, b in moves],
         "revealed_moves": [], "is_picked": picked}
    d.update(kw)
    return d


def _state(my, opp, my_rem=None, opp_rem=None, turn=5, mega_used=(False, False)):
    def side(party, rem):
        return {"active_index": 0, "remaining": rem, "tailwind": False,
                "hazards": {"stealth_rock": False, "spikes": 0, "toxic_spikes": 0, "sticky_web": False},
                "screens": {"reflect": False, "light_screen": False, "aurora_veil": False}, "party": party}
    return {"field": {"weather": None, "terrain": None, "trick_room": False}, "turn": turn,
            "mega_used": {"player": mega_used[0], "opponent": mega_used[1]},
            "player": side(my, my_rem), "opponent": side(opp, opp_rem)}


# battle_20261009_113830.jsonl 171 行目 (ターン 5): 自分 メガミミロップ 100% (控え ペロリーム 18.5% まひ、セグレイブ ひんし)、
# 相手 パーモット 100% (残り 1 体、ライチュウ ひんし)。当時の助言は「交代: ペロリーム (被ダメ 23% / 交代後の打点 約104%)」が 1 位
def _state_171():
    lop = _mon("lopunny", "ミミロップ", ["ノーマル"], [("インファイト", "closecombat"), ("つるぎのまい", "swordsdance"),
                                                     ("トリプルアクセル", "tripleaxel"), ("かみなりパンチ", "thunderpunch")],
               is_mega=True, item_id="lopunnite", ability_id="scrappy")
    slurp = _mon("slurpuff", "ペロリーム", ["フェアリー"],
                 [("ひかりのかべ", "lightscreen"), ("ミストバースト", "mistyexplosion"), ("あくび", "yawn"),
                  ("ねばねばネット", "stickyweb")], hp=18.5, status="paralysis", ability_id="sweetveil",
                 item_id="mentalherb")
    bax = _mon("baxcalibur", "セグレイブ", ["こおり", "ドラゴン"], hp=0.0, status="fainted")
    paw = _mon("pawmot", "パーモット", ["でんき", "かくとう"], picked=False, revealed_moves=["マッハパンチ", "でんこうそうげき"])
    rai = _mon("raichu", "ライチュウ", ["でんき"], hp=0.0, status="fainted", picked=False)
    return _state([lop, slurp, bax], [paw, rai], my_rem=2, opp_rem=1, mega_used=(True, True))


SLURPUFF_MOVES = (("ミストバースト", "mistyexplosion"), ("ひかりのかべ", "lightscreen"),
                  ("ねばねばネット", "stickyweb"), ("あくび", "yawn"))


def _capture_inputs(state):
    """evaluate の中で作られた TransitionInputs を取り出す"""
    cap = {}
    orig = ST.score

    def spy(inp, budget_ms=None):
        cap["inp"] = inp
        return orig(inp, budget_ms)

    ST.score = spy
    try:
        adv = evaluate(copy.deepcopy(state), resolver, rl_blend_weight=0, scoring_mode="transition")
    finally:
        ST.score = orig
    return adv, cap.get("inp")


# ==============================================================================
# (a) 着地直後に倒れる交代先に反撃利益を付けない (113830:171)
# ==============================================================================
def test_a_switch_target_that_faints_on_landing():
    st = _state_171()
    with _Hint():
        leg = evaluate(copy.deepcopy(st), resolver, rl_blend_weight=0, scoring_mode="legacy")
        adv, inp = _capture_inputs(st)
    assert adv["scoring_mode"] == "transition" and adv["scoring_used"] == "transition", adv.get("scoring_fallback")
    # legacy は交代先の反撃を架空の代表技で「交代後の打点」として足している (旧挙動の再現)
    sw_leg = next(a for a in leg["actions"] if a["kind"] == "switch")
    assert "交代後の打点" in sw_leg["reason"], sw_leg
    # transition: 第一候補は交代以外
    assert adv["best"]["kind"] != "switch", adv["actions"]
    sw = next(a for a in adv["actions"] if a["kind"] == "switch" and a["id"] == "slurpuff")
    assert sw["branches"]["p_faint"] > 0.5, sw          # 着地した ペロリーム が倒れる分岐が過半
    # 着地直後に倒れる応手 (でんこうそうげき) の分岐の期待値は、ペロリームの技の有無で変わらない (反撃利益が付かない)
    cand = next(c for c in inp.candidates if c.kind == "switch" and c.id == "slurpuff")
    resp = next(oa for _p, oa in inp.responses if oa.move_id == "doubleshock")
    brs = resolve_turn_branches(inp.me, inp.opp, cand.action, resp, inp.my_field, inp.opp_field, damage_fn=inp.damage_fn)
    assert all(r.fainted["me"] and r.me.active.species_id != "slurpuff" for _p, r in brs), [r.events for _p, r in brs]
    e_with = sum(p * VA.value(r.me, r.opp, inp.vctx) for p, r in brs)
    no_moves = copy.copy(inp.vctx)
    no_moves.my_moves = dict(inp.vctx.my_moves, slurpuff=())
    no_moves._cache = {}
    e_without = sum(p * VA.value(r.me, r.opp, no_moves) for p, r in brs)
    assert abs(e_with - e_without) < 1e-12, (e_with, e_without)
    print(f"test_a_switch_target_that_faints_on_landing OK (best {adv['best']['id']} {adv['best']['score']} / "
          f"交代 {sw['score']} p_faint {sw['branches']['p_faint']})")


# ==============================================================================
# (b) 自滅が不利な局面では自滅技が 1 位にならない / 有利な交換では上位
# ==============================================================================
def test_b_selfko_disadvantage_and_advantage():
    """112839:39 相当: ペロリーム 100% vs キュウコン 100% (残り 3 体)、ミストバーストは 2 割ほど。当時は RL 94% の加点込みで 1 位。
    transition では RL の確率 (94%) を渡しても 1 位にならない。相手が残り 1 体で瀕死・ペロリームが先手なら自滅技が 1 位"""
    sl = _mon("slurpuff", "ペロリーム", ["フェアリー"], SLURPUFF_MOVES, ability_id="sweetveil")
    lp = _mon("lopunny", "ミミロップ", ["ノーマル"], [("インファイト", "closecombat")])
    nt = _mon("ninetales", "キュウコン", ["ほのお"], picked=False, revealed_moves=["かえんほうしゃ"])
    st = _state([sl, lp], [nt], my_rem=3, opp_rem=3)
    top = [{"label": "ミストバースト", "prob": 0.94}, {"label": "ねばねばネット", "prob": 0.04}, {"label": "あくび", "prob": 0.02}]
    with _Hint(top):
        leg = evaluate(copy.deepcopy(st), resolver, rl_blend_weight=25.0, scoring_mode="legacy")
        tr = evaluate(copy.deepcopy(st), resolver, rl_blend_weight=25.0, scoring_mode="transition")
    assert leg["best"]["id"] == "mistyexplosion", leg["actions"]          # 旧挙動の再現 (RL ×25 の加点で 1 位)
    assert tr["scoring_used"] == "transition", tr.get("scoring_fallback")
    assert tr["best"]["id"] != "mistyexplosion", tr["actions"]
    mx = next(a for a in tr["actions"] if a["id"] == "mistyexplosion")
    assert mx["rl_prob"] == 0.94 and mx["self_cost"]["faints"] and mx["branches"]["p_faint"] == 1.0, mx
    assert tr["actions"][-1]["id"] == "mistyexplosion", [a["id"] for a in tr["actions"]]
    # 有利な交換: 相手は残り 1 体 (カバルドン 15%、ペロリームより遅い)。自滅技だけが倒せる → 1 位
    hp = _mon("hippowdon", "カバルドン", ["じめん"], hp=15.0, picked=False, revealed_moves=["じしん"])
    st2 = _state([copy.deepcopy(sl), copy.deepcopy(lp)], [hp], my_rem=2, opp_rem=1)
    with _Hint():
        tr2 = evaluate(st2, resolver, rl_blend_weight=0, scoring_mode="transition")
    assert tr2["scoring_used"] == "transition" and tr2["best"]["id"] == "mistyexplosion", tr2["actions"]
    assert tr2["best"]["q"] == 1.0
    print("test_b_selfko_disadvantage_and_advantage OK")


# ==============================================================================
# (d) service.Advisor.advise を通した最終順位が、据え置き・旧補正で戻されない
# ==============================================================================
def test_d_service_final_ranking():
    from advisor import service
    st = _state_171()
    # RL が交代に 100% を出しても (legacy なら ×25 の加点)、transition の順位・点は Q だけで決まる
    with _Hint([{"label": "交代:ペロリーム", "prob": 1.0}]):
        adv = service.Advisor(resolver)
        r1 = adv.advise(copy.deepcopy(st), rl_blend_weight=25.0, scoring_mode="transition")
        r2 = adv.advise(copy.deepcopy(st), rl_blend_weight=25.0, scoring_mode="transition")
        leg = service.Advisor(resolver).advise(copy.deepcopy(st), rl_blend_weight=25.0, scoring_mode="legacy")
        leg0 = service.Advisor(resolver).advise(copy.deepcopy(st), rl_blend_weight=0.0, scoring_mode="legacy")
    assert r1["scoring_used"] == "transition"
    for a in r1["actions"]:
        # RL 加点・×0.25・最善交代 +2 が乗っていない (score は Q の線形変換だけ。q は 4 桁・score は 1 桁に丸めてある)
        assert abs(a["score"] - (50.0 + 50.0 * a["q"])) <= 0.06, a
        assert "RL" not in a["reason"]
    sw = next(a for a in r1["actions"] if a["kind"] == "switch")
    assert sw["rl_prob"] == 1.0
    assert r1["best"]["kind"] != "switch" and r2["best"]["id"] == r1["best"]["id"], (r1["best"], r2["best"])
    # 旧挙動の再現: legacy では同じ RL の確率で交代に +25 の加点が乗る
    sw_l = next(a for a in leg["actions"] if a["kind"] == "switch")
    sw_l0 = next(a for a in leg0["actions"] if a["kind"] == "switch")
    assert abs(sw_l["score"] - sw_l0["score"] - 25.0) < 1e-6 and "RL100%" in sw_l["reason"], (sw_l, sw_l0)
    # 据え置き: 前回 (legacy の結果) の第一候補を、採点方式が違う次の結果で戻さない
    last = {"turn": 5, "kind": "switch", "id": "slurpuff", "position": r1["position_key"],
            "legal": service.legal_action_set(r1), "scoring": "legacy"}
    fake = dict(r1, actions=[dict(a) for a in r1["actions"]])
    fake["best"] = fake["actions"][0]
    # 差が据え置きの幅 (6) より小さくなるように点を寄せても、方式が違えば据え置かない
    for a in fake["actions"]:
        if a["kind"] == "switch":
            a["score"] = fake["best"]["score"] - 1.0
    out, new_last = service.apply_advice_hysteresis(fake, last, 5)
    assert out["best"]["id"] == r1["best"]["id"] and new_last["scoring"] == "transition", out["best"]
    # 同じ方式なら従来どおり据え置く (幅は変えていない)
    out2, _ = service.apply_advice_hysteresis(fake, dict(last, scoring="transition"), 5)
    assert out2["best"]["kind"] == "switch", out2["best"]
    print("test_d_service_final_ranking OK")


# ==============================================================================
# (e) 退避: 未対応の効果・予算超過・相手不明は決定全体を legacy に退避し、理由を残す。退避率を集計できる
# ==============================================================================
def test_e_fallback_recorded():
    # 2026-10-09 M2c で期待値を変更 (仕様修正): 未対応の自分の技の例を アンコール から トリックルーム に替えた。アンコールは M2c で
    # 遷移に入れた (1 ターンの状態変化) ので退避しない。旧挙動 (アンコールで "unsupported:effect_unmodeled:encore" に退避) と
    # 変更後の挙動 (アンコールで退避しない) は tests.test_advice_m2c.test_opp_encore_not_fallback_and_unsupported_fallback
    ind = _mon("indeedee", "イエッサン", ["エスパー", "ノーマル"],
               [("マジカルフレイム", "mysticalfire"), ("トリックルーム", "trickroom"), ("ワイドフォース", "expandingforce")])
    opp = _mon("garchomp", "ガブリアス", ["ドラゴン", "じめん"], picked=False, revealed_moves=["じしん"])
    st = _state([ind, _mon("blastoise", "カメックス", ["みず"], [("なみのり", "surf")])], [opp], my_rem=3, opp_rem=3)
    with _Hint():
        leg = evaluate(copy.deepcopy(st), resolver, rl_blend_weight=0, scoring_mode="legacy")
        tr = evaluate(copy.deepcopy(st), resolver, rl_blend_weight=0, scoring_mode="transition")
    assert tr["scoring_mode"] == "transition" and tr["scoring_used"] == "legacy"
    assert tr["scoring_fallback"] == "unsupported:effect_unmodeled:trickroom", tr["scoring_fallback"]
    assert tr["actions"] == leg["actions"]                       # 退避先は legacy の結果そのもの (未評価の技を低得点にしない)
    # 予算超過
    old = C.TRANSITION_BUDGET_MS
    C.TRANSITION_BUDGET_MS = 0.0
    try:
        with _Hint():
            tb = evaluate(_state_171(), resolver, rl_blend_weight=0, scoring_mode="transition")
    finally:
        C.TRANSITION_BUDGET_MS = old
    assert tb["scoring_fallback"] == "budget" and tb["scoring_used"] == "legacy", tb.get("scoring_fallback")
    # 相手の場の個体が分からない
    st3 = copy.deepcopy(st)
    st3["player"]["party"][0]["moves"] = [{"name_ja": "マジカルフレイム", "move_id": "mysticalfire", "pp": 5}]
    st3["opponent"]["active_index"] = None
    with _Hint():
        tu = evaluate(st3, resolver, rl_blend_weight=0, scoring_mode="transition")
    assert tu["scoring_fallback"] == "opp_unknown", tu.get("scoring_fallback")
    with _Hint():
        ok = evaluate(_state_171(), resolver, rl_blend_weight=0, scoring_mode="transition")
    rows = [{"type": "advice", "advice": a} for a in (tr, tb, tu, ok, leg)]
    s = ST.summarize_fallbacks(rows)
    assert s["n"] == 4 and s["n_fallback"] == 3 and abs(s["rate"] - 0.75) < 1e-12, s
    assert s["by_reason"] == {"unsupported:effect_unmodeled:trickroom": 1, "budget": 1, "opp_unknown": 1}, s
    print("test_e_fallback_recorded OK")


# ==============================================================================
# (f) legacy の出力は不変 (既定は legacy、方式の指定なし = config の既定と同じ。transition の計算は legacy の値を変えない)
# ==============================================================================
def test_f_legacy_unchanged_and_default():
    assert C.ADVICE_SCORING_MODE == "legacy" or os.environ.get("ADVICE_SCORING_MODE")
    st = _state_171()
    with _Hint([{"label": "交代:ペロリーム", "prob": 0.6}, {"label": "インファイト", "prob": 0.4}]):
        d = evaluate(copy.deepcopy(st), resolver, rl_blend_weight=25.0)
        l = evaluate(copy.deepcopy(st), resolver, rl_blend_weight=25.0, scoring_mode="legacy")
        common = evaluate_common(copy.deepcopy(st), resolver, scoring_mode="transition")
        frozen = copy.deepcopy(common)
        tr_leg = finish_evaluation(dict(common, scoring_mode="legacy"), 25.0)
        tr = finish_evaluation(common, 25.0)
    assert d["scoring_mode"] == "legacy" and "scoring_used" not in d and "transition_stats" not in d
    assert d["actions"] == l["actions"]
    # 同じ共通部分から legacy の後段を通した結果は、方式を legacy にした評価と同じ (transition の計算が legacy の行動を書き換えない)
    assert tr_leg["actions"] == l["actions"], (tr_leg["actions"], l["actions"])
    assert common == frozen                                    # finish_evaluation は共通部分を書き換えない
    assert tr["scoring_used"] == "transition" and tr["transition_stats"]["elapsed_ms"] is not None
    import json
    json.dumps(tr, ensure_ascii=False)                         # advice 行にそのまま書ける (battle_logger.on_advice)
    # config の既定を transition にすると、方式の指定なしでも transition (advisor-as-player の経路)
    old = C.ADVICE_SCORING_MODE
    C.ADVICE_SCORING_MODE = "transition"
    try:
        with _Hint():
            d2 = evaluate(copy.deepcopy(st), resolver, rl_blend_weight=0)
    finally:
        C.ADVICE_SCORING_MODE = old
    assert d2["scoring_mode"] == "transition", d2["scoring_mode"]
    print("test_f_legacy_unchanged_and_default OK")


def test_env_and_advisor_player():
    """環境変数 ADVICE_SCORING_MODE で config の既定が変わる (起動時に 1 回)。読めない値は legacy。
    advisor-as-player の状態に相手の選出数 (team_size) が入り、transition の未判明の残りに使われる"""
    code = "import champions_agent.config as c; print(c.ADVICE_SCORING_MODE)"
    for val, want in (("transition", "transition"), ("legacy", "legacy"), ("bogus", "legacy"), ("", "legacy")):
        env = dict(os.environ, ADVICE_SCORING_MODE=val)
        out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                             cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        assert out.stdout.strip().splitlines()[-1] == want, (val, out.stdout, out.stderr)
    from types import SimpleNamespace as NS
    from champions_agent.env.advisor_player import battle_to_state

    def mon(species, hp=1.0, moves=()):
        return NS(species=species, current_hp_fraction=hp, fainted=False,
                  moves={m: NS(id=m, current_pp=10, max_pp=10) for m in moves}, item="leftovers", ability=None,
                  status=None, boosts={}, max_hp=200, current_hp=int(200 * hp), types=[])
    own = [mon("garchomp", moves=("earthquake",)), mon("clefable", moves=("moonblast",))]
    opp = [mon("heatran", moves=("magmastorm",))]
    b = NS(active_pokemon=own[0], opponent_active_pokemon=opp[0], team={p.species: p for p in own},
           opponent_team={p.species: p for p in opp}, available_moves=[NS(id="earthquake")],
           available_switches=[own[1]], can_mega_evolve=False, weather={}, fields={}, side_conditions={}, turn=3,
           opponent_side_conditions={})
    s = battle_to_state(b, resolver)
    assert s["opponent"]["team_size"] == C.BSS_PICK_COUNT and s["opponent"]["remaining"] == 1
    assert "team_size" not in s["player"]
    with _Hint():
        adv, inp = _capture_inputs(s)
    assert inp is not None and inp.vctx.opp_unknown == C.BSS_PICK_COUNT - 1, (adv.get("scoring_fallback"), inp)
    # advisor-as-player の stats: transition の決定数と退避の件数 (legacy では何も足さない)
    from champions_agent.env.advisor_player import _count_scoring
    stats: dict = {}
    _count_scoring(stats, {"scoring_mode": "legacy"})
    assert stats == {}
    _count_scoring(stats, {"scoring_mode": "transition", "scoring_fallback": None})
    _count_scoring(stats, {"scoring_mode": "transition", "scoring_fallback": "unsupported:effect_unmodeled:encore"})
    _count_scoring(stats, {"scoring_mode": "transition", "scoring_fallback": "budget"})
    assert stats == {"scoring_transition": 3, "scoring_fallback": 2,
                     "scoring_fallback:unsupported:effect_unmodeled:encore": 1, "scoring_fallback:budget": 1}, stats
    print("test_env_and_advisor_player OK")


# ==============================================================================
# ひんし後の繰り出し・交代技の後の繰り出し・計算時間
# ==============================================================================
def test_replacement_and_budget():
    """ひんし後の繰り出し: 相手は行動しない (通常交代と同じ被弾を課さない)。候補どうしを V で比べる。計算は予算内"""
    dead = _mon("slurpuff", "ペロリーム", ["フェアリー"], SLURPUFF_MOVES, hp=0.0, status="fainted")
    lp = _mon("lopunny", "ミミロップ", ["ノーマル"], [("インファイト", "closecombat")], hp=100.0)
    ar = _mon("archaludon", "ブリジュラス", ["はがね", "ドラゴン"], [("ラスターカノン", "flashcannon")], hp=30.0)
    nt = _mon("ninetales", "キュウコン", ["ほのお"], picked=False, revealed_moves=["かえんほうしゃ"])
    st = _state([dead, lp, ar], [nt], my_rem=2, opp_rem=3)
    with _Hint():
        adv, inp = _capture_inputs(st)
    assert adv["scoring_used"] == "transition", adv.get("scoring_fallback")
    assert inp.responses is None and all(a["kind"] == "switch" and a["mode"] == "replace" for a in adv["actions"])
    for a in adv["actions"]:
        assert a["branches"]["n"] == 1 and a["branches"]["p_faint"] == 0.0, a
    assert "ひんし後の繰り出し" in adv["actions"][0]["reason"]
    assert adv["transition_stats"]["elapsed_ms"] < C.TRANSITION_BUDGET_MS
    print("test_replacement_and_budget OK")


if __name__ == "__main__":
    test_a_switch_target_that_faints_on_landing()
    test_b_selfko_disadvantage_and_advantage()
    test_d_service_final_ranking()
    test_e_fallback_recorded()
    test_f_legacy_unchanged_and_default()
    test_env_and_advisor_player()
    test_replacement_and_budget()
    print("ALL OK")
