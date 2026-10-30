"""型推定 (advisor/ev_infer) の追加修正 3 件の検証 (2026-10-31 レビューの再現、docs/KNOWN_ISSUES.md E-ev-1 / E-ev-2)。

- (a) 版を明示した DB にテーブルが無い (usage_snapshot だけ) と InputsPinError。明示が無ければ従来どおり空
- (b) 持ち物の喪失・消費で実効素早さの範囲を捨てる (スカーフ喪失後の推定値が残った仮説の範囲 134〜169 に入る)。初判明 (変更なし) では捨てない
- (c) 候補外の持ち物の初判明: 先手の観測 → 初判明 の重みが 初判明 → 同じ先手の観測 と一致する (252 振り 68.2% / 無振り 31.8%)
- (d) 実際の変更 (スカーフを渡し済み → トリックで たべのこし): 変更前の観測は変更前の持ち物に帰属し配分の事後を保つ。範囲は捨てる。
  以後の観測は新しい持ち物で計算する
- (e) 評価し直せない観測 (記録の上限で捨てた分 / 評価し直しを切ったとき) の重みは流用しない (事前の重みのまま)
- 純粋な関数 (item_reveal_kind / speed_range_stale / prior_item_hyps / switch_item_hyps / replay_observations / trim_obs_log)

使い方: python -m tests.test_ev_infer_reeval  (使用率 DB は一時ファイルに作る。本体の DB は読まない)
"""
from __future__ import annotations

import math
import sqlite3
import tempfile
from pathlib import Path

import advisor.data_pin as DP
import advisor.ev_infer as EI
from advisor.damage import effective_speed
from advisor.data_pin import InputsPinError
from advisor.ev_infer import (SpreadEstimator, SpreadTracker, item_reveal_kind, prior_item_hyps, replay_observations,
                              speed_range_stale, switch_item_hyps, trim_obs_log)
from champions_agent import config as C
from tests.test_ev_infer_item_pin import _Patch, _no_pin_env, _state


def _review_hyps(*_):
    """レビュワーの再現と同じ仮説: ようき 無振り (0.7) / 252 振り (0.3) × スカーフ / タスキ (0.5 ずつ)"""
    return [{"nature": "jolly", "evs": {"spe": spe}, "item": item, "logw": math.log(ps * .5)}
            for spe, ps in ((0, .7), (252, .3)) for item in ("choicescarf", "focussash")]


def _patched():
    return _Patch((SpreadEstimator, "_build_hypotheses", _review_hyps),
                  (SpreadEstimator, "_query_item_share", staticmethod(lambda sid, iid: None)))


def _norm(est) -> dict:
    """{(性格, 努力値, 持ち物): 正規化した重み}"""
    mx = max(h["logw"] for h in est.hyps)
    ws = [math.exp(h["logw"] - mx) for h in est.hyps]
    tot = sum(ws)
    return {(h["nature"], tuple(sorted(h["evs"].items())), h["item"]): w / tot for h, w in zip(est.hyps, ws)}


def _spread_post(est) -> dict:
    """配分ごとの事後 (持ち物で合計)"""
    out: dict = {}
    for (n, e, _i), w in _norm(est).items():
        out[(n, e)] = out.get((n, e), 0.0) + w
    return out


def _close(a: dict, b: dict, tol=1e-9) -> bool:
    return a.keys() == b.keys() and all(abs(a[k] - b[k]) < tol for k in a)


