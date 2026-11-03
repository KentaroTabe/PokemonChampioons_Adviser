"""自分の HP バー照合の座標整合と、HP の読みの棄却理由の記録の検証 (2026-10-09、KNOWN_ISSUES A1 の 10/8 の行)。

- 自分の HP バーの割合は、バーの実範囲のゾーン (zones.BATTLE["my_hp_bar_track"]) で測る。従来の my_hp_bar (HUD の有無の
  判定用) で測ると実際の 0.7〜0.9 倍に出て、オオニューラ 120/159 の正しい読みがバー照合で捨てられ 93 秒間 100% のままだった
- 照合の許容 (HP_BAR_MATCH_TOL = 0.15) は変えない。桁落ち・別の画面の数値は引き続き捨てる
- 捨てた読みは state.hp_reject (側ごとの最後の 1 件) と起動からの件数 (HP_REJECT_COUNTS) に残す

画像は tests/fixtures/hp_intake/ の切り出し (原寸。manifest.json の offset で元の解像度の黒い画面に貼り戻す)。

使い方: python -m tests.test_hp_intake
"""
import json
import time
from pathlib import Path

import cv2
import numpy as np

from vision import ocr, zones
from vision.zones import crop
from vision.state import BattleStateV2, PokemonState

FIX = Path(__file__).resolve().parent / "fixtures" / "hp_intake"


def _frame(name: str):
    """切り出しを元の解像度の黒い画面の元の位置に貼り戻す (列の座標とゾーンの相対座標を元のフレームと同じにする)"""
    meta = json.loads((FIX / "manifest.json").read_text(encoding="utf-8"))[name]
    part = cv2.imread(str(FIX / name))
    assert part is not None, name
    w, h = meta["frame_size"]
    x0, y0 = meta["offset"]
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[y0:y0 + part.shape[0], x0:x0 + part.shape[1]] = part
    return img


# (fixture, 正解の現在値, 最大値) — 正解は保存フレームの HUD の数字を目視で確認
MY_CASES = [
    ("my_full_137of137.png", 137, 137),
    ("my_mid_120of159.png", 120, 159),
    ("my_low_14of161.png", 14, 161),
]


def _bars(zone_name: str) -> dict:
    return {name: ocr.hp_bar_ratio(crop(_frame(name), zones.BATTLE[zone_name])) for name, _c, _m in MY_CASES}


def test_track_zone_ratio_matches_truth():
    """バーの実範囲のゾーンで測った割合は、満タン・中程度・瀕死付近で正しい分数と許容内で一致する"""
    from vision.extractors import my_bar_agrees
    bars = _bars("my_hp_bar_track")
    for name, cur, mx in MY_CASES:
        bar = bars[name]
        assert bar is not None, name
        # 実測の誤差は 0.015 以内 (1920x1080 の 11 枚)。許容 0.15 よりかなり小さいことを確かめる
        assert abs(bar - cur / mx) <= 0.05, (name, bar, cur / mx)
        assert my_bar_agrees(cur, mx, bar), (name, bar)
    print("test_track_zone_ratio_matches_truth OK", {k: round(float(v), 3) for k, v in bars.items()})


def test_old_zone_rejected_correct_reading():
    """修正前のゾーン (my_hp_bar) では、中程度の HP の正しい読みが照合で落ちていた (不具合の再現)"""
    from vision.extractors import my_bar_agrees
    bar = _bars("my_hp_bar")["my_mid_120of159.png"]
    assert bar is not None and bar < 0.6, bar   # 実測 0.518 (正解 0.755)
    assert not my_bar_agrees(120, 159, bar)
    print("test_old_zone_rejected_correct_reading OK", round(float(bar), 3))


def test_garbled_fractions_still_rejected():
    """座標を直しても、桁落ち・別の画面の数値は同じバーの割合に当てると落ちる (許容は変えていない)"""
    from vision.extractors import my_bar_agrees
    bars = _bars("my_hp_bar_track")
    full, mid, low = (bars[n] for n, _c, _m in MY_CASES)
    # 満タンのバー: 正しい読みは通り、桁落ち ("201/201"→"20/201"、"162/162"→"16/162"、"137/137"→"13/137") は落ちる
    assert my_bar_agrees(137, 137, full)
    for cur, mx in ((20, 201), (16, 162), (13, 137)):
        assert not my_bar_agrees(cur, mx, full), (cur, mx, full)
    # 中程度 (120/159): 桁落ち "12/159"・様子見の左列の別の個体の値 (59/137、65/159) は落ちる
    assert my_bar_agrees(120, 159, mid)
    for cur, mx in ((12, 159), (20, 159), (59, 137), (65, 159)):
        assert not my_bar_agrees(cur, mx, mid), (cur, mx, mid)
    # 瀕死付近 (14/161): 数字が増えた誤読 "141/161" と満タンの値は落ちる
    assert my_bar_agrees(14, 161, low)
    for cur, mx in ((141, 161), (161, 161)):
        assert not my_bar_agrees(cur, mx, low), (cur, mx, low)
    # 実戦の例 "111/162"→"16/162" (バーは 111/162 を示す)
    assert my_bar_agrees(111, 162, 111 / 162) and not my_bar_agrees(16, 162, 111 / 162)
    # バーが読めないフレームは照合しない (従来どおり)
    assert my_bar_agrees(16, 162, None)
    print("test_garbled_fractions_still_rejected OK")


