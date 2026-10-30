"""壁 (リフレクター・ひかりのかべ・オーロラベール) の残りターンのテスト (2026-10-31)。

陣営ごとの欄 reflect_turns / light_screen_turns / aurora_veil_turns に、開始時に advisor.dex.effect_duration
(基本 5、設定者 = 技の使用者がひかりのねんど持ちなら 8) を入れ、新しいターンごとに 1 減らして 0 で壁を消す。
終了の文言を読めたらそちらが先に消す。残りターンが None (旧ログ・読めなかった) の壁は減らさない (消えない)。

    python -m tests.test_screen_turns
"""
from __future__ import annotations

import json

import numpy as np

from advisor.engine import build_field_view
from tests.test_field_extender import age_recent, new_parser, tick
from vision import ocr
from vision.state import SCREEN_KEYS, BattleStateV2

CORV_CLAY = [("アーマーガア", "corviknight", "lightclay")]
NINE_CLAY = [("キュウコン", "ninetalesalola", "lightclay")]


class _EmptyRegistry:
    """登録 (config/my_team.json、未コミット) の有無で結果が変わらないように空の登録にする"""

    def __enter__(self):
        from advisor.my_team import set_registry_override
        set_registry_override({})
        return self

    def __exit__(self, *a):
        from advisor.my_team import clear_registry_override
        clear_registry_override()


def test_light_clay_reflect_8_turns():
    """(a) ひかりのねんど持ちの自分の個体のリフレクターで 8。5 回の減算では残り、8 回で消える"""
    state, p = new_parser(CORV_CLAY)
    p.parse("アーマーガアの リフレクター!")
    assert state.player.reflect is True and state.player.reflect_turns == 8, state.player
    tick(state, 5)
    assert state.player.reflect is True and state.player.reflect_turns == 3, state.player
    tick(state, 2)
    assert state.player.reflect is True and state.player.reflect_turns == 1, state.player
    tick(state, 1)
    assert state.player.reflect is False and state.player.reflect_turns is None, state.player
    # 終了の推定はログに残る
    assert any(e.get("event") == "reflect_expire_player" for e in state.events), state.events[-3:]
    # ひかりのかべも同じ
    state, p = new_parser(CORV_CLAY)
    p.parse("アーマーガアの ひかりのかべ!")
    assert state.player.light_screen is True and state.player.light_screen_turns == 8, state.player
    print("test_light_clay_reflect_8_turns OK")


def test_base_turns_no_item_or_unknown():
    """(b) 持ち物なし・相手の不明 → 5。消費・喪失済み → 5。登録のひかりのねんどは補う"""
    with _EmptyRegistry():
        state, p = new_parser([("アーマーガア", "corviknight", None)])
        p.parse("アーマーガアの リフレクター!")
        assert state.player.reflect and state.player.reflect_turns == 5, state.player

    state, p = new_parser([("アーマーガア", "corviknight", "leftovers")])
    p.parse("アーマーガアの ひかりのかべ!")
    assert state.player.light_screen and state.player.light_screen_turns == 5, state.player

    for flag in ("item_consumed", "item_removed"):
        state, p = new_parser(CORV_CLAY)
        setattr(state.player.active(), flag, True)
        p.parse("アーマーガアの リフレクター!")
        assert state.player.reflect_turns == 5, (flag, state.player)

    # 相手側: 持ち物は不明 → 5 (相手の壁は相手の陣営に張られる)
    state, p = new_parser([("ブリジュラス", "duraludon", None)],
                          opponent_mons=[("オーロンゲ", "grimmsnarl", None)])
    p.parse("相手の オーロンゲの リフレクター!")
    assert state.opponent.reflect and state.opponent.reflect_turns == 5, state.opponent
    assert not state.player.reflect and state.player.reflect_turns is None, state.player

    # 自分側で state に持ち物が無ければ登録で補う (天候と同じ _setter_item)
    from advisor.my_team import clear_registry_override, set_registry_override
    set_registry_override({"アーマーガア": {"持ち物": "ひかりのねんど"}})
    try:
        state, p = new_parser([("アーマーガア", "corviknight", None)])
        p.parse("アーマーガアの リフレクター!")
        assert state.player.reflect_turns == 8, state.player
    finally:
        clear_registry_override()
    print("test_base_turns_no_item_or_unknown OK")


