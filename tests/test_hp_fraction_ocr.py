"""自分の HP の分数の文字の読み (2026-10-09 fix/hp-ocr-watch、KNOWN_ISSUES A1「自分の HP が満タンのとき HUD の分数が最大 HP の数字だけに読まれる」)。

原因: HUD の分数は「大きい現在値 + 小さい '/最大'」で、小さい数字の上端が HP バーの下端に接している。バーの塗りがその列まで
伸びる (HP が約 7 割以上、満タンで全部) と、塗りの緑が小さい数字とつながり、Apple Vision がゾーン全体の読みで
'/189' を落とす・'g' 'л' に読む ('189' '189/' '189/8' '1897')。
対処: vision.ocr.read_zone_text(fraction=True) は、ゾーン全体の読みが 'a/b' の分数にならないとき、大きい数字の右端で左右に分け、
現在値は生のまま、分母はバーの塗りを暗くしたグレーの反転で読み直す (read_fraction_split)。分けて読んだ分数は
最大 HP の決定 (実測採用の数え上げ・初回の多数決) に使わない。

画像は tests/fixtures/hp_ocr/ の原寸の切り出し (manifest.json の offset で元の解像度の黒い画面に貼り戻す)。
実 OCR (Apple Vision) が無い環境では、実画像の読みのテストはスキップする。

使い方: python -m tests.test_hp_fraction_ocr
"""
import json
import time
from pathlib import Path

import cv2
import numpy as np

from vision import ocr, zones
from vision.zones import crop

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _frame(topic: str, name: str):
    """切り出しを元の解像度の黒い画面の元の位置に貼り戻す"""
    meta = json.loads((FIXTURES / topic / "manifest.json").read_text(encoding="utf-8"))[name]
    part = cv2.imread(str(FIXTURES / topic / name))
    assert part is not None, name
    w, h = meta["frame_size"]
    x0, y0 = meta["offset"]
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[y0:y0 + part.shape[0], x0:x0 + part.shape[1]] = part
    return img


# (topic, fixture, 修正前の読み (ゾーン全体を 1 回), 修正後の読み, 読み方)
REAL_CASES = [
    ("hp_ocr", "my_full_189of189_a.png", "189", "189/189", "split"),
    ("hp_ocr", "my_full_189of189_b.png", "189/8", "189/189", "split"),
    ("hp_intake", "my_full_137of137.png", None, "137/137", "split"),   # 10/8 の '13723' 等と同じ原因
    ("hp_ocr", "my_high_124of141.png", "1247141", "124/141", "split"),
    ("hp_ocr", "my_mid_71of189.png", "71/189", "71/189", "direct"),    # 分数として読めるものは従来の 1 回の読みのまま
]


# ------------------------------------------------------------------ 純粋な部分
def test_fraction_text_ok_pure():
    """'a/b' の分数として読めているかの判定"""
    for t in ("189/189", "71/189", "4/159", "0/137"):
        assert ocr.fraction_text_ok(t), t
    for t in ("189", "189/", "189/8", "189/89", "1897", "13723", "", None, "158/89", "3/30"):
        assert not ocr.fraction_text_ok(t), t
    print("test_fraction_text_ok_pure OK")


def test_denominator_digits_pure():
    """分母側の読み: 4 桁なら先頭 ('/' の誤読) を落とす。3 桁以下はそのまま、5 桁以上は空"""
    assert ocr.denominator_digits("7189") == "189"
    assert ocr.denominator_digits("2189") == "189"
    assert ocr.denominator_digits("1189") == "189"
    assert ocr.denominator_digits("/189") == "189"
    assert ocr.denominator_digits("189") == "189"
    assert ocr.denominator_digits("1137") == "137"
    assert ocr.denominator_digits("89") == "89"
    assert ocr.denominator_digits("71899") == ""
    assert ocr.denominator_digits("") == "" and ocr.denominator_digits(None) == ""
    print("test_denominator_digits_pure OK")


