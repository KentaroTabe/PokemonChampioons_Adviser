"""相手の型推定 (advisor/ev_infer) の検証。

使い方: python -m tests.test_ev_infer
"""
from advisor.damage import MonView
from advisor.dex import get_dex
from advisor.ev_infer import SpreadEstimator, SpreadTracker


def _mk_view(sid, ev, nature=None):
    sp = get_dex().species(sid)
    return MonView(species_id=sid, types=sp["types"], base=sp["baseStats"],
                   ev=ev, nature=nature or {})


def test_hypotheses_loaded():
    est = SpreadEstimator("garchomp")
    assert est.hyps, "ガブリアスの型仮説が空"
    natures = {h["nature"] for h in est.hyps}
    assert "jolly" in natures or "impish" in natures, natures
    print(f"test_hypotheses_loaded OK ({len(est.hyps)}仮説, {sorted(natures)})")


def test_speed_observation():
    est = SpreadEstimator("garchomp")
    # 相手ガブリアスが実速145の自分より先に動いた -> ようき/スカーフ系が残る
    opp_state = {"boosts": {}, "status": None}
    for _ in range(2):
        est.observe_speed(True, 145, opp_state)
    b = est.best()
    v = est._view([h for h in est.hyps if h["nature"] == b["nature"]
                   and h["evs"] == b["evs"] and h["item"] == b["item"]][0],
                  opp_state)
    spe = v.stat("spe") * (1.5 if b["item"] == "choicescarf" else 1.0)
    assert spe > 145, f"先行観測後も遅い型が最有力: {b['summary']} spe={spe}"
    print(f"test_speed_observation OK: {b['summary']}")


def test_damage_observation():
    # 自分のじゃれつく(ミミッキュA252珠)でガブリアスに約90% -> 無振り耐久系が有力
    est = SpreadEstimator("garchomp")
    atk = _mk_view("mimikyu", {"atk": 252}, {"atk": 1.1})
    atk.item = "lifeorb"
    opp_state = {"boosts": {}, "status": None}
    est.observe_damage(atk, True, "playrough", 92.0, opp_state)
    b = est.best()
    assert b["evs"].get("hp", 0) < 100, f"大ダメージ観測でHP振り型が残った: {b}"
    # 逆に約55%しか入らない -> HP振り耐久型が有力
    est2 = SpreadEstimator("garchomp")
    est2.observe_damage(atk, True, "playrough", 55.0, opp_state)
    b2 = est2.best()
    assert b2["evs"].get("hp", 0) >= 100, f"小ダメージ観測で殻型のまま: {b2}"
    print(f"test_damage_observation OK: 大ダメ->{b['summary']} / 小ダメ->{b2['summary']}")


def test_tracker_flow():
    import time
    tr = SpreadTracker()
    state = {
        "scene": "field", "turn": 3, "field": {},
        "player": {"active_index": 0, "party": [
            {"species_id": "mimikyu", "species_ja": "ミミッキュ",
             "types": ["ゴースト", "フェアリー"], "hp_percent": 100.0,
             "item_id": "lifeorb", "boosts": {}, "status": None}]},
        "opponent": {"active_index": 0, "party": [
            {"species_id": "garchomp", "species_ja": "ガブリアス",
             "types": ["ドラゴン", "じめん"], "hp_percent": 100.0,
             "boosts": {}, "status": None}]},
        "events": [],
    }
    tr.on_frame(state, ["move_player_playrough"])
    state["events"] = [{"source": "hp", "ts": time.time(),
                        "detail": {"side": "opponent", "from": 100.0, "to": 8.0}}]
    tr.on_frame(state, [])
    b = tr.best_for("garchomp")
    assert b and b["n_obs"] == 1, b
    print(f"test_tracker_flow OK: {b['summary']}")


if __name__ == "__main__":
    test_hypotheses_loaded()
    test_speed_observation()
    test_damage_observation()
    test_tracker_flow()
    print("ALL OK")
