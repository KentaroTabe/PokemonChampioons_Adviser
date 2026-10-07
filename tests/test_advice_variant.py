"""影の計算 (advice_variant) と RL 加点の重みの引数化のテスト (2026-10-07、計画 §2 の隔離条件 (a)〜(d))。

    python -m tests.test_advice_variant

- 重みは引数で渡り、環境変数は起動時の既定値の供給源としてだけ読まれる (a)
- 採点の 2 段分割: 既定の重みで、改修前の加点以降 (下の _reference_tail は f8076061 の evaluate の該当部分をそのまま写したもの)
  と同じ出力になる (順位・点・理由)。共通部分は書き換えない (b)(c)
- ワーカー: 状態の更新と期限による待機中の破棄 / 期限による実行中の中止 / 待ち行列の上限 / 本番の状態を書き換えない (d)
- battle_logger.on_advice_variant と tools.advice_trace の集計
RL のモデルは使わない (rl_bridge.policy_hint を固定の分布に差し替える)。CI で回る。
"""
from __future__ import annotations

import copy
import json
import os
import random
import tempfile
import threading
from pathlib import Path

import advisor.engine as engine
from advisor.engine import (ACT_BEFORE_KO_DISCOUNT, PIVOT_MOVE_IDS, PIVOT_OVER_SWITCH_BONUS, evaluate,
                            evaluate_common, finish_evaluation, rescore_actions)
from advisor import shadow as S


# ------------------------------------------------------------------ 改修前の加点以降 (f8076061 の evaluate から写した参照実装)
def _reference_tail(actions: list, rl_hint, move_type_mult: dict, rl_blend: float) -> list:
    """改修前 (advisor/engine.py f8076061 の 786〜862 行) の RL 加点 → KO 前割引 → 交代技補正 を、重みを
    os.environ から読む代わりに引数で受けるようにしただけの写し。actions をその場で書き換える (改修前と同じ)"""
    try:
        if rl_hint and rl_hint.get("top"):
            probs = {}
            for t in rl_hint["top"]:
                base_label = t["label"].replace("+メガ", "")
                probs[base_label] = max(probs.get(base_label, 0.0), t["prob"])
                if t["label"] != base_label:
                    probs[t["label"]] = max(probs.get(t["label"], 0.0), t["prob"])
            RL_BLEND = float(rl_blend)
            for a in actions:
                if a["score"] <= -90:
                    continue
                key = a["name"] if a["kind"] == "move" else f"交代:{a['name']}"
                p = probs.get(key)
                if p is not None:
                    a["score"] = round(a["score"] + RL_BLEND * p, 1)
                    a["reason"] = (a.get("reason") or "") + f" / RL{p:.0%}"
            actions.sort(key=lambda a: -a["score"])
    except Exception:
        pass
    ko_before_act_ids = set()
    for a in actions:
        if a.pop("act_discount", False):
            a["score"] = round(a["score"] * ACT_BEFORE_KO_DISCOUNT, 1)
            ko_before_act_ids.add(a.get("id"))
    actions.sort(key=lambda a: -a["score"])
    try:
        if actions and actions[0]["kind"] == "switch":
            best_switch = actions[0]
            for a in actions:
                if (a["kind"] == "move" and a["id"] in PIVOT_MOVE_IDS
                        and a["score"] > -90
                        and move_type_mult.get(a["id"], 0) > 0):
                    if a["id"] in ko_before_act_ids:
                        a["reason"] += (" / 先に倒される見込みなので交代技は"
                                        f"発動しない: 素の交代 ({best_switch['name']}) を優先")
                        continue
                    a["score"] = round(
                        best_switch["score"] + PIVOT_OVER_SWITCH_BONUS, 1)
                    a["reason"] += (f" / 交代するならまずこの技: ダメージを"
                                    f"入れつつ {best_switch['name']} に引ける")
            actions.sort(key=lambda a: -a["score"])
    except Exception:
        pass
    return actions


# ------------------------------------------------------------------ 局面
def _haz():
    return {"stealth_rock": False, "spikes": 0, "toxic_spikes": 0, "sticky_web": False}


def _scr():
    return {"reflect": False, "light_screen": False, "aurora_veil": False}


