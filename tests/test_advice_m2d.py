"""操縦提案の精度改善 M2d (正式測定の前に直す点、2026-10-09 レビュー 1〜6) のテスト。

段 1 未評価の確率の区間 (推定技の未対応の応手を ε として残し、区間で順位が決まらなければ退避)
段 2 実験専用の構築プール (tools/scoring_pool、check_advisor_player --own-pool、usefulness_p1 --team general / general-pinned)
段 3 感度の分離 (TRANSITION_SENSITIVITY_ENABLED、transition_stats の main / sens / elapsed_total_ms)
段 4 Showdown の版とルールの記録、同時全滅の回帰を 自滅 / 反動 / HP 消費 / 持ち物の反動 で別に確かめる
段 5 メガ後の登録参照 (登録は基本種の名前で引く。種族値・タイプ・固定特性はメガのまま)
段 6 対応の確認 (送った自チームの本文の sha16 と、助言が参照した型の一致。verdict の invalid)

使用率 DB・登録ファイル (config/my_team.json)・Showdown の対戦には依存しない (登録は合成、相手の技は固定)。

    python -m tests.test_advice_m2d
"""
from __future__ import annotations

import copy
import io
import json
import os
import random
import tempfile
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("RL_BLEND_WEIGHT", "0")

import advisor.engine as ENG
from advisor import scoring_transition as ST
from advisor import transition as T
from advisor import valuation as VA
from advisor.engine import OFFENSIVE_EV, build_mon_view, evaluate
from advisor.transition import SimSide
from champions_agent import config as C
from tests.test_advice_m1a import _Patch
from tools import usefulness_p1 as P1
from tests.test_advice_m2c import _inp, _mv, _view
from tests.test_scoring_transition import _Hint, _capture_inputs, _mon, _state, resolver


# ==============================================================================
# 段 1: 未評価の確率の区間
# ==============================================================================
def _gar_vs_heatran(eps: float, moves: list, estimated=("trickroom",)):
    me = SimSide(_view("garchomp"), 1.0, bench=[])
    opp = SimSide(_view("heatran"), 1.0, bench=[])
    resp = [(1.0 - eps, _mv("magmastorm")), (eps, _mv("trickroom"))]
    inp = _inp(me, opp, moves, resp, opp_moves={"heatran": ("magmastorm", "trickroom")})
    return replace(inp, opp_estimated=frozenset(estimated))


def test_unevaluated_interval_continue_and_fallback():
    """応手を固定した合成局面。トリックルーム (未対応の効果) が推定技なら退避せず未評価 ε に回す:
    ε が小さく差が大きい (じしん 1 発で倒す vs つるぎのまい) → 続行、差が小さい (じしん vs 10まんばりき、どちらも倒す) → 退避。
    判明技 (推定の集合に無い) の未対応は従来どおり opp_unsupported で退避"""
    r = ST.score(_gar_vs_heatran(0.05, ["earthquake", "swordsdance"]), budget_ms=10_000)
    assert r["ok"] and r["unevaluated"] == {"eps": 0.05, "moves": ["trickroom"], "effects": ["effect_unmodeled:trickroom"]}, r
    a = {x["id"]: x for x in r["actions"]}
    assert r["actions"][0]["id"] == "earthquake"
    # q は未評価の応手を除いて正規化した期待値、区間は [(1 − ε) q − ε, (1 − ε) q + ε]
    for x in r["actions"]:
        assert abs(x["q_lo"] - (0.95 * x["q"] - 0.05)) < 1e-3 and abs(x["q_hi"] - (0.95 * x["q"] + 0.05)) < 1e-3, x
        assert x["eps_unevaluated"] == 0.05 and "未評価の応手 5%" in x["reason"]
    assert a["earthquake"]["q"] == 1.0 and a["earthquake"]["q_lo"] > a["swordsdance"]["q_hi"]
    # 未評価の応手を除いた q = 応手 マグマストーム だけの期待値 (正規化)
    only = ST.score(replace(_gar_vs_heatran(0.0, ["earthquake", "swordsdance"]),
                            responses=[(1.0, _mv("magmastorm"))]), budget_ms=10_000)
    assert {x["id"]: x["q"] for x in only["actions"]} == {x["id"]: x["q"] for x in r["actions"]}
    # 差が小さい → 退避 (ε と未評価技を記録)
    amb = ST.score(_gar_vs_heatran(0.05, ["earthquake", "highhorsepower"]), budget_ms=10_000)
    assert not amb["ok"] and amb["fallback"] == "opp_unevaluated_ambiguous", amb
    assert amb["unevaluated"]["eps"] == 0.05 and amb["unevaluated"]["moves"] == ["trickroom"]
    assert amb["unevaluated"]["best_lo"] <= amb["unevaluated"]["other_hi"]
    # ε が大きいと差が大きくても順位が決まらない (下限 0.0 < 上限 0.56)
    big = ST.score(_gar_vs_heatran(0.5, ["earthquake", "swordsdance"]), budget_ms=10_000)
    assert big["fallback"] == "opp_unevaluated_ambiguous", big
    # 判明技 (推定の集合に入っていない) の未対応 → 従来どおり退避
    rev = ST.score(_gar_vs_heatran(0.05, ["earthquake", "swordsdance"], estimated=()), budget_ms=10_000)
    assert rev["fallback"] == "opp_unsupported:effect_unmodeled:trickroom", rev
    # 候補が 1 つなら区間の比較は要らない (続行)。じしん だけだと先に倒すのでトリックルームが解決されず未評価も出ない
    one = ST.score(_gar_vs_heatran(0.5, ["swordsdance"]), budget_ms=10_000)
    assert one["ok"] and one["unevaluated"]["eps"] == 0.5 and "q_lo" in one["actions"][0]
    assert ST.score(_gar_vs_heatran(0.5, ["earthquake"]), budget_ms=10_000)["unevaluated"] is None
    print("test_unevaluated_interval_continue_and_fallback OK")


def test_eps_zero_unchanged():
    """未評価の応手が出ない局面では、推定の集合を入れても結果は従来と同じ (区間の欄も付かない)"""
    me = SimSide(_view("garchomp"), 0.8, bench=[(_view("lopunny"), 1.0)])
    opp = SimSide(_view("heatran"), 1.0)
    resp = [(0.7, _mv("magmastorm")), (0.3, _mv("earthpower"))]
    inp = _inp(me, opp, ["earthquake", "dragonclaw"], resp)
    a = ST.score(inp, budget_ms=10_000)
    b = ST.score(replace(inp, opp_estimated=frozenset({"earthpower"})), budget_ms=10_000)
    keys = ("id", "q", "q_worst", "branches", "score", "reason")
    assert [{k: x[k] for k in keys} for x in a["actions"]] == [{k: x[k] for k in keys} for x in b["actions"]]
    assert b["unevaluated"] is None and all("q_lo" not in x for x in b["actions"])
    print("test_eps_zero_unchanged OK")