def test_big_digits_right_col_synthetic():
    """大きい数字 (高い白の塊) の右端を、小さい数字 (低い白の塊) と HUD の縁 (上端の白い線) に惑わされずに取る"""
    from vision.zones import MY_HP_TEXT_SPLIT
    h, w = 74, 317
    c = np.full((h, w, 3), 30, dtype=np.uint8)
    c[2:6, :] = 255                      # HUD の白い縁 (帯の外)
    c[13:43, 0:240] = (40, 200, 60)      # HP バーの塗り (緑、彩度が高いので白文字に入らない)
    c[28:60, 80:150] = 255               # 大きい数字 (28〜60 行)
    c[41:60, 156:216] = 255              # 小さい '/189' (41〜60 行)
    assert ocr.big_digits_right_col(c) == 149
    # 大きい数字が無い (帯に白が無い) → None
    c2 = c.copy()
    c2[28:60, 80:150] = 30
    assert ocr.big_digits_right_col(c2) is None
    # 右端が big_right_max_x より右 (演出の白い粒など) → None
    c3 = c.copy()
    x = int(MY_HP_TEXT_SPLIT["big_right_max_x"] * w) + 5
    c3[30:36, x:x + 3] = 255
    assert ocr.big_digits_right_col(c3) is None
    print("test_big_digits_right_col_synthetic OK")


def test_desaturate_gray_synthetic():
    """バーの塗り (彩度が高い) は暗く、白文字は白いまま。反転すると逆"""
    c = np.zeros((4, 4, 3), dtype=np.uint8)
    c[:2] = (40, 220, 60)     # 緑の塗り
    c[2:] = (245, 245, 245)   # 白文字
    g = ocr.desaturate_gray(c)
    assert g.shape == c.shape
    assert g[0, 0, 0] < 60 and g[3, 3, 0] > 230, (g[0, 0], g[3, 3])
    gi = ocr.desaturate_gray(c, invert=True)
    assert gi[0, 0, 0] > 195 and gi[3, 3, 0] < 25
    print("test_desaturate_gray_synthetic OK")


def test_ocr_text_method():
    """OcrText は str として扱え、読み方を持つ。素の str は direct"""
    t = ocr.OcrText("189/189", "split")
    assert t == "189/189" and isinstance(t, str) and ocr.ocr_method(t) == "split"
    assert str(t) == "189/189" and json.dumps(str(t)) == '"189/189"'
    assert ocr.ocr_method("71/189") == "direct" and ocr.ocr_method(ocr.OcrText("x")) == "direct"
    print("test_ocr_text_method OK")


# ------------------------------------------------------------------ 実画像 (Apple Vision)
def test_real_fixture_reads():
    """満タン・高い HP の切り出しで、修正前は分数にならない読みが、修正後は正しい分数になる。中程度は従来の読みのまま"""
    if not ocr._get_apple_vision():
        print("test_real_fixture_reads SKIP (Apple Vision が無い)")
        return
    out = []
    for topic, name, before, after, method in REAL_CASES:
        img = _frame(topic, name)
        raw = ocr.read_zone_text(img, zones.BATTLE["my_hp_text"], mode="panel", allowlist="0123456789/")
        if before is not None:
            assert raw == before, (name, raw)
        assert isinstance(raw, str) and not isinstance(raw, ocr.OcrText)   # fraction=False は従来どおり素の str
        got = ocr.read_zone_text(img, zones.BATTLE["my_hp_text"], mode="panel", allowlist="0123456789/",
                                 fraction=True)
        assert str(got) == after and ocr.ocr_method(got) == method, (name, str(got), ocr.ocr_method(got))
        out.append((name, raw, str(got)))
    print("test_real_fixture_reads OK", out)


def test_real_full_hp_reaches_set_hp():
    """満タンの切り出しで extract_my_hud が分数を _set_hp まで通し (fraction_unparsable にならない)、読みの経過に ocr_method を残す"""
    if not ocr._get_apple_vision():
        print("test_real_full_hp_reaches_set_hp SKIP (Apple Vision が無い)")
        return
    from vision import extractors
    from vision.state import BattleStateV2, PokemonState
    img = _frame("hp_ocr", "my_full_189of189_a.png")
    st = BattleStateV2()
    st.battle_active = True
    me = PokemonState(species_ja="ペロリーム", species_id="aromatisse", display_name="ペロリーム")
    me.hp_percent = 50.0
    st.player.party.append(me)
    st.player.active_index = 0
    orig = extractors._expected_my_max, extractors._my_legal_maxes
    extractors._expected_my_max = lambda m: 189
    extractors._my_legal_maxes = lambda: None
    from vision.normalize import NameResolver
    res = NameResolver()
    try:
        for _ in range(3):
            extractors.extract_my_hud(img, st, res)   # 名前も実 OCR で読む (HP の読みには関係しない)
            me._hp_stable_since = 0.0
    finally:
        extractors._expected_my_max, extractors._my_legal_maxes = orig
    rows = list(st.my_hp_trace)
    assert rows and all(r["hp_text"] == "189/189" and r["frac"] == [189, 189] for r in rows), rows
    assert all(r.get("ocr_method") == "split" for r in rows), rows
    assert all(r["decision"] in ("commit", "pending_stable") for r in rows), [r["decision"] for r in rows]
    assert me.hp_current == 189 and me.hp_max == 189 and me.hp_percent == 100.0, (me.hp_current, me.hp_max, me.hp_percent)
    print("test_real_full_hp_reaches_set_hp OK", [r["decision"] for r in rows])


