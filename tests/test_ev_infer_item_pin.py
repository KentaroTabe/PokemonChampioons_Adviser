"""型推定 (advisor/ev_infer) の持ち物の判明の受け渡しとデータ固定の経路の検証 (2026-10-30、docs/DESIGN_ITEM_INFERENCE_20261030.md §6.4 の論点 3・4)。

- (a) 相手の state に item_id が入ると、トラッカーが observe_item を個体ごとに 1 回だけ呼び仮説が絞られる。同じ state を続けて渡しても
  2 回目は掛からない。持ち物が変わったら (トリック等) 新しい持ち物の観測を別の 1 回として渡す
- (b) 仮説に無い持ち物の確定で、配分ごとに持ち物を差し替えた仮説が足される (事前の重みは DB の採用率、一覧に無ければ下限)
- (c) 消費・喪失の解釈 (item_observation / knocked_off_items / item_to_pass) と、消費・喪失の後の出力の持ち物が「なし」
- (d) 版を明示したとき (data_pin) は DB・snapshot が無ければ InputsPinError で止まり、その snapshot だけを読む。明示が無ければ従来どおり
  (チャンピオンズの全 snapshot の合計)
- (e) トラッカーの推定器がデータの版で分かれ、data_pin.reset_caches() で消える

使い方: python -m tests.test_ev_infer_item_pin  (使用率 DB は一時ファイルに作る。本体の DB は読まない)
"""
from __future__ import annotations

import math
import os
import sqlite3
import tempfile
from pathlib import Path

import advisor.data_pin as DP
import advisor.ev_infer as EI
from advisor.data_pin import InputsPinError
from advisor.ev_infer import (SpreadEstimator, SpreadTracker, confirm_item_hyps, item_observation, item_share,
                              item_to_pass, knocked_off_items)
from champions_agent import config as C

_SPREAD_A = "2/32/0/0/0/32"
_SPREAD_B = "32/0/32/0/2/0"
_SPREAD_C = "0/32/2/0/0/32"


def _make_db(path: Path) -> None:
    """snapshot 1 と 2 (どちらも チャンピオンズ、足切り以上) に garchomp の配分・持ち物を入れた最小の使用率 DB"""
    c = sqlite3.connect(str(path))
    c.executescript("""
        CREATE TABLE usage_snapshot (id INTEGER PRIMARY KEY, source TEXT NOT NULL, format TEXT NOT NULL,
            number_of_battles INTEGER, fetched_at TEXT NOT NULL DEFAULT (datetime('now')));
        CREATE TABLE spread_usage (id INTEGER PRIMARY KEY AUTOINCREMENT, snapshot_id INTEGER NOT NULL, pokemon_name TEXT NOT NULL,
            nature TEXT, evs TEXT, usage_percent REAL NOT NULL);
        CREATE TABLE item_usage (id INTEGER PRIMARY KEY AUTOINCREMENT, snapshot_id INTEGER NOT NULL, pokemon_name TEXT NOT NULL,
            item_name TEXT NOT NULL, usage_percent REAL NOT NULL);
    """)
    c.executemany("INSERT INTO usage_snapshot (id, source, format, number_of_battles) VALUES (?,?,?,?)",
                  [(1, "pokedb", "champions-singles", 500), (2, "pokedb", "champions-singles", 500)])
    c.executemany("INSERT INTO spread_usage (snapshot_id, pokemon_name, nature, evs, usage_percent) VALUES (?,?,?,?,?)",
                  [(1, "garchomp", None, _SPREAD_A, 60.0), (1, "garchomp", None, _SPREAD_B, 40.0),
                   (2, "garchomp", None, _SPREAD_C, 100.0)])
    c.executemany("INSERT INTO item_usage (snapshot_id, pokemon_name, item_name, usage_percent) VALUES (?,?,?,?)",
                  [(1, "garchomp", "choicescarf", 50.0), (1, "garchomp", "focussash", 30.0), (1, "garchomp", "lifeorb", 20.0),
                   (1, "garchomp", "rockyhelmet", 10.0), (1, "garchomp", "leftovers", 4.0),
                   (2, "garchomp", "sitrusberry", 100.0)])
    c.commit()
    c.close()


def _pts_evs(s: str) -> dict:
    """DB の能力ポイント表記を _build_hypotheses と同じく努力値に換算"""
    vals = [min(252, int(x) * 8) for x in s.split("/")]
    return dict(zip(("hp", "atk", "def", "spa", "spd", "spe"), vals))