def _duraludon_state():
    """tests/test_advisor の test_evaluate_end_to_end と同じ局面 (ブリジュラス + ライチュウ vs リザードン)"""
    return {
        "field": {"weather": None, "terrain": None, "trick_room": False},
        "mega_used": {"player": False, "opponent": False},
        "player": {"active_index": 0, "tailwind": False, "hazards": _haz(), "screens": _scr(), "party": [
            {"species_id": "duraludon", "species_ja": "ブリジュラス", "types": ["ドラゴン", "はがね"], "hp_percent": 100.0,
             "hp_current": 197, "hp_max": 197, "status": None, "boosts": {}, "ability_id": "stamina", "item_id": "leftovers",
             "moves": [
                 {"name_ja": "りゅうのはどう", "move_id": "dragonpulse", "pp": 12, "max_pp": 12, "effectiveness": "neutral"},
                 {"name_ja": "エレクトロビーム", "move_id": "electroshot", "pp": 12, "max_pp": 12, "effectiveness": "super"},
                 {"name_ja": "はどうだん", "move_id": "aurasphere", "pp": 20, "max_pp": 20, "effectiveness": "resist"},
                 {"name_ja": "まもる", "move_id": "protect", "pp": 8, "max_pp": 8, "effectiveness": None},
             ], "revealed_moves": []},
            {"species_id": "raichu", "species_ja": "ライチュウ", "types": [], "hp_percent": 100.0, "hp_current": 137,
             "hp_max": 137, "status": None, "boosts": {}, "ability_id": None, "item_id": "megastone",
             "item_ja": "ライチュウナイトY", "moves": [], "revealed_moves": []},
        ]},
        "opponent": {"active_index": 0, "tailwind": False, "hazards": _haz(), "screens": _scr(), "party": [
            {"species_id": "charizard", "species_ja": "リザードン", "types": ["ほのお", "ひこう"], "hp_percent": 100.0,
             "hp_current": None, "hp_max": None, "status": None, "boosts": {}, "ability_id": None, "item_id": None,
             "moves": [], "revealed_moves": ["フレアドライブ"]},
        ]},
    }


def _pivot_state(spe_boost: int = 0):
    """tests/test_advisor の _pivot_state と同じ局面 (ハッサム + ガブリアス vs リザードン)。spe_boost で先手にできる"""
    return {
        "field": {"weather": None, "terrain": None, "trick_room": False},
        "mega_used": {"player": False, "opponent": False},
        "player": {"active_index": 0, "tailwind": False, "hazards": _haz(), "screens": _scr(), "party": [
            {"species_id": "scizor", "species_ja": "ハッサム", "types": ["むし", "はがね"], "hp_percent": 100.0,
             "hp_current": 145, "hp_max": 145, "status": None, "boosts": ({"spe": spe_boost} if spe_boost else {}),
             "ability_id": None, "item_id": None,
             "moves": [
                 {"name_ja": "とんぼがえり", "move_id": "uturn", "pp": 20, "max_pp": 20, "effectiveness": "resist"},
                 {"name_ja": "バレットパンチ", "move_id": "bulletpunch", "pp": 30, "max_pp": 30, "effectiveness": "resist"},
             ], "revealed_moves": []},
            {"species_id": "garchomp", "species_ja": "ガブリアス", "types": ["ドラゴン", "じめん"], "hp_percent": 100.0,
             "hp_current": 183, "hp_max": 183, "status": None, "boosts": {}, "ability_id": None, "item_id": None,
             "moves": [], "revealed_moves": []},
        ]},
        "opponent": {"active_index": 0, "tailwind": False, "hazards": _haz(), "screens": _scr(), "party": [
            {"species_id": "charizard", "species_ja": "リザードン", "types": ["ほのお", "ひこう"], "hp_percent": 100.0,
             "hp_current": None, "hp_max": None, "status": None, "boosts": {}, "ability_id": None, "item_id": None,
             "moves": [], "revealed_moves": ["フレアドライブ"]},
        ]},
    }


# 局面ごとの固定の RL 分布 (ラベルは rl_bridge の合法手の表記: 技名 / 技名+メガ / 交代:名前)
_FAKE_HINTS = {
    "duraludon": {"top": [{"label": "りゅうのはどう", "prob": 0.93, "kind": "move"},
                          {"label": "エレクトロビーム+メガ", "prob": 0.04, "kind": "move"},
                          {"label": "まもる", "prob": 0.02, "kind": "move"},
                          {"label": "交代:ライチュウ", "prob": 0.01, "kind": "switch"}],
                  "value": -3.2, "style": "balance"},
    "scizor": {"top": [{"label": "交代:ガブリアス", "prob": 0.7, "kind": "switch"},
                       {"label": "とんぼがえり", "prob": 0.2, "kind": "move"},
                       {"label": "バレットパンチ", "prob": 0.1, "kind": "move"}],
               "value": -10.0, "style": "balance"},
}