def _run_my_hud(img, st, hp_text, expected_max, bar_zone=None, name_text=""):
    """OCR の文字と型登録の理論値をモックして extract_my_hud を 1 回実行する (バーの割合は画像から実際に測る)"""
    from vision import extractors
    from vision.normalize import NameResolver
    orig_read, orig_expected, orig_legal = ocr.read_zone_text, extractors._expected_my_max, extractors._my_legal_maxes

    def fake_read(_img, zone, **kw):
        if zone is zones.BATTLE["my_name"]:
            return name_text
        if zone is zones.BATTLE["my_hp_text"]:
            return hp_text
        return ""

    ocr.read_zone_text = fake_read
    extractors._expected_my_max = lambda m: expected_max
    extractors._my_legal_maxes = lambda: None
    try:
        extractors.extract_my_hud(img, st, NameResolver(), bar_zone=bar_zone)
    finally:
        ocr.read_zone_text, extractors._expected_my_max, extractors._my_legal_maxes = \
            orig_read, orig_expected, orig_legal


def _state_with_me(ja="オオニューラ", sid="sneasler", hp=100.0):
    st = BattleStateV2()
    mon = PokemonState(species_ja=ja, species_id=sid, display_name=ja)
    mon.hp_percent = hp
    st.player.party.append(mon)
    st.player.active_index = 0
    return st, mon


def test_my_hud_bar_mismatch_before_fix_and_adopted_after():
    """オオニューラ 120/159 (frame_1791452766): 修正前のゾーンでは bar_mismatch が残り、修正後は 120/159 が採用される"""
    from vision import state as state_mod
    img = _frame("my_mid_120of159.png")
    saved = dict(state_mod.HP_REJECT_COUNTS)
    try:
        # 修正前 (my_hp_bar で測る) の再現
        st, mon = _state_with_me()
        for _ in range(3):
            _run_my_hud(img, st, "120/159", 159, bar_zone=zones.BATTLE["my_hp_bar"])
            mon._hp_stable_since = 0.0
        rej = st.hp_reject["player"]
        assert rej and rej["reason"] == "bar_mismatch", rej
        assert rej["hp_candidate"] == [120, 159] and rej["bar_ratio"] < 0.6, rej
        assert rej["n"] == 3 and rej["slot"] == 0 and rej["source"] == "hud", rej
        json.dumps(st.to_dict()["hp_reject"])   # 対戦ログ (json.dumps、default なし) に書ける型であること
        assert mon.hp_percent == 100.0, mon.hp_percent

        # 修正後 (既定の my_hp_bar_track)
        st, mon = _state_with_me()
        for _ in range(2):
            _run_my_hud(img, st, "120/159", 159)
            mon._hp_stable_since = 0.0   # 600ms の安定条件を満たしたことにする
        assert (mon.hp_current, mon.hp_max) == (120, 159), (mon.hp_current, mon.hp_max)
        assert abs(mon.hp_percent - 75.5) < 0.1, mon.hp_percent
        assert st.hp_reject["player"] is None, st.hp_reject

        # 修正後も桁落ちは捨てる ("120/159"→"12/159")。理由と候補が残る
        _run_my_hud(img, st, "12/159", 159)
        rej = st.hp_reject["player"]
        assert rej["reason"] == "bar_mismatch" and rej["hp_candidate"] == [12, 159], rej
        assert abs(mon.hp_percent - 75.5) < 0.1, mon.hp_percent
    finally:
        state_mod.HP_REJECT_COUNTS.clear()
        state_mod.HP_REJECT_COUNTS.update(saved)
    print("test_my_hud_bar_mismatch_before_fix_and_adopted_after OK")