def _fixed_hyps() -> list:
    """DB を読まない仮説 (配分 2 × 持ち物 2)"""
    out = []
    for nat, evs, ws in (("jolly", {"atk": 252, "spe": 252}, 0.6), ("impish", {"hp": 252, "def": 252}, 0.4)):
        for item, wi in (("choicescarf", 0.7), ("focussash", 0.3)):
            out.append({"nature": nat, "evs": dict(evs), "item": item, "logw": math.log(ws * wi)})
    return out


class _Patch:
    """属性の一時差し替え (with で戻す)"""

    def __init__(self, *triples):
        self.triples = triples
        self.saved = []

    def __enter__(self):
        for obj, name, val in self.triples:
            # クラスの staticmethod は __dict__ の値 (staticmethod のまま) を退避する (getattr だと素の関数になり、戻すと束縛される)
            d = vars(obj)
            self.saved.append((obj, name, d[name] if name in d else getattr(obj, name)))
            setattr(obj, name, val)
        return self

    def __exit__(self, *exc):
        for obj, name, val in reversed(self.saved):
            setattr(obj, name, val)


def _no_pin_env():
    for k in (C.USAGE_DB_ENV, C.USAGE_SNAPSHOT_ENV):
        assert not os.environ.get(k), f"環境変数 {k} が設定されている (このテストは版の明示なしで始める)"


def _state(opp: dict, scene: str = "field") -> dict:
    return {
        "scene": scene, "turn": 2, "field": {}, "events": [],
        "player": {"active_index": 0, "party": [
            {"species_id": "mimikyu", "species_ja": "ミミッキュ", "types": [], "hp_percent": 100.0, "boosts": {}, "status": None}]},
        "opponent": {"active_index": 0, "party": [dict({"species_id": "garchomp", "species_ja": "ガブリアス", "types": [],
                                                        "hp_percent": 100.0, "boosts": {}, "status": None}, **opp)]},
    }


def _counting_observe_item(calls: list):
    orig = SpreadEstimator.observe_item

    def wrapped(self, item_id, add_missing=False):
        calls.append((self.species_id, item_id, add_missing))
        return orig(self, item_id, add_missing=add_missing)
    return wrapped


# ---------------------------------------------------------------- (a)
def test_tracker_passes_item_once():
    _no_pin_env()
    calls: list = []
    with _Patch((SpreadEstimator, "_build_hypotheses", lambda self, sid: _fixed_hyps()),
                (SpreadEstimator, "_query_item_share", staticmethod(lambda sid, iid: None)),
                (SpreadEstimator, "observe_item", _counting_observe_item(calls))):
        tr = SpreadTracker()
        st = _state({})
        tr.on_frame(st, [])
        assert calls == [] and "garchomp" not in tr._est        # 持ち物が未判明なら何もしない
        st = _state({"item_id": "focussash", "item_ja": "きあいのタスキ"})
        tr.on_frame(st, [])
        assert calls == [("garchomp", "focussash", True)], calls
        est = tr._est["garchomp"]
        assert {h["item"] for h in est.hyps} == {"focussash"} and len(est.hyps) == 2, est.hyps
        assert est.n_obs == 1 and tr.best_for("garchomp")["item"] == "focussash"
        before = [dict(h) for h in est.hyps]
        for _ in range(3):                                        # 同じ state を続けて渡しても 2 回目は掛からない
            tr.on_frame(st, [])
        assert len(calls) == 1 and est.n_obs == 1 and [dict(h) for h in est.hyps] == before
        # 持ち物が変わった (トリック等): 新しい持ち物の観測は別の 1 回。仮説に無い (落としてある) ので配分ごとに足される
        st = _state({"item_id": "choicescarf"})
        tr.on_frame(st, [])
        tr.on_frame(st, [])
        assert calls[1:] == [("garchomp", "choicescarf", True)], calls
        assert {h["item"] for h in est.hyps} == {"choicescarf"} and len(est.hyps) == 2 and est.n_obs == 2
        # 対戦が変わる (選出画面に戻る) と記録も消える
        tr.on_frame(_state({}, scene="command"), [])
        tr.on_frame(_state({}, scene="selection"), [])
        assert tr._item_passed == {}
    print("test_tracker_passes_item_once OK")