def _pool_with_estimated(pool):
    def f(opp_p, opp_view, resolver):
        return list(pool)
    return f


def test_unevaluated_through_engine_and_summary():
    """engine 経由: 相手の技の候補の推定技 (重み < 100、判明技でない) の未対応は未評価に回り、transition_stats.unevaluated に残る。
    同じ技が判明技なら退避。summarize_fallbacks は未評価技の頻度 (決定単位) と本採点 / 感度 / 全処理時間を分けて数える"""
    gar = _mon("garchomp", "ガブリアス", ["ドラゴン", "じめん"], [("じしん", "earthquake"), ("ドラゴンクロー", "dragonclaw")])
    hea = _mon("heatran", "ヒードラン", ["ほのお", "はがね"], picked=False, revealed_moves=["マグマストーム"])
    st = _state([gar], [hea], my_rem=3, opp_rem=1)          # 相手は残り 1 体 (じしん で倒せば勝ち = 差が大きい)
    pool = [("magmastorm", 100.0), ("trickroom", 20.0)]
    assert ST.estimated_moves(pool, hea, resolver) == frozenset({"trickroom"})
    with _Hint():
        ENG.opponent_move_pool = _pool_with_estimated(pool)
        adv, inp = _capture_inputs(st)
    assert inp.opp_estimated == frozenset({"trickroom"})
    assert adv["scoring_used"] == "transition" and adv["scoring_fallback"] is None, adv.get("scoring_fallback")
    une = adv["transition_stats"]["unevaluated"]
    assert une["moves"] == ["trickroom"] and 0 < une["eps"] < 0.2, une
    best = adv["actions"][0]
    assert best["id"] == "earthquake" and best["eps_unevaluated"] == une["eps"] and best["q_lo"] <= best["q"] <= best["q_hi"]
    # 判明技なら退避 (M2c と同じ)
    hea2 = _mon("heatran", "ヒードラン", ["ほのお", "はがね"], picked=False, revealed_moves=["マグマストーム", "トリックルーム"])
    st2 = _state([copy.deepcopy(gar)], [hea2], my_rem=3, opp_rem=1)
    pool2 = [("magmastorm", 100.0), ("trickroom", 100.0)]
    with _Hint():
        ENG.opponent_move_pool = _pool_with_estimated(pool2)
        adv2 = evaluate(st2, resolver, rl_blend_weight=0, scoring_mode="transition")
    assert adv2["scoring_fallback"] == "opp_unsupported:effect_unmodeled:trickroom", adv2.get("scoring_fallback")
    # 集計 (決定単位)
    rows = [{"type": "advice", "advice_id": "a1", "turn": 1, "advice": dict(adv, position_key="P1")},
            {"type": "advice", "advice_id": "a2", "turn": 2, "advice": dict(adv2, position_key="P2")},
            {"type": "advice", "advice_id": "a3", "turn": 3, "advice": {
                "scoring_mode": "transition", "scoring_fallback": "opp_unevaluated_ambiguous", "position_key": "P3",
                "transition_stats": {"unevaluated": {"eps": 0.3, "moves": ["trickroom", "batonpass"]},
                                     "main": {"elapsed_ms": 9.0}}}},
            {"type": "advice", "advice_id": "a4", "turn": 4, "advice": {
                "scoring_mode": "transition", "scoring_fallback": "budget", "position_key": "P4",
                "transition_stats": {"main": {"elapsed_ms": 301.0, "budget_fallback": True}}}}]
    s = ST.summarize_fallbacks(rows)["decisions"]
    assert s["n"] == 4 and s["n_fallback"] == 3
    assert s["unevaluated"] == {"n": 2, "rate": 0.5, "continued": 1, "ambiguous": 1,
                                "by_move": {"trickroom": 2, "batonpass": 1}}, s["unevaluated"]
    assert s["main"]["budget_fallback"] == 1 and s["main"]["n"] == 1 and s["main"]["p50_ms"] is not None
    assert s["sens"]["status"] == {adv["transition_stats"]["sens"]["status"]: 1} and s["total"]["n"] == 1
    # advisor-as-player の stats: 未評価の決定と技、感度の状態、時間 (json には要約だけ)
    from champions_agent.env.advisor_player import _count_scoring, transition_timing
    stats: dict = {}
    for r in rows:
        _count_scoring(stats, r["advice"])
    assert stats["scoring_unevaluated"] == 2 and stats["scoring_unevaluated:trickroom"] == 2
    assert stats["scoring_fallback:budget"] == 1 and len(stats["_tr_main_ms"]) == 1
    tt = transition_timing(stats)
    assert tt["main"]["n"] == 1 and tt["total"]["n"] == 1
    print("test_unevaluated_through_engine_and_summary OK")


# ==============================================================================
# 段 3: 感度の分離
# ==============================================================================
class _Sens:
    def __init__(self, on: bool):
        self.on = on

    def __enter__(self):
        self.orig = C.TRANSITION_SENSITIVITY_ENABLED
        C.TRANSITION_SENSITIVITY_ENABLED = self.on

    def __exit__(self, *a):
        C.TRANSITION_SENSITIVITY_ENABLED = self.orig


