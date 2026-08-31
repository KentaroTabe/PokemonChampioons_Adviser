"""選出画面・パーティ管理画面からの型登録 (my_team取込) のテスト。

    scripts/run_test.sh test_team_menu

2026-08-30 第10回後の要望: 対戦中の「もっと見る」を開かなくても登録できる
ようにする。対戦状態との分離 (battle_active=False のみ・書き込みはmy_teamのみ)
と、誤読を登録しない保守性 (実数値×種族値の整合検証・2フレーム裏付け) を検証。
"""
from __future__ import annotations

import numpy as np

import advisor.my_team as mt
from vision import extractors, ocr, zones
from vision.normalize import NameResolver
from vision.state import BattleStateV2, PokemonState

resolver = NameResolver()

# ドリュウズ実フレーム (frame_1788070936) の行OCRを模した合成行。
# 2スケール統合による重複崩れ (が暮じごく/すなじごく) も再現する
_LINES = [
    ("ドリュウズ", (0.065, 0.095, 0.262, 0.128)),
    ("HP", (0.117, 0.235, 0.196, 0.267)),
    ("※こうけき", (0.071, 0.290, 0.289, 0.325)),
    ("ぼうぎょ", (0.136, 0.346, 0.287, 0.379)),
    ("とくこう", (0.127, 0.401, 0.283, 0.435)),
    ("とくぼう", (0.133, 0.456, 0.288, 0.490)),
    ("くすばやさ", (0.067, 0.509, 0.298, 0.545)),
    ("185", (0.500, 0.240, 0.583, 0.265)),
    ("205", (0.500, 0.291, 0.579, 0.323)),
    ("32", (0.860, 0.291, 0.915, 0.321)),
    ("82", (0.520, 0.348, 0.575, 0.377)),
    ("2", (0.870, 0.348, 0.913, 0.377)),
    ("70", (0.523, 0.401, 0.583, 0.430)),
    ("117", (0.506, 0.461, 0.575, 0.484)),
    ("32", (0.860, 0.458, 0.913, 0.487)),
    ("97", (0.523, 0.512, 0.581, 0.544)),
    ("じしん", (0.138, 0.596, 0.263, 0.634)),
    ("12", (0.838, 0.594, 0.898, 0.630)),
    ("マッドショット", (0.142, 0.668, 0.408, 0.702)),
    ("16", (0.838, 0.666, 0.898, 0.698)),
    ("が暮じごく", (0.144, 0.738, 0.338, 0.770)),
    ("すなじごく", (0.146, 0.740, 0.340, 0.772)),
    ("16", (0.837, 0.734, 0.898, 0.767)),
    ("すなあらし", (0.142, 0.805, 0.346, 0.840)),
    ("8", (0.862, 0.807, 0.896, 0.836)),
    ("緑性", (0.225, 0.891, 0.298, 0.922)),
    ("すなか", (0.548, 0.892, 0.650, 0.919)),
]


def test_parse_team_menu_lines():
    """行OCRからの構造化: とくこう/とくぼうの1字違い混線を起こさず、
    技は帯グループ化で崩れ違いを同一枠の候補にまとめる"""
    p = extractors.parse_team_menu_lines(_LINES)
    assert p is not None
    assert p["stats"]["c"] == (70, 0), p["stats"]
    assert p["stats"]["d"] == (117, 32), p["stats"]
    assert p["stats"]["b"] == (82, 2), p["stats"]
    assert p["move_slots"][2] == ["が暮じごく", "すなじごく"], p["move_slots"]
    assert "すなか" in p["ability_texts"], p["ability_texts"]
    assert "ドリュウズ" in p["name_texts"]
    # ステータスラベルが無い画面 (対戦のメッセージ等) は None
    assert extractors.parse_team_menu_lines(
        [("こうかはばつぐんだ", (0.1, 0.5, 0.5, 0.55))]) is None
    print("test_parse_team_menu_lines OK")