# ---------------------------------------------------------------- (b)
def test_confirm_item_hyps_pure():
    hyps = _fixed_hyps()
    snap = [dict(h) for h in hyps]
    got = confirm_item_hyps(hyps, "focussash", 0.5)
    assert [h["item"] for h in got] == ["focussash", "focussash"] and [h["nature"] for h in got] == ["jolly", "impish"]
    assert [dict(h) for h in hyps] == snap                      # 元の一覧は変えない
    # 仮説に無い持ち物: 配分ごとに 1 つ、重み = その配分の重みの合計 × 事前の重み
    add = confirm_item_hyps(hyps, "Left-overs", 0.02)
    assert [(h["nature"], h["item"]) for h in add] == [("jolly", "leftovers"), ("impish", "leftovers")], add
    assert abs(add[0]["logw"] - math.log(0.6 * 0.02)) < 1e-9 and abs(add[1]["logw"] - math.log(0.4 * 0.02)) < 1e-9
    assert add[0]["evs"] == hyps[0]["evs"] and add[0]["evs"] is not hyps[0]["evs"]
    assert confirm_item_hyps([], "leftovers", 0.1) == [] and confirm_item_hyps(hyps, None, 0.1) == hyps
    assert item_share([("Choice Scarf", 30.0), ("leftovers", 10.0)], "choicescarf") == 0.75
    assert item_share([("choicescarf", 30.0)], "leftovers") is None and item_share([], "x") is None
    print("test_confirm_item_hyps_pure OK")


def test_observe_item_adds_missing_with_db_prior():
    """仮説に無い持ち物: DB の一覧にあれば採用率 (上位 4 の外の leftovers)、無ければ config.EV_INFER_ITEM_FLOOR"""
    _no_pin_env()
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "u.sqlite3"
        _make_db(db)
        with _Patch((EI, "DB_PATH", db)):
            est = SpreadEstimator("garchomp")
            assert "leftovers" not in {h["item"] for h in est.hyps}
            n_spreads = len({(h["nature"], tuple(sorted(h["evs"].items()))) for h in est.hyps})
            assert SpreadEstimator._query_item_share("garchomp", "leftovers") == 4.0 / 214.0
            est.observe_item("leftovers", add_missing=True)
            assert {h["item"] for h in est.hyps} == {"leftovers"} and len(est.hyps) == n_spreads and est.n_obs == 1
            est2 = SpreadEstimator("garchomp")
            seen = []
            orig = SpreadEstimator._item_prior
            with _Patch((SpreadEstimator, "_item_prior", lambda self, iid: seen.append(orig(self, iid)) or seen[-1])):
                est2.observe_item("weaknesspolicy", add_missing=True)
            assert seen == [C.EV_INFER_ITEM_FLOOR] and {h["item"] for h in est2.hyps} == {"weaknesspolicy"}
            # 既定 (add_missing=False) は従来どおり: 仮説に無い持ち物なら変えない
            est3 = SpreadEstimator("garchomp")
            before = [dict(h) for h in est3.hyps]
            est3.observe_item("leftovers")
            assert [dict(h) for h in est3.hyps] == before and est3.n_obs == 0
    print("test_observe_item_adds_missing_with_db_prior OK")


# ---------------------------------------------------------------- (c)
def test_item_observation_pure():
    assert item_observation(None) == (None, False)
    assert item_observation({}) == (None, False)
    assert item_observation({"item_id": "Focus-Sash"}) == ("focussash", False)                       # 持っている
    assert item_observation({"item_id": "sitrusberry", "item_consumed": True}) == ("sitrusberry", True)  # 消費
    assert item_observation({"item_id": None, "item_removed": True}) == (None, True)              # 喪失 (物は不明)
    assert item_observation({"item_id": "choicescarf", "item_removed": True}) == ("choicescarf", False)  # 喪失の後に入った物
    assert item_observation({"item_consumed": True}) == (None, True)
    assert knocked_off_items(["move_player_knockoff", "knockoff_opponent_choicescarf", "knockoff_player_lifeorb"]) == ["choicescarf"]
    assert knocked_off_items(["knockoff_opponent_"]) == [] and knocked_off_items(None) == []
    assert item_to_pass(None, "focussash") == "focussash" and item_to_pass("focussash", "focussash") is None
    assert item_to_pass("focussash", "choicescarf") == "choicescarf" and item_to_pass("focussash", None) is None
    print("test_item_observation_pure OK")