class _FakePolicy:
    """advisor.rl_bridge.policy_hint を固定の分布に差し替える (エンジンは関数内で import するので属性の差し替えで効く)"""

    def __enter__(self):
        import advisor.rl_bridge as rb
        self._rb, self._orig = rb, rb.policy_hint

        def fake(state, my_spe_actual=None):
            p = state["player"]["party"][state["player"]["active_index"]]
            hint = _FAKE_HINTS.get(p.get("species_id"))
            return copy.deepcopy(hint) if hint else None

        rb.policy_hint = fake
        return self

    def __exit__(self, *exc):
        self._rb.policy_hint = self._orig
        return False


def _fixtures():
    return [("duraludon", _duraludon_state()), ("pivot_slow", _pivot_state()), ("pivot_fast", _pivot_state(2))]


# ------------------------------------------------------------------ (a) 重みの引数化
def test_default_weight_from_env_once():
    """既定値の供給源: 環境変数があればその値、無い・空なら config、数値でなければ config (警告)。evaluate は env を読まない"""
    from champions_agent.config import RL_BLEND_WEIGHT_DEFAULT
    assert RL_BLEND_WEIGHT_DEFAULT == 25.0
    prev = os.environ.get("RL_BLEND_WEIGHT")
    try:
        os.environ["RL_BLEND_WEIGHT"] = "0"
        assert engine._rl_blend_default_from_env() == 0.0
        os.environ["RL_BLEND_WEIGHT"] = "7.5"
        assert engine._rl_blend_default_from_env() == 7.5
        os.environ["RL_BLEND_WEIGHT"] = ""
        assert engine._rl_blend_default_from_env() == 25.0
        os.environ["RL_BLEND_WEIGHT"] = "abc"
        assert engine._rl_blend_default_from_env() == 25.0
        os.environ.pop("RL_BLEND_WEIGHT")
        assert engine._rl_blend_default_from_env() == 25.0
        # 評価のたびには読まない: 環境変数を書き換えても evaluate(既定) の結果は import 時の既定値のまま
        with _FakePolicy():
            st = _pivot_state(2)
            base = evaluate(copy.deepcopy(st))
            os.environ["RL_BLEND_WEIGHT"] = "0" if engine.RL_BLEND_DEFAULT != 0 else "25"
            again = evaluate(copy.deepcopy(st))
        assert [(a["id"], a["score"]) for a in base["actions"]] == [(a["id"], a["score"]) for a in again["actions"]]
    finally:
        if prev is None:
            os.environ.pop("RL_BLEND_WEIGHT", None)
        else:
            os.environ["RL_BLEND_WEIGHT"] = prev
    print("test_default_weight_from_env_once OK")


def test_check_advisor_player_rl_blend_args():
    """check_advisor_player: --rl-blend の値 / --no-rl-blend は 0 の別名 / 無指定は既定値。環境変数を書かない"""
    from tools.check_advisor_player import resolve_rl_blend
    assert resolve_rl_blend(None, False, 25.0) == 25.0
    assert resolve_rl_blend(5.0, False, 25.0) == 5.0
    assert resolve_rl_blend(None, True, 25.0) == 0.0
    assert resolve_rl_blend(0.0, True, 25.0) == 0.0
    try:
        resolve_rl_blend(5.0, True, 25.0)
        raise AssertionError("矛盾する指定を通した")
    except SystemExit:
        pass
    src = (Path(__file__).resolve().parent.parent / "tools" / "check_advisor_player.py").read_text(encoding="utf-8")
    assert 'os.environ["RL_BLEND_WEIGHT"]' not in src and "os.environ['RL_BLEND_WEIGHT']" not in src
    assert 'environ.get("RL_BLEND_WEIGHT"' not in src
    print("test_check_advisor_player_rl_blend_args OK")


