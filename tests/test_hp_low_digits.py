"""自分の HP が 1〜2 桁のときの分数の読み (2026-10-15、KNOWN_ISSUES A1「自分の HP が 1 桁のとき分数を誤読し、確定が最大 17 秒遅れる」)。

10/15 10:12:48 試験 (battle_20261015_100950): イエッサン 7/137 の画面が '7/37' (field) '737' '40' (hud) と読まれて
fraction_unparsable が続き、10:13:05 のバー推定まで 44.5% のまま。メガカメックス 92/171 も '92/178' で max_hp_mismatch が続いた。
原因 (保存フレーム frame_1792026768 で測定): ゾーン全体の読みでは小さい '/1' が 1 字に崩れる ('7/37' '737')。分母が崩れた読みは
分けて読む経路 (read_fraction_split) に回るが、Apple Vision は 1 桁の大きい数字 '7' だけの切り出しを読めず (空)、経路ごと失敗していた
(大きい数字の右端の検出 151 と分母の読み '137' は正しい)。
対処: (1) 現在値が読めないときは切り出しを横に並べて読む (read_current_tiled: '77' → '7')。(2) 最大 HP が既知なら、分母の読みが
外れても現在値だけを読み、既知の最大 HP で入れる (my_cur_only_candidate。バーの割合と HP_BAR_MATCH_TOL 以内のときだけ。
hp_source="cur_only"、hp_reject に cur_only、読みの経過に reason "cur_only (...)")。

画像: tests/fixtures/hp_low_digits/ (frame_1792026768 の 7/137、frame_1792027051 の 18/171 の HUD の切り出し。92/171 の画面は
保存されていない)。実 OCR (Apple Vision) が無い環境では実画像の読みのテストはスキップする。

使い方: python -m tests.test_hp_low_digits
"""
import time

from vision import ocr, zones
from vision.zones import crop
from tests.test_hp_fraction_ocr import _frame
from tests.test_hp_bar_estimate import _run, _state_with_me, _saved_counts, _restore_counts

TOPIC = "hp_low_digits"


# ------------------------------------------------------------------ 純粋な部分
def test_tiled_repeat_unit_pure():
    """並べ読みの数字が同じ並びの繰り返しなら 1 回分、違えば None"""
    assert ocr.tiled_repeat_unit("77", 2) == "7" and ocr.tiled_repeat_unit("1818", 2) == "18"
    assert ocr.tiled_repeat_unit("189189", 2) == "189" and ocr.tiled_repeat_unit("777", 3) == "7"
    for d, k in (("7", 2), ("1371137", 2), ("7178", 2), ("", 2), ("12341234", 2)):
        assert ocr.tiled_repeat_unit(d, k) is None, (d, k)
    print("test_tiled_repeat_unit_pure OK")


def test_cur_only_candidate_pure():
    """現在値だけの読みの採否: 最大 HP が既知・バーが測れた・現在値 <= 最大・バーと HP_BAR_MATCH_TOL 以内のときだけ採る"""
    from vision.extractors import my_cur_only_candidate as f
    assert f("7/37", None, 137, 0.067) == 7          # '/1' が '/' に崩れた読み
    assert f("92/178", None, 171, 0.542) == 92       # 分母の誤読 (max_hp_mismatch)
    assert f("737", "7", 137, 0.067) == 7            # 区切りが読めない: 分けて読んだ現在値
    assert f("7/37", None, None, 0.067) is None      # 最大 HP が未知なら採らない
    assert f("7/37", None, 137, None) is None        # バーが測れなければ採らない
    assert f("40", "40", 137, 0.067) is None         # バーと合わない (40/137 = 0.29)
    assert f("737", None, 137, 0.067) is None        # 区切りも分けて読んだ現在値も無い
    assert f("180/78", None, 171, 1.0) is None       # 現在値 > 最大 HP
    assert f("7/37", None, 40, 0.067) is None        # 既知の最大 HP が 50 未満 (誤読の基準) なら採らない
    # 分母の読みが既知の最大 HP の 1 字違いでなければ別の個体の疑いで採らない (交代直後の別の個体の分数)
    assert f("150/150", None, 171, 1.0) is None and f("150/150", None, 171, 0.88) is None
    assert f("189/8", None, 189, 1.0) == 189 and f("92/771", None, 171, 0.542) == 92 and f("5/089", None, 189, 0.044) == 5
    print("test_cur_only_candidate_pure OK")


def test_denominator_resembles_pure():
    """'/' の後の数字が既知の最大 HP の 1 字違いの読みか (10/9〜10/15 の対戦ログの max_hp_mismatch の読みはすべて True)"""
    from vision.extractors import denominator_resembles as r
    for den, k in (("178", 171), ("771", 171), ("71", 171), ("37", 137), ("8", 189), ("89", 189), ("089", 189),
                   ("91", 191), ("67", 167), ("141", 142), ("1137", 137), ("", 137)):
        assert r(den, k), (den, k)
    for den, k in (("150", 171), ("159", 137), ("12", 189), ("17111", 171), ("98", 189)):
        assert not r(den, k), (den, k)
    print("test_denominator_resembles_pure OK")