def test_tracker_consumed_and_removed():
    _no_pin_env()
    calls: list = []
    with _Patch((SpreadEstimator, "_build_hypotheses", lambda self, sid: _fixed_hyps()),
                (SpreadEstimator, "_query_item_share", staticmethod(lambda sid, iid: None)),
                (SpreadEstimator, "observe_item", _counting_observe_item(calls))):
        # 消費: 以前その持ち物を持っていた (1 回) + 現在は持ち物なし (出力は「なし」、仮説の持ち物はそのまま)
        tr = SpreadTracker()
        st = _state({"item_id": "focussash", "item_consumed": True})
        tr.on_frame(st, [])
        tr.on_frame(st, [])
        assert calls == [("garchomp", "focussash", True)], calls
        est = tr._est["garchomp"]
        assert est.item_gone and {h["item"] for h in est.hyps} == {"focussash"}
        b = tr.best_for("garchomp")
        assert b["item"] is None and "持ち物なし" in b["summary"] and "@" not in b["summary"], b
        assert all(h["item"] is None for h in tr.hypotheses_for("garchomp", 4))
        assert est._view(est.hyps[0], {}).item is None
        # 先に判明 → 後で消費: 2 回目の観測は掛けず、現在なしだけ立つ
        calls.clear()
        tr2 = SpreadTracker()
        tr2.on_frame(_state({"item_id": "focussash"}), [])
        assert not tr2._est["garchomp"].item_gone
        tr2.on_frame(_state({"item_id": "focussash", "item_consumed": True}), [])
        assert len(calls) == 1 and tr2._est["garchomp"].item_gone
        # 喪失 (はたきおとす): 同じフレームの発火で失った物が分かれば、その観測 (1 回) + 現在なし
        calls.clear()
        tr3 = SpreadTracker()
        st3 = _state({"item_id": None, "item_removed": True})
        tr3.on_frame(st3, ["knockoff_opponent_choicescarf"])
        tr3.on_frame(st3, [])
        assert calls == [("garchomp", "choicescarf", True)], calls
        e3 = tr3._est["garchomp"]
        assert e3.item_gone and {h["item"] for h in e3.hyps} == {"choicescarf"} and tr3.best_for("garchomp")["item"] is None
        # 喪失で物が分からない: 観測は渡さず、現在なしだけ
        calls.clear()
        tr4 = SpreadTracker()
        tr4.on_frame(st3, [])
        assert calls == [] and tr4._est["garchomp"].item_gone and len(tr4._est["garchomp"].hyps) == 4
        # 喪失の後に新しい持ち物 (トリック等): 別の 1 回 + 現在なしを外す
        tr3.on_frame(_state({"item_id": "lifeorb", "item_removed": True}), [])
        assert calls[-1] == ("garchomp", "lifeorb", True) and not e3.item_gone
        assert tr3.best_for("garchomp")["item"] == "lifeorb"
    print("test_tracker_consumed_and_removed OK")


# ---------------------------------------------------------------- (d)
def test_pinned_reads_only_that_snapshot():
    _no_pin_env()
    opened = []
    orig_connect = sqlite3.connect

    def rec(db, *a, **k):
        opened.append(str(db))
        return orig_connect(db, *a, **k)
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "pin.sqlite3"
        _make_db(db)
        sentinel = Path(td) / "main_db_must_not_be_read.sqlite3"
        try:
            with _Patch((EI, "DB_PATH", sentinel), (sqlite3, "connect", rec)):
                DP.set_override(usage_db=db, usage_snapshot=1)
                est = SpreadEstimator("garchomp")
                assert {tuple(sorted(h["evs"].items())) for h in est.hyps} == \
                    {tuple(sorted(_pts_evs(s).items())) for s in (_SPREAD_A, _SPREAD_B)}
                assert {h["item"] for h in est.hyps} == {"choicescarf", "focussash", "lifeorb", "rockyhelmet"}
                # 採用率もその snapshot だけ (snapshot 2 の sitrusberry は一覧に無い)
                assert SpreadEstimator._query_item_share("garchomp", "leftovers") == 4.0 / 114.0
                assert SpreadEstimator._query_item_share("garchomp", "sitrusberry") is None
                DP.set_override(usage_db=db, usage_snapshot=2)
                est2 = SpreadEstimator("garchomp")
                assert {h["item"] for h in est2.hyps} == {"sitrusberry"} and len(est2.hyps) == 1
                assert est2.hyps[0]["evs"] == _pts_evs(_SPREAD_C)
                DP.set_override(usage_db=db)                 # DB だけ明示: その DB の最新 snapshot (2) だけ
                assert {h["item"] for h in SpreadEstimator("garchomp").hyps} == {"sitrusberry"}
            assert opened and all(o == f"file:{db}?mode=ro" for o in opened), opened
            assert not sentinel.exists()
        finally:
            DP.clear_override()
    print("test_pinned_reads_only_that_snapshot OK")


