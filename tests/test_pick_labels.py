"""相手の選出ラベル 3 値の読み手 (2026-10-07 段 0) の検証。

opp_picks 行があれば 3 値を使い、無い古いログは従来の推定 (ロースター − 場に出た = 選出外) にフォールバックする:
tools/pick_labels、tools/analyze_battles、tools/party_improvements.parse_battle、tools/real_opponents.build_bank。
「3 体すべて判明した対戦だけ」の集計は部分集合の件数も返す。

    python -m tests.test_pick_labels
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from tools import pick_labels as PL


def _scene(scene, opp, active=None, t=1.0):
    return {"type": "scene", "scene": scene, "t": t, "state": {"opponent": {"active": active, "party": opp},
                                                                "player": {"active": None, "party": []}}}


def _opp(fielded_hp: dict):
    names = ["A", "B", "C", "D", "E", "F"]
    return [{"ja": n, "species": n.lower(), "hp": fielded_hp.get(n)} for n in names]


def _picks_row(appeared: list, complete: bool):
    slots = []
    for i, n in enumerate(["A", "B", "C", "D", "E", "F"]):
        app = n in appeared
        st = "picked_confirmed" if app else ("unpicked_confirmed" if complete else "unknown")
        slots.append({"slot": i, "species": n.lower(), "ja": n, "guess": False, "appeared": app, "pick_status": st})
    slots.append({"slot": 6, "species": "g", "ja": "G", "guess": True, "appeared": False, "pick_status": "unknown"})
    return {"type": "opp_picks", "slots": slots, "n_appeared": len(appeared), "complete": complete}


def _write(td, name, recs):
    p = Path(td) / name
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n", encoding="utf-8")
    return str(p)


def _battle_records(fielded: list, picks_row=None, outcome="win"):
    recs = [_scene("selection", _opp({}), t=1.0)]
    for k, n in enumerate(fielded):
        recs.append(_scene("command", _opp({x: 50.0 for x in fielded[:k + 1]}), active=None, t=2.0 + k))
    recs += [_scene("field", _opp({x: 50.0 for x in fielded}), t=9.0), _scene("field", _opp({x: 50.0 for x in fielded}), t=10.0)]
    recs.append({"type": "outcome", "outcome": outcome, "t": 11.0})
    if picks_row:
        recs.append(picks_row)
    return recs


def test_pure_readers():
    assert PL.last_opp_picks([{"type": "scene"}]) is None and PL.status_by_ja(None) is None
    row = _picks_row(["A", "B"], False)
    st = PL.status_by_ja(PL.last_opp_picks([{"type": "opp_picks", "slots": []}, row]))
    assert "G" not in st                                    # 推定の枠は除く
    picked, unpicked, unknown = PL.split_status(st)
    assert picked == ["A", "B"] and unpicked == [] and unknown == ["C", "D", "E", "F"]
    battles = [{"opp_picks_complete": True, "outcome": "win", "opp_fielded": ["A", "B", "C"]},
               {"opp_picks_complete": False, "outcome": "loss", "opp_fielded": ["A", "B"]},
               {"opp_picks_complete": None, "outcome": "loss", "opp_fielded": ["A", "D", "E"]},    # 古いログ: 3 体出た
               {"opp_picks_complete": None, "outcome": "unknown", "opp_fielded": ["A", "D", "E"]},
               {"outcome": "win", "opp_fielded": ["A"]}]
    sub = PL.complete_subset(battles)
    assert sub["n_battles"] == 5 and sub["n_complete"] == 3 and sub["n_decided"] == 2
    assert sub["stats"]["A"] == [1, 1] and sub["stats"]["D"] == [0, 1] and "B" in sub["stats"]
    print("test_pure_readers OK")


def test_analyze_battles_uses_labels_with_fallback():
    from tools import analyze_battles as AB
    with tempfile.TemporaryDirectory() as td:
        old = AB._parse_battle(_write(td, "old.jsonl", _battle_records(["A", "B"])))
        # 古いログ: 従来どおり 6 − 場に出た = 選出外
        assert old["opp_benched"] == ["C", "D", "E", "F"] and old["opp_pick_status"] is None
        assert old["opp_picks_complete"] is None
        new = AB._parse_battle(_write(td, "new.jsonl", _battle_records(["A", "B"], _picks_row(["A", "B"], False))))
        # 2 体しか出ていない対戦: 選出外は確定しない (選出されたが出なかった個体がありうる)
        assert new["opp_benched"] == [] and new["opp_pick_unknown"] == ["C", "D", "E", "F"]
        assert new["opp_fielded"] == ["A", "B"] and new["opp_picks_complete"] is False
        full = AB._parse_battle(_write(td, "full.jsonl", _battle_records(["A", "B", "C"], _picks_row(["A", "B", "C"], True))))
        assert full["opp_benched"] == ["D", "E", "F"] and full["opp_picks_complete"] is True
        s = AB.summarize([old, new, full])
        assert s["opp_complete"]["n_complete"] == 1 and s["opp_complete"]["n_battles"] == 3 and s["n_pick_labels"] == 2
        assert s["opp_benched_stats"]["C"] == [1, 0] and "D" in s["opp_benched_stats"]
        text = AB.report(s)
        assert "部分集合 1/3戦" in text
    print("test_analyze_battles_uses_labels_with_fallback OK")


def test_party_improvements_and_bank_use_labels():
    from tools import party_improvements as PI
    from tools.real_opponents import build_bank
    with tempfile.TemporaryDirectory() as td:
        old = PI.parse_battle(_write(td, "old.jsonl", _battle_records(["A", "B"])))
        new = PI.parse_battle(_write(td, "new.jsonl", _battle_records(["A", "B"], _picks_row(["A", "B"], False))))
    assert old["opp_pick_status"] is None and new["opp_pick_status"]["C"] == "unknown"
    resolve = lambda ja: ja.lower()      # noqa: E731
    b_old = build_bank([old], resolve)
    # 古いログ: 出なかった 4 体も出現に数える (従来どおり)
    assert b_old["species"]["c"]["appear"] == 1 and b_old["species"]["c"]["picked"] == 0
    b_new = build_bank([new], resolve)
    # ラベルあり: 選出が分からない個体は出現にも数えない (選出率を下げない)。選出確定は選出に数える
    assert "c" not in b_new["species"] or b_new["species"]["c"]["appear"] == 0
    assert b_new["species"]["a"] == dict(b_new["species"]["a"], appear=1, picked=1)
    print("test_party_improvements_and_bank_use_labels OK")


def test_selection_prediction_join():
    """相手の選出の予測 (opp_pick_pred) は selection_record 行にあり、最後の選出の助言と advice_id で結ぶ"""
    recs = [{"type": "advice", "kind": "selection", "advice_id": "s1", "advice": {"ok": True}},
            {"type": "selection_record", "advice_id": "s1", "opp_pick_pred": {"combos": [1]}},
            {"type": "advice", "kind": "selection", "advice_id": "s2", "advice": {"ok": True}},
            {"type": "advice", "kind": "selection", "advice_id": "s3", "advice": {"ok": False}},
            {"type": "selection_record", "advice_id": "s2", "opp_pick_pred": {"combos": [1, 2]}, "candidates": {"rule": None}}]
    assert set(PL.selection_records_by_advice(recs)) == {"s1", "s2"}
    got = PL.last_selection_prediction(recs)
    assert got == {"advice_id": "s2", "candidates": {"rule": None}, "opp_pick_pred": {"combos": [1, 2]}}
    old = PL.last_selection_prediction(recs[:1])                  # 記録の行が無い (古いログ)
    assert old == {"advice_id": "s1", "candidates": None, "opp_pick_pred": None}
    assert PL.last_selection_prediction([{"type": "scene"}]) is None
    print("test_selection_prediction_join OK")


def main():
    test_pure_readers()
    test_selection_prediction_join()
    test_analyze_battles_uses_labels_with_fallback()
    test_party_improvements_and_bank_use_labels()
    print("ALL OK")


if __name__ == "__main__":
    main()