# ---------------------------------------------------------------- 純粋な関数
def test_pure_helpers():
    assert item_reveal_kind(None, False, "leftovers") == "first"
    assert item_reveal_kind("leftovers", False, "Left-overs") == "first"         # 同じ物 (表記ゆれ) は変更ではない
    assert item_reveal_kind("choicescarf", False, "leftovers") == "change"
    assert item_reveal_kind(None, True, "leftovers") == "change"                  # 持ち物なし (物は不明) の後に判明
    assert item_reveal_kind(None, False, None) is None
    assert speed_range_stale(True, False, False) and speed_range_stale(False, False, True)
    assert speed_range_stale(False, True, False) and not speed_range_stale(False, True, True)
    assert not speed_range_stale(False, False, False)
    hyps = _review_hyps()
    snap = [dict(h) for h in hyps]
    pri = prior_item_hyps(hyps, "Left-overs", 0.01)
    assert [(h["evs"]["spe"], h["item"]) for h in pri] == [(0, "leftovers"), (252, "leftovers")]
    assert abs(pri[0]["logw"] - math.log(0.7 * 0.01)) < 1e-9 and abs(pri[1]["logw"] - math.log(0.3 * 0.01)) < 1e-9
    assert prior_item_hyps([], "leftovers", 0.1) == [] and prior_item_hyps(hyps, None, 0.1) == []
    post = [dict(h) for h in hyps]
    post[0]["logw"] += math.log(0.2)                                             # 観測で 無振り×スカーフ を下げた事後
    post_snap = [dict(h) for h in post]
    sw = switch_item_hyps(post, "leftovers")
    assert [h["item"] for h in sw] == ["leftovers", "leftovers"]
    assert abs(sw[0]["logw"] - math.log(0.7 * .5 * 0.2 + 0.7 * .5)) < 1e-9 and abs(sw[1]["logw"] - math.log(0.3)) < 1e-9
    assert sw[0]["evs"] is not post[0]["evs"]
    assert [dict(h) for h in hyps] == snap and [dict(h) for h in post] == post_snap   # 元の一覧は変えない
    seen = []
    n = replay_observations([], [{"kind": "speed"}, {"kind": "unknown"}, "x", {"kind": "item"}], lambda hs, e: seen.append(e["kind"]))
    assert n == 2 and seen == ["speed", "item"]
    assert trim_obs_log([1, 2, 3], 2) == [2, 3] and trim_obs_log([1, 2], 0) == [] and trim_obs_log([1, 2], 5) == [1, 2]
    print("test_pure_helpers OK")


# ---------------------------------------------------------------- (a)
def test_pinned_missing_tables_raise():
    _no_pin_env()
    with tempfile.TemporaryDirectory() as td:
        only_snap = Path(td) / "only_snapshot.sqlite3"
        c = sqlite3.connect(str(only_snap))
        c.execute("CREATE TABLE usage_snapshot (id INTEGER PRIMARY KEY)")
        c.execute("INSERT INTO usage_snapshot VALUES (1)")
        c.commit()
        c.close()
        no_snap_table = Path(td) / "no_snapshot_table.sqlite3"
        c = sqlite3.connect(str(no_snap_table))
        c.execute("CREATE TABLE spread_usage (x INTEGER)")
        c.commit()
        c.close()
        try:
            for kw in ({"usage_db": only_snap, "usage_snapshot": 1}, {"usage_db": only_snap},
                       {"usage_db": no_snap_table, "usage_snapshot": 1}):
                DP.set_override(**kw)
                for fn in (lambda: SpreadEstimator("garchomp"),
                           lambda: SpreadEstimator._query_item_share("garchomp", "leftovers"),
                           lambda: SpreadTracker().on_frame(_state({}, scene="selection"), []),
                           lambda: SpreadTracker().hypotheses_for("garchomp", 3)):
                    try:
                        fn()
                    except InputsPinError:
                        pass
                    else:
                        raise AssertionError(f"テーブル欠落で InputsPinError にならない: {kw}")
        finally:
            DP.clear_override()
        # 明示が無いとき: 従来どおり空 (止めない)
        with _Patch((EI, "DB_PATH", only_snap)):
            assert SpreadEstimator("garchomp").hyps == []
            assert SpreadEstimator._query_item_share("garchomp", "leftovers") is None
    print("test_pinned_missing_tables_raise OK")