def test_sensitivity_toggle_same_ranking():
    """感度オフ: transition_stats.sens が「無効」(enabled False / status disabled)、sensitivity なし、HP の楽観側も計算しない。
    本採点の順位と Q は感度オンと同じ。HP の悲観側を T と V に渡す本体処理は感度オフでも行う"""
    from tests.test_advice_m2c import _uncertain_bench_state
    gar = _mon("garchomp", "ガブリアス", ["ドラゴン", "じめん"], [("じしん", "earthquake"), ("ドラゴンクロー", "dragonclaw")])
    hea = _mon("heatran", "ヒードラン", ["ほのお", "はがね"], picked=False, revealed_moves=["マグマストーム"])
    st = _state([gar], [hea], my_rem=3, opp_rem=3)
    with _Hint():
        with _Sens(True):
            on = evaluate(copy.deepcopy(st), resolver, rl_blend_weight=0, scoring_mode="transition")
            unc_on, inp_on = _capture_inputs(_uncertain_bench_state(True))
        with _Sens(False):
            off = evaluate(copy.deepcopy(st), resolver, rl_blend_weight=0, scoring_mode="transition")
            unc_off, inp_off = _capture_inputs(_uncertain_bench_state(True))
    so, sf = on["transition_stats"], off["transition_stats"]
    assert so["sens"]["enabled"] is True and so["sens"]["status"] in ("run", "partial"), so["sens"]
    assert set(so["sens"]["run"]) == {"scarf", "sash"} and so["sensitivity"]["scarf"]["flip"] == "add"
    assert sf["sens"] == {"enabled": False, "status": "disabled", "run": [], "omitted": {}, "elapsed_ms": sf["sens"]["elapsed_ms"]}
    assert sf["sensitivity"] is None
    assert [(a["id"], a["q"]) for a in on["actions"]] == [(a["id"], a["q"]) for a in off["actions"]]
    assert so["main"]["budget_fallback"] is False and so["elapsed_total_ms"] >= so["main"]["elapsed_ms"]
    # HP の悲観側: 感度オフでも控えの HP は下限 (1 HP)。楽観側の仮説は計算しない (omitted: disabled)
    j = next(j for j, (v, _hp) in enumerate(inp_off.me.bench) if v.species_id == "lopunny")
    assert abs(inp_off.me.bench[j][1] - 1 / 200) < 1e-9
    assert unc_off["transition_stats"]["hp_range"]["optimistic"] == {"omitted": "disabled"}
    assert unc_on["transition_stats"]["hp_range"]["optimistic"].get("best") is not None
    assert [(a["id"], a["q"]) for a in unc_on["actions"]] == [(a["id"], a["q"]) for a in unc_off["actions"]]
    assert all("q_hp_opt" not in a for a in unc_off["actions"])
    print("test_sensitivity_toggle_same_ranking OK")


class _FakeClock:
    """time.perf_counter の代わりに、呼ばれるたびに 1 ms 進む時計 (予算の判定を決定的にする)"""

    def __init__(self):
        self.t = 0.0

    def __call__(self):
        self.t += 0.001
        return self.t


def test_sensitivity_budget_omitted_not_fallback():
    """感度の予算超過は感度を省略するだけで、本採点の結果を返す (退避ではない)。本採点の予算超過だけが退避 (budget)"""
    me = SimSide(_view("garchomp"), 1.0, bench=[])
    opp = SimSide(_view("heatran"), 1.0)
    inp = _inp(me, opp, ["earthquake", "swordsdance"], [(0.6, _mv("magmastorm")), (0.4, _mv("earthpower"))])
    inp = replace(inp, item_alts=[("scarf", replace(opp, active=replace(opp.active, item="choicescarf")), "add"),
                                  ("sash", replace(opp, active=replace(opp.active, item="focussash")), "add")])
    orig = ST.time.perf_counter
    try:
        clk = _FakeClock()
        ST.time.perf_counter = clk
        full = ST.score(inp, budget_ms=10_000)
        n_main = full["main"]["elapsed_ms"]
        assert full["sens"]["status"] == "run" and full["sens"]["run"] == ["scarf", "sash"], full["sens"]
        ST.time.perf_counter = _FakeClock()
        part = ST.score(inp, budget_ms=n_main + 1.5)        # 本採点は収まり、感度は途中で予算を超える
        ST.time.perf_counter = _FakeClock()
        over = ST.score(inp, budget_ms=2.0)                 # 本採点が予算を超える
    finally:
        ST.time.perf_counter = orig
    assert part["ok"] and [a["id"] for a in part["actions"]] == [a["id"] for a in full["actions"]]
    assert part["sens"]["status"] in ("partial", "omitted") and "budget" in part["sens"]["omitted"].values(), part["sens"]
    assert part["main"]["budget_fallback"] is False
    assert not over["ok"] and over["fallback"] == "budget" and over["main"]["budget_fallback"] is True, over
    print("test_sensitivity_budget_omitted_not_fallback OK")


def test_sensitivity_env():
    """環境変数 TRANSITION_SENSITIVITY (config.TRANSITION_SENSITIVITY_ENV): 未設定は有効、0 / false / no / off は無効"""
    key = C.TRANSITION_SENSITIVITY_ENV
    orig = os.environ.get(key)
    try:
        for raw, want in ((None, True), ("", True), ("1", True), ("0", False), ("false", False), ("off", False)):
            if raw is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = raw
            assert C._transition_sensitivity_from_env() is want, (raw, want)
    finally:
        if orig is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = orig
    print("test_sensitivity_env OK")


# ==============================================================================
# 段 4: 同時全滅 (自滅 / 反動 / HP 消費 / 持ち物の反動 を別に確かめる) と Showdown の版とルールの記録
# ==============================================================================
def _last_one(me_sid: str, opp_sid: str = "garchomp"):
    me = SimSide(_view(me_sid), 0.0, bench=[])
    me = replace(me, active_hp=1.0 / me.active.max_hp())
    opp = SimSide(_view(opp_sid), 0.0, bench=[])
    opp = replace(opp, active_hp=1.0 / opp.active.max_hp())
    return me, opp


def test_wipe_selfko_user_loses():
    """自滅 (ミストバースト): 使用者が先に倒れる (useMoveInner の selfdestruct "always" は命中判定の前) → 使用側の負け"""
    me, opp = _last_one("slurpuff")
    for _p, r in T.resolve_turn_branches(me, opp, _mv("mistyexplosion"), _mv("swordsdance"), None, None):
        assert r.faint_seq == ["me", "opp"] and r.winner == "opp" and r.end_reason == "last_faint", r.faint_seq
    print("test_wipe_selfko_user_loses OK")


def test_wipe_recoil_user_wins():
    """反動 (すてみタックル): 対象のひんしを処理した後に applyRecoilDamage (champions の hitStepMoveHitLoop) → 使用側の勝ち"""
    me, opp = _last_one("garchomp")
    brs = T.resolve_turn_branches(me, opp, _mv("doubleedge"), _mv("swordsdance"), None, None)
    hit = [r for _p, r in brs if r.fainted["opp"]]
    assert hit and all(r.faint_seq == ["opp", "me"] and r.winner == "me" for r in hit)
    assert all(any(k == "recoil" for _w, k, _d in r.events) for r in hit)
    print("test_wipe_recoil_user_wins OK")


