"""選出の advice 行に足す欄 (advisor.selection_record、2026-10-07 段 0) の検証。

candidates: 方式ごとの候補を同じ形に揃える / 登録チーム専用モデルが無ければ null と理由。
opp_pick_pred: combo_prior の全分布 (6 枠判明なら 20 通り) / 未判明の枠があれば incomplete / 確定と推定の区別 / バンクの版。

    python -m tests.test_selection_record
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from advisor import selection_record as SR


def _p(sid, ja, guess=False):
    return {"species_id": sid, "species_ja": ja, "species_guess": guess}


MY = [_p("archaludon", "ブリジュラス"), _p("raichu", "ライチュウ"), _p(None, None), _p("garchomp", "ガブリアス"),
      _p("kingambit", "ドドゲザン"), _p("gholdengo", "サーフゴー"), _p("rotomwash", "ウォッシュロトム")]


def test_build_candidates_same_shape():
    advice = {"ok": True, "primary": "model",
              "recommend": [{"index": 3, "name": "ガブリアス", "lead": True}],
              "rule_recommend": [{"index": 0, "name": "ブリジュラス", "lead": False},
                                 {"index": 1, "name": "ライチュウ", "lead": True},
                                 {"index": 4, "name": "ドドゲザン", "lead": False}],
              "model_pick": {"model": "deployed"},
              "model_pick_real": {"names": ["サーフゴー", "ガブリアス", "ブリジュラス"], "expected_win_prob": 0.6123}}
    # perm は species_id のある枠だけの座標 (2 番の空枠を飛ばす): 2 → 枠 3 (ガブリアス)、0 → 枠 0
    res = {"deployed": ([2, 0, 3], 0.71234), "general": ([4, 5, 1], 0.5)}
    c = SR.build_candidates(advice, MY, res, {"registered": "未登録 (鍵 x)"})
    keys = {"indices", "species", "names", "lead", "lead_index", "prob", "win_prob"}
    assert set(c["rule"]) == keys and set(c["deployed"]) == keys and set(c["model_pick_real"]) == keys
    assert c["rule"]["indices"] == [0, 1, 4] and c["rule"]["lead"] == "ライチュウ" and c["rule"]["win_prob"] is None
    assert c["deployed"]["indices"] == [3, 0, 4] and c["deployed"]["lead"] == "ガブリアス" and c["deployed"]["win_prob"] == 0.7123
    assert c["general"]["species"] == ["gholdengo", "rotomwash", "raichu"]
    assert c["model_pick_real"]["indices"] == [5, 3, 0] and c["model_pick_real"]["win_prob"] == 0.6123
    assert c["registered"] is None and c["reasons"]["registered"] == "未登録 (鍵 x)"
    assert c["experiment"] is None and "experiment" in c["reasons"]
    assert "rule" not in c["reasons"] and c["primary"] == "model" and c["used"] == "deployed"
    # 第一候補が規則のときは recommend が規則の推奨
    c2 = SR.build_candidates({"ok": True, "primary": "rule", "recommend": advice["rule_recommend"]}, MY, {})
    assert c2["rule"]["indices"] == [0, 1, 4] and c2["model_pick_real"] is None and "model_pick_real" in c2["reasons"]
    c3 = SR.build_candidates({"ok": False, "reason": "情報不足"}, MY, {})
    assert c3["rule"] is None and c3["reasons"]["rule"] == "情報不足"
    json.dumps(c)                                   # 対戦ログにそのまま書ける
    print("test_build_candidates_same_shape OK")


def test_selection_candidates_with_injected_scorer():
    seen = []

    def scorer(mine, opp, path):
        seen.append(path)
        return [((0, 1, 2), 0.66), ((1, 2, 3), 0.5)]

    c = SR.selection_candidates({"ok": True, "primary": "rule", "recommend": []}, MY, [_p("a", "A")], score_fn=scorer)
    for m in ("deployed", "general", "registered", "experiment"):
        assert (c[m] is None) == (m in c["reasons"]), (m, c)
    # モデルの有無に依らず確かめる: モデルのパスを差し替える
    orig = SR.model_sources
    try:
        SR.model_sources = lambda mine: ({"deployed": Path("d.pt"), "general": Path("g.pt")},
                                         {"registered": "未登録", "experiment": "試用中 Package なし"})
        c = SR.selection_candidates({"ok": True, "primary": "rule", "recommend": []}, MY, [_p("a", "A")], score_fn=scorer)
    finally:
        SR.model_sources = orig
    assert c["deployed"]["win_prob"] == 0.66 and c["deployed"]["indices"] == [0, 1, 3] and c["general"] is not None
    assert c["registered"] is None and c["reasons"]["registered"] == "未登録"
    assert seen[-2:] == [Path("d.pt"), Path("g.pt")]
    few = SR.selection_candidates({"ok": True}, [_p("a", "A")], [], score_fn=scorer)
    assert few["deployed"] is None and "1 体" in few["reasons"]["deployed"]
    print("test_selection_candidates_with_injected_scorer OK")


def test_opp_pick_prediction_full_distribution():
    from champions_agent.agent.selection_model import combo_prior
    opp = [_p("a", "A"), _p("b", "B"), _p("c", "C", guess=True), _p("d", "D"), _p("e", "E"), _p("f", "F")]
    rates = {"a": 0.9, "b": 0.2, "c": None, "d": 0.5, "e": 0.5, "f": 0.1}
    bank = {"sha256": "abc", "cutoff": 1791300000.0, "cutoff_basis": "data_until"}
    pred = SR.opp_pick_prediction(opp, lambda sid: rates.get(sid), bank, prior_fn=combo_prior)
    assert len(pred["combos"]) == 20 and abs(sum(c["p"] for c in pred["combos"]) - 1.0) < 1e-4
    assert pred["incomplete"] is False and pred["n_known"] == 6 and pred["n_guess"] == 1
    assert [s["status"] for s in pred["slots"]] == ["confirmed", "confirmed", "guess", "confirmed", "confirmed", "confirmed"]
    assert pred["slots"][2]["pick_prob"] is None and pred["bank"] == bank
    top = max(pred["combos"], key=lambda c: c["p"])
    assert "a" in top["species"] and "f" not in top["species"]
    # 未判明の枠: 判明している枠だけの分布 + incomplete
    opp2 = opp[:4] + [_p(None, None), {"types": ["みず"]}]
    pred2 = SR.opp_pick_prediction(opp2, lambda sid: rates.get(sid), None, prior_fn=combo_prior)
    assert pred2["incomplete"] is True and pred2["n_known"] == 4 and len(pred2["combos"]) == 4
    assert pred2["slots"][5]["status"] == "unknown" and pred2["combos"][0]["slots"] == [0, 1, 2]
    assert SR.opp_pick_prediction(opp[:2], lambda sid: None, None, prior_fn=combo_prior)["combos"] == []
    json.dumps(pred)
    print("test_opp_pick_prediction_full_distribution OK")


def test_bank_version_and_data_until():
    from advisor.real_prior import bank_version
    from tools.real_opponents import build_bank
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "bank.json"
        p.write_text(json.dumps({"built_at": "2026-10-07 01:00", "n_battles": 3, "species": {}}), encoding="utf-8")
        v = bank_version(p)
        assert v["cutoff"] == "2026-10-07 01:00" and v["cutoff_basis"] == "built_at" and len(v["sha256"]) == 16
        p.write_text(json.dumps({"built_at": "x", "data_until": 1791300000.5, "species": {}}), encoding="utf-8")
        import os
        os.utime(p, (1, 2))                      # mtime を変えてキャッシュを外す
        v2 = bank_version(p)
        assert v2["cutoff"] == 1791300000.5 and v2["cutoff_basis"] == "data_until"
        assert bank_version(Path(td) / "none.json") is None
    battles = [{"opp_roster": ["A", "B", "C", "D"], "opp_fielded": ["A"], "t1": 100.0},
               {"opp_roster": ["A", "B", "C", "D"], "opp_fielded": ["B"], "t1": 250.0},
               {"opp_roster": ["A"], "opp_fielded": ["A"], "t1": 999.0}]          # ロースター不足で使わない
    bank = build_bank(battles, lambda ja: ja.lower())
    assert bank["data_until"] == 250.0
    print("test_bank_version_and_data_until OK")


def main():
    test_build_candidates_same_shape()
    test_selection_candidates_with_injected_scorer()
    test_opp_pick_prediction_full_distribution()
    test_bank_version_and_data_until()
    print("ALL OK")


if __name__ == "__main__":
    main()