# ------------------------------------------------------------------ (b)(c) 2 段分割と前後一致
def _synthetic_actions(rng: random.Random) -> tuple:
    """加点前の行動の乱択 (技・交代・選べない行動・行動前に倒される技・交代技・同点) と RL 分布"""
    names = ["A", "B", "C", "D"]
    acts = []
    for i, n in enumerate(names):
        a = {"kind": "move", "id": n.lower(), "name": n, "score": round(rng.choice([rng.uniform(-10, 120), 40.0]), 1),
             "reason": f"r{n}"}
        if rng.random() < 0.3:
            a["act_discount"] = True
        acts.append(a)
    if rng.random() < 0.7:
        acts.append({"kind": "move", "id": rng.choice(sorted(PIVOT_MOVE_IDS)), "name": "ピボット",
                     "score": round(rng.uniform(0, 60), 1), "reason": "rp", **({"act_discount": True} if rng.random() < 0.4 else {})})
    acts.append({"kind": "move", "id": "sealed", "name": "封じ", "score": -99.0, "reason": "かなしばり"})
    for n in ("X", "Y"):
        acts.append({"kind": "switch", "id": n.lower(), "name": n, "score": round(rng.uniform(0, 90), 1), "reason": f"s{n}",
                     "counter": 1.0, "incoming": 2.0, "hazard": 0.0})
    acts.sort(key=lambda a: -a["score"])
    labels = ["A", "B+メガ", "B", "C", "ピボット", "交代:X", "交代:Y", "封じ"]
    top = [{"label": l, "prob": round(rng.random(), 3), "kind": "x"} for l in rng.sample(labels, 4)]
    hint = {"top": top, "value": 0.0, "style": "balance"} if rng.random() < 0.9 else None
    mult = {a["id"]: rng.choice([0.0, 0.5, 1.0, 2.0]) for a in acts if a["kind"] == "move"}
    return acts, hint, mult


def test_rescore_matches_reference_synthetic():
    """加点以降の純粋関数は、改修前の写しと同じ結果 (順位・点・理由) を返し、入力を書き換えない (重み 0 / 5 / 25、乱択 400 通り)"""
    rng = random.Random(20261007)
    n_changed = 0
    for _ in range(400):
        acts, hint, mult = _synthetic_actions(rng)
        snap = copy.deepcopy((acts, hint, mult))
        tops = {}
        for w in (0.0, 5.0, 25.0):
            ref = _reference_tail(copy.deepcopy(acts), copy.deepcopy(hint), dict(mult), w)
            got = rescore_actions(acts, hint, mult, w)
            assert got == ref, (w, got, ref)
            tops[w] = (got[0]["kind"], got[0]["id"])
        assert (acts, hint, mult) == snap, "入力を書き換えた"
        n_changed += int(tops[0.0] != tops[25.0])
    assert n_changed > 0, "重みで第一候補が変わる例が 1 つも無い (乱択の作りの誤り)"
    print(f"test_rescore_matches_reference_synthetic OK (第一候補が 0 と 25 で違う {n_changed}/400)")


def test_evaluate_split_matches_reference_on_fixtures():
    """既存の局面 (test_advisor と同じ) で、evaluate の出力 = 共通部分 + 改修前の加点以降の写し。既定の重みは引数なしと同じ。
    finish_evaluation は共通部分を書き換えない (同じ共通部分から何度でも再計算できる)"""
    from vision.normalize import NameResolver
    res = NameResolver()
    with _FakePolicy():
        for name, st in _fixtures():
            common = evaluate_common(copy.deepcopy(st), res)
            assert common["ok"] and common["rl_hint"], name
            frozen = copy.deepcopy(common)
            for w in (0.0, 5.0, 25.0):
                adv = evaluate(copy.deepcopy(st), res, rl_blend_weight=w)
                ref = _reference_tail(copy.deepcopy(common["actions"]), common["rl_hint"], common["move_type_mult"], w)
                assert adv["actions"] == ref, (name, w, adv["actions"], ref)
                assert adv["best"] == ref[0]
                fin = finish_evaluation(common, w)
                assert fin["actions"] == ref and fin["speed_note"] == adv["speed_note"], (name, w)
                assert fin["sacrifice_note"] == adv["sacrifice_note"], (name, w)
            assert common == frozen, f"{name}: finish_evaluation が共通部分を書き換えた"
            # 既定 (引数なし) は RL_BLEND_DEFAULT を渡したのと同じ
            d0 = evaluate(copy.deepcopy(st), res)
            d1 = evaluate(copy.deepcopy(st), res, rl_blend_weight=engine.RL_BLEND_DEFAULT)
            assert d0["actions"] == d1["actions"], name
            # 返す辞書の鍵は改修前と同じ (共通部分の内部の欄を漏らさない)
            assert set(d0) == {"ok", "actions", "threats", "speed_note", "mega_note", "opp_inference", "opp_moves_note",
                               "opp_spread_note", "gtheory", "endgame_note", "sacrifice_note", "rl_hint", "best"}, set(d0)
    print("test_evaluate_split_matches_reference_on_fixtures OK")