def test_wipe_hp_cost_user_wins_on_hit():
    """HP 消費 (てっていこうせん、mindBlownRecoil): 当たれば反動と同じく対象のひんしの後に HP 1/2 を払う → 使用側の勝ち。
    外れれば onMoveFail で使用者だけが倒れる → 使用側の負け (相手は残る)"""
    me, opp = _last_one("aegislash")
    brs = T.resolve_turn_branches(me, opp, _mv("steelbeam"), _mv("swordsdance"), None, None)
    hit = [(p, r) for p, r in brs if r.fainted["opp"]]
    miss = [(p, r) for p, r in brs if not r.fainted["opp"]]
    assert hit and all(r.faint_seq == ["opp", "me"] and r.winner == "me" for _p, r in hit), [r.faint_seq for _p, r in hit]
    assert all(any(k == "hp_cost" for _w, k, _d in r.events) for _p, r in hit)
    assert miss and all(r.faint_seq == ["me"] and r.winner == "opp" for _p, r in miss)
    assert abs(sum(p for p, _r in hit) - 0.95) < 1e-9                   # 命中 95
    print("test_wipe_hp_cost_user_wins_on_hit OK")


def test_wipe_item_recoil_user_wins():
    """持ち物の反動 (いのちのたま): ダメージの後に使用者が倒れる → 使用側の勝ち"""
    _me, opp = _last_one("garchomp")
    v = replace(_view("garchomp"), item="lifeorb")
    me = SimSide(v, 1.0 / v.max_hp(), bench=[])
    rl = T.resolve_turn_branches(me, opp, _mv("earthquake"), _mv("swordsdance"), None, None)
    assert rl and all(r.winner == "me" and r.faint_seq == ["opp", "me"] for _p, r in rl if r.fainted["opp"])
    print("test_wipe_item_recoil_user_wins OK")


_FORMATS_TS = """export const Formats: import('../sim/dex-formats').FormatList = [
	{
		name: "[Gen 9 Champions] OU",
		mod: 'champions',
		ruleset: ['Standard'],
	},
	{
		name: "[Gen 9 Champions] BSS Reg M-C",
		mod: 'champions',
		ruleset: ['Flat Rules', 'VGC Timer'],
	},
	{
		name: "[Gen 9 Champions] BSS Reg M-B",
		mod: 'championsregmb',
		ruleset: ['Flat Rules', 'VGC Timer'],
	},
];
"""


def test_showdown_version_and_rules_recorded():
    """条件表の showdown に commit・フォーマットの name / mod / ruleset を残し、commit か mod が取れなければ起動しない"""
    from tools import usefulness_p1 as P
    r = P.format_rule(_FORMATS_TS, "gen9championsbssregmc")
    assert r == {"name": "[Gen 9 Champions] BSS Reg M-C", "mod": "champions", "ruleset": "['Flat Rules', 'VGC Timer']"}, r
    assert P.format_rule(_FORMATS_TS, "gen9championsbssregmb")["mod"] == "championsregmb"
    assert P.format_rule(_FORMATS_TS, "gen9nothing") == {"name": None, "mod": None, "ruleset": None}
    with tempfile.TemporaryDirectory() as d:
        sd = Path(d) / "pokemon-showdown"
        (sd / "config").mkdir(parents=True)
        (sd / "data" / "mods" / "champions").mkdir(parents=True)
        (sd / "config" / "formats.ts").write_text(_FORMATS_TS, encoding="utf-8")
        notes: list = []
        f = P.showdown_facts(sd, notes)
        assert f["mod"] == "champions" and f["mod_dir_exists"] is True and f["battle_format"] == C.TRAINING_BATTLE_FORMAT
        assert f["commit"] is None and any("showdown.commit" in n for n in notes)       # git でない dir
    from tests.test_usefulness_p1 import _cond
    c = _cond()
    assert P.preflight_errors(c) == []
    c["showdown"] = dict(c["showdown"], commit=None)
    assert any("Showdown の版" in e for e in P.preflight_errors(c))
    c["showdown"] = dict(c["showdown"], commit="abc", mod=None, battle_format="x")
    assert any("mod" in e for e in P.preflight_errors(c))
    print("test_showdown_version_and_rules_recorded OK")


# ==============================================================================
# 段 5: メガ後の登録参照
# ==============================================================================
# 合成の登録: ミミロップ HP 252 / 素早さ 252 ようき (レビュワーの表)
LOP_BUILD = {"ev": {"hp": 252, "atk": 0, "def": 0, "spa": 0, "spd": 4, "spe": 252}, "nature": {"spe": 1.1, "spa": 0.9},
             "item_ja": None, "ability_ja": None}


def _mega_lop(sim_name: bool) -> dict:
    """メガ後のミミロップ。sim_name = シム (poke-env の species = lopunnymega → 日本語名 メガミミロップ)。
    False は実機の HUD (通常名 ミミロップ、種族 id は基本種で is_mega + 石から形態を引く)"""
    if sim_name:
        return {"species_id": "lopunnymega", "species_ja": "メガミミロップ", "is_mega": True, "item_id": "lopunnite",
                "hp_percent": 100.0, "types": []}
    return {"species_id": "lopunny", "species_ja": "ミミロップ", "is_mega": True, "item_id": "lopunnite",
            "hp_percent": 100.0, "types": ["ノーマル"]}


class _OldLookup:
    """修正前の登録参照 (個体の species_ja だけで引く) を再現する"""

    def __enter__(self):
        self.orig = ENG.registration_names
        ENG.registration_names = lambda p: [p.get("species_ja")] if p and p.get("species_ja") else []

    def __exit__(self, *a):
        ENG.registration_names = self.orig


def _stats(v):
    """実数値 (Lv50、MonView.stat / max_hp と同じ式)"""
    return {"hp": v.max_hp(), "atk": v.stat("atk", ignore_boost=True), "spe": v.stat("spe", ignore_boost=True)}