def test_validate_menu_stats():
    """実数値×種族値の整合検証と性格の数値導出 (1点=8EV換算)。
    帯を外れる読み (点数の読み落とし等) は配分ごと棄却する"""
    stats = {"hp": (185, 0), "a": (205, 32), "b": (82, 2),
             "c": (70, 0), "d": (117, 32), "s": (97, 0)}
    pts, nat = extractors._validate_menu_stats("excadrill", stats)
    assert pts == {"a": 32, "b": 2, "d": 32}, pts
    assert nat == "ゆうかん", nat   # +こうげき/−すばやさを数値から導出
    # 点数の読み落とし (32→0) は実数値と合わず全体を棄却
    bad = dict(stats)
    bad["a"] = (205, 0)
    assert extractors._validate_menu_stats("excadrill", bad) == (None, None)
    # 実数値が1つでも読めていなければ登録しない (別フレームを待つ)
    inc = dict(stats)
    inc["b"] = (None, 2)
    assert extractors._validate_menu_stats("excadrill", inc) == (None, None)
    print("test_validate_menu_stats OK")


def test_extract_team_menu_two_frame_guard():
    """e2e: 2フレーム連続の一致で初めて my_team に書き込み、
    対戦状態 (player/opponent) には一切触れない"""
    captured = []
    orig_build = mt.update_build
    orig_lines = ocr.apple_ocr_lines
    mt.update_build = lambda ja, patch: captured.append((ja, patch)) or True
    ocr.apple_ocr_lines = lambda bgr, scale=1.5, **kw: list(_LINES)
    try:
        state = BattleStateV2()
        img = np.zeros((1080, 1920, 3), dtype=np.uint8)
        extractors.extract_team_menu(img, state, resolver)
        assert captured == [], "1フレーム目で書き込まれた"
        extractors.extract_team_menu(img, state, resolver)
        assert len(captured) == 1, captured
        ja, patch = captured[0]
        assert ja == "ドリュウズ"
        assert patch["技"] == ["じしん", "マッドショット", "すなじごく",
                              "すなあらし"], patch
        assert patch["能力ポイント"] == {"a": 32, "b": 2, "d": 32}, patch
        assert patch["性格"] == "ゆうかん" and patch["特性"] == "すなかき"
        assert state.player.party == [] and state.opponent.party == [], \
            "対戦状態に書き込まれた"
    finally:
        mt.update_build = orig_build
        ocr.apple_ocr_lines = orig_lines
    print("test_extract_team_menu_two_frame_guard OK")


def test_selection_items_register_to_my_team():
    """選出画面の持ち物表示から my_team を更新する (2フレーム裏付け)"""
    captured = []
    orig_build = mt.update_build
    orig_read = ocr.read_zone_text
    mt.update_build = lambda ja, patch: captured.append((ja, patch)) or True

    def fake_read(img, zone, **kw):
        if kw.get("val_min") == 150:      # SELECTION_MY[i]["item"]
            return "いのちのたま"
        return ""

    ocr.read_zone_text = fake_read
    try:
        state = BattleStateV2()
        state.player.party = [
            PokemonState(species_ja="ブリジュラス", species_id="duraludon")]
        img = np.zeros((1080, 1920, 3), dtype=np.uint8)
        extractors.extract_selection(img, state, resolver)
        assert captured == [], "1フレーム目で書き込まれた"
        extractors.extract_selection(img, state, resolver)
        assert ("ブリジュラス", {"持ち物": "いのちのたま"}) in captured, captured
    finally:
        mt.update_build = orig_build
        ocr.read_zone_text = orig_read
    print("test_selection_items_register_to_my_team OK")