def test_weight_changes_best_on_fixture():
    """重みで第一候補が変わる局面がある (影の計算に比べる意味がある): 先手のハッサムで RL が交代 70% のとき、
    重み 0 は交代技 (とんぼがえり、補正で交代 +2)、25 は交代の点が上がり交代技も追随する — 少なくとも点は変わる"""
    from vision.normalize import NameResolver
    res = NameResolver()
    with _FakePolicy():
        common = evaluate_common(_pivot_state(2), res)
    a0 = finish_evaluation(common, 0.0)["actions"]
    a25 = finish_evaluation(common, 25.0)["actions"]
    s0 = {(a["kind"], a["id"]): a["score"] for a in a0}
    s25 = {(a["kind"], a["id"]): a["score"] for a in a25}
    sw = next(k for k in s0 if k[0] == "switch")
    assert s25[sw] > s0[sw], (a0, a25)
    print("test_weight_changes_best_on_fixture OK")


# ------------------------------------------------------------------ (d) ワーカー
class _Sink:
    def __init__(self):
        self.rows = []
        self.lock = threading.Lock()
        self.gate = None          # threading.Event: 最初の 1 行で止める (ワーカーを実行中に留める)
        self.gate_used = False

    def __call__(self, rec):
        with self.lock:
            self.rows.append(rec)
            gate = None if self.gate_used else self.gate
            self.gate_used = self.gate_used or gate is not None
        if gate is not None:
            gate.wait(5.0)


def _common_for_tests():
    acts = [{"kind": "move", "id": "a", "name": "A", "score": 50.0, "reason": "ra"},
            {"kind": "switch", "id": "x", "name": "X", "score": 45.0, "reason": "sx"},
            {"kind": "move", "id": "uturn", "name": "とんぼがえり", "score": 30.0, "reason": "ru"},
            {"kind": "move", "id": "sealed", "name": "封じ", "score": -99.0, "reason": "かなしばり"}]
    return {"ok": True, "actions": acts,
            "rl_hint": {"top": [{"label": "A", "prob": 0.1, "kind": "move"}, {"label": "交代:X", "prob": 0.8, "kind": "switch"}],
                        "value": 0.0, "style": "balance"},
            "move_type_mult": {"a": 1.0, "uturn": 1.0},
            "context": {"turn": 3, "threat_faces_me": False, "switch_only": False, "my_remaining": 3, "opp_remaining": 2}}


def test_worker_computes_variants_and_isolation():
    """計算できた行: 重み 0 / 5 / 25 の上位 3 と点差、基準 (25) からの第一候補の変化。本番の状態を書き換えない"""
    sink = _Sink()
    common = _common_for_tests()
    frozen = copy.deepcopy(common)
    env_before = dict(os.environ)
    consts_before = (engine.RL_BLEND_DEFAULT, engine.ACT_BEFORE_KO_DISCOUNT, engine.PIVOT_OVER_SWITCH_BONUS,
                     engine.SEARCH_BLEND)
    w = S.ShadowWorker(sink, weights=(0.0, 5.0, 25.0), base_weight=25.0, queue_max=2, deadline_sec=3.0)
    job = S.make_job(common, "A-0001", "sid1", shown_best={"kind": "move", "id": "a"})
    common["actions"][0]["score"] = 999.0     # 投入後に表示側が書き換えても snapshot は影響を受けない
    w.submit(job)
    assert w.wait_idle(5.0)
    w.stop()
    common["actions"][0]["score"] = 50.0
    assert common == frozen
    assert dict(os.environ) == env_before
    assert consts_before == (engine.RL_BLEND_DEFAULT, engine.ACT_BEFORE_KO_DISCOUNT, engine.PIVOT_OVER_SWITCH_BONUS,
                             engine.SEARCH_BLEND)
    assert len(sink.rows) == 1, sink.rows
    r = sink.rows[0]
    assert r["type"] == "advice_variant" and r["advice_id"] == "A-0001" and r["state_id"] == "sid1" and r["skipped"] is None
    assert [v["rl_blend"] for v in r["variants"]] == [0.0, 5.0, 25.0]
    v0, v25 = r["variants"][0], r["variants"][2]
    assert v0["top"][0]["id"] == "a" and len(v0["top"]) == 3 and len(v0["gaps"]) == 2
    # 25: 交代 45 + 20 = 65、A 50 + 2.5 = 52.5 → 交代が最善 → とんぼがえりが 交代 + 2 = 67 で 1 位 (交代技補正まで再計算)
    assert v25["top"][0]["id"] == "uturn" and v25["top"][0]["score"] == 67.0, v25
    assert v25["gaps"][0] == 2.0, v25
    assert r["base_best"] == "move:uturn" and r["best_changed"] and r["changed_weights"] == [0.0, 5.0], r
    assert r["shown_best"] == "move:a" and r["context"]["turn"] == 3 and r["rl_top"][1]["label"] == "交代:X"
    json.dumps(r, ensure_ascii=False)      # 行は JSON にできる
    assert w.stats["done"] == 1 and w.stats["changed"] == 1
    print("test_worker_computes_variants_and_isolation OK")


