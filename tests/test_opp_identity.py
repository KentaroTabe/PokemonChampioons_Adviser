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


def test_watch_screen_attribution():
    # 様子見画面: 表示中の個体を特定してから書き込む (実戦: ブリジュラスの
    # 詳細を見た瞬間に場のメガラグラージがドラゴンタイプになった)
    from vision.state import BattleStateV2, PokemonState
    from vision import extractors, zones

    state = BattleStateV2()
    state.player.party = [
        PokemonState(species_ja="ラグラージ", species_id="swampert"),
        PokemonState(species_ja="ブリジュラス", species_id="duraludon"),
    ]
    state.player.switch_to(0)
    swampert = state.player.party[0]
    duraludon = state.player.party[1]

    def make_read(type_text):
        def fake_read(img, zone, **kw):
            if zone is zones.WATCH["type_row"]:
                return type_text
            return ""
        return fake_read

    img = np.zeros((1080, 1920, 3), np.uint8)
    # ブリジュラスの詳細 (はがね/ドラゴン) を見た -> ブリジュラスへ帰属
    with mock.patch.object(extractors.ocr, "read_zone_text",
                           side_effect=make_read("はがね ドラゴン")):
        extractors.extract_watch(img, state, extractors_resolver())
    assert swampert.types == [], swampert.types          # 汚染されない
    assert set(duraludon.types) == {"はがね", "ドラゴン"}, duraludon.types

    # 誰の図鑑タイプとも一致しない読取 -> 書き込まない
    with mock.patch.object(extractors.ocr, "read_zone_text",
                           side_effect=make_read("ドラゴン フェアリー")):
        extractors.extract_watch(img, state, extractors_resolver())
    assert swampert.types == [] and set(duraludon.types) == {"はがね", "ドラゴン"}

    # activeと一致する読取は従来どおりactiveへ
    with mock.patch.object(extractors.ocr, "read_zone_text",
                           side_effect=make_read("みず じめん")):
        extractors.extract_watch(img, state, extractors_resolver())
    assert set(swampert.types) == {"みず", "じめん"}, swampert.types
    print("test_watch_screen_attribution OK")


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


def test_alias_cache():
    # 個体名キャッシュ: 手動確定で紐づいた別名が以後のイベント帰属に効く
    from vision.state import BattleStateV2, PokemonState
    from vision.events import EventParser

    state = BattleStateV2()
    state.opponent.party = [
        PokemonState(species_ja="ハラバリー", species_id="bellibolt"),
        PokemonState(species_ja="ヤミラミ", species_id="sableye"),
    ]
    state.opponent.switch_to(1)   # activeはヤミラミ
    p = EventParser(state, extractors_resolver())

    # 別名なし: 崩れた名前の技は誰の判明技にもならない (イベントのみ)
    fired = p.parse("相手のタメルテンキのパラボラチャージ")
    assert any("paraboliccharge" in f for f in fired), fired
    assert not state.opponent.party[0].revealed_moves
    assert not state.opponent.party[1].revealed_moves

    # 手動確定相当: 「タメルテンキ」をハラバリーの別名として登録
    state.opponent.party[0].aliases.append("タメルテンキ")
    fired = p.parse("相手のタメルテンキのみずびたし")
    assert any("soak" in f for f in fired), fired
    assert "みずびたし" in state.opponent.party[0].revealed_moves, \
        state.opponent.party[0].revealed_moves
    assert not state.opponent.party[1].revealed_moves
    # find_by_display_name も別名で引ける (HUD追跡の復帰用)
    assert state.opponent.find_by_display_name("タメルテンキ") == 0
    print("test_alias_cache OK")


def test_roster_lock():
    # 満枠 (6) のロスターは試合中に増えない (帰属不明は使い捨て枠が吸収)
    from vision.state import SideState, PokemonState
    side = SideState()
    side.party = [PokemonState(species_ja=f"モン{i}") for i in range(6)]
    side.active_index = None
    mon = side.ensure_active()
    assert len(side.party) == 6, len(side.party)   # 7枠目が生えない
    assert mon not in side.party
    print("test_roster_lock OK")


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
    test_watch_screen_attribution()
    test_prankster_speed_observation_skipped()
    test_alias_cache()
    test_roster_lock()
    test_prankster_search_priority()
    print("\nALL OK")
