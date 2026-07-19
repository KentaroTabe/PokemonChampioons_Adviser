"""選出アドバイザーのテスト。

    python -m tests.test_selection_advice
"""
from __future__ import annotations

from advisor.selection import advise_selection, format_selection_advice


def make_state(picked=0):
    """実戦相当のフィクスチャ: 自分6体(種族判明) vs 相手6枠(タイプのみ)"""
    def mon(species_id, species_ja, item_id=None):
        return {"species_id": species_id, "species_ja": species_ja,
                "item_id": item_id, "is_picked": False, "types": []}

    def opp(types):
        return {"species_id": None, "species_ja": None, "types": types}

    return {
        "selection_picked": picked,
        "player": {"party": [
            mon("pelipper", "ペリッパー", "damprock"),
            mon("swampert", "ラグラージ", "megastone"),
            mon("mimikyu", "ミミッキュ", "lifeorb"),
            mon("duraludon", "ブリジュラス", "leftovers"),
            mon("rotomheat", "ロトム", "sitrusberry"),
            mon("raichu", "ライチュウ", "megastone"),
        ]},
        "opponent": {"party": [
            opp(["ほのお", "ひこう"]),   # リザードン系
            opp(["じめん"]),             # カバルドン系
            opp(["はがね", "エスパー"]),  # メタグロス系
            opp(["あく", "はがね"]),      # キリキザン系
            opp(["みず", "あく"]),
            opp(["ドラゴン", "ゴースト"]),
        ]},
    }


def test_basic_recommendation():
    state = make_state(picked=0)
    advice = advise_selection(state)
    assert advice["ok"], advice
    assert advice["done"] is False
    assert advice["picked"] == 0
    rec = advice["recommend"]
    assert len(rec) == 3
    names = [r["name"] for r in rec]
    assert len(set(names)) == 3
    leads = [r for r in rec if r["lead"]]
    assert len(leads) == 1
    # 相手にほのお/ひこう・じめんがいる -> みず打点(ペリッパー/ラグラージ)の
    # どちらかは選出に入るはず
    assert any(n in names for n in ("ペリッパー", "ラグラージ")), names
    print("test_basic_recommendation OK")
    print(format_selection_advice(advice))


def test_done_detection():
    state = make_state(picked=3)
    advice = advise_selection(state)
    assert advice["done"] is True
    text = format_selection_advice(advice)
    assert "選出完了" in text
    print("test_done_detection OK")


def test_insufficient_info():
    state = make_state()
    for p in state["player"]["party"]:
        p["species_id"] = None
    advice = advise_selection(state)
    assert advice["ok"] is False
    assert "不足" in advice["reason"]
    print("test_insufficient_info OK")


if __name__ == "__main__":
    test_basic_recommendation()
    test_done_detection()
    test_insufficient_info()
    print("\nALL OK")