def test_mega_registration_lookup_reproduces_review_table():
    """レビュワーの表の再現: 修正前はメガミミロップ (シムの名前) が既定の 攻撃 / 特攻 / 素早さ 252・性格なし、
    修正後は登録の配分・性格 (HP 252 / 素早さ 252 ようき) でメガの種族値。通常ミミロップと実機の HUD 名は修正前も登録どおり"""
    from advisor.dex import get_dex
    mega_base = get_dex().species("lopunnymega")["baseStats"]
    with _Patch(builds={"ミミロップ": LOP_BUILD}):
        with _OldLookup():
            old_sim = build_mon_view(_mega_lop(True), resolver, side="player")
            old_hud = build_mon_view(_mega_lop(False), resolver, side="player")
        new_sim = build_mon_view(_mega_lop(True), resolver, side="player")
        new_hud = build_mon_view(_mega_lop(False), resolver, side="player")
        normal = build_mon_view({"species_id": "lopunny", "species_ja": "ミミロップ", "hp_percent": 100.0, "types": []},
                                resolver, side="player")
        assert ENG.registration_names(_mega_lop(True)) == ["ミミロップ", "メガミミロップ"]
        assert ENG.registration_names(_mega_lop(False)) == ["ミミロップ"]
    # 修正前 (シムの名前): 既定の攻撃系 252・性格なし
    assert old_sim.ev == OFFENSIVE_EV and old_sim.nature == {} and old_sim.species_id == "lopunnymega"
    # 修正後: 登録の配分・性格、種族値・特性はメガ
    for v in (new_sim, new_hud):
        assert v.ev == LOP_BUILD["ev"] and v.nature == LOP_BUILD["nature"], (v.ev, v.nature)
        assert v.species_id == "lopunnymega" and v.base == mega_base and v.ability == "scrappy" and v.item == "lopunnite"
    assert old_hud.ev == LOP_BUILD["ev"]                                      # 実機の HUD 名は修正前も登録どおり
    assert normal.ev == LOP_BUILD["ev"] and normal.species_id == "lopunny"
    # 再現表 (実数値、メガミミロップ): 修正前 H 140 / A 188 / S 187 (既定の攻撃・素早さ 252、性格なし)
    #                                  → 修正後 H 172 / A 156 / S 205 (登録の HP・素早さ 252 ようき)
    assert _stats(old_sim) == {"hp": 140, "atk": 188, "spe": 187}, _stats(old_sim)
    assert _stats(new_sim) == {"hp": 172, "atk": 156, "spe": 205}, _stats(new_sim)
    print("test_mega_registration_lookup_reproduces_review_table OK", _stats(old_sim), "->", _stats(new_sim))


def test_mega_registration_sim_path_and_moves():
    """シムの経路: register_team_text (通常名 ミミロップ で登録) の後、メガ名の個体でも登録の型と技を引く"""
    from champions_agent.env import advisor_player as AP
    text = ("Lopunny @ Lopunnite\nAbility: Limber\nEVs: 32 HP / 32 Spe\nJolly Nature\n"
            "- Close Combat\n- Fake Out\n- Triple Axel\n- Ice Punch")
    AP._SIM_BUILDS.clear()
    AP._SIM_MOVES.clear()
    try:
        AP.register_team_text(text, resolver)
        v = build_mon_view(_mega_lop(True), resolver, side="player")
        assert v.ev["hp"] == 252 and v.ev["spe"] == 252 and v.ev["atk"] == 0 and v.nature == {"spe": 1.1, "spa": 0.9}, v.ev
        mv = [m for _ja, m in ENG.registered_moves_of_mon(_mega_lop(True), resolver)]
        assert sorted(mv) == ["closecombat", "fakeout", "icepunch", "tripleaxel"], mv
        with _OldLookup():
            assert build_mon_view(_mega_lop(True), resolver, side="player").ev == OFFENSIVE_EV
            assert ENG.registered_moves_of_mon(_mega_lop(True), resolver) == []
        # 対応の確認 (段 6): 参照した型と送った型が一致 (メガ後も)
        corr: dict = {}
        assert AP.check_reference(corr, _mega_lop(True), text, resolver) is True and corr["ref_checked"] == 1
        with _OldLookup():
            corr2: dict = {}
            assert AP.check_reference(corr2, _mega_lop(True), text, resolver) is False and corr2["ref_mismatch"] == 1
    finally:
        AP._SIM_BUILDS.clear()
        AP._SIM_MOVES.clear()
    print("test_mega_registration_sim_path_and_moves OK")


def test_mega_registration_legacy_change():
    """legacy にも効く仕様修正: メガミミロップ (シムの名前) の技のダメージが、修正前の既定 (攻撃 252) から登録 (攻撃 0) に変わる。
    旧挙動 (_OldLookup) と変更後をここに残す。通常名 (実機の HUD) の局面の legacy は変わらない"""
    def st(sim_name):
        lop = dict(_mega_lop(sim_name), moves=[{"name_ja": "インファイト", "move_id": "closecombat", "pp": 5, "max_pp": None,
                                                 "effectiveness": None}], revealed_moves=[], is_picked=True, boosts={},
                   status=None, ability_id=None, hp_current=None, hp_max=None)
        gar = _mon("garchomp", "ガブリアス", ["ドラゴン", "じめん"], picked=False, revealed_moves=["じしん"])
        return _state([lop], [gar], my_rem=3, opp_rem=3, mega_used=(True, False))

    def cc(adv):
        return next(a for a in adv["actions"] if a["kind"] == "move" and a["id"] == "closecombat")
    with _Hint(), _Patch(builds={"ミミロップ": LOP_BUILD}):
        with _OldLookup():
            old = evaluate(st(True), resolver, rl_blend_weight=0)
            old_hud = evaluate(st(False), resolver, rl_blend_weight=0)
        new = evaluate(st(True), resolver, rl_blend_weight=0)
        new_hud = evaluate(st(False), resolver, rl_blend_weight=0)
    assert old["scoring_mode"] == "legacy" and new["scoring_mode"] == "legacy"
    assert cc(old)["reason"] != cc(new)["reason"], (cc(old)["reason"], cc(new)["reason"])
    assert cc(old)["score"] > cc(new)["score"], (cc(old)["score"], cc(new)["score"])      # 攻撃 252 → 0 で与ダメージが下がる
    assert cc(old_hud)["reason"] == cc(new_hud)["reason"] and cc(old_hud)["score"] == cc(new_hud)["score"]
    print("test_mega_registration_legacy_change OK", (cc(old)["score"], cc(new)["score"]))


