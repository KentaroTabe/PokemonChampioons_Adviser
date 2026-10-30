"""延長の持ち物 (しめったいわ・あついいわ・さらさらいわ・つめたいいわ・グランドコート・ひかりのねんど) のテスト。

設定者の持ち物が分かるとき、天候・フィールドの残りターンを開始時に延長後のターン (8) にする。
表は advisor/data/field_effects.json の duration、読み取りは advisor.dex.effect_duration
(2026-10-30、docs/DESIGN_ITEM_INFERENCE_20261030.md §6.4 論点 5)。

    python -m tests.test_field_extender
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

from advisor.dex import effect_duration, field_effects
from advisor.engine import build_field_view
from vision.events import EventParser
from vision.normalize import NameResolver
from vision.pipeline import VisionPipeline
from vision.state import BattleStateV2, PokemonState

resolver = NameResolver()
ROOT = Path(__file__).resolve().parent.parent


def new_parser(player_mons, active=0, opponent_mons=None):
    """player_mons: [(species_ja, species_id, item_id)]。active の枠を場に出す"""
    state = BattleStateV2()
    state.player.party = [PokemonState(species_ja=ja, species_id=sid, item_id=item)
                          for ja, sid, item in player_mons]
    state.player.switch_to(active)
    if opponent_mons:
        state.opponent.party = [PokemonState(species_ja=ja, species_id=sid, item_id=item)
                                for ja, sid, item in opponent_mons]
        state.opponent.switch_to(0)
    return state, EventParser(state, resolver)


def tick(state, n):
    """新ターン確定時の減算を n 回 (VisionPipeline._tick_field_effects は self.state しか使わない)"""
    holder = SimpleNamespace(state=state)
    for _ in range(n):
        VisionPipeline._tick_field_effects(holder)


def age_recent(p, seconds):
    """重複排除の記録時刻を seconds 秒だけ過去にずらす (時間経過の模擬)"""
    for k in list(p._recent_fired):
        p._recent_fired[k] -= seconds


PELIPPER_DAMP = [("ペリッパー", "pelipper", "damprock")]


def test_damprock_drizzle_8_turns():
    """(a) しめったいわ持ちの自分の あめふらし で 8。5 回の減算では残り、8 回で消える"""
    state, p = new_parser(PELIPPER_DAMP)
    p.parse("ペリッパーの あめふらし", source="left_popup")
    assert state.field.weather == "rain" and state.field.weather_turns == 8, state.field
    tick(state, 5)
    assert state.field.weather == "rain" and state.field.weather_turns == 3, state.field
    tick(state, 3)
    assert state.field.weather is None and state.field.weather_turns is None, state.field
    print("test_damprock_drizzle_8_turns OK")


def test_terrain_extender_psychic_surge_8():
    """(b) グランドコート + サイコメイカー でフィールド 8"""
    state, p = new_parser([("イエッサン", "indeedee", "terrainextender")])
    p.parse("イエッサンの サイコメイカー", source="left_popup")
    assert state.field.terrain == "psychic" and state.field.terrain_turns == 8, state.field
    tick(state, 7)
    assert state.field.terrain == "psychic", state.field
    tick(state, 1)
    assert state.field.terrain is None, state.field
    print("test_terrain_extender_psychic_surge_8 OK")


def test_light_clay_screens_8():
    """(c) ひかりのねんど + 壁 で 8 (表と読み取り)。壁の残りターンの欄は state に無いので、ここでは継続ターンの表だけを確かめる
    (欄を足すのは対戦ログのスキーマの変更になるため今回はしない)。壁の展開そのものは従来どおり"""
    for wall in ("reflect", "light_screen", "aurora_veil"):
        assert effect_duration(wall, "lightclay") == 8, wall
        assert effect_duration(wall, None) == 5, wall
        assert effect_duration(wall, "damprock") == 5, wall
    state, p = new_parser([("ペリッパー", "pelipper", "lightclay")])
    p.parse("ペリッパーの リフレクター!")
    assert state.player.reflect is True
    print("test_light_clay_screens_8 OK")


def test_base_turns_when_unknown_or_consumed():
    """(d) 持ち物なし・不明 → 5、消費・喪失済み → 5、設定者不明の文言だけの開始 → 5"""
    from advisor.my_team import clear_registry_override, set_registry_override
    set_registry_override({})   # 登録 (config/my_team.json、未コミット) の有無で結果が変わらないように空の登録にする
    try:
        state, p = new_parser([("ペリッパー", "pelipper", None)])
        p.parse("ペリッパーの あめふらし", source="left_popup")
        assert state.field.weather_turns == 5, state.field
    finally:
        clear_registry_override()

    state, p = new_parser([("ペリッパー", "pelipper", "leftovers")])
    p.parse("ペリッパーの あめふらし", source="left_popup")
    assert state.field.weather_turns == 5, state.field

    for flag in ("item_consumed", "item_removed"):
        state, p = new_parser(PELIPPER_DAMP)
        setattr(state.player.active(), flag, True)
        p.parse("ペリッパーの あめふらし", source="left_popup")
        assert state.field.weather == "rain" and state.field.weather_turns == 5, (flag, state.field)

    # 文言だけ (直前に設定者の分かる開始が無い): 場の個体がしめったいわ持ちでも基本のターン
    state, p = new_parser(PELIPPER_DAMP)
    p.parse("雨が 降り始めた!")
    assert state.field.weather == "rain" and state.field.weather_turns == 5, state.field

    # 相手側: 持ち物は原則不明 → 5
    state, p = new_parser([("ブリジュラス", "duraludon", None)],
                          opponent_mons=[("ペリッパー", "pelipper", None)])
    p.parse("相手の ペリッパーの あまごい!")
    p.parse("雨が 降り始めた!")
    assert state.field.weather == "rain" and state.field.weather_turns == 5, state.field

    # トリックルームは延長の持ち物なし → 5
    state, p = new_parser([("ペリッパー", "pelipper", None)])
    p.parse("ゆがんだ時空を作り出した!")
    assert state.field.trick_room and state.field.trick_room_turns == 5, state.field
    print("test_base_turns_when_unknown_or_consumed OK")


def test_registered_item_fills_player_item():
    """自分側で state に持ち物が無ければ登録 (my_team) の持ち物で補う"""
    from advisor.my_team import clear_registry_override, set_registry_override
    set_registry_override({"ペリッパー": {"持ち物": "しめったいわ"}})
    try:
        state, p = new_parser([("ペリッパー", "pelipper", None)])
        p.parse("ペリッパーの あめふらし", source="left_popup")
        assert state.field.weather_turns == 8, state.field
        # はたきおとされた後は登録から補わない
        state, p = new_parser([("ペリッパー", "pelipper", None)])
        state.player.active().item_removed = True
        p.parse("ペリッパーの あめふらし", source="left_popup")
        assert state.field.weather_turns == 5, state.field
    finally:
        clear_registry_override()
    print("test_registered_item_fills_player_item OK")


def test_move_start_uses_user_item_both_orders():
    """技による開始: 技の使用 → 開始の文言、開始の文言 → 技の使用 (連結文を含む) のどちらでも使用者の持ち物で 8"""
    state, p = new_parser(PELIPPER_DAMP)
    p.parse("ペリッパーの あまごい!")
    assert state.field.weather is None   # 技の使用だけでは天候を張らない (従来どおり、文言が張る)
    p.parse("雨が 降り始めた!")
    assert state.field.weather == "rain" and state.field.weather_turns == 8, state.field

    state, p = new_parser(PELIPPER_DAMP)
    p.parse("雨が 降り始めた!")
    assert state.field.weather_turns == 5
    p.parse("ペリッパーの あまごい!")
    assert state.field.weather == "rain" and state.field.weather_turns == 8, state.field

    state, p = new_parser([("イエッサン", "indeedee", "terrainextender")])
    p.parse("イエッサンの サイコフィールド!")
    p.parse("足元が 不思議な感じに なった!")
    assert state.field.terrain == "psychic" and state.field.terrain_turns == 8, state.field
    print("test_move_start_uses_user_item_both_orders OK")


def test_restart_message_does_not_reset():
    """(e) 特性の表示で張った後の開始の文言・その再読みで残りターンが戻らない。窓の外の文言だけの再開始は基本のターン"""
    state, p = new_parser(PELIPPER_DAMP)
    p.parse("ペリッパーの あめふらし", source="left_popup")
    p.parse("雨が 降り始めた!")
    assert state.field.weather_turns == 8, state.field
    tick(state, 1)
    p.parse("雨が 降りはじめた!")   # 窓内の再読み (OCR 揺れ)
    assert state.field.weather_turns == 7, state.field

    # 文言が先、表示が後でも 8
    state, p = new_parser(PELIPPER_DAMP)
    p.parse("雨が 降り始めた!")
    p.parse("ペリッパーの あめふらし", source="left_popup")
    assert state.field.weather_turns == 8, state.field

    # 窓の外の文言だけの開始 (設定者不明の本当の再発動) は再設定してよい: 基本のターン
    age_recent(p, 30.0)
    tick(state, 3)
    p.parse("雨が 降り始めた!")
    assert state.field.weather == "rain" and state.field.weather_turns == 5, state.field
    print("test_restart_message_does_not_reset OK")


def test_build_field_view_keeps_weather_turns_6_to_8():
    """(f) 助言の入力が 6〜8 ターン目も天候ありを返し、9 ターン目 (8 回の減算後) は無し"""
    state, p = new_parser(PELIPPER_DAMP)
    p.parse("ペリッパーの あめふらし", source="left_popup")
    tick(state, 5)
    for _ in range(3):
        assert build_field_view(state.to_dict(), "player").weather == "rain", state.field
        tick(state, 1)
    assert build_field_view(state.to_dict(), "player").weather is None, state.field
    print("test_build_field_view_keeps_weather_turns_6_to_8 OK")


def test_duration_table_and_no_hardcoded_turns():
    """(g) 表の読み込みと、vision の開始・減算に直書きの継続ターンが無いこと"""
    dur = field_effects().get("duration") or {}
    base = dur.get("base") or {}
    for eff in ("sun", "rain", "sandstorm", "snow", "electric", "grassy", "psychic", "misty",
                "reflect", "light_screen", "aurora_veil", "trick_room"):
        assert base.get(eff) == 5, (eff, base)
    pairs = {"sun": "heatrock", "rain": "damprock", "sandstorm": "smoothrock", "snow": "icyrock",
             "electric": "terrainextender", "grassy": "terrainextender", "psychic": "terrainextender",
             "misty": "terrainextender"}
    for eff, item in pairs.items():
        assert effect_duration(eff, item) == 8, (eff, item)
        assert effect_duration(eff, None) == 5, eff
    assert effect_duration("rain", "heatrock") == 5   # 別の天候の岩は延長しない
    assert effect_duration("trick_room", "lightclay") == 5
    assert effect_duration("unknown_effect", "damprock") is None

    pat = re.compile(r"(weather|terrain|trick_room)_turns\s*=\s*\d|_turns\s*=\s*\d+\s+if\b")
    for rel in ("vision/events.py", "vision/pipeline.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        hits = [ln for ln in src.splitlines() if pat.search(ln)]
        assert not hits, (rel, hits)
    print("test_duration_table_and_no_hardcoded_turns OK")


if __name__ == "__main__":
    test_damprock_drizzle_8_turns()
    test_terrain_extender_psychic_surge_8()
    test_light_clay_screens_8()
    test_base_turns_when_unknown_or_consumed()
    test_registered_item_fills_player_item()
    test_move_start_uses_user_item_both_orders()
    test_restart_message_does_not_reset()
    test_build_field_view_keeps_weather_turns_6_to_8()
    test_duration_table_and_no_hardcoded_turns()
    print("ALL OK")