def test_worker_drops_pending_on_state_change_and_queue_full():
    """待機中の破棄: 次の助言の state_id が変わったら待機中の仕事を捨てる (state_changed)。同じ状態なら捨てない
    (次のフレームが来ただけでは捨てない)。上限を超えたら古い方を捨てる (queue_full)"""
    sink = _Sink()
    sink.gate = threading.Event()
    w = S.ShadowWorker(sink, weights=(0.0, 25.0), base_weight=25.0, queue_max=2, deadline_sec=60.0)
    c = _common_for_tests()
    w.submit(S.make_job(c, "id1", "s1"))          # 実行して sink で止まる
    for _ in range(200):
        if sink.rows:
            break
        threading.Event().wait(0.01)
    assert len(sink.rows) == 1
    w.submit(S.make_job(c, "id2", "s1"))          # 待機
    w.submit(S.make_job(c, "id3", "s1"))          # 同じ状態: 捨てない (待機 2 件)
    w.submit(S.make_job(c, "id4", "s1"))          # 上限 2 を超える: id2 を queue_full で捨てる
    w.submit(S.make_job(c, "id5", "s2"))          # 状態の更新: id3 / id4 を state_changed で捨てる
    sink.gate.set()
    assert w.wait_idle(5.0)
    w.stop()
    by_id = {r["advice_id"]: r for r in sink.rows}
    assert by_id["id1"]["skipped"] is None and by_id["id5"]["skipped"] is None
    assert by_id["id2"]["skipped"] == S.SKIP_DROPPED and by_id["id2"]["skip_reason"] == S.REASON_QUEUE_FULL
    for k in ("id3", "id4"):
        assert by_id[k]["skipped"] == S.SKIP_DROPPED and by_id[k]["skip_reason"] == S.REASON_STATE_CHANGED, by_id[k]
    assert w.stats[S.SKIP_DROPPED] == {S.REASON_QUEUE_FULL: 1, S.REASON_STATE_CHANGED: 2}, w.stats
    assert w.stats["done"] == 2
    print("test_worker_drops_pending_on_state_change_and_queue_full OK")


class _ManualClock:
    def __init__(self, t=0.0, step=0.0):
        self.t, self.step = t, step
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            now = self.t
            self.t += self.step
            return now


