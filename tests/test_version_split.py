"""版の識別と実行状態の分離 (advisor.versions)、advice 行の採点の実計算の有無と時刻 (advice_key.advise_cached_ex /
battle_logger.evaluation_fields_of)、spe_est と ev_infer の保存・復元 (battle_logger.compact_state / ev_infer_of、
tools.advice_replay.compact_to_engine_state) のテスト (2026-10-20 レビュー)。

- version_id: RL 方策の読み込み前 (None) と後 (True) で同じ、runtime は違う。モデルの sha・埋め込み・使用率の snapshot・
  採点設定・鍵の版が変われば違う。旧版の version 行も identity_of で読める
- runtime 行: version 行の後に読み込みが完了したら 1 行だけ書く
- advice 行の evaluated / t_eval / cache_hit: 採点したら t_eval = そのときの now、キャッシュを使ったら元の採点の時刻
- spe_est と ev_infer の往復: 保存 (JSON を通す) → 復元 → 採点 (RL の入力 / evaluate) が同じ

比較の間はモデル・型・DB を固定する (tests.test_state_schema_roundtrip._Fixed)。CI でも本体でも同じ条件で動く。

    python -m tests.test_version_split
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path

os.environ.setdefault("RL_BLEND_WEIGHT", "0")

import advice_key as K
import advisor.engine as E
import advisor.ev_infer as EVI
from advisor import versions as V
from battle_logger import (MON_INPUT_FIELDS, MON_V2_FIELDS, BattleLogger, compact_state, ev_infer_of, evaluation_counts,
                           evaluation_fields_of)
from tools.advice_replay import apply_ev_infer, compact_to_engine_state, restore_unknowns, scoring_inputs_missing
from tests.test_state_schema_roundtrip import _Fixed, _base, _row, _sig, resolver

SCORING = {"scoring_mode": "transition", "sensitivity": False, "display_search": "off", "rl_blend": 0.3, "search_blend": 0.5,
           "key_version": 2}


def _doc(rl_loaded=None, rl_error=None, **over):
    """summarize_versions の固定の引数 (over で差し替え)"""
    a = dict(designated=None, sel_path="/m/deployed.pt", sel_sha="sel1", sel_pkg=None, pkg_species=None,
             my_species=["a", "b", "c", "d", "e", "f"], rl_path="/m/rl.zip", rl_sha="rl1", team_sha="team1", git="git1",
             dex="dex1", eff="eff1", features="v1", embedding_sha="emb1", usage_snapshot=7, usage_snapshot_source="db_latest",
             scoring=dict(SCORING))
    a.update(over)
    return V.summarize_versions(a["designated"], a["sel_path"], a["sel_sha"], a["sel_pkg"], a["pkg_species"], a["my_species"],
                                a["rl_path"], a["rl_sha"], rl_loaded, rl_error, a["team_sha"], a["git"], a["dex"], a["eff"],
                                a["features"], embedding_sha=a["embedding_sha"], usage_snapshot=a["usage_snapshot"],
                                usage_snapshot_source=a["usage_snapshot_source"], scoring=a["scoring"])


# ------------------------------------------------------------------ 版の識別と実行状態
def test_version_id_ignores_runtime():
    """読み込み前 (None) / 成功 (True) / 失敗 (False) で version_id は同じ、runtime は違う。退避理由・指定のラベルも digest の外"""
    before, after, failed = _doc(None), _doc(True), _doc(False, "load_failed")
    assert before["version_id"] == after["version_id"] == failed["version_id"], (before["version_id"], after["version_id"])
    assert before["runtime"]["rl_loaded"] is None and after["runtime"]["rl_loaded"] is True
    assert failed["runtime"]["rl_loaded"] is False and failed["runtime"]["rl_error"] == "load_failed"
    # 実行状態の情報は従来の欄にも残る (読み込みの成否は照合に使う)
    assert before["action_policy"]["loaded"] is None and after["action_policy"]["loaded"] is True
    assert after["runtime"]["usage_snapshot_source"] == "db_latest"
    # 指定のラベルと退避理由 (選出モデルの実体が同じなら同じ版)
    lab = _doc(None, designated="pkg1", pkg_species=["x"])
    assert lab["selection_model"]["fallback_reason"] == "party_not_in_package" and lab["version_id"] == before["version_id"]
    assert lab["runtime"]["designated_package"] == "pkg1" and lab["runtime"]["selection_fallback_reason"] == "party_not_in_package"
    # 時刻は入らない。digest は identity_of だけから
    assert V.digest(dict(before, collected_at=1.0, t=2.0)) == before["version_id"] == V.digest(before)
    assert set(V.identity_of(before)) == {"code", "models", "data", "scoring"}
    print("test_version_id_ignores_runtime OK")


def test_version_id_changes_with_identity():
    """コード・モデルの実体の sha・データ版・採点設定のどれかが変われば version_id が変わる"""
    base = _doc(None)["version_id"]
    changes = {
        "git": dict(git="git2"), "features": dict(features="v2"),
        "selection_sha": dict(sel_sha="sel2"), "rl_sha": dict(rl_sha="rl2"), "embedding": dict(embedding_sha="emb2"),
        "usage_snapshot": dict(usage_snapshot=8), "dex": dict(dex="dex2"), "effects": dict(eff="eff2"),
        "team_sha": dict(team_sha="team2"),
        "scoring_mode": dict(scoring=dict(SCORING, scoring_mode="legacy")),
        "sensitivity": dict(scoring=dict(SCORING, sensitivity=True)),
        "display_search": dict(scoring=dict(SCORING, display_search="on")),
        "rl_blend": dict(scoring=dict(SCORING, rl_blend=0.0)),
        "search_blend": dict(scoring=dict(SCORING, search_blend=0.0)),
        "key_version": dict(scoring=dict(SCORING, key_version=3)),
    }
    seen = {base}
    for name, over in changes.items():
        vid = _doc(None, **over)["version_id"]
        assert vid != base, name
        seen.add(vid)
    assert len(seen) == len(changes) + 1
    # 識別に入らない欄 (モデルの経路・Package の id) は変えても同じ (実体の sha が同じなら同じ版)
    assert _doc(None, rl_path="/other/rl.zip", sel_path="/other/sel.pt")["version_id"] == base
    print("test_version_id_changes_with_identity OK")


def test_identity_of_old_version_row():
    """2026-10-20 より前の version 行 (scoring / runtime / embedding の欄が無い): 採点設定は上位の欄から、実行状態は action_policy
    等から読む。loaded だけが違う 2 行は同じ識別"""
    old = {"type": "version", "version_id": "old1", "designated_package": None,
           "selection_model": {"package": None, "path": "/m/d.pt", "sha256": "s", "fallback_reason": None, "source": "deployed"},
           "action_policy": {"path": "/m/rl.zip", "sha256": "r", "loaded": None, "error": None},
           "team": {"sha256": "t", "species": ["b", "a"]},
           "rules": {"git_commit": "g", "dex_sha256": "d", "effects_sha256": "e", "selection_features": "v1"},
           "scoring_mode": "transition", "transition_sensitivity": False, "display_search": "off"}
    old2 = copy.deepcopy(old)
    old2["action_policy"]["loaded"] = True
    old2["version_id"] = "old2"
    i1 = V.identity_of(old)
    assert i1["scoring"]["scoring_mode"] == "transition" and i1["scoring"]["sensitivity"] is False
    assert i1["scoring"]["rl_blend"] is None and i1["models"]["embedding_sha256"] is None
    assert i1["data"]["team_species"] == ["a", "b"]
    assert V.digest(old) == V.digest(old2) and V.runtime_of(old)["rl_loaded"] is None and V.runtime_of(old2)["rl_loaded"] is True
    old3 = dict(copy.deepcopy(old), scoring_mode="legacy")
    assert V.digest(old3) != V.digest(old)
    split = V.version_split_of([{"type": "session"}, old,
                                {"type": "advice", "kind": "battle", "policy": {"rl_loaded": True}},
                                {"type": "advice", "kind": "selection", "policy": {"rl_loaded": None}}])
    assert split["version_id"] == "old1" and split["identity_digest"] == V.digest(old2)
    assert split["runtime"]["rl_loaded"] is None and split["rl_loaded_advice"] == [True] and split["runtime_rows"] == []
    assert V.version_split_of([])["version_id"] is None
    print("test_identity_of_old_version_row OK")


def test_runtime_change():
    rt = _doc(None)["runtime"]
    assert V.runtime_change(rt, None) is None                  # まだ試していない
    ch = V.runtime_change(rt, True)
    assert ch["rl_loaded"] is True and ch["rl_error"] is None and rt["rl_loaded"] is None
    assert V.runtime_change(ch, True) is None                  # 同じなら書かない
    ch2 = V.runtime_change(ch, False)
    assert ch2["rl_loaded"] is False and ch2["rl_error"]
    assert V.runtime_change(None, True)["rl_loaded"] is True
    print("test_runtime_change OK")


class _Versions:
    """battle_logger が読む advisor.versions の runtime_versions / rl_loaded_now を固定する"""

    def __init__(self, doc: dict, loaded):
        self.doc, self.loaded = doc, loaded

    def __enter__(self):
        self.orig = (V.runtime_versions, V.rl_loaded_now)
        V.runtime_versions = lambda refresh=False: copy.deepcopy(self.doc)
        V.rl_loaded_now = lambda: self.loaded[0]
        return self

    def __exit__(self, *a):
        V.runtime_versions, V.rl_loaded_now = self.orig


def _st(turn=2):
    return {"scene": "command", "turn": turn, "field": {},
            "player": {"active_index": 0, "party": [{"species_id": "scizor"}]},
            "opponent": {"active_index": 0, "party": [{"species_id": "garchomp", "spe_est": 140}]}}


def _rows(lg) -> list:
    return [json.loads(x) for x in lg._file.read_text(encoding="utf-8").splitlines()]


def test_logger_runtime_row_and_eval_fields():
    """version 行は読み込み前 (runtime.rl_loaded = null)。読み込みが完了した後の最初の助言の前に runtime 行を 1 行だけ書く。
    advice 行に evaluated / t_eval / cache_hit / ev_infer を必ず書く (渡されなければ null)。evaluation_counts で数えられる"""
    loaded = [None]
    with _Versions(_doc(None), loaded), tempfile.TemporaryDirectory() as td:
        lg = BattleLogger(log_dir=Path(td))
        adv = {"ok": True, "best": {"kind": "move", "id": "uturn"}, "actions": [{"kind": "move", "id": "uturn", "score": 1}]}
        lg.on_advice(dict(adv), "battle", _st(), evaluated=True, t_eval=100.0, ev_infer={"garchomp": None})
        loaded[0] = True                       # 1 回目の採点で RL 方策を読み込んだ
        lg.on_advice(dict(adv), "battle", _st(), evaluated=False, t_eval=100.0, ev_infer={"garchomp": None})
        lg.on_advice(dict(adv), "battle", _st(3), evaluated=True, t_eval=105.5, ev_infer=None)
        lg.on_advice(dict(adv), "battle", _st(3))                       # 呼び出し側が知らない
        lg.on_advice({"ok": True, "recommend": []}, "selection", _st())
        rows = _rows(lg)
    types = [r["type"] for r in rows]
    ver = next(r for r in rows if r["type"] == "version")
    assert ver["runtime"]["rl_loaded"] is None and ver["version_id"] == _doc(True)["version_id"]
    rt = [r for r in rows if r["type"] == "runtime"]
    assert len(rt) == 1 and rt[0]["runtime"]["rl_loaded"] is True and rt[0]["version_id"] == ver["version_id"], rt
    assert types.index("runtime") == [i for i, t in enumerate(types) if t == "advice"][1] - 1     # 2 本目の助言の直前
    adv_rows = [r for r in rows if r["type"] == "advice" and r["kind"] == "battle"]
    assert [(r["evaluated"], r["t_eval"], r["cache_hit"]) for r in adv_rows] == \
        [(True, 100.0, False), (False, 100.0, True), (True, 105.5, False), (None, None, None)]
    assert [r["ev_infer"] for r in adv_rows] == [{"garchomp": None}, {"garchomp": None}, None, None]
    assert [r["policy"]["rl_loaded"] for r in adv_rows] == [None, True, True, True]
    sel = next(r for r in rows if r["type"] == "advice" and r["kind"] == "selection")
    assert "evaluated" not in sel and "ev_infer" not in sel
    # 版 2 の state に spe_est (個体ごと。無い個体は null)
    assert adv_rows[0]["state"]["opponent"]["party"][0]["spe_est"] == 140
    assert adv_rows[0]["state"]["player"]["party"][0]["spe_est"] is None
    cnt = evaluation_counts(rows)
    assert cnt == {"n_advice": 4, "n_evaluated": 2, "n_cache_hit": 1, "n_unknown": 1, "n_eval_times": 2}, cnt
    split = V.version_split_of(rows)
    assert split["runtime"]["rl_loaded"] is None and split["runtime_rows"][0]["rl_loaded"] is True
    print("test_logger_runtime_row_and_eval_fields OK")


def test_evaluation_fields_of():
    assert evaluation_fields_of(None, 5.0) == {"evaluated": None, "t_eval": None, "cache_hit": None}
    assert evaluation_fields_of(True, 5.12345) == {"evaluated": True, "t_eval": 5.123, "cache_hit": False}
    assert evaluation_fields_of(False, None) == {"evaluated": False, "t_eval": None, "cache_hit": True}
    print("test_evaluation_fields_of OK")


class _FakeAdvisor:
    def __init__(self):
        self.resolver = None
        self._last_best = None
        self._stab_hist = None
        self.last_common = None

    @staticmethod
    def suggest_check(state):
        return ""


def test_advise_cached_ex_eval_time():
    """採点したら t_eval = now・入力の写しはそのとき取った値。キャッシュを使ったら元の採点の時刻と写し (その後に入力が変わっても)。
    advise_cached は従来どおり 2 値"""
    calls = []

    def fake_evaluate(state, resolver=None, **kw):
        calls.append(1)
        return {"ok": True, "actions": [{"kind": "move", "id": "surf", "score": 50.0}],
                "best": {"kind": "move", "id": "surf", "score": 50.0}, "position_key": "p", "scoring_mode": "legacy"}

    snap = {"v": 1}
    orig = E.evaluate
    E.evaluate = fake_evaluate
    try:
        adv, cache, st = _FakeAdvisor(), K.AdviceCache(), _st()
        a1, ev1, t1, in1 = K.advise_cached_ex(adv, st, cache, "k", 100.0, inputs_fn=lambda: dict(snap))
        assert ev1 and t1 == 100.0 and in1 == {"v": 1} and len(calls) == 1
        snap["v"] = 2                          # 採点の後に推定が変わった
        a2, ev2, t2, in2 = K.advise_cached_ex(adv, st, cache, "k", 100.7, inputs_fn=lambda: dict(snap))
        assert not ev2 and t2 == 100.0 and in2 == {"v": 1} and len(calls) == 1
        in2["v"] = 99                          # 呼び出し側が書き換えてもキャッシュの写しは変わらない
        assert cache.inputs == {"v": 1}
        _a3, ev3, t3, in3 = K.advise_cached_ex(adv, st, cache, "k2", 101.0, inputs_fn=lambda: dict(snap))
        assert ev3 and t3 == 101.0 and in3 == {"v": 2}
        # 入力の写しの失敗は採点を止めない
        _a4, ev4, _t4, in4 = K.advise_cached_ex(adv, st, cache, "k3", 102.0, inputs_fn=lambda: 1 / 0)
        assert ev4 and in4 is None
        a5 = K.advise_cached(adv, st, cache, "k3", 102.5)
        assert len(a5) == 2 and a5[1] is False
        cache.clear()
        assert cache.inputs is None
    finally:
        E.evaluate = orig
    print("test_advise_cached_ex_eval_time OK")


# ------------------------------------------------------------------ spe_est と ev_infer の往復
def test_spe_est_roundtrip_rl_input():
    """spe_est を保存 (JSON を通す) → 復元すると同じ値に戻り、RL の入力 (encode_state の素早さ比較) が同じ。
    spe_est の無い行 (10/20 以前) は欄なしのまま (推定なし)。restore_unknowns は spe_est の欠けを数えない"""
    from advisor.rl_bridge import encode_state
    st = _base()
    opp = st["opponent"]["party"][st["opponent"]["active_index"]]
    opp["spe_est"] = 200
    row = _row(st)
    m = row["state"]["opponent"]["party"][st["opponent"]["active_index"]]
    assert m["spe_est"] == 200 and "spe_est" not in MON_V2_FIELDS and "spe_est" in MON_INPUT_FIELDS
    r = compact_to_engine_state(row["state"], resolver)
    assert r["opponent"]["party"][st["opponent"]["active_index"]]["spe_est"] == 200
    r["now"] = st["now"]
    a, b = encode_state(copy.deepcopy(st), my_spe_actual=112), encode_state(copy.deepcopy(r), my_spe_actual=112)
    assert float(a[226]) == float(b[226]), (a[226], b[226])
    # 推定値を変えると RL の素早さ比較が変わる (往復の比較が欠落を検出できること)
    st_slow = copy.deepcopy(st)
    st_slow["opponent"]["party"][st["opponent"]["active_index"]]["spe_est"] = 50
    assert float(encode_state(st_slow, my_spe_actual=112)[226]) != float(a[226])
    # 10/20 以前の版 2 の行 (spe_est の欄が無い): 欄なしで戻す。restore_unknowns は欠けに数えず、scoring_inputs_missing が示す
    old = json.loads(json.dumps(row))
    for sname in ("player", "opponent"):
        for mm in old["state"][sname]["party"]:
            del mm["spe_est"]
    ro = compact_to_engine_state(old["state"], resolver)
    assert "spe_est" not in ro["opponent"]["party"][st["opponent"]["active_index"]]
    assert restore_unknowns(old) == [] and scoring_inputs_missing(old) == ["spe_est", "ev_infer"]
    assert scoring_inputs_missing(dict(row, ev_infer={})) == []
    print("test_spe_est_roundtrip_rl_input OK")


class _FakeTracker:
    def __init__(self, table: dict):
        self.table = table

    def best_for(self, sid):
        g = self.table.get(sid)
        return copy.deepcopy(g) if g is not None else None


class _Tracker:
    """advisor.ev_infer.get_tracker を差し替える (engine は呼び出しのたびに読む)"""

    def __init__(self, tracker):
        self.tracker = tracker

    def __enter__(self):
        self.orig = EVI.get_tracker
        EVI.get_tracker = lambda: self.tracker
        return self

    def __exit__(self, *a):
        EVI.get_tracker = self.orig


GUESS = {"nature": "bold", "nature_ja": "ずぶとい", "evs": {"hp": 252, "atk": 0, "def": 252, "spa": 0, "spd": 4, "spe": 0},
         "item": "assaultvest", "prob": 0.8, "n_obs": 3, "spe_lower": None, "spe_upper": 120.0, "summary": "テスト用の推定"}


def test_ev_infer_roundtrip_evaluate():
    """ev_infer を保存 (場の個体の best_for、JSON を通す) → 復元 (apply_ev_infer で ev_infer_override) → evaluate が、保存したときのトラッカーで
    評価した結果と同じ (legacy / transition)。復元しない (トラッカーが空) と結果が変わる (往復の比較が欠落を検出できること)"""
    st = _base()
    opp_sid = st["opponent"]["party"][st["opponent"]["active_index"]]["species_id"]
    me_sid = st["player"]["party"][st["player"]["active_index"]]["species_id"]
    live = _FakeTracker({opp_sid: GUESS})
    snap = ev_infer_of(st, live.best_for)
    assert set(snap) == {opp_sid, me_sid} and snap[me_sid] is None and snap[opp_sid]["evs"]["def"] == 252
    row = _row(st)
    row["ev_infer"] = snap
    row = json.loads(json.dumps(row, ensure_ascii=False))
    restored = apply_ev_infer(compact_to_engine_state(row["state"], resolver), row["ev_infer"])
    restored["now"] = st["now"]
    assert restored["ev_infer_override"] == snap
    empty = _FakeTracker({})
    with _Fixed():
        with _Tracker(live):
            want = {mode: _sig(st, mode) for mode in ("legacy", "transition")}
        with _Tracker(empty):
            got = {mode: _sig(restored, mode) for mode in ("legacy", "transition")}
            no_override = compact_to_engine_state(row["state"], resolver)
            no_override["now"] = st["now"]
            assert "ev_infer_override" not in no_override and "ev_infer_override" not in apply_ev_infer(dict(no_override), None)
            lost = {mode: _sig(no_override, mode) for mode in ("legacy", "transition")}
        # 記録に無い種族は推定なし (トラッカーに推定があっても読まない)
        with _Tracker(live):
            other = dict(copy.deepcopy(restored), ev_infer_override={})
            ignored = {mode: _sig(other, mode) for mode in ("legacy", "transition")}
    for mode in ("legacy", "transition"):
        assert got[mode] == want[mode], (mode, {k: (want[mode][k], got[mode][k]) for k in want[mode] if want[mode][k] != got[mode][k]})
        assert ignored[mode] == lost[mode], mode
    assert any(lost[m] != want[m] for m in lost), "推定の有無で結果が変わらない (テストの局面が検出に使えない)"
    print("test_ev_infer_roundtrip_evaluate OK")


def test_ev_infer_of_shapes():
    st = {"player": {"active_index": 0, "party": [{"species_id": "a"}]},
          "opponent": {"active_index": None, "party": [{"species_id": "b"}]}}
    assert ev_infer_of(st, lambda s: {"x": (1, 2)}) == {"a": {"x": [1, 2]}}
    assert ev_infer_of(st, lambda s: 1 / 0) == {"a": None}
    assert ev_infer_of({}, lambda s: None) == {} and ev_infer_of(None, lambda s: None) == {}
    same = {"player": {"active_index": 0, "party": [{"species_id": "a"}]}, "opponent": {"active_index": 0, "party": [{"species_id": "a"}]}}
    assert ev_infer_of(same, lambda s: {"n_obs": 1}) == {"a": {"n_obs": 1}}
    print("test_ev_infer_of_shapes OK")


def main():
    test_version_id_ignores_runtime()
    test_version_id_changes_with_identity()
    test_identity_of_old_version_row()
    test_runtime_change()
    test_logger_runtime_row_and_eval_fields()
    test_evaluation_fields_of()
    test_advise_cached_ex_eval_time()
    test_spe_est_roundtrip_rl_input()
    test_ev_infer_roundtrip_evaluate()
    test_ev_infer_of_shapes()
    print("ALL OK")


if __name__ == "__main__":
    main()