def test_watch_roster_confirms_picks():
    """交代メニュー (watch側柱) の3行=選出3体を正とし、やり直した選出画面の
    白リボン残留フラグを下ろす (2026-08-31 第11回: 未参加のミミッキュへの
    交代を推奨し続けた)"""
    st = BattleStateV2()
    names = [("ミミッキュ", "mimikyu"), ("ムクホーク", "staraptor"),
             ("ペロリーム", "slurpuff"), ("キラフロル", "glimmora")]
    st.player.party = [PokemonState(species_ja=j, species_id=i)
                       for j, i in names]
    st.player.party[0].is_picked = True   # やり直し選出の残留フラグ

    orig_read = ocr.read_zone_text
    rows = ["ムクホーク", "ペロリーム", "キラフロル"]

    def fake_read(img, zone, **kw):
        allow = kw.get("allowlist") or ""
        for i, z in enumerate(zones.WATCH_MY[:3]):
            if zone is z["name"]:
                return rows[i]
            if zone is z["hp"]:
                return "150/150"
        if "%" in allow:
            return ""
        return ""

    ocr.read_zone_text = fake_read
    try:
        img = np.zeros((1080, 1920, 3), dtype=np.uint8)
        extractors.extract_watch_side_columns(img, st, resolver)
        assert st.player.party[1].is_picked
        assert st.player.party[2].is_picked
        assert st.player.party[3].is_picked
        assert not st.player.party[0].is_picked, \
            "3体確定後もミミッキュの残留フラグが残った"
        # 対戦リセットで確定集合が持ち越されない
        st.reset_battle()
        assert getattr(st, "_watch_roster_idx", None) == set()
    finally:
        ocr.read_zone_text = orig_read
    print("test_watch_roster_confirms_picks OK")


def test_selection_detail_ability_tab():
    """選出画面の詳細オーバーレイ (能力タブ) から技/特性/持ち物を登録する
    (2026-08-31 第11回: 選出中の詳細閲覧が登録されなかった)。
    書き込みはmy_teamのみで、2フレーム裏付けを要求する"""
    lines = [
        ("じめん", (0.10, 0.10, 0.25, 0.16)),
        ("じしん", (0.08, 0.25, 0.25, 0.31)),
        ("12", (0.80, 0.25, 0.87, 0.31)),
        ("あくび", (0.08, 0.35, 0.25, 0.41)),
        ("12", (0.80, 0.35, 0.87, 0.41)),
        ("ふきとばし", (0.08, 0.45, 0.30, 0.51)),
        ("20", (0.80, 0.45, 0.87, 0.51)),
        ("ステルスロック", (0.08, 0.55, 0.36, 0.61)),
        ("20", (0.80, 0.55, 0.87, 0.61)),
        ("特性", (0.15, 0.72, 0.25, 0.78)),
        ("すなおこし", (0.45, 0.72, 0.70, 0.78)),
        ("持ち物", (0.14, 0.84, 0.26, 0.90)),
        ("オボンのみ", (0.45, 0.84, 0.68, 0.90)),
    ]
    parsed = extractors.parse_ability_tab_lines(lines)
    assert parsed is not None
    assert [c[0] for c in parsed["move_slots"]] == \
        ["じしん", "あくび", "ふきとばし", "ステルスロック"], parsed
    assert parsed["ability_texts"] == ["すなおこし"]
    assert parsed["item_texts"] == ["オボンのみ"]

    captured = []
    orig_build = mt.update_build
    orig_lines = ocr.apple_ocr_lines
    orig_focus = extractors._selection_focused_row
    mt.update_build = lambda ja, patch: captured.append((ja, patch)) or True
    ocr.apple_ocr_lines = lambda bgr, scale=1.5, **kw: list(lines)
    extractors._selection_focused_row = lambda img: 0
    try:
        st = BattleStateV2()
        st.player.party = [
            PokemonState(species_ja="カバルドン", species_id="hippowdon")]
        img = np.zeros((1080, 1920, 3), dtype=np.uint8)
        extractors.extract_selection_detail(img, st, resolver)
        assert captured == [], "1フレーム目で書き込まれた"
        extractors.extract_selection_detail(img, st, resolver)
        assert len(captured) == 1, captured
        ja, patch = captured[0]
        assert ja == "カバルドン"
        assert patch["技"] == ["じしん", "あくび", "ふきとばし",
                              "ステルスロック"], patch
        assert patch["特性"] == "すなおこし" and patch["持ち物"] == "オボンのみ"
        assert st.opponent.party == [], "対戦状態に書き込まれた"
    finally:
        mt.update_build = orig_build
        ocr.apple_ocr_lines = orig_lines
        extractors._selection_focused_row = orig_focus
    print("test_selection_detail_ability_tab OK")


def main() -> None:
    test_parse_team_menu_lines()
    test_validate_menu_stats()
    test_extract_team_menu_two_frame_guard()
    test_selection_items_register_to_my_team()
    test_watch_roster_confirms_picks()
    test_selection_detail_ability_tab()
    print("\nALL OK")


if __name__ == "__main__":
    main()