def test_end_message_clears_matching_side():
    """(c) 終了の文言で消える。側を合わせる (相手の壁の終了で自分の壁は消えない)"""
    state, p = new_parser(CORV_CLAY, opponent_mons=[("オーロンゲ", "grimmsnarl", None)])
    p.parse("アーマーガアの リフレクター!")
    p.parse("相手の オーロンゲの リフレクター!")
    assert state.player.reflect_turns == 8 and state.opponent.reflect_turns == 5
    tick(state, 2)
    p.parse("相手の リフレクターが なくなった!")
    assert state.opponent.reflect is False and state.opponent.reflect_turns is None, state.opponent
    assert state.player.reflect is True and state.player.reflect_turns == 6, state.player
    age_recent(p, 30.0)
    p.parse("味方の リフレクターが なくなった!")
    assert state.player.reflect is False and state.player.reflect_turns is None, state.player
    # 消えた後の減算は何もしない
    tick(state, 1)
    assert state.player.reflect is False and state.player.reflect_turns is None

    # ひかりのかべの終了の文言 (実戦の読み「ひかりのかべがなくなった」)
    state, p = new_parser(CORV_CLAY)
    p.parse("アーマーガアの ひかりのかべ!")
    p.parse("味方の ひかりのかべが なくなった!")
    assert state.player.light_screen is False and state.player.light_screen_turns is None, state.player
    print("test_end_message_clears_matching_side OK")


def test_old_log_and_roundtrip():
    """(d) 旧ログ (欄なし) の復元 → None で、減算しても消えない。to_dict → JSON → 復元の往復で残りターンが保たれる"""
    state, p = new_parser(CORV_CLAY, opponent_mons=[("オーロンゲ", "grimmsnarl", None)])
    p.parse("アーマーガアの リフレクター!")
    p.parse("相手の オーロンゲの ひかりのかべ!")
    tick(state, 2)
    d = json.loads(json.dumps(state.to_dict(), ensure_ascii=False))
    sc = d["player"]["screens"]
    # 既存の欄は bool のまま、新しい欄を足すだけ
    assert sc["reflect"] is True and sc["light_screen"] is False and sc["aurora_veil"] is False, sc
    assert sc["reflect_turns"] == 6 and sc["light_screen_turns"] is None and sc["aurora_veil_turns"] is None, sc
    assert d["opponent"]["screens"]["light_screen_turns"] == 3, d["opponent"]["screens"]

    restored = BattleStateV2()
    restored.restore_from_dict(d)
    for side_name in ("player", "opponent"):
        a, b = state.side(side_name), restored.side(side_name)
        for key in SCREEN_KEYS:
            assert getattr(a, key) == getattr(b, key), (side_name, key)
            assert getattr(a, f"{key}_turns") == getattr(b, f"{key}_turns"), (side_name, key)
    assert restored.to_dict()["player"]["screens"] == state.to_dict()["player"]["screens"]

    # 旧ログ: screens に残りターンの欄が無い
    for side_name in ("player", "opponent"):
        for key in SCREEN_KEYS:
            d[side_name]["screens"].pop(f"{key}_turns")
    d["player"]["screens"]["aurora_veil"] = True
    old = BattleStateV2()
    old.restore_from_dict(d)
    assert old.player.reflect is True and old.player.reflect_turns is None, old.player
    assert old.player.aurora_veil is True and old.player.aurora_veil_turns is None, old.player
    assert old.opponent.light_screen is True and old.opponent.light_screen_turns is None, old.opponent
    tick(old, 10)
    assert old.player.reflect and old.player.aurora_veil and old.opponent.light_screen, "None の壁が消えた"
    assert old.player.reflect_turns is None and old.opponent.light_screen_turns is None
    # 欄が無い (screens 自体が無い) ログも従来どおり
    d2 = json.loads(json.dumps(state.to_dict(), ensure_ascii=False))
    d2["player"].pop("screens")
    old2 = BattleStateV2()
    old2.restore_from_dict(d2)
    assert old2.player.reflect is False and old2.player.reflect_turns is None
    print("test_old_log_and_roundtrip OK")