def test_pinned_missing_raises():
    _no_pin_env()
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "pin.sqlite3"
        _make_db(db)
        try:
            for kw in ({"usage_db": Path(td) / "none.sqlite3"}, {"usage_db": db, "usage_snapshot": 99}):
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
                        raise AssertionError(f"InputsPinError で止まらない: {kw}")
            assert not (Path(td) / "none.sqlite3").exists()
        finally:
            DP.clear_override()
    print("test_pinned_missing_raises OK")


def test_unpinned_unchanged():
    """明示が無いとき: 従来どおり DB_PATH のチャンピオンズの全 snapshot の合計 (snapshot 1 と 2 の行が両方入る)"""
    _no_pin_env()
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "u.sqlite3"
        _make_db(db)
        with _Patch((EI, "DB_PATH", db)):
            assert EI.data_version() == (str(db), None, False)
            est = SpreadEstimator("garchomp")
            assert {h["item"] for h in est.hyps} == {"sitrusberry", "choicescarf", "focussash", "lifeorb"}
            assert len(est.hyps) == 3 * 4
            h = [x for x in est.hyps if x["evs"] == _pts_evs(_SPREAD_A) and x["item"] == "sitrusberry"][0]
            assert abs(h["logw"] - math.log((60.0 / 200.0) * (100.0 / 200.0) + 1e-9)) < 1e-12
        missing = Path(td) / "none.sqlite3"
        with _Patch((EI, "DB_PATH", missing)):
            assert SpreadEstimator("garchomp").hyps == [] and not missing.exists()   # 無ければ空 (ファイルも作らない)
            tr = SpreadTracker()
            tr.on_frame(_state({"item_id": "focussash"}), [])                       # 仮説が空なら何もしない (止まらない)
            assert tr._est["garchomp"].hyps == []
    print("test_unpinned_unchanged OK")


# ---------------------------------------------------------------- (e)
def test_cache_split_by_data_version():
    _no_pin_env()
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "pin.sqlite3"
        _make_db(db)
        try:
            tr = SpreadTracker()
            DP.set_override(usage_db=db, usage_snapshot=1)
            v1 = EI.data_version()
            e1 = tr.estimator("garchomp")
            assert "sitrusberry" not in {h["item"] for h in e1.hyps}
            tr.on_frame(_state({"item_id": "focussash"}), [])
            assert tr._item_passed and tr.estimator("garchomp") is e1
            DP.set_override(usage_db=db, usage_snapshot=2)
            assert EI.data_version() != v1 and EI.data_version() == (str(db), 2, True)
            assert tr.best_for("garchomp") is None                     # 版が変わると前の版の推定器は使わない
            e2 = tr.estimator("garchomp")
            assert e2 is not e1 and {h["item"] for h in e2.hyps} == {"sitrusberry"} and tr._item_passed == {}
            # 新しい版の推定器にも持ち物の観測を (一度だけ) 渡し直す
            tr.on_frame(_state({"item_id": "focussash"}), [])
            assert {h["item"] for h in e2.hyps} == {"focussash"} and e2.n_obs == 1
            # data_pin.reset_caches() は共有のトラッカーの推定器を消す
            g = EI.get_tracker()
            g.estimator("garchomp")
            assert g._est
            DP.reset_caches()
            assert g._est == {} and g._item_passed == {}
        finally:
            DP.clear_override()
            EI.get_tracker().drop_estimators()
    print("test_cache_split_by_data_version OK")


if __name__ == "__main__":
    test_item_observation_pure()
    test_confirm_item_hyps_pure()
    test_tracker_passes_item_once()
    test_tracker_consumed_and_removed()
    test_observe_item_adds_missing_with_db_prior()
    test_pinned_reads_only_that_snapshot()
    test_pinned_missing_raises()
    test_unpinned_unchanged()
    test_cache_split_by_data_version()
    print("ALL OK")