def test_worker_expired_pending_and_aborted_running():
    """期限: 待機中に期限を過ぎた仕事は捨てる (dropped_pending / expired)。実行中に期限を越えたら重みの区切りで止める
    (aborted_running / deadline)"""
    # 待機中の期限切れ: 1 件目で止めている間に時計を進める
    sink = _Sink()
    sink.gate = threading.Event()
    clk = _ManualClock()
    w = S.ShadowWorker(sink, weights=(0.0, 25.0), base_weight=25.0, queue_max=2, deadline_sec=3.0, clock=clk)
    c = _common_for_tests()
    w.submit(S.make_job(c, "e1", "s1", clock=clk))
    for _ in range(200):
        if sink.rows:
            break
        threading.Event().wait(0.01)
    w.submit(S.make_job(c, "e2", "s1", clock=clk))     # t=0 で生成
    clk.t = 10.0                                         # 期限 3 秒を過ぎる
    sink.gate.set()
    assert w.wait_idle(5.0)
    w.stop()
    by_id = {r["advice_id"]: r for r in sink.rows}
    assert by_id["e2"]["skipped"] == S.SKIP_DROPPED and by_id["e2"]["skip_reason"] == S.REASON_EXPIRED, by_id["e2"]
    assert by_id["e2"]["age_ms"] >= 3000

    # 実行中の中止: 時計は問い合わせのたびに 1 秒進む (0, 1, 2, ...)。生成 0 / 投入 0 / 開始 1 / 区切り 2・3 (≤ 3 で
    # 重み 0 と 5 を計算) / 区切り 4 (> 3) で重み 25 の前に止める
    sink2 = _Sink()
    clk2 = _ManualClock(step=1.0)
    w2 = S.ShadowWorker(sink2, weights=(0.0, 5.0, 25.0), base_weight=25.0, queue_max=2, deadline_sec=3.0, clock=clk2)
    w2.submit(S.make_job(c, "r1", "s1", clock=lambda: 0.0))
    assert w2.wait_idle(5.0)
    w2.stop()
    assert len(sink2.rows) == 1
    r = sink2.rows[0]
    assert r["skipped"] == S.SKIP_ABORTED and r["skip_reason"] == S.REASON_DEADLINE, r
    assert w2.stats[S.SKIP_ABORTED] == {S.REASON_DEADLINE: 1} and w2.stats["done"] == 0
    # compute_variants 単体: 1 本目を計算した後の区切りで止まる
    calls = iter([False, True, True])
    vs, aborted = S.compute_variants(S.snapshot_inputs(c), (0.0, 5.0, 25.0), 3, lambda: next(calls))
    assert aborted and len(vs) == 1 and vs[0]["rl_blend"] == 0.0
    print("test_worker_expired_pending_and_aborted_running OK")


def test_shadow_off_by_default():
    """既定 OFF。Advisor.advise は既定では共通部分を保持しない"""
    from champions_agent.config import (SHADOW_DEADLINE_SEC, SHADOW_QUEUE_MAX, SHADOW_RL_BLEND_WEIGHTS,
                                        SHADOW_VARIANTS_ENABLED)
    assert SHADOW_VARIANTS_ENABLED is False
    assert tuple(SHADOW_RL_BLEND_WEIGHTS) == (0.0, 5.0, 25.0) and SHADOW_QUEUE_MAX == 2 and SHADOW_DEADLINE_SEC == 3.0
    from advisor.service import Advisor
    with _FakePolicy():
        adv = Advisor()
        r1 = adv.advise(_pivot_state(2))
        assert adv.last_common is None
        r2 = adv.advise(_pivot_state(2), keep_common=True)
        assert adv.last_common is not None and adv.last_common["ok"]
    assert r1["actions"] == r2["actions"]
    print("test_shadow_off_by_default OK")


# ------------------------------------------------------------------ 記録と集計
def test_logger_writes_variant_to_advice_file():
    """battle_logger.on_advice_variant: 助言を書いた対戦のファイルに advice_variant 行を足す。知らない advice_id は書かない"""
    from battle_logger import BattleLogger
    with tempfile.TemporaryDirectory() as td:
        bl = BattleLogger(log_dir=Path(td))
        adv = {"ok": True, "actions": [], "best": None}
        aid = bl.on_advice(adv, "battle", None)
        bl.on_advice_variant({"type": "advice_variant", "advice_id": aid, "skipped": None, "variants": []})
        bl.on_advice_variant({"type": "advice_variant", "advice_id": "nope-0001", "skipped": None})
        files = list(Path(td).glob("battle_*.jsonl"))
        assert len(files) == 1
        recs = [json.loads(l) for l in files[0].read_text(encoding="utf-8").splitlines() if l.strip()]
        vs = [r for r in recs if r.get("type") == "advice_variant"]
        assert len(vs) == 1 and vs[0]["advice_id"] == aid and "t" in vs[0], vs
    print("test_logger_writes_variant_to_advice_file OK")