def test_reread_and_recast_do_not_reset():
    """(e) 技の再読み・開始の文言の再読み・張ってある壁の張り直しで残りターンが戻らない。読みの順の入れ替わりでも使用者の持ち物"""
    state, p = new_parser(CORV_CLAY)
    p.parse("アーマーガアの リフレクター!")
    tick(state, 1)
    p.parse("アーマーガアの リフレクタ-!")   # 窓内の再読み (OCR 揺れ)
    assert state.player.reflect_turns == 7, state.player
    # 窓の外の張り直し (ゲームでは失敗): 戻さない
    age_recent(p, 30.0)
    tick(state, 2)
    p.parse("アーマーガアの リフレクター!")
    assert state.player.reflect is True and state.player.reflect_turns == 5, state.player

    # 技の使用 → 開始の文言 → 文言の再読み: 8 のまま、減算の後の再読みでも戻らない
    state, p = new_parser(CORV_CLAY)
    p.parse("アーマーガアの リフレクター!")
    p.parse("物理攻撃に 強くなった!")
    assert state.player.reflect_turns == 8, state.player
    tick(state, 1)
    p.parse("物理攻撃に 強く なった!")
    assert state.player.reflect_turns == 7, state.player

    # 開始の文言 → 技の使用 (読みの順の入れ替わり): 使用者の持ち物で 8 に直す
    state, p = new_parser(CORV_CLAY)
    p.parse("特殊攻撃に 強くなった!")
    assert state.player.light_screen and state.player.light_screen_turns == 5, state.player
    p.parse("アーマーガアの ひかりのかべ!")
    assert state.player.light_screen_turns == 8, state.player

    # 文言だけの開始 (直前に技の使用が無い) は基本のターン
    state, p = new_parser(CORV_CLAY)
    p.parse("物理攻撃に 強くなった!")
    assert state.player.reflect and state.player.reflect_turns == 5, state.player

    # 相手の壁の開始の文言は自分の壁の残りターンを直さない (陣営つきの記録)
    state, p = new_parser(CORV_CLAY, opponent_mons=[("オーロンゲ", "grimmsnarl", None)])
    p.parse("アーマーガアの リフレクター!")
    tick(state, 2)
    age_recent(p, 30.0)
    p.parse("相手の オーロンゲの リフレクター!")
    p.parse("相手の チームは 物理攻撃に 強くなった!")
    assert state.opponent.reflect_turns == 5, state.opponent
    p.parse("アーマーガアの リフレクター!")   # 張ってある壁の張り直し (失敗)
    assert state.player.reflect_turns == 6, state.player
    print("test_reread_and_recast_do_not_reset OK")


def test_aurora_veil_same():
    """(f) オーロラベールも同じ: ひかりのねんどで 8、減算で消える、終了の文言で消える、持ち物不明は 5"""
    state, p = new_parser(NINE_CLAY)
    p.parse("キュウコンの オーロラベール!")
    assert state.player.aurora_veil is True and state.player.aurora_veil_turns == 8, state.player
    tick(state, 7)
    assert state.player.aurora_veil is True and state.player.aurora_veil_turns == 1, state.player
    tick(state, 1)
    assert state.player.aurora_veil is False and state.player.aurora_veil_turns is None, state.player

    state, p = new_parser(NINE_CLAY)
    p.parse("キュウコンの オーロラベール!")
    p.parse("味方の オーロラベールが なくなった!")
    assert state.player.aurora_veil is False and state.player.aurora_veil_turns is None, state.player

    state, p = new_parser([("ブリジュラス", "duraludon", None)],
                          opponent_mons=[("キュウコン", "ninetalesalola", None)])
    p.parse("相手の キュウコンの オーロラベール!")
    assert state.opponent.aurora_veil and state.opponent.aurora_veil_turns == 5, state.opponent
    tick(state, 1)
    p.parse("相手の キュウコンの オーロラベール!")   # 窓内の再読み
    assert state.opponent.aurora_veil_turns == 4, state.opponent
    print("test_aurora_veil_same OK")