# ------------------------------------------------------------------ 最大 HP の決定に使わない (モック)
def test_split_read_not_used_for_max_adoption():
    """分けて読んだ分数 (分母の誤読 '183' 等が混じる) は、登録の理論値と違う最大 HP の実測採用の数え上げに入れない。
    同じ文字の 1 回の読み (direct) は従来どおり数える (tests.test_hp_bar_estimate.test_max_hp_adoption_guards と同じ条件)"""
    from tests.test_hp_bar_estimate import _run, _state_with_me, _saved_counts, _restore_counts
    from tests.test_hp_intake import _frame as intake_frame
    from vision.extractors import MY_MAX_ADOPT_READS
    saved = _saved_counts()
    try:
        full = intake_frame("my_full_137of137.png")
        st, mon = _state_with_me(ja="ムクホーク", sid="staraptor")
        for _ in range(MY_MAX_ADOPT_READS + 2):
            _run(full, st, ocr.OcrText("181/181", "split"), expected_max=161, name_text="")
        assert getattr(mon, "_my_max_adopted", None) is None, mon._my_max_adopted
        assert mon._max_adopt_counts.get(181) == 0, mon._max_adopt_counts
        assert st.hp_reject["player"]["reason"] == "max_hp_mismatch"
        assert st.my_hp_trace[-1]["ocr_method"] == "split"
        st, mon = _state_with_me(ja="ムクホーク", sid="staraptor")
        for _ in range(MY_MAX_ADOPT_READS):
            _run(full, st, ocr.OcrText("181/181", "direct"), expected_max=161, name_text="")
        assert mon._my_max_adopted == 181, getattr(mon, "_my_max_adopted", None)
    finally:
        _restore_counts(saved)
    print("test_split_read_not_used_for_max_adoption OK")


def test_split_read_not_used_for_max_votes():
    """基準 (登録の理論値・過去の実測) の無い個体では、分けて読んだ分数で最大 HP を決めない (桁分割の推測と同じ扱い)"""
    from vision import extractors
    from vision.state import BattleStateV2, PokemonState
    from tests.test_hp_bar_estimate import _saved_counts, _restore_counts
    from tests.test_hp_intake import _frame as intake_frame
    img = intake_frame("my_full_137of137.png")
    texts = {"t": ""}
    orig = ocr.read_zone_text, extractors._expected_my_max, extractors._my_legal_maxes

    def fake_read(_img, zone, **kw):
        return texts["t"] if zone is zones.BATTLE["my_hp_text"] else ""

    def fresh():
        st = BattleStateV2()
        st.battle_active = True
        me = PokemonState()
        me.hp_percent = 100.0
        st.player.party.append(me)
        st.player.active_index = 0
        return st, me

    ocr.read_zone_text = fake_read
    extractors._expected_my_max = lambda m: None
    extractors._my_legal_maxes = lambda: None
    saved = _saved_counts()
    try:
        st, me = fresh()
        texts["t"] = ocr.OcrText("137/137", "split")
        for _ in range(4):
            extractors.extract_my_hud(img, st, None)
            me._hp_stable_since = 0.0
        assert me.hp_max is None, me.hp_max
        st, me = fresh()
        texts["t"] = ocr.OcrText("137/137", "direct")
        for _ in range(4):
            extractors.extract_my_hud(img, st, None)
            me._hp_stable_since = 0.0
        assert me.hp_max == 137, me.hp_max
    finally:
        ocr.read_zone_text, extractors._expected_my_max, extractors._my_legal_maxes = orig
        _restore_counts(saved)
    print("test_split_read_not_used_for_max_votes OK")


if __name__ == "__main__":
    t0 = time.time()
    test_fraction_text_ok_pure()
    test_denominator_digits_pure()
    test_big_digits_right_col_synthetic()
    test_desaturate_gray_synthetic()
    test_ocr_text_method()
    test_real_fixture_reads()
    test_real_full_hp_reaches_set_hp()
    test_split_read_not_used_for_max_adoption()
    test_split_read_not_used_for_max_votes()
    print(f"\nALL OK ({time.time() - t0:.1f}s)")