def test_my_hud_other_reject_reasons():
    """自分の HP の他の棄却の分岐にも理由が付く (分数が読めない / 最大 HP が基準と合わない)"""
    from vision import state as state_mod
    img = _frame("my_full_137of137.png")
    saved = dict(state_mod.HP_REJECT_COUNTS)
    try:
        st, mon = _state_with_me("イエッサン", "indeedeef")
        _run_my_hud(img, st, "137", 137)          # 分母が落ちた (10/8 の試験で多かった形)
        rej = st.hp_reject["player"]
        assert rej["reason"] == "fraction_unparsable" and rej["hp_candidate"] is None and rej["hp_text"] == "137", rej
        _run_my_hud(img, st, "", 137)             # 文字が無いフレームは「捨てた読み」に数えない
        assert st.hp_reject["player"]["n"] == 1, st.hp_reject
        _run_my_hud(img, st, "0/3", 137)          # 最大 HP 50 未満 (選出画面の「0/3」の重なり) も分数として読めない扱い
        assert st.hp_reject["player"]["reason"] == "fraction_unparsable"
        _run_my_hud(img, st, "1377", 137)          # parse_fraction → 13/77: 基準 137 と合わない
        rej = st.hp_reject["player"]
        assert rej["reason"] == "max_hp_mismatch" and rej["hp_candidate"] == [13, 77], rej
        assert mon.hp_percent == 100.0, mon.hp_percent
        assert state_mod.HP_REJECT_COUNTS[("player", "fraction_unparsable")] - \
            saved.get(("player", "fraction_unparsable"), 0) == 2
    finally:
        state_mod.HP_REJECT_COUNTS.clear()
        state_mod.HP_REJECT_COUNTS.update(saved)
    print("test_my_hud_other_reject_reasons OK")


def _opp_state():
    st = BattleStateV2()
    st.battle_active = True
    for ja, sid in (("ニョロトノ", "politoed"), ("ヤドキング", "slowking"), ("ミミッキュ", "mimikyu")):
        m = PokemonState(species_ja=ja, species_id=sid, display_name=ja)
        m.hp_percent = 100.0
        st.opponent.party.append(m)
    st.opponent.active_index = 0
    return st, st.opponent.party[0]


def test_opp_verdict_names_each_branch():
    """相手 (field 経路) の判定は従来の 2 分岐のまま、理由と名前の分類を返す (純粋)"""
    from vision.extractors import opp_field_hp_verdict
    known = ["ニョロトノ"]
    others = ["ヤドキング", "ミミッキュ"]
    lookup = {"ピカチュウ": "ピカチュウ"}.get
    v = opp_field_hp_verdict("/BEニ", known, 42, 100.0, others, lookup)
    assert v["reason"] == "name_mismatch" and v["name_kind"] == "unreadable" and v["name_match"] is None, v
    assert 0.2 <= v["name_similarity"] < 0.5, v
    v = opp_field_hp_verdict("ヤドキンク", known, 42, 100.0, others, lookup)
    assert v["name_kind"] == "other_member" and v["name_match"] == "ヤドキング", v
    v = opp_field_hp_verdict("ピカチュウ", known, 42, 100.0, others, lookup)
    assert v["name_kind"] == "other_species" and v["name_match"] == "ピカチュウ", v
    v = opp_field_hp_verdict("", known, 42, 100.0, others, lookup)
    assert v == {"reason": "name_unreadable_big_change", "name_similarity": None}, v
    v = opp_field_hp_verdict("", known, 42, None, others, lookup)     # HP 未知
    assert v["reason"] == "name_unreadable_big_change", v
    # 書いてよい読み: 名前が合う / 名前が読めず小さな変化 / 既知の名前が無い
    assert opp_field_hp_verdict("ニョロトノ", known, 42, 100.0, others, lookup) is None
    assert opp_field_hp_verdict("ニョロト/", known, 42, 100.0, others, lookup) is None
    assert opp_field_hp_verdict("", known, 90, 100.0, others, lookup) is None
    assert opp_field_hp_verdict("ホゲ", [], 42, 100.0, others, lookup) is None
    print("test_opp_verdict_names_each_branch OK")