# ------------------------------------------------------------------ 実画像 (Apple Vision)
def test_real_split_reads_low_digits():
    """7/137 と 18/171 の切り出しで、分けて読む経路が正しい分数を返す (修正前は 7/137 が None: 現在値 '7' が読めない)。
    ゾーン全体の読み (このフレームでは正しく読める) と read_zone_text(fraction=True) も正しい分数"""
    if not ocr._get_apple_vision():
        print("test_real_split_reads_low_digits SKIP (Apple Vision が無い)")
        return
    out = []
    for name, want in (("my_low_7of137.png", "7/137"), ("my_low_18of171.png", "18/171")):
        img = _frame(TOPIC, name)
        c = crop(img, zones.BATTLE["my_hp_text"])
        right = ocr.big_digits_right_col(c)
        assert right is not None and ocr.big_digits_left_col(c, right) is not None, name
        assert ocr.read_fraction_split(c) == want, (name, ocr.read_fraction_split(c))
        got = ocr.read_zone_text(img, zones.BATTLE["my_hp_text"], mode="panel", allowlist="0123456789/", fraction=True)
        assert str(got) == want, (name, str(got))
        out.append((name, right, ocr.read_current_tiled(c, right), str(got)))
    print("test_real_split_reads_low_digits OK", out)


def test_real_broken_full_read_recovered_by_split():
    """接続テストの読み ('737' = '/1' の崩れ、'7/37') をゾーン全体の読みにモックすると、分けて読む経路で '7/137' になる
    (修正前は現在値が読めず、'737' のまま fraction_unparsable)"""
    if not ocr._get_apple_vision():
        print("test_real_broken_full_read_recovered_by_split SKIP (Apple Vision が無い)")
        return
    img = _frame(TOPIC, "my_low_7of137.png")
    out = []
    for broken in ("737", "7/37"):
        orig = ocr.apple_ocr_text
        calls = {"n": 0}

        def first_broken(bgr, *a, **kw):
            calls["n"] += 1
            return broken if calls["n"] == 1 else orig(bgr, *a, **kw)
        ocr.apple_ocr_text = first_broken
        try:
            got = ocr.read_zone_text(img, zones.BATTLE["my_hp_text"], mode="panel", allowlist="0123456789/",
                                     fraction=True)
        finally:
            ocr.apple_ocr_text = orig
        assert str(got) == "7/137" and ocr.ocr_method(got) == "split", (broken, str(got), ocr.ocr_method(got))
        out.append((broken, str(got)))
    print("test_real_broken_full_read_recovered_by_split OK", out)


# ------------------------------------------------------------------ 現在値だけの読み (OCR の文字はモック、バーは画像から実測)
def test_hud_cur_only_adopted():
    """'7/37' (fraction_unparsable)、'737' + 分けて読んだ現在値 '7'、'18/178' (max_hp_mismatch) を、最大 HP が既知なら現在値だけで
    入れる。hp_source="cur_only" (推定の印なし)、hp_reject に cur_only、読みの経過の reason に cur_only"""
    saved = _saved_counts()
    try:
        img = _frame(TOPIC, "my_low_7of137.png")
        for text in ("7/37", ocr.OcrText("737", "direct", split_cur="7")):
            st, mon = _state_with_me(ja="イエッサン", sid="indeedee", hp=44.5)
            mon.hp_current, mon.hp_max = 61, 137
            for _ in range(2):
                _run(img, st, text, expected_max=137, name_text="イエッサン")
            assert (mon.hp_current, mon.hp_max) == (7, 137) and mon.hp_percent == round(7 / 137 * 100, 1), \
                (text, mon.hp_current, mon.hp_max, mon.hp_percent)
            assert mon.hp_source == "cur_only" and not mon.hp_estimated and not mon.hp_uncertain, mon.hp_source
            rej = st.hp_reject["player"]
            assert rej["reason"] == "fraction_unparsable" and rej["cur_only"] == [7, 137], rej
            row = st.my_hp_trace[-1]
            assert row["decision"] == "commit" and row["reason"] == "cur_only (fraction_unparsable)", row
            assert row["cur_only"] == [7, 137] and row["bar"] is not None, row
            assert not (st.hp_max_votes.get(("player", "イエッサン")) or {}).get(137), st.hp_max_votes   # 最大 HP の票に入れない
        img2 = _frame(TOPIC, "my_low_18of171.png")
        st, mon = _state_with_me(ja="カメックス", sid="blastoisemega", hp=53.8)
        for _ in range(2):
            _run(img2, st, "18/178", expected_max=171, name_text="カメックス")
        assert (mon.hp_current, mon.hp_max, mon.hp_source) == (18, 171, "cur_only"), (mon.hp_current, mon.hp_max)
        assert st.hp_reject["player"]["reason"] == "max_hp_mismatch" and st.hp_reject["player"]["cur_only"] == [18, 171]
        assert getattr(mon, "_my_max_adopted", None) is None
        # scene 行の state の個体に出所の印が出る (推定の印は付けない)
        from battle_logger import scene_row_state
        row = scene_row_state(st.to_dict())["player"]["party"][0]
        assert row.get("hp_source") == "cur_only" and "hp_estimated" not in row, row
        # 次に分数が正しく読めたら実読みの印 (None) に戻る
        _run(img2, st, "18/171", expected_max=171, name_text="カメックス")
        _run(img2, st, "18/171", expected_max=171, name_text="カメックス")
        assert mon.hp_source is None and (mon.hp_current, mon.hp_max) == (18, 171)
    finally:
        _restore_counts(saved)
    print("test_hud_cur_only_adopted OK")