# ==============================================================================
# 段 6: 対応の確認
# ==============================================================================
def test_reference_set_pure():
    from champions_agent.env import advisor_player as AP
    text = ("Garchomp @ Choice Scarf\nAbility: Rough Skin\nEVs: 2 HP / 32 Atk / 32 Spe\nJolly Nature\n- Earthquake\n- Outrage\n\n"
            "Lopunny @ Lopunnite\nEVs: 252 HP / 252 Spe\nJolly Nature\n- Fake Out")
    g = AP.text_set_of(text, "garchomp")
    assert g == {"ev": {"atk": 252, "hp": 16, "spe": 252}, "nature": {"spa": 0.9, "spe": 1.1}, "item": "choicescarf",
                 "moves": ["earthquake", "outrage"]}, g
    assert AP.text_set_of(text, "lopunnymega")["item"] == "lopunnite"            # メガ後は本文の通常名の前方一致
    assert AP.text_set_of(text, "dragapult") is None
    ref = dict(g)
    assert AP.sets_match(ref, g) and AP.sets_match(dict(g, item=None), g)          # 持ち物を失った (参照側が無い) は比べない
    assert not AP.sets_match(dict(g, item="leftovers"), g) and not AP.sets_match(dict(g, ev={"atk": 252}), g)
    assert not AP.sets_match(None, g) and not AP.sets_match(g, None)
    assert AP.set_sha16(g) == AP.set_sha16(AP.canonical_set({"atk": 252, "hp": 16, "spe": 252, "def": 0}, {"spe": 1.1, "spa": 0.9},
                                                              "Choice Scarf", ["Outrage", "Earthquake"]))
    assert AP.team_text_sha16(text) and AP.team_text_sha16(None) is None
    rec = AP.correspondence_record({"own_team_sha16": "s1", "ref_checked": 3, "ref_mismatch": 0})
    assert rec["own_team_sha16"] == "s1" and rec["ref_check"]["n"] == 3 and rec["ref_check"]["mismatch"] == 0
    # 助言の図鑑に無い種は参照していないので不一致に数えない (2026-10-09 M2e: indeedeef は別名の表で図鑑を引けるようになったので、
    # 図鑑に無い種の例を架空の id に替えた。期待 (None・ref_unbuilt 1・不一致なし) は変えていない)
    corr: dict = {}
    assert AP.check_reference(corr, {"species_id": "notindexmon", "species_ja": "notindexmon"}, text, resolver) is None
    assert corr.get("ref_unbuilt") == 1 and "ref_mismatch" not in corr
    print("test_reference_set_pure OK")


def _battles(n, sha=lambda i: f"s{i}", mismatch=lambda i: 0, checked=3, drop=()):
    out = []
    for i in range(n):
        r = {"won": i % 2 == 0, "opponent_team_id": f"t{i % 33}"}
        if i not in drop:
            r.update(own_team_sha16=sha(i), ref_check={"n": checked, "mismatch": mismatch(i), "unbuilt": 0})
        out.append(r)
    return out