# ---------------------------------------------------------------- (b)
def test_speed_range_dropped_on_loss_and_consume():
    _no_pin_env()
    with _patched():
        # スカーフ喪失 (レビュワーの再現): 喪失後は範囲を捨て、推定値は残った仮説の実効素早さの範囲に入る
        tr = SpreadTracker()
        tr.on_frame(_state({"item_id": "choicescarf"}), [])
        e = tr.estimator("garchomp")
        e.observe_speed(True, 180, {})
        assert e.spe_lower == 180 and e.speed_estimate({})["est"] == 201
        tr.on_frame(_state({"item_id": None, "item_removed": True}), ["knockoff_opponent_choicescarf"])
        assert e.item_gone and e.spe_lower is None and e.spe_upper is None
        raw = sorted(effective_speed(e._view(h, {})) for h in e.hyps)
        assert raw == [134, 169], raw
        sp = e.speed_estimate({})
        assert raw[0] <= sp["est"] <= raw[-1] and sp["lo"] is None and sp["hi"] is None, sp
        assert e.best()["spe_lower"] is None and "実効S" not in e.best()["summary"]
        # 同じ state を続けても (持ち物なしのまま) 次の観測の範囲は残る
        e.observe_speed(False, 150, {})
        tr.on_frame(_state({"item_id": None, "item_removed": True}), [])
        assert e.spe_upper == 150
        # 消費 (タスキ): 消費した後は範囲を捨てる
        tr2 = SpreadTracker()
        tr2.on_frame(_state({"item_id": "focussash"}), [])
        e2 = tr2.estimator("garchomp")
        e2.observe_speed(False, 140, {})
        assert e2.spe_upper == 140
        tr2.on_frame(_state({"item_id": "focussash", "item_consumed": True}), [])
        assert e2.item_gone and e2.spe_upper is None
        # 初判明 (観測の間ずっとその持ち物) では範囲を捨てない
        tr3 = SpreadTracker()
        e3 = tr3.estimator("garchomp")
        e3.observe_speed(True, 180, {})
        tr3.on_frame(_state({"item_id": "choicescarf"}), [])
        assert e3.spe_lower == 180 and not e3.item_gone
    print("test_speed_range_dropped_on_loss_and_consume OK")


# ---------------------------------------------------------------- (c)
def test_first_reveal_outside_support_reevaluates():
    _no_pin_env()
    with _patched():
        unknown = SpreadEstimator("garchomp")
        unknown.observe_speed(True, 160, {})
        unknown.observe_item("leftovers", add_missing=True)
        known = SpreadEstimator("garchomp")
        known.observe_item("leftovers", add_missing=True)
        known.observe_speed(True, 160, {})
        a, b = _norm(unknown), _norm(known)
        assert _close(a, b), (a, b)
        w252 = a[("jolly", (("spe", 252),), "leftovers")]
        w0 = a[("jolly", (("spe", 0),), "leftovers")]
        assert round(w252, 3) == 0.682 and round(w0, 3) == 0.318, (w252, w0)
        assert unknown.n_obs == known.n_obs == 2
        assert unknown.spe_lower == 160                                           # 初判明では範囲は残る
        # トラッカー経由 (_item_passed に無い = 初判明) でも同じ
        tr = SpreadTracker()
        e = tr.estimator("garchomp")
        e.observe_speed(True, 160, {})
        tr.on_frame(_state({"item_id": "leftovers"}), [])
        assert _close(_norm(e), b)
        # ダメージ・こだわりの固定・持ち物の絞り込みが混ざっても順序に依らない (記録の時点の持ち物なしも保つ)
        from advisor.damage import MonView
        from advisor.dex import get_dex
        sp = get_dex().species("mimikyu")
        atk = MonView(species_id="mimikyu", types=sp["types"], base=sp["baseStats"], ev={"atk": 252}, nature={"atk": 1.1})
        u2, k2 = SpreadEstimator("garchomp"), SpreadEstimator("garchomp")
        u2.observe_speed(False, 150, {"boosts": {}})
        u2.observe_damage(atk, True, "playrough", 70.0, {"boosts": {}})
        u2.observe_choice_lock()
        u2.observe_item("leftovers", add_missing=True)
        k2.observe_item("leftovers", add_missing=True)
        k2.observe_speed(False, 150, {"boosts": {}})
        k2.observe_damage(atk, True, "playrough", 70.0, {"boosts": {}})
        k2.observe_choice_lock()
        assert _close(_norm(u2), _norm(k2)), (_norm(u2), _norm(k2))
        # 候補にある持ち物の初判明は従来どおり (その仮説だけ残す)
        c = SpreadEstimator("garchomp")
        c.observe_speed(True, 160, {})
        before = {k: v for k, v in _norm(c).items() if k[2] == "focussash"}
        c.observe_item("focussash", add_missing=True)
        tot = sum(before.values())
        assert _close(_norm(c), {k: v / tot for k, v in before.items()})
    print("test_first_reveal_outside_support_reevaluates OK")