def test_hud_cur_only_not_adopted():
    """最大 HP が未知なら採らない、バーと合わない現在値は採らない (従来どおり棄却のまま、バー推定の経路に任せる)"""
    saved = _saved_counts()
    try:
        img = _frame(TOPIC, "my_low_7of137.png")
        # 最大 HP が未知 (型登録の理論値・実測採用値・過去の実測が無い)
        st, mon = _state_with_me(ja="イエッサン", sid="indeedee", hp=44.5)
        for _ in range(2):
            _run(img, st, "7/37", expected_max=None, name_text="イエッサン")
        assert mon.hp_percent == 44.5 and mon.hp_source is None and mon.hp_current is None
        assert st.hp_reject["player"]["reason"] == "fraction_unparsable" and "cur_only" not in st.hp_reject["player"]
        assert st.my_hp_trace[-1]["decision"] == "fraction_unparsable"
        # バーと合わない ('40': 40/137 = 0.29、バー 0.067)
        st, mon = _state_with_me(ja="イエッサン", sid="indeedee", hp=44.5)
        for _ in range(2):
            _run(img, st, ocr.OcrText("40", "direct", split_cur="40"), expected_max=137, name_text="イエッサン")
        assert mon.hp_current != 40 and mon.hp_source != "cur_only", (mon.hp_current, mon.hp_source)
        assert "cur_only" not in st.hp_reject["player"]
        # bar_mismatch (分母は合っていて現在値が疑わしい) は対象外
        st, mon = _state_with_me(ja="イエッサン", sid="indeedee", hp=44.5)
        for _ in range(2):
            _run(img, st, "70/137", expected_max=137, name_text="イエッサン")
        assert st.hp_reject["player"]["reason"] == "bar_mismatch" and mon.hp_current != 70
    finally:
        _restore_counts(saved)
    print("test_hud_cur_only_not_adopted OK")


def test_field_cur_only_adopted():
    """field の経路 (extract_field_hp) でも同じ規則: '7/37' を最大 HP 既知 (137) で 7/137 として入れる"""
    from vision import extractors
    saved = _saved_counts()
    img = _frame(TOPIC, "my_low_7of137.png")
    st, mon = _state_with_me(ja="イエッサン", sid="indeedee", hp=44.5)
    orig = ocr.read_zone_text, extractors._expected_my_max, extractors._my_legal_maxes
    ocr.read_zone_text = lambda _img, zone, **kw: "7/37" if zone is zones.BATTLE["my_hp_text"] else ""
    extractors._expected_my_max = lambda m: 137
    extractors._my_legal_maxes = lambda: None
    try:
        for _ in range(2):
            extractors.extract_field_hp(img, st)
            mon._hp_stable_since = 0.0
    finally:
        ocr.read_zone_text, extractors._expected_my_max, extractors._my_legal_maxes = orig
        _restore_counts(saved)
    row = st.my_hp_trace[-1]
    if row["decision"] == "skip_no_my_hud":
        raise AssertionError(f"fixture に自分の HUD のバーが無い: {row}")
    assert (mon.hp_current, mon.hp_max, mon.hp_source) == (7, 137, "cur_only"), (mon.hp_current, mon.hp_max, row)
    assert row["source"] == "field" and row["reason"] == "cur_only (fraction_unparsable)" and row["cur_only"] == [7, 137], row
    print("test_field_cur_only_adopted OK")


if __name__ == "__main__":
    t0 = time.time()
    test_tiled_repeat_unit_pure()
    test_cur_only_candidate_pure()
    test_denominator_resembles_pure()
    test_real_split_reads_low_digits()
    test_real_broken_full_read_recovered_by_split()
    test_hud_cur_only_adopted()
    test_hud_cur_only_not_adopted()
    test_field_cur_only_adopted()
    print(f"\nALL OK ({time.time() - t0:.1f}s)")