def test_field_hp_records_name_mismatch_on_real_frame():
    """10/8 18:37 のニョロトノ 42% (連続保存の 1 枚): 名前の OCR が崩れて見送られ、name_mismatch と観測が残る。
    OCR は実物 (Apple Vision)。使えない環境ではスキップする"""
    from vision import extractors
    from vision import state as state_mod
    if not ocr._get_apple_vision():
        print("test_field_hp_records_name_mismatch_on_real_frame SKIP (Apple Vision が無い)")
        return
    saved = dict(state_mod.HP_REJECT_COUNTS)
    try:
        st, mon = _opp_state()
        extractors.extract_field_hp(_frame("opp_nyorotono_42.png"), st)
        rej = st.hp_reject["opponent"]
        assert rej and rej["reason"] == "name_mismatch", rej
        assert rej["name_text"] and rej["name_similarity"] < 0.5, rej
        assert rej["name_kind"] == "unreadable", rej
        assert rej["hp_candidate"] == 42 and rej["pct_from_bar"] is False, rej
        assert rej["slot"] == 0 and rej["source"] == "field", rej
        json.dumps(st.to_dict()["hp_reject"])   # 対戦ログ (json.dumps、default なし) に書ける型であること
        assert mon.hp_percent == 100.0, mon.hp_percent
        assert state_mod.HP_REJECT_COUNTS[("opponent", "name_mismatch")] - \
            saved.get(("opponent", "name_mismatch"), 0) == 1
        print(f"test_field_hp_records_name_mismatch_on_real_frame OK (name_text={rej['name_text']!r} "
              f"similarity={rej['name_similarity']} bar={rej['bar_ratio']})")
    finally:
        state_mod.HP_REJECT_COUNTS.clear()
        state_mod.HP_REJECT_COUNTS.update(saved)


def test_merge_counts_and_log_fields():
    """同じ理由・候補・枠の連続は 1 件にまとめる。件数は対戦をまたいで残り、scene 行の state に hp_reject が載る"""
    from battle_logger import _compact_state, scene_row_state, state_digest
    from vision import state as state_mod
    from vision.state import format_hp_reject_counts, merge_hp_reject

    a = {"reason": "bar_mismatch", "hp_candidate": [120, 159], "slot": 0, "t": 1.0}
    m1 = merge_hp_reject(None, a)
    assert m1["n"] == 1 and m1["t_first"] == 1.0
    m2 = merge_hp_reject(m1, dict(a, t=2.0, bar_ratio=0.5))
    assert m2["n"] == 2 and m2["t_first"] == 1.0 and m2["t"] == 2.0 and m2["bar_ratio"] == 0.5
    m3 = merge_hp_reject(m2, dict(a, hp_candidate=[12, 159], t=3.0))
    assert m3["n"] == 1 and m3["t_first"] == 3.0
    m4 = merge_hp_reject(m3, dict(a, hp_candidate=[12, 159], slot=1, t=4.0))
    assert m4["n"] == 1

    assert format_hp_reject_counts({}) == "HP棄却=0"
    s = format_hp_reject_counts({("opponent", "name_mismatch"): 3, ("player", "bar_mismatch"): 12,
                                 ("player", "fraction_unparsable"): 12})
    assert s == "HP棄却 自分[bar_mismatch=12 fraction_unparsable=12] 相手[name_mismatch=3]", s

    saved = dict(state_mod.HP_REJECT_COUNTS)
    try:
        st, _mon = _opp_state()
        before = st.to_dict()
        st.record_hp_reject("opponent", {"reason": "name_mismatch", "hp_candidate": 42, "slot": 0})
        st.record_hp_reject("opponent", {"reason": "name_mismatch", "hp_candidate": 42, "slot": 0})
        d = st.to_dict()
        assert d["hp_reject"]["opponent"]["n"] == 2 and d["hp_reject"]["player"] is None, d["hp_reject"]
        row = scene_row_state(d)
        assert row["hp_reject"]["opponent"]["reason"] == "name_mismatch"
        # 助言の行の state と digest は変えない (hp_reject は scene 行にだけ載せる)
        assert "hp_reject" not in _compact_state(d)
        assert state_digest(_compact_state(d)) == state_digest(_compact_state(before))
        st.reset_battle()
        assert st.hp_reject == {"player": None, "opponent": None}
        assert state_mod.HP_REJECT_COUNTS[("opponent", "name_mismatch")] - \
            saved.get(("opponent", "name_mismatch"), 0) == 2
        # 古い形の state (hp_reject の欄が無い) でも scene 行は書ける
        old = dict(d)
        old.pop("hp_reject")
        assert scene_row_state(old)["hp_reject"] == {"player": None, "opponent": None}
    finally:
        state_mod.HP_REJECT_COUNTS.clear()
        state_mod.HP_REJECT_COUNTS.update(saved)
    print("test_merge_counts_and_log_fields OK")


if __name__ == "__main__":
    t0 = time.time()
    test_track_zone_ratio_matches_truth()
    test_old_zone_rejected_correct_reading()
    test_garbled_fractions_still_rejected()
    test_my_hud_bar_mismatch_before_fix_and_adopted_after()
    test_my_hud_other_reject_reasons()
    test_opp_verdict_names_each_branch()
    test_field_hp_records_name_mismatch_on_real_frame()
    test_merge_counts_and_log_fields()
    print(f"\nALL OK ({time.time() - t0:.1f}s)")