def test_advice_trace_variant_summary():
    """advice_trace: 第一候補が重みで変わった回数 (重み別・局面別・残り体数別) とスキップの内訳"""
    from tools.advice_trace import summarize_variants, variant_phase, format_variants
    c = _common_for_tests()
    job = S.make_job(c, "v1", "s1")
    vs, _ = S.compute_variants(job.inputs, (0.0, 5.0, 25.0), 3)
    rec_changed = S.variant_record(job, vs, 25.0, (0.0, 5.0, 25.0), 1.0, 0.1)
    c2 = copy.deepcopy(c)
    c2["rl_hint"] = None
    c2["context"] = {"turn": 5, "threat_faces_me": True, "my_remaining": 1, "opp_remaining": 1}
    job2 = S.make_job(c2, "v2", "s2")
    vs2, _ = S.compute_variants(job2.inputs, (0.0, 5.0, 25.0), 3)
    rec_same = S.variant_record(job2, vs2, 25.0, (0.0, 5.0, 25.0), 1.0, 0.1)
    recs = [rec_changed, rec_same,
            S.skip_record(job, S.SKIP_DROPPED, S.REASON_STATE_CHANGED, 10.0),
            S.skip_record(job, S.SKIP_DROPPED, S.REASON_STATE_CHANGED, 10.0),
            S.skip_record(job2, S.SKIP_ABORTED, S.REASON_DEADLINE, 3100.0),
            {"type": "advice", "advice_id": "v1"}]
    s = summarize_variants(recs)
    assert s["n_rows"] == 5 and s["n_computed"] == 2 and s["n_changed"] == 1, s
    assert s["changed_by_weight"]["0"] == {"n": 2, "changed": 1, "rate": 0.5}, s["changed_by_weight"]
    assert s["by_phase"]["攻め"] == {"n": 1, "changed": 1} and s["by_phase"]["受け"] == {"n": 1, "changed": 0}, s["by_phase"]
    assert s["by_layer"]["3v2"]["changed"] == 1 and s["by_layer"]["1v1"]["changed"] == 0
    assert s["skipped"]["dropped_pending"] == {"state_changed": 2} and s["skipped"]["aborted_running"] == {"deadline": 1}
    assert variant_phase({"switch_only": True, "threat_faces_me": True}) == "交代先選択"
    assert variant_phase(None) == "不明"
    assert "第一候補が重みで変わった 1" in format_variants(s)
    print("test_advice_trace_variant_summary OK")


def test_load_check_pure_parts():
    """負荷確認の道具 (tools/shadow_load_check) の純粋な部分: 分位点、期限超過率、スキップの内訳、フレームの選び方、
    状態の鍵 (server._advice_key と同じ)"""
    from tools.shadow_load_check import advice_key, percentiles, run_summary, select_frame_paths
    p = percentiles([float(x) for x in range(1, 101)])
    assert p == {"n": 100, "p50": 51.0, "p95": 95.0, "max": 100.0}, p
    assert percentiles([])["p95"] is None
    stats = {"submitted": 10, "done": 6, "changed": 2,
             "dropped_pending": {"state_changed": 2, "expired": 1}, "aborted_running": {"deadline": 1}}
    s = run_summary([100.0, 200.0, 3500.0, 50.0], [10.0], [5.0, 6.0], 40, 3, 3.0, stats, 10)
    assert s["over_deadline"] == 1 and s["over_deadline_rate"] == 0.25, s
    assert s["shadow"]["n_skipped"] == 4 and s["shadow"]["deadline_miss_rate"] == 0.2, s["shadow"]
    assert "shadow" not in run_summary([], [], [], 0, 0, 3.0)
    with tempfile.TemporaryDirectory() as td:
        for name in ("frame_100.png", "frame_300.png", "sel_200.png", "frame_x.png", "frame_50.png"):
            (Path(td) / name).write_bytes(b"")
        got = [q.name for q in select_frame_paths(Path(td), 60, None, ("frame",))]
        assert got == ["frame_100.png", "frame_300.png"], got
        got = [q.name for q in select_frame_paths(Path(td), None, None, ("frame", "sel"), limit=3)]
        assert got == ["frame_50.png", "frame_100.png", "sel_200.png"], got
    st = _pivot_state()
    st["scene"] = "command"
    assert advice_key(st) and advice_key({}) == ""
    from tools.shadow_load_check import build_sequence
    assert build_sequence(2, 3) == [(0, None)] * 3 + [(1, None)] * 3
    assert build_sequence(2, 2, 3) == [(0, 0), (1, 0), (0, 1), (1, 1), (0, 2), (1, 2)]
    print("test_load_check_pure_parts OK")


def main():
    test_default_weight_from_env_once()
    test_check_advisor_player_rl_blend_args()
    test_rescore_matches_reference_synthetic()
    test_evaluate_split_matches_reference_on_fixtures()
    test_weight_changes_best_on_fixture()
    test_worker_computes_variants_and_isolation()
    test_worker_drops_pending_on_state_change_and_queue_full()
    test_worker_expired_pending_and_aborted_running()
    test_shadow_off_by_default()
    test_logger_writes_variant_to_advice_file()
    test_advice_trace_variant_summary()
    test_load_check_pure_parts()
    print("ALL OK")


if __name__ == "__main__":
    main()
