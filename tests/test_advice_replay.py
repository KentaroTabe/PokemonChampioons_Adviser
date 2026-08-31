"""決定再生ハーネス (tools/advice_replay) のテスト。

    scripts/run_test.sh test_advice_replay

エンジン実行は伴わない: 簡約状態の復元と摂動の適用可否 (純粋計算) を検証。
"""
from __future__ import annotations

from tools.advice_replay import (
    PERTURBATIONS, compact_to_engine_state, p_boosts_missed,
    p_opp_faint_missed, p_opp_hp_stale, p_picks_lost,
)


class _R:
    def ja_of(self, cat, v):
        return {"playrough": "じゃれつく"}.get(v)


def _compact():
    return {
        "field": {"weather": None, "terrain": None, "trick_room": False},
        "mega_used": {"player": False, "opponent": False},
        "player": {"active": 0, "remaining": 2, "tailwind": False,
                   "hazards": None, "screens": None,
                   "party": [
                       {"species": "mimikyu", "ja": "ミミッキュ",
                        "types": ["ゴースト", "フェアリー"], "hp": 80.0,
                        "hp_raw": [104, 131], "status": None,
                        "boosts": {"atk": 2}, "mega": False,
                        "item": "lifeorb", "ability": "disguise",
                        "moves": [["playrough", 10]],
                        "revealed": [], "picked": True},
                       {"species": "staraptor", "ja": "ムクホーク",
                        "types": ["ノーマル", "ひこう"], "hp": 0.0,
                        "hp_raw": [0, 181], "status": "fainted",
                        "boosts": {}, "mega": False, "item": None,
                        "ability": None, "moves": [], "revealed": [],
                        "picked": True}]},
        "opponent": {"active": 0, "remaining": 2, "tailwind": False,
                     "hazards": None, "screens": None,
                     "party": [
                         {"species": "garchomp", "ja": "ガブリアス",
                          "types": ["ドラゴン", "じめん"], "hp": 60.0,
                          "hp_raw": [None, None], "status": None,
                          "boosts": {}, "mega": False, "item": "lifeorb",
                          "ability": None, "moves": [],
                          "revealed": ["じしん"], "picked": False},
                         {"species": "gengar", "ja": "ゲンガー",
                          "types": ["ゴースト", "どく"], "hp": 0.0,
                          "hp_raw": [None, None], "status": "fainted",
                          "boosts": {}, "mega": False, "item": None,
                          "ability": None, "moves": [], "revealed": [],
                          "picked": False}]},
    }


def test_compact_to_engine_state():
    st = compact_to_engine_state(_compact(), _R())
    me = st["player"]["party"][0]
    assert me["species_id"] == "mimikyu" and me["hp_percent"] == 80.0
    assert me["hp_current"] == 104 and me["hp_max"] == 131
    assert me["moves"][0]["move_id"] == "playrough"
    assert me["moves"][0]["name_ja"] == "じゃれつく"   # ja復元
    assert me["boosts"] == {"atk": 2} and me["is_picked"]
    assert st["player"]["hazards"]["stealth_rock"] is False   # 既定補完
    assert st["opponent"]["party"][0]["revealed_moves"] == ["じしん"]
    print("test_compact_to_engine_state OK")


def test_perturbations_applicability():
    st = compact_to_engine_state(_compact(), _R())
    # 相手HP60% → +25%の固着模擬が適用でき、基準状態は不変 (deepcopy)
    p = p_opp_hp_stale(st)
    assert p is not None
    assert p["opponent"]["party"][0]["hp_percent"] == 85.0
    assert st["opponent"]["party"][0]["hp_percent"] == 60.0
    # 相手にひんしが居る → 蘇生模擬が適用できる
    p2 = p_opp_faint_missed(st)
    assert p2 is not None
    assert p2["opponent"]["party"][1]["status"] is None
    assert p2["opponent"]["party"][1]["hp_percent"] == 25.0
    # ランク変化あり → 消去模擬適用
    p3 = p_boosts_missed(st)
    assert p3 is not None
    assert p3["player"]["party"][0]["boosts"] == {}
    # 選出フラグあり → 消去適用
    p4 = p_picks_lost(st)
    assert p4 is not None
    assert not any(m["is_picked"] for m in p4["player"]["party"])
    # 適用不能ケース: 相手が高HPなら固着模擬はスキップ
    st2 = compact_to_engine_state(_compact(), _R())
    st2["opponent"]["party"][0]["hp_percent"] = 100.0
    assert p_opp_hp_stale(st2) is None
    # 全摂動が (名前, 関数) で列挙されている
    assert len(PERTURBATIONS) == 8
    print("test_perturbations_applicability OK")


def main() -> None:
    test_compact_to_engine_state()
    test_perturbations_applicability()
    print("\nALL OK")


if __name__ == "__main__":
    main()