# ---------------------------------------------------------------- (d)
def test_real_change_keeps_spread_posterior():
    _no_pin_env()
    with _patched():
        tr = SpreadTracker()
        tr.on_frame(_state({"item_id": "choicescarf"}), [])
        e = tr.estimator("garchomp")
        e.observe_speed(False, 230, {})                     # スカーフで 230 より遅い: 無振り (201) は合い、252 振り (253) は合わない
        post_before = _spread_post(e)
        assert post_before[("jolly", (("spe", 0),))] > 0.9
        assert e.spe_upper == 230
        # トリックで たべのこし に変わった (渡し済みの持ち物と違う = 実際の変更)
        tr.on_frame(_state({"item_id": "leftovers"}), [])
        assert {h["item"] for h in e.hyps} == {"leftovers"} and len(e.hyps) == 2
        assert _close(_spread_post(e), post_before)          # 変更前の観測は変更前の持ち物 (スカーフ) で評価したまま
        assert e.spe_lower is None and e.spe_upper is None and e.n_obs == 3
        # 以後の観測は新しい持ち物で計算する: 実効 150 より速い → 無振り (134) は合わない、252 振り (169) は合う
        e.observe_speed(True, 150, {})
        p0, p252 = post_before[("jolly", (("spe", 0),))], post_before[("jolly", (("spe", 252),))]
        want = {("jolly", (("spe", 0),)): p0 * 0.2 / (p0 * 0.2 + p252), ("jolly", (("spe", 252),)): p252 / (p0 * 0.2 + p252)}
        assert _close(_spread_post(e), want, 1e-9), (_spread_post(e), want)
        # 喪失 (物は不明) の後に持ち物が判明 → 変更として扱う (以前の観測を新しい持ち物で評価し直さない)
        tr2 = SpreadTracker()
        e2 = tr2.estimator("garchomp")
        e2.observe_speed(False, 230, {})
        post2 = _spread_post(e2)
        tr2.on_frame(_state({"item_id": None, "item_removed": True}), [])
        assert e2.item_gone
        tr2.on_frame(_state({"item_id": "leftovers", "item_removed": True}), [])
        assert not e2.item_gone and {h["item"] for h in e2.hyps} == {"leftovers"}
        assert _close(_spread_post(e2), post2)
        # change を明示した呼び出し
        e3 = SpreadEstimator("garchomp")
        e3.observe_speed(False, 230, {})
        post3 = _spread_post(e3)
        e3.observe_item("leftovers", add_missing=True, change=True)
        assert _close(_spread_post(e3), post3) and e3.spe_upper is None
    print("test_real_change_keeps_spread_posterior OK")


# ---------------------------------------------------------------- (e)
def test_unreevaluable_observations_not_carried():
    _no_pin_env()
    with _patched():
        prior = _spread_post(SpreadEstimator("garchomp"))
        # 評価し直しを切ったとき: 事前の重みのまま (以前の仮説の重みは流用しない)
        with _Patch((C, "EV_INFER_REEVAL_ON_REVEAL", False)):
            e = SpreadEstimator("garchomp")
            e.observe_speed(True, 160, {})
            e.observe_item("leftovers", add_missing=True)
            assert _close(_spread_post(e), prior), (_spread_post(e), prior)
        # 記録の上限で捨てた (古い) 観測は評価し直さない: 上限 1 なら最後の観測だけ
        with _Patch((C, "EV_INFER_OBS_LOG_MAX", 1)):
            e = SpreadEstimator("garchomp")
            e.observe_speed(False, 140, {})                 # 捨てられる観測
            e.observe_speed(True, 160, {})
            assert len(e._obs_log) == 1 and e._obs_log[0]["kind"] == "speed" and e._obs_log[0]["opp_first"]
            e.observe_item("leftovers", add_missing=True)
            ref = SpreadEstimator("garchomp")
            ref.observe_item("leftovers", add_missing=True)
            ref.observe_speed(True, 160, {})
            assert _close(_spread_post(e), _spread_post(ref))
        # 記録が無い (__init__ を通さずに作った) 推定器: add_missing=False の経路は従来どおり動く
        bare = SpreadEstimator.__new__(SpreadEstimator)
        bare.species_id, bare.n_obs, bare.spe_lower, bare.spe_upper, bare._choice_locked = "garchomp", 0, None, None, False
        bare.hyps = _review_hyps()
        bare.observe_item("focussash")
        assert bare.top_k(1)[0]["item"] == "focussash" and bare.n_obs == 1
    print("test_unreevaluable_observations_not_carried OK")


if __name__ == "__main__":
    test_pure_helpers()
    test_pinned_missing_tables_raise()
    test_speed_range_dropped_on_loss_and_consume()
    test_first_reveal_outside_support_reevaluates()
    test_real_change_keeps_spread_posterior()
    test_unreevaluable_observations_not_carried()
    print("ALL OK")
