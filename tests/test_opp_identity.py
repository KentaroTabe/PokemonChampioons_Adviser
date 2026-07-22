"""相手ポケモンの同定まわりのテスト。

    scripts/run_test.sh test_opp_identity

- HUD名不一致ガード: 交代を見逃した状態で新ポケモンの表示名が
  前のポケモンのdisplay_nameへ上書きされない (技の誤帰属の根本原因)
- いたずらごころ: 先後観測のスキップ / 探索の優先度補正
"""
from __future__ import annotations

from unittest import mock

import numpy as np


def test_hud_name_mismatch_guard():
    from vision.state import BattleStateV2, PokemonState
    from vision import extractors, zones

    state = BattleStateV2()
    state.opponent.party = [
        PokemonState(species_ja="ヤミラミ", species_id="sableye")]
    state.opponent.switch_to(0)
    mon = state.opponent.party[0]
    mon.display_name = "ヤミラミ"

    # HUDに「別のポケモンの名前」(交代見逃し) が表示されたケース
    def fake_read(img, zone, **kw):
        if zone is zones.BATTLE["opp_name"]:
            return "タメルテンキ"
        return ""

    img = np.zeros((1080, 1920, 3), np.uint8)
    with mock.patch.object(extractors.ocr, "read_zone_text",
                           side_effect=fake_read):
        extractors.extract_battle_hud(img, state, extractors_resolver())

    assert mon.display_name == "ヤミラミ", mon.display_name
    assert any(e.get("event") == "hud_name_mismatch"
               for e in state.events), [e.get("event") for e in state.events]

    # OCR揺れ程度 (類似名) は従来どおり更新される
    def fake_read2(img, zone, **kw):
        if zone is zones.BATTLE["opp_name"]:
            return "ヤミラ三"
        return ""
    with mock.patch.object(extractors.ocr, "read_zone_text",
                           side_effect=fake_read2):
        extractors.extract_battle_hud(img, state, extractors_resolver())
    assert mon.display_name == "ヤミラ三", mon.display_name
    print("test_hud_name_mismatch_guard OK")


_RESOLVER = None


def extractors_resolver():
    global _RESOLVER
    if _RESOLVER is None:
        from vision.normalize import NameResolver
        _RESOLVER = NameResolver()
    return _RESOLVER


def test_prankster_speed_observation_skipped():
    from advisor.ev_infer import SpreadTracker

    tracker = SpreadTracker()
    state = {
        "field": {},
        "player": {"active_index": 0, "party": [
            {"species_id": "pelipper", "species_ja": "ペリッパー",
             "types": ["みず", "ひこう"], "hp_percent": 100.0}]},
        "opponent": {"active_index": 0, "party": [
            {"species_id": "sableye", "species_ja": "ヤミラミ",
             "types": ["あく", "ゴースト"], "hp_percent": 100.0}]},
    }
    est = tracker.estimator("sableye")
    # いたずらごころ持ち (可能特性) の変化技先制 -> 観測しない
    tracker._observe_order(state, (10.0, "surf"), (9.0, "willowisp"))
    assert est.n_obs == 0, est.n_obs
    # 攻撃技同士なら通常どおり観測する
    tracker._observe_order(state, (10.0, "surf"), (9.0, "knockoff"))
    assert est.n_obs == 1, est.n_obs
    print("test_prankster_speed_observation_skipped OK")


def test_prankster_search_priority():
    from advisor.search import _priority
    from advisor.damage import MonView
    yam = MonView(species_id="sableye", types=["Dark", "Ghost"],
                  base={"spe": 50}, ability="prankster")
    plain = MonView(species_id="sableye", types=["Dark", "Ghost"],
                    base={"spe": 50})
    # 変化技はいたずらごころで+1、攻撃技は変わらない
    assert _priority("willowisp", yam) == _priority("willowisp", plain) + 1
    assert _priority("knockoff", yam) == _priority("knockoff", plain)
    print("test_prankster_search_priority OK")


if __name__ == "__main__":
    test_hud_name_mismatch_guard()
    test_prankster_speed_observation_skipped()
    test_prankster_search_priority()
    print("\nALL OK")