def test_build_field_view_reads_bool():
    """(g) engine の build_field_view は壁を従来どおり真偽で読む (残りターンの欄は読まない)。尽きたら壁なし"""
    state, p = new_parser(CORV_CLAY, opponent_mons=[("オーロンゲ", "grimmsnarl", None)])
    p.parse("アーマーガアの リフレクター!")
    for _ in range(8):
        fv = build_field_view(state.to_dict(), "opponent")   # 相手の攻撃 → 自分の壁を見る
        assert fv.reflect is True and fv.light_screen is False and fv.aurora_veil is False, fv
        assert build_field_view(state.to_dict(), "player").reflect is False
        tick(state, 1)
    fv = build_field_view(state.to_dict(), "opponent")
    assert fv.reflect is False, fv
    # 旧ログ (欄なし) も同じ
    d = state.to_dict()
    d["player"]["screens"] = {"reflect": True, "light_screen": False, "aurora_veil": False, "safeguard": False}
    assert build_field_view(d, "opponent").reflect is True
    print("test_build_field_view_reads_bool OK")


def test_field_check_screen_turns():
    """場の状況画面の壁の n/m を残りターンに入れる (天候と同じ total − elapsed)。n/m が無ければ残りターンは触らない"""
    from vision import extractors
    from vision.normalize import NameResolver

    def run(lines, st):
        orig = ocr.apple_ocr_lines
        ocr.apple_ocr_lines = lambda img, scale=1.0: list(lines)
        try:
            extractors.extract_field_check(np.zeros((720, 1280, 3), dtype=np.uint8), st, NameResolver())
        finally:
            ocr.apple_ocr_lines = orig

    state, _p = new_parser(CORV_CLAY)
    run([("効果と場の状態", (0.52, 0.22, 0.63, 0.25)),
         ("アーマーガア", (0.20, 0.22, 0.30, 0.26)),
         ("リフレクター状態", (0.53, 0.32, 0.68, 0.36)),
         ("3/8", (0.75, 0.32, 0.80, 0.36))], state)
    assert state.player.reflect is True and state.player.reflect_turns == 5, state.player

    state, _p = new_parser(CORV_CLAY)
    state.player.light_screen, state.player.light_screen_turns = True, 4
    run([("効果と場の状態", (0.52, 0.22, 0.63, 0.25)),
         ("アーマーガア", (0.20, 0.22, 0.30, 0.26)),
         ("ひかりのかべ状態", (0.53, 0.32, 0.68, 0.36))], state)
    assert state.player.light_screen is True and state.player.light_screen_turns == 4, state.player
    print("test_field_check_screen_turns OK")


def test_tick_screens_pure():
    """SideState.tick_screens は自分の陣営の欄だけを変え、消した壁を返す"""
    st = BattleStateV2()
    st.player.reflect, st.player.reflect_turns = True, 1
    st.player.light_screen, st.player.light_screen_turns = True, None
    st.player.aurora_veil, st.player.aurora_veil_turns = False, 3   # 壁なしの残りターンは減らさない
    assert st.player.tick_screens() == ["reflect"]
    assert (st.player.reflect, st.player.reflect_turns) == (False, None)
    assert (st.player.light_screen, st.player.light_screen_turns) == (True, None)
    assert st.player.aurora_veil_turns == 3
    assert st.opponent.tick_screens() == []
    print("test_tick_screens_pure OK")


if __name__ == "__main__":
    test_light_clay_reflect_8_turns()
    test_base_turns_no_item_or_unknown()
    test_end_message_clears_matching_side()
    test_old_log_and_roundtrip()
    test_reread_and_recast_do_not_reset()
    test_aurora_veil_same()
    test_build_field_view_reads_bool()
    test_field_check_screen_turns()
    test_tick_screens_pure()
    print("ALL OK")
