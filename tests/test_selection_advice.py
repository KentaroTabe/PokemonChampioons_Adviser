"""選出アドバイザーのテスト。

    python -m tests.test_selection_advice
"""
from __future__ import annotations

from advisor.selection import advise_selection, format_selection_advice


def make_state(picked=0):
    """実戦相当のフィクスチャ: 自分6体(種族判明) vs 相手6枠(タイプのみ)"""
    def mon(species_id, species_ja, item_id=None, ability_id=None):
        return {"species_id": species_id, "species_ja": species_ja,
                "item_id": item_id, "ability_id": ability_id,
                "is_picked": False, "types": []}

    def opp(types):
        return {"species_id": None, "species_ja": None, "types": types}

    return {
        "selection_picked": picked,
        "player": {"party": [
            mon("pelipper", "ペリッパー", "damprock", ability_id="drizzle"),
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


def test_hazard_setter_becomes_lead():
    """設置技持ちは同等マッチアップなら先発に置かれる (欠陥#4)。

    同一種族3体 (マッチアップ完全同点) のうち、ステルスロック持ちの
    1体だけが先発に選ばれることで、先発ボーナスの効きを検証する。
    """
    def mon(ja, moves=None):
        return {"species_id": "garchomp", "species_ja": ja,
                "item_id": None, "ability_id": None,
                "is_picked": False, "types": [], "moves": moves or []}

    state = {
        "selection_picked": 0,
        "player": {"party": [
            mon("ガブA"),
            mon("ガブB", moves=[{"move_id": "stealthrock",
                                 "name_ja": "ステルスロック", "pp": 20}]),
            mon("ガブC"),
        ]},
        "opponent": {"party": [
            {"species_id": None, "species_ja": None, "types": ["みず"]},
        ]},
    }
    advice = advise_selection(state)
    assert advice["ok"], advice
    lead = next(r["name"] for r in advice["recommend"] if r["lead"])
    assert lead == "ガブB", advice["recommend"]
    print("test_hazard_setter_becomes_lead OK")


def test_advice_hysteresis():
    """同一ターン内の小差の入れ替わりでは前回の推奨を据え置く (欠陥#3)"""
    from advisor.service import apply_advice_hysteresis

    r1 = {"ok": True, "actions": [
        {"kind": "move", "id": "bravebird", "name": "ブレイブバード", "score": 93.6},
        {"kind": "move", "id": "doubleedge", "name": "すてみタックル", "score": 91.1},
    ], "best": None}
    r1["best"] = r1["actions"][0]
    out1, last = apply_advice_hysteresis(r1, None, turn=1)
    assert out1["actions"][0]["id"] == "bravebird"

    # 小差 (2.5点) の反転 → 据え置き
    r2 = {"ok": True, "actions": [
        {"kind": "move", "id": "doubleedge", "name": "すてみタックル", "score": 93.0},
        {"kind": "move", "id": "bravebird", "name": "ブレイブバード", "score": 90.5},
    ]}
    r2["best"] = r2["actions"][0]
    out2, last = apply_advice_hysteresis(r2, last, turn=1)
    assert out2["best"]["id"] == "bravebird", out2["best"]
    assert out2["actions"][0]["id"] == "bravebird"

    # 大差 (30点超) の反転 → 通す (新情報でダメ計が変わったケース)
    r3 = {"ok": True, "actions": [
        {"kind": "move", "id": "doubleedge", "name": "すてみタックル", "score": 91.1},
        {"kind": "move", "id": "bravebird", "name": "ブレイブバード", "score": 56.5},
    ]}
    r3["best"] = r3["actions"][0]
    out3, last = apply_advice_hysteresis(r3, last, turn=1)
    assert out3["best"]["id"] == "doubleedge", out3["best"]

    # ターンが変われば据え置きしない
    r4 = {"ok": True, "actions": [
        {"kind": "move", "id": "bravebird", "name": "ブレイブバード", "score": 60.0},
        {"kind": "move", "id": "doubleedge", "name": "すてみタックル", "score": 58.0},
    ]}
    r4["best"] = r4["actions"][0]
    out4, last = apply_advice_hysteresis(r4, last, turn=2)
    assert out4["best"]["id"] == "bravebird"
    assert last == {"turn": 2, "kind": "move", "id": "bravebird"}, last
    print("test_advice_hysteresis OK")


def test_advice_stability_gate():
    """推奨が連続2回一致するまで provisional、揺れ続けたら注記つきで出す (第3回)"""
    from advisor.service import (STABLE_MAX_WAIT_SEC, apply_advice_stability)

    def res(kind, mid):
        best = {"kind": kind, "id": mid, "name": mid, "score": 50.0}
        return {"ok": True, "actions": [best], "best": best, "speed_note": ""}

    # 1回目 = 確定前
    r1, h = apply_advice_stability(res("move", "a"), None, turn=1, now=100.0)
    assert r1.get("provisional") is True, r1
    # 2回目同じ = 確定
    r2, h = apply_advice_stability(res("move", "a"), h, turn=1, now=100.3)
    assert not r2.get("provisional"), r2
    # 別のbestへ揺れる = また確定前
    r3, h = apply_advice_stability(res("move", "b"), h, turn=1, now=100.6)
    assert r3.get("provisional") is True, r3
    # 制限時間を超えて揺れ続けたら、注記つきでそのまま出す
    r4, h = apply_advice_stability(res("move", "c"), h, turn=1,
                                   now=100.0 + STABLE_MAX_WAIT_SEC + 1.0)
    assert not r4.get("provisional"), r4
    assert "揺れています" in r4["speed_note"], r4["speed_note"]
    # ターンが変われば新規判定 (1回目=確定前)
    r5, h = apply_advice_stability(res("move", "c"), h, turn=2, now=200.0)
    assert r5.get("provisional") is True, r5
    print("test_advice_stability_gate OK")


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
    # メガストーン持ち(ラグラージ/ライチュウ)が2体同時に選出されないこと
    mega_count = sum(1 for r in rec if r["mega_holder"])
    assert mega_count <= 1, rec
    # 種族推測が表示されること (タイプのみの相手が6枠)
    assert advice["inference"], advice
    print("test_basic_recommendation OK")
    print(format_selection_advice(advice))


def test_weather_synergy_and_mega_form():
    # ペリッパー(あめふらし) + ラグラージ(ラグラージナイト=メガですいすい)
    # の雨シナジーが選出評価に反映されること
    state = make_state(picked=0)
    state["player"]["party"][1]["item_id"] = "swampertite"  # 実ストーンID
    advice = advise_selection(state)
    assert advice["ok"], advice
    names = [r["name"] for r in advice["recommend"]]
    # 雨コア2枚は揃って選出されるはず (シナジーボーナス)
    assert "ペリッパー" in names and "ラグラージ" in names, names
    syn = advice.get("synergy")
    assert syn and syn["weather"] == "rain", syn
    assert syn["setter"] == "ペリッパー", syn
    assert "ラグラージ" in syn["abusers"], syn
    # メガ枠が明示されること
    assigns = [r["name"] for r in advice["recommend"] if r.get("mega_assign")]
    assert assigns == ["ラグラージ"], advice["recommend"]
    text = format_selection_advice(advice)
    assert "シナジー" in text, text
    print("test_weather_synergy_and_mega_form OK")
    print(text)


def test_mega_form_evaluation():
    # メガ後の姿の行列が使われること (swampertmega が dex に存在する前提)
    from advisor.selection import _mega_species_id
    assert _mega_species_id("swampert", "swampertite") == "swampertmega"
    # X/Y分岐
    mega_x = _mega_species_id("charizard", "charizarditex")
    mega_y = _mega_species_id("charizard", "charizarditey")
    if mega_x or mega_y:   # dexにあるときのみ検証
        assert mega_x != mega_y
    print("test_mega_form_evaluation OK")


def test_type_inference():
    """ユーザー例: ほのお/ゴースト -> ラウドボーン or ソウブレイズ を使用率から推測"""
    from advisor.infer import get_inference
    cands = get_inference().candidates(["ほのお", "ゴースト"])
    assert cands, "候補が空"
    names = [ja for _sid, _p, ja in cands[:2]]
    assert "ラウドボーン" in names or "ソウブレイズ" in names, names
    total = sum(p for _s, p, _j in cands)
    assert abs(total - 1.0) < 0.01
    print(f"test_type_inference OK: {[(ja, round(p, 2)) for _s, p, ja in cands[:3]]}")


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


def test_partial_reads_do_not_crash():
    """読み取りが部分的な瞬間 (2026-08-05接続テストで実際に発生) の耐性。

    - 相手のタイプ2枠のうち1枠だけ読めて [str, None] になる
    - 自分のパーティに種族未判明の欠け枠が混ざる
    どちらも従来は join / インデックスずれで落ちていた。
    """
    state = make_state()
    # 相手: タイプ欠け (Noneが混ざる)
    state["opponent"]["party"][0]["types"] = ["ほのお", None]
    state["opponent"]["party"][1]["types"] = [None]
    # 自分: 2枠が未判明 (species_idなし) — モデル推しのインデックスずれ検証
    state["player"]["party"][1]["species_id"] = None
    state["player"]["party"][1]["species_ja"] = None
    state["player"]["party"][3]["species_id"] = None
    state["player"]["party"][3]["species_ja"] = None
    advice = advise_selection(state)
    text = format_selection_advice(advice)   # 従来ここで落ちた
    assert isinstance(text, str) and text
    if advice.get("model_pick"):
        assert all(n for n in advice["model_pick"]["names"]), \
            advice["model_pick"]
    print("test_partial_reads_do_not_crash OK")


if __name__ == "__main__":
    test_hazard_setter_becomes_lead()
    test_advice_hysteresis()
    test_advice_stability_gate()
    test_basic_recommendation()
    test_weather_synergy_and_mega_form()
    test_mega_form_evaluation()
    test_type_inference()
    test_done_detection()
    test_insufficient_info()
    test_partial_reads_do_not_crash()
    print("\nALL OK")