def test_verdict_own_correspondence():
    """疑似の battles.jsonl: 一致 → final、自チームの本文の sha16 の不一致 1 戦 → invalid、参照の不一致 → invalid、
    対応を確認できない対戦 (欄が無い) → invalid。条件表に own_check.required が無ければ (M2d より前) 確かめず注記"""
    from tests.test_usefulness_verdict import SC_BASE, SC_TR, _pair, _scoring_conditions
    from tools import usefulness_verdict as V
    base, cond = _pair(166, 130, 150, 154)
    outs = {SC_BASE: cond, SC_TR: base}
    ids = {c: [f"t{i % 33}" for i in range(600)] for c in outs}
    kw = dict(baseline_cond=SC_BASE, compare_conds=[SC_TR], final_battles=600, opp_ids_by_cond=ids)
    good = {SC_BASE: _battles(600), SC_TR: _battles(600)}
    v = V.build_verdict(_scoring_conditions(), outs, own_records_by_cond=good, **kw)
    assert v["mode"] == V.MODE_FINAL and v["own_correspondence"]["hash_mismatch"] == 0, v["invalid_reasons"]
    bad_hash = {SC_BASE: _battles(600), SC_TR: _battles(600, sha=lambda i: "zz" if i == 7 else f"s{i}")}
    vh = V.build_verdict(_scoring_conditions(), outs, own_records_by_cond=bad_hash, **kw)
    assert vh["mode"] == V.MODE_INVALID and any("sha16" in r and "1 件" in r for r in vh["invalid_reasons"]), vh["invalid_reasons"]
    bad_ref = {SC_BASE: _battles(600), SC_TR: _battles(600, mismatch=lambda i: 2 if i == 3 else 0)}
    vr = V.build_verdict(_scoring_conditions(), outs, own_records_by_cond=bad_ref, **kw)
    assert vr["mode"] == V.MODE_INVALID and any("参照した型" in r for r in vr["invalid_reasons"]), vr["invalid_reasons"]
    unv = {SC_BASE: _battles(600, drop={5}), SC_TR: _battles(600)}
    vu = V.build_verdict(_scoring_conditions(), outs, own_records_by_cond=unv, **kw)
    assert vu["mode"] == V.MODE_INVALID and any("確認できない" in r for r in vu["invalid_reasons"]), vu["invalid_reasons"]
    assert "自チームの対応" in V.render(vu, _scoring_conditions())
    # 渡さなければ確かめない (RL の比較・M2d より前の条件表)
    assert V.build_verdict(_scoring_conditions(), outs, **kw)["mode"] == V.MODE_FINAL
    # ファイル経由 (run): own_check.required があれば battles.jsonl の欄を読む
    with tempfile.TemporaryDirectory() as d:
        out = Path(d)
        conds = dict(_scoring_conditions(), own_check={"required": True})
        (out / "conditions.json").write_text(json.dumps(conds), encoding="utf-8")
        for c in (SC_BASE, SC_TR):
            f = V.cond_file(c)
            (out / f"{f}.json").write_text(json.dumps({"outcomes": outs[c], "elapsed_s": 1.0, "stats": {}}), encoding="utf-8")
            recs = _battles(600, sha=(lambda i: "zz" if i == 9 else f"s{i}") if c == SC_TR else (lambda i: f"s{i}"))
            for r, w in zip(recs, outs[c]):
                r["won"] = bool(w)
            (out / f"{f}.battles.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs), encoding="utf-8")
        with redirect_stdout(io.StringIO()):
            vv = V.run(out)
        assert vv["mode"] == V.MODE_INVALID and vv["own_correspondence"]["hash_mismatch"] == 1
        # own_check が無い条件表 → 確かめず注記
        (out / "conditions.json").write_text(json.dumps(_scoring_conditions()), encoding="utf-8")
        with redirect_stdout(io.StringIO()):
            v0 = V.run(out)
        assert v0["mode"] == V.MODE_FINAL and any("own_check" in n for n in v0["notes"]), v0["notes"]
    print("test_verdict_own_correspondence OK")


# ==============================================================================
# 段 2: 実験専用の構築プール
# ==============================================================================
_TEAM_A = "Garchomp @ Choice Scarf\nEVs: 32 Atk / 32 Spe\nJolly Nature\n- Earthquake"
_TEAM_B = "Lopunny @ Lopunnite\nEVs: 32 HP / 32 Spe\nJolly Nature\n- Fake Out"


def test_pool_doc_and_check():
    from tools import scoring_pool as SP
    doc = SP.pool_doc([_TEAM_A, _TEAM_B], ["ta", "tb"], snapshot_id=61, battle_format="gen9championsbssregmc", seed=1,
                      n_requested=3, n_generated=3, excluded=[{"team_id": "tc", "errors": ["x"]}], db_path="/db")
    assert doc["n"] == 2 and doc["conditions"]["n_excluded"] == 1 and doc["conditions"]["snapshot_id"] == 61
    assert doc["sha16"] == SP.pool_sha16([_TEAM_A, _TEAM_B]) and SP.check_pool_doc(doc) == []
    assert SP.pool_sha16([_TEAM_B, _TEAM_A]) != doc["sha16"]                          # 並びも sha16 に入る
    bad = json.loads(json.dumps(doc))
    bad["teams"][0]["text"] = _TEAM_A.replace("Jolly", "Adamant")                      # 型の違いも検出する
    assert any("sha16" in e for e in SP.check_pool_doc(bad))
    bad2 = dict(doc, n=5)
    assert any("件数" in e for e in SP.check_pool_doc(bad2))
    assert SP.check_pool_doc(None) and SP.check_pool_doc(dict(doc, teams=[], sha16=None))
    assert SP.default_out_path(61, 0.0, root=Path("/r")).parent == Path("/r/pools")
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "pool.json"
        p.write_text(json.dumps(doc), encoding="utf-8")
        texts, d2 = SP.load_pool(p)
        assert texts == [_TEAM_A, _TEAM_B] and d2["sha16"] == doc["sha16"]
        p.write_text(json.dumps(bad), encoding="utf-8")
        try:
            SP.load_pool(p)
            raise AssertionError("不正なプールが読めた")
        except ValueError:
            pass
    print("test_pool_doc_and_check OK")


def test_own_pool_teambuilder_correspondence():
    """--own-pool: Random(相手 seed + 1) で対戦ごとに引く。同じ seed の 2 条件で自チームの列 (本文) が一致する"""
    from tools.check_advisor_player import _PoolSource, _remembering_teambuilder
    texts = [_TEAM_A, _TEAM_B, _TEAM_A.replace("Earthquake", "Outrage")]
    tb1 = _remembering_teambuilder(_PoolSource(texts, random.Random(20261009 + 1)))
    tb2 = _remembering_teambuilder(_PoolSource(texts, random.Random(20261009 + 1)))
    for _ in range(10):
        tb1.yield_team()
        tb2.yield_team()
    assert tb1.texts == tb2.texts and len(set(tb1.texts)) > 1
    assert [tb1.text_for_battle(k) for k in range(10)] == tb1.texts
    print("test_own_pool_teambuilder_correspondence OK")


def test_usefulness_p1_pool_and_pinned():
    """usefulness_p1: --team general はプールを --own-pool で渡し、条件表にパスと sha16、起動前検査でプールの sha16 と件数を確かめる。
    --team general-pinned は旧固定プール (条件表に「旧固定プール上の比較」)。採点方式の比較は子プロセスで感度を切り、own_check を書く"""
    from champions_agent.config import MY_TEAM_DISABLED_ENV, P1_SEED
    from tests.test_usefulness_p1 import _facts, _ranked_team
    from tools import scoring_pool as SP
    from tools import usefulness_p1 as P

    def cond(team_source, facts, own_pool=None):
        d = P.plan_scoring()
        cmds = {c: P.build_command(d["cond_weight"][c], Path("/out"), None, Path("/s.json"), 1, P1_SEED, 600, Path("/sel.pt"),
                                   "/pin", python="py", cond=c, own_pool=own_pool) for c in d["conditions"]}
        env = P.child_environment("/pin", base={}, team_source=team_source, sensitivity_off=True)
        return P.build_conditions(d, facts, 600, False, P1_SEED, 1, "/s.json", 7200, "/out", cmds, env, None, ["n1"],
                                  team_source=team_source)
    assert P.resolve_team_source("general-pinned", "scoring") == "general-pinned"
    with tempfile.TemporaryDirectory() as d:
        doc = SP.pool_doc([_TEAM_A, _TEAM_B], ["ta", "tb"], snapshot_id=61, battle_format=C.TRAINING_BATTLE_FORMAT, seed=1,
                          n_requested=2, n_generated=2, excluded=[], db_path="/db")
        pf = Path(d) / "pool.json"
        pf.write_text(json.dumps(doc), encoding="utf-8")
        notes: list = []
        team = P.pool_team_facts(pf, P1_SEED, notes)
        assert team["source"] == "pool" and team["sha16"] == doc["sha16"] and team["n_teams"] == 2 and team["errors"] == []
        assert team["seed"] == P1_SEED + 1 and team["snapshot_id"] == 61
        g = cond("general", _facts(team=team), own_pool=pf)
        assert g["team"]["source"] == "pool" and g["team"]["label"] == "一般的なチーム (実験専用の構築プール)"
        assert all(f"--own-pool {pf}" in " ".join(c) for c in g["commands"].values())
        assert all("--team-file" not in " ".join(c) for c in g["commands"].values())
        assert g["env"][MY_TEAM_DISABLED_ENV] == "1" and g["env"][C.TRANSITION_SENSITIVITY_ENV] == "0"
        assert g["own_check"]["required"] is True and any(doc["sha16"] in n for n in g["notes"])
        assert P.preflight_errors(g) == []
        assert any("期待 sha16" in w for w in P.preflight_warnings(g))                   # 期待 sha16 が未固定なら注意
        assert any("--prelim-dir" in w for w in P.preflight_warnings(g))
        # 壊れたプール・指定なし・空
        bad = json.loads(json.dumps(doc))
        bad["teams"][1]["text"] = _TEAM_B + "\n- Return"
        pf.write_text(json.dumps(bad), encoding="utf-8")
        tb = P.pool_team_facts(pf, P1_SEED, [])
        assert any("不正" in e for e in P.preflight_errors(cond("general", _facts(team=tb), own_pool=pf)))
        none = P.pool_team_facts(None, P1_SEED, notes)
        assert any("指定されていない" in e for e in P.preflight_errors(cond("general", _facts(team=none))))
        # フォーマットの違い
        pf.write_text(json.dumps(dict(doc, conditions=dict(doc["conditions"], battle_format="gen9other"))), encoding="utf-8")
        assert P.pool_team_facts(pf, P1_SEED, [])["errors"]
    # 期待 sha16 を固定したら照合する
    orig = P.P1_SCORING_POOL_SHA16
    try:
        P.P1_SCORING_POOL_SHA16 = "0000000000000000"
        g2 = cond("general", _facts(team=dict(team)))
        assert any("期待" in e for e in P.preflight_errors(g2))
        P.P1_SCORING_POOL_SHA16 = team["sha16"]
        assert P.preflight_errors(cond("general", _facts(team=dict(team)))) == []
    finally:
        P.P1_SCORING_POOL_SHA16 = orig
    # general-pinned (旧固定プール)
    gp = cond("general-pinned", _facts(team=_ranked_team()))
    assert gp["team"]["source"] == "ranked" and gp["team"]["pinned"] is True and "旧固定プール上の比較" in gp["purpose"]
    assert any("旧固定プール上の比較" in n for n in gp["notes"]) and gp["env"][MY_TEAM_DISABLED_ENV] == "1"
    assert any("旧固定プール" in w for w in P.preflight_warnings(gp))
    from tools import usefulness_verdict as V
    assert "旧固定プール上の比較" in dict(V.condition_summary(gp))["自チーム"]
    assert "実験専用の構築プール" in dict(V.condition_summary(g))["自チーム"]
    # RL の比較の環境は感度を切らない (従来どおり)
    assert C.TRANSITION_SENSITIVITY_ENV not in P.child_environment("/pin", base={})
    print("test_usefulness_p1_pool_and_pinned OK")


def test_prelim_correspondence():
    """予備測定の出力 dir の対応の確認: 相手 id と自チームが揃えば理由なし、自チームの sha16 が食い違えば起動前検査 NG"""
    from tests.test_usefulness_verdict import SC_BASE, SC_TR, _scoring_conditions
    from tools import usefulness_p1 as P
    from tools import usefulness_verdict as V
    with tempfile.TemporaryDirectory() as d:
        out = Path(d)
        (out / "conditions.json").write_text(json.dumps(dict(_scoring_conditions(), own_check={"required": True})),
                                             encoding="utf-8")
        for c in (SC_BASE, SC_TR):
            (out / f"{V.cond_file(c)}.battles.jsonl").write_text("".join(json.dumps(r) + "\n" for r in _battles(50)),
                                                                 encoding="utf-8")
        ok = P.prelim_correspondence(out)
        assert ok["n"] == 50 and ok["reasons"] == [], ok
        (out / f"{V.cond_file(SC_TR)}.battles.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in _battles(50, sha=lambda i: "zz" if i == 1 else f"s{i}")), encoding="utf-8")
        ng = P.prelim_correspondence(out)
        assert ng["reasons"] and any("sha16" in r for r in ng["reasons"])
        from tests.test_usefulness_p1 import _cond
        c = dict(_cond(), prelim_check=ng)
        assert any("予備測定" in e for e in P.preflight_errors(c))
    assert P.prelim_correspondence(None) is None
    print("test_prelim_correspondence OK")


def test_pool_expected_sha16_fixed():
    """期待 sha16 を固定したとき: 一致するプールは起動前検査 OK (注意なし)、違うプールは NG"""
    import tempfile
    from pathlib import Path
    from tests.test_usefulness_p1 import _facts
    from tools import scoring_pool as SP
    from tools import usefulness_p1 as P
    from champions_agent.config import P1_SEED
    with tempfile.TemporaryDirectory() as d:
        doc = SP.pool_doc([_TEAM_A, _TEAM_B], ["ta", "tb"], snapshot_id=61, battle_format=C.TRAINING_BATTLE_FORMAT, seed=1,
                          n_requested=2, n_generated=2, excluded=[], db_path="/db")
        pf = Path(d) / "pool.json"
        pf.write_text(json.dumps(doc), encoding="utf-8")
        old = P.P1_SCORING_POOL_SHA16
        try:
            for expected, ok in ((doc["sha16"], True), ("0000000000000000", False)):
                P.P1_SCORING_POOL_SHA16 = expected
                team = P.pool_team_facts(pf, P1_SEED, [])
                design = P.plan_scoring()
                g = P.build_conditions(design, _facts(team=team), 50, True, P1_SEED, 1, "split.json", 7200,
                                       str(Path(d) / "out"), {c: ["py"] for c in design["conditions"]}, {}, None,
                                       team_source="general")
                errs = P.preflight_errors(g)
                assert (errs == []) is ok, (expected, errs)
                if ok:
                    assert not any("期待 sha16" in w for w in P.preflight_warnings(g))
        finally:
            P.P1_SCORING_POOL_SHA16 = old
    print("test_pool_expected_sha16_fixed OK")


def main():
    test_unevaluated_interval_continue_and_fallback()
    test_eps_zero_unchanged()
    # 本体の config/my_team.json (ガブリアスの登録) に依存しない: 登録なしで評価する (2026-10-09: 本体の CI で登録の型と技の補完が
    # 入って区間が重なり opp_unevaluated_ambiguous になった。アサーションは変えない)
    with _Patch():
        test_unevaluated_through_engine_and_summary()
    test_sensitivity_toggle_same_ranking()
    test_sensitivity_budget_omitted_not_fallback()
    test_sensitivity_env()
    test_wipe_selfko_user_loses()
    test_wipe_recoil_user_wins()
    test_wipe_hp_cost_user_wins_on_hit()
    test_wipe_item_recoil_user_wins()
    test_showdown_version_and_rules_recorded()
    test_mega_registration_lookup_reproduces_review_table()
    test_mega_registration_sim_path_and_moves()
    test_mega_registration_legacy_change()
    test_reference_set_pure()
    test_verdict_own_correspondence()
    test_pool_doc_and_check()
    test_own_pool_teambuilder_correspondence()
    # config で P1_SCORING_POOL_SHA16 を固定 (2026-10-09、c7a32f8897d71375) したので、模擬のプールの検査は「期待が未固定」の
    # 条件で行う (このテストはその分岐を確かめている)。固定時の挙動は test_pool_expected_sha16_fixed で確かめる
    _old_sha = P1.P1_SCORING_POOL_SHA16
    P1.P1_SCORING_POOL_SHA16 = None
    try:
        test_usefulness_p1_pool_and_pinned()
    finally:
        P1.P1_SCORING_POOL_SHA16 = _old_sha
    test_pool_expected_sha16_fixed()
    test_prelim_correspondence()
    print("ALL OK")


if __name__ == "__main__":
    main()
