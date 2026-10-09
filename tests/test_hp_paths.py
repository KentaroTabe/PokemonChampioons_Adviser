"""HP の取り込み経路の検証 (2026-10-09 fix/hp-paths、KNOWN_ISSUES A1 の 10/7〜10/9 の行)。

段 1: extract_field_hp は相手バナーの判定 (赤の割合) を相手側だけに掛け、自分の HUD だけが出ているフレームでも自分の HP を読む
      (10/8 オオニューラ 120/159 が 93 秒間 100%、10/9 オオニューラ 4/159 の取りこぼしの原因)
段 2: 最大 HP の多数決 (_set_hp の hp_max_votes) に、桁分割で推測した分数 ('1595' → 15/95) と物理可能域外の最大 HP を入れない
段 3: 相手の HP バーの割合はバーの実範囲のゾーン (zones の opp_hp_bar_track) で測る (旧ゾーン opp_hp_bar は +4〜9 ポイント過大)
段 4: 自分の HP の読みの経過 (state.my_hp_trace) と、対戦ログの my_hp_trace 行
段 5: 様子見画面の右列の行ごとの読み (state.watch_opp_rows / scene 行) と、交代の文言なしの +60 超の増加の見送り

画像は tests/fixtures/ の原寸の切り出し (manifest.json の offset で元の解像度の黒い画面に貼り戻す):
  hp_intake/my_mid_120of159.png (frame_1791452766)、hp_estimate/my_low_4of159.png (frame_1791503228)、
  opp_hp_bar/opp_high_87.png・opp_mid_58.png・opp_low_13.png (相手の HUD 周辺)

使い方: python -m tests.test_hp_paths
"""
import json
import shutil
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np

from champions_agent.config import (MY_HP_TRACE_DUMP_STREAK, MY_HP_TRACE_LEN, MY_HP_TRACE_MAX_DUMPS,
                                    WATCH_OPP_BIG_INCREASE)
from vision import ocr, zones
from vision.state import BattleStateV2, PokemonState
from vision.zones import crop

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _frame(topic: str, name: str):
    """切り出しを元の解像度の黒い画面の元の位置に貼り戻す (列の座標とゾーンの相対座標を元のフレームと同じにする)"""
    meta = json.loads((FIXTURES / topic / "manifest.json").read_text(encoding="utf-8"))[name]
    part = cv2.imread(str(FIXTURES / topic / name))
    assert part is not None, name
    w, h = meta["frame_size"]
    x0, y0 = meta["offset"]
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[y0:y0 + part.shape[0], x0:x0 + part.shape[1]] = part
    return img


def _saved_counts():
    from vision import state as state_mod
    return dict(state_mod.HP_REJECT_COUNTS)


def _restore_counts(saved):
    from vision import state as state_mod
    state_mod.HP_REJECT_COUNTS.clear()
    state_mod.HP_REJECT_COUNTS.update(saved)


def _state(me_ja="オオニューラ", me_sid="sneasler", me_hp=100.0, opp=True):
    st = BattleStateV2()
    st.battle_active = True
    st.scene = "field"
    me = PokemonState(species_ja=me_ja, species_id=me_sid, display_name=me_ja)
    me.hp_percent = me_hp
    st.player.party.append(me)
    st.player.active_index = 0
    if opp:
        o = PokemonState(species_ja="ミミッキュ", species_id="mimikyu", display_name="ミミッキュ")
        o.hp_percent = 90.0
        st.opponent.party.append(o)
        st.opponent.active_index = 0
    return st, me


def _run_field(img, st, my_text, expected_max=159, read_log=None):
    """自分の HP の文字と型登録の理論値をモックして extract_field_hp を 1 回実行する (相手バナーの赤と画素数は画像から実際に測る)。
    read_log を渡すと、OCR を読みに行ったゾーンの名前を足す"""
    from vision import extractors
    names = {id(z): k for k, z in zones.BATTLE.items()}
    orig = ocr.read_zone_text, extractors._expected_my_max, extractors._my_legal_maxes

    def fake_read(_img, zone, **kw):
        if read_log is not None:
            read_log.append(names.get(id(zone)))
        if zone is zones.BATTLE["my_hp_text"]:
            return my_text
        return ""

    ocr.read_zone_text = fake_read
    extractors._expected_my_max = lambda m: expected_max
    extractors._my_legal_maxes = lambda: None
    try:
        extractors.extract_field_hp(img, st)
    finally:
        ocr.read_zone_text, extractors._expected_my_max, extractors._my_legal_maxes = orig


# ------------------------------------------------------------------ 段 1
def test_field_reads_my_hp_without_opp_banner():
    """相手バナーが無く自分の HUD だけがあるフレーム (frame_1791452766 の自分の HUD、相手側は黒) で自分の HP を読む。
    相手側は従来どおり読まない (相手のゾーンの OCR もしない)"""
    from vision.scenes import _crimson_ratio, _hp_bar_pixels
    img = _frame("hp_intake", "my_mid_120of159.png")
    assert _crimson_ratio(crop(img, zones.BATTLE["opp_banner"])) < 0.15
    assert _hp_bar_pixels(crop(img, zones.BATTLE["my_hp_bar"])) > 30
    saved = _saved_counts()
    try:
        st, me = _state()
        opp = st.opponent.party[0]
        reads = []
        for _ in range(2):
            _run_field(img, st, "120/159", read_log=reads)
            me._hp_stable_since = 0.0   # 600ms の安定条件を満たしたことにする
        assert (me.hp_current, me.hp_max) == (120, 159), (me.hp_current, me.hp_max)
        assert abs(me.hp_percent - 75.5) < 0.1, me.hp_percent
        # 相手側は読まない (OCR もしない)、書かない、棄却も残らない
        assert set(reads) == {"my_hp_text"}, reads
        assert opp.hp_percent == 90.0 and st.hp_reject["opponent"] is None
        rows = st.my_hp_trace
        assert [r["decision"] for r in rows] == ["pending_stable", "commit"], rows
        assert rows[0]["reason"] == "no_prev_read" and rows[1]["reason"] == "stable"
        r = rows[-1]
        assert r["source"] == "field" and r["frac"] == [120, 159] and r["opp_banner"] < 0.15 and r["my_bar_px"] > 30, r
        assert r["new"] == 75.5 and r["stable_count"] == 2 and r["split_guess"] is False, r

        # 分数が読めなければ棄却理由が残る
        _run_field(img, st, "12")
        assert st.hp_reject["player"]["reason"] == "fraction_unparsable" and st.hp_reject["player"]["source"] == "field"
        assert st.my_hp_trace[-1]["decision"] == "fraction_unparsable"
    finally:
        _restore_counts(saved)
    print("test_field_reads_my_hp_without_opp_banner OK")


def test_field_passes_4of159_to_set_hp():
    """10/9 08:47 のオオニューラ 4/159 (frame_1791503228 の自分の HUD。相手バナー側は黒): 相手バナーが無くても
    自分の読み 4/159 が _set_hp に渡る (3% 以下なので即確定はしない。経過の記録に pending_stable / commit が残る)"""
    img = _frame("hp_estimate", "my_low_4of159.png")
    texts = ["4/159"]
    if ocr._get_apple_vision():
        # 実物の OCR で読める場合はそれも使う (読めた文字をそのまま渡す)
        real = ocr.read_zone_text(img, zones.BATTLE["my_hp_text"], mode="panel", allowlist="0123456789/")
        assert ocr.parse_fraction(real) == (4, 159), real
        texts.append(real)
    for text in texts:
        st, me = _state()
        _run_field(img, st, text)
        r = st.my_hp_trace[-1]
        assert r["decision"] in ("pending_stable", "commit"), r
        assert r["frac"] == [4, 159] and r["new"] == 2.5 and r["opp_banner"] < 0.15, r
        assert me.hp_percent == 100.0   # 1 回目は保留
        # 3 回 (+ 600ms) で確定する (3% 以下の安定条件は従来どおり)
        for _ in range(2):
            me._hp_stable_since = 0.0
            _run_field(img, st, text)
        assert [x["decision"] for x in st.my_hp_trace] == ["pending_stable", "pending_stable", "commit"], st.my_hp_trace
        assert [x["reason"] for x in st.my_hp_trace[:2]] == ["no_prev_read", "low_needs_reads"]
        assert (me.hp_current, me.hp_max) == (4, 159)
    print(f"test_field_passes_4of159_to_set_hp OK (texts={texts})")


def test_field_skips_without_my_hud():
    """自分の HUD も無いフレーム (全面黒) は自分側を読まず、skip_no_my_hud と判定の材料を残す"""
    img = np.zeros((1080, 1920, 3), dtype=np.uint8)
    st, me = _state()
    reads = []
    _run_field(img, st, "120/159", read_log=reads)
    assert reads == [] and me.hp_percent == 100.0
    r = st.my_hp_trace[-1]
    assert r["decision"] == "skip_no_my_hud" and r["my_bar_px"] == 0 and r["opp_banner"] == 0.0, r
    assert st.my_hp_reject_streak == 0
    print("test_field_skips_without_my_hud OK")


# ------------------------------------------------------------------ 段 2
def test_split_guess_does_not_decide_max_hp():
    """基準 (型登録の理論値・過去の実測) の無い状態で '1595' (15/95) を何度読んでも最大 HP が 95 に決まらない。
    '4/159' なら従来どおり決まる。物理可能域外の最大 HP も決まらない"""
    from vision import extractors
    img = _frame("hp_estimate", "my_low_4of159.png")
    orig = ocr.read_zone_text, extractors._expected_my_max, extractors._my_legal_maxes
    texts = {"t": ""}

    def fake_read(_img, zone, **kw):
        return texts["t"] if zone is zones.BATTLE["my_hp_text"] else ""

    def run(st, text, n):
        texts["t"] = text
        for _ in range(n):
            extractors.extract_my_hud(img, st, None)
            st.player.active()._hp_stable_since = 0.0

    ocr.read_zone_text = fake_read
    extractors._expected_my_max = lambda m: None
    extractors._my_legal_maxes = lambda: None
    saved = _saved_counts()
    try:
        assert ocr.fraction_is_split_guess("1595") and ocr.parse_fraction("1595") == (15, 95)
        # 種族未特定 (物理可能域で弾けない) の個体: 桁分割の 15/95 は使わない・票に入れない
        st, me = _state(me_ja=None, me_sid=None)
        me.display_name = None
        run(st, "1595", 4)
        assert me.hp_max is None and me.hp_percent == 100.0, (me.hp_max, me.hp_percent)
        assert all(95 not in v for v in st.hp_max_votes.values()), st.hp_max_votes
        assert {r["decision"] for r in st.my_hp_trace} == {"max_split_guess"}, st.my_hp_trace
        assert st.my_hp_trace[-1]["split_guess"] is True and st.my_hp_trace[-1]["reason"] == "no_base"
        # '/' のある読みは従来どおり決まる (2.5% は 3 回 + 600ms)
        run(st, "4/159", 3)
        assert (me.hp_current, me.hp_max) == (4, 159), (me.hp_current, me.hp_max)
        # 基準 (159) ができた後の桁分割は、既存の基準との照合で捨てる (最大 HP の実測採用の数え上げにも入らない)
        run(st, "1595", 4)
        assert me.hp_max == 159 and st.my_hp_trace[-1]["decision"] == "max_hp_mismatch", st.my_hp_trace[-1]
        assert getattr(me, "_my_max_adopted", None) is None
        # 物理可能域外: オオニューラ (HP 種族値 80、Lv50 で 147〜195) に '15/95' は '/' があっても決まらない
        st2, me2 = _state()
        run(st2, "15/95", 4)
        assert me2.hp_max is None and me2.hp_percent == 100.0
        assert st2.hp_reject["player"]["reason"] == "max_hp_implausible"
    finally:
        ocr.read_zone_text, extractors._expected_my_max, extractors._my_legal_maxes = orig
        _restore_counts(saved)
    print("test_split_guess_does_not_decide_max_hp OK")


def test_set_hp_split_guess_rules():
    """_set_hp 単体: 桁分割の読みは票に入れず、基準 (理論値 / 2 票以上の多数 / 過去の最大 HP) と合うときだけ使う"""
    from vision.extractors import _set_hp, split_guess_max_base
    assert split_guess_max_base(161, {}, None) == 161
    assert split_guess_max_base(None, {159: 2, 95: 1}, None) == 159
    assert split_guess_max_base(None, {159: 1}, None) is None
    assert split_guess_max_base(None, {159: 1}, 159) == 159
    assert split_guess_max_base(None, {}, 40) is None
    st = BattleStateV2()
    mon = PokemonState(species_ja="テスト", display_name="テスト")
    st.player.party.append(mon)
    st.player.active_index = 0
    from vision import extractors
    orig = extractors._expected_my_max, extractors._my_legal_maxes
    extractors._expected_my_max = lambda m: None
    extractors._my_legal_maxes = lambda: None
    try:
        for _ in range(3):
            _set_hp(st, "player", mon, cur=15, mx=95, split_guess=True)
        assert mon.hp_max is None and st.hp_max_votes.get(("player", "テスト"), {}) == {}
        assert mon._hp_set_result["decision"] == "max_split_guess"
        _set_hp(st, "player", mon, cur=150, mx=159)            # 初回の '/' のある読みは即反映 (従来どおり)
        assert (mon.hp_current, mon.hp_max) == (150, 159)
        assert mon._hp_set_result["decision"] == "commit" and mon._hp_set_result["reason"] == "first_read"
        _set_hp(st, "player", mon, cur=120, mx=159, split_guess=True)   # 過去の最大 HP と合う桁分割は使う (票は増えない)
        assert st.hp_max_votes[("player", "テスト")] == {159: 1}
        assert mon._hp_set_result["decision"] == "pending_stable" and mon._hp_set_result["new"] == 75.5
        assert mon._hp_set_result["stable_count"] == 1 and 0.0 <= mon._hp_set_result["since_commit"] < 5.0, \
            mon._hp_set_result   # 前回の確定 (hp_read_ts) からの秒数
    finally:
        extractors._expected_my_max, extractors._my_legal_maxes = orig
    print("test_set_hp_split_guess_rules OK")


# ------------------------------------------------------------------ 段 3
OPP_CASES = ("opp_high_87.png", "opp_mid_58.png", "opp_low_13.png")


def test_opp_track_zone_matches_text():
    """相手の HUD 周辺の原寸の切り出し 3 枚: 新ゾーン (opp_hp_bar_track) の割合は文字の % と 3 ポイント以内、
    旧ゾーン (opp_hp_bar) は +4 ポイント以上ずれる (不具合の再現)"""
    man = json.loads((FIXTURES / "opp_hp_bar" / "manifest.json").read_text(encoding="utf-8"))
    out = {}
    for name in OPP_CASES:
        img = _frame("opp_hp_bar", name)
        pct = man[name]["pct_text"]
        new = ocr.hp_bar_ratio(crop(img, zones.BATTLE["opp_hp_bar_track"])) * 100
        old = ocr.hp_bar_ratio(crop(img, zones.BATTLE["opp_hp_bar"])) * 100
        assert abs(new - pct) <= 3.0, (name, new, pct)
        assert old - pct >= 4.0, (name, old, pct)
        out[name] = (pct, round(new, 1), round(old, 1))
        if ocr._get_apple_vision():
            text = ocr.read_zone_text(img, zones.BATTLE["opp_hp_text"], mode="panel", allowlist="0123456789%")
            assert ocr.parse_percent(text) == pct, (name, text)
    print("test_opp_track_zone_matches_text OK", out)


def test_opp_bar_substitute_rule_unchanged():
    """文字の % とバーの差が 15 を超えたらバーで代用する規則は閾値を変えずに残す (純粋)"""
    from vision.extractors import opp_bar_substitute
    assert opp_bar_substitute(90, 0.88) == (90.0, False)
    assert opp_bar_substitute(19, 0.01) == (1.0, True)        # 「1%」→「19」の誤読はバーで代用
    assert opp_bar_substitute(None, 0.42) == (42.0, True)
    assert opp_bar_substitute(42, None) == (42.0, False)
    assert opp_bar_substitute(None, None) == (None, False)
    assert opp_bar_substitute(50, 0.65) == (50.0, False)      # 差 15 ちょうどは代用しない
    print("test_opp_bar_substitute_rule_unchanged OK")


def test_field_opp_side_uses_track_zone():
    """field 経路の相手側は新ゾーンでバーを測る: 文字が読めないフレームの代用値が文字の % に近い (13% → 旧ゾーンなら 18.1)。
    代用した読みの棄却の記録 (pct_from_bar) は従来どおり"""
    from vision import extractors
    img = _frame("opp_hp_bar", "opp_low_13.png")
    orig = ocr.read_zone_text
    ocr.read_zone_text = lambda _img, zone, **kw: ""   # % の文字も名前も読めない
    saved = _saved_counts()
    try:
        st, _me = _state()
        opp = st.opponent.party[0]
        extractors.extract_field_hp(img, st)
        rej = st.hp_reject["opponent"]
        # 名前が読めず 90 → 約 13 の大変化なので書かない (従来どおり)。候補はバーの代用値
        assert rej["reason"] == "name_unreadable_big_change" and rej["pct_from_bar"] is True, rej
        assert abs(rej["hp_candidate"] - 13) <= 3.0, rej
        assert opp.hp_percent == 90.0
    finally:
        ocr.read_zone_text = orig
        _restore_counts(saved)
    print("test_field_opp_side_uses_track_zone OK", rej["hp_candidate"])


# ------------------------------------------------------------------ 段 4
def test_hp_settle_step_pure():
    """確定の判定 (純粋): 従来の規則のまま切り出したもの"""
    from vision.extractors import hp_settle_step
    s = hp_settle_step(None, None, 50.0, 1, 0.0, 10.0)
    assert (s["decision"], s["reason"], s["first"]) == ("commit", "first_read", True)
    s = hp_settle_step(None, None, 2.0, 1, 0.0, 10.0)
    assert (s["decision"], s["reason"], s["stable_count"]) == ("pending_stable", "first_low", 1)
    s = hp_settle_step(80.0, None, 50.0, 3, 0.0, 10.0)
    assert (s["decision"], s["reason"], s["stable_count"], s["stable_since"]) == ("pending_stable", "no_prev_read", 1, 10.0)
    s = hp_settle_step(80.0, 60.0, 50.0, 3, 0.0, 10.0)
    assert (s["decision"], s["reason"]) == ("pending_stable", "value_changed")
    s = hp_settle_step(80.0, 50.5, 50.0, 1, 9.8, 10.0)
    assert (s["decision"], s["reason"], s["stable_count"]) == ("pending_stable", "wait_time", 2)
    s = hp_settle_step(80.0, 50.5, 50.0, 1, 9.0, 10.0)
    assert (s["decision"], s["reason"], s["stable_count"]) == ("commit", "stable", 2)
    s = hp_settle_step(80.0, 2.5, 2.5, 1, 9.0, 10.0)
    assert (s["decision"], s["reason"]) == ("pending_stable", "low_needs_reads")
    s = hp_settle_step(80.0, 2.5, 2.5, 2, 9.0, 10.0)
    assert (s["decision"], s["reason"]) == ("commit", "stable")
    print("test_hp_settle_step_pure OK")


def test_my_hud_trace_rows():
    """extract_my_hud の経過の記録: 確定したフレームは commit、安定待ちは pending_stable、捨てたフレームは理由。各行に decision と reason"""
    from vision import extractors
    img = _frame("hp_intake", "my_mid_120of159.png")
    orig = ocr.read_zone_text, extractors._expected_my_max, extractors._my_legal_maxes
    texts = {"t": ""}

    def fake_read(_img, zone, **kw):
        if zone is zones.BATTLE["my_name"]:
            return "オオニューラ"
        return texts["t"] if zone is zones.BATTLE["my_hp_text"] else ""

    ocr.read_zone_text = fake_read
    extractors._expected_my_max = lambda m: 159
    extractors._my_legal_maxes = lambda: None
    saved = _saved_counts()
    try:
        from vision.normalize import NameResolver
        res = NameResolver()
        st, me = _state()
        for t in ("120/159", "120/159", "12/159", "", "1201"):
            texts["t"] = t
            extractors.extract_my_hud(img, st, res)
            me._hp_stable_since = 0.0
        rows = st.my_hp_trace
        assert [r["decision"] for r in rows] == ["pending_stable", "commit", "bar_mismatch", "no_text",
                                                 "fraction_unparsable"], rows
        for r in rows:
            assert "decision" in r and "reason" in r and r["source"] == "hud", r
            for k in ("t", "scene", "name_text", "hud_species", "active_species", "hp_text", "frac", "split_guess",
                      "known_max", "bar"):
                assert k in r, (k, r)
        assert rows[0]["new"] == 75.5 and rows[0]["stable_count"] == 1 and rows[1]["stable_count"] == 2
        assert rows[1]["hud_species"] == "オオニューラ" and rows[1]["known_max"] == 159
        assert rows[2]["reason"].startswith("frac=0.075 bar=") and rows[2]["frac"] == [12, 159]
        assert st.my_hp_reject_streak == 2   # 文字の無いフレームは数えない
        json.dumps(rows)   # 対戦ログに書ける型
    finally:
        ocr.read_zone_text, extractors._expected_my_max, extractors._my_legal_maxes = orig
        _restore_counts(saved)
    print("test_my_hud_trace_rows OK")


def _trace_row(decision, reason=None):
    return {"source": "hud", "decision": decision, "reason": reason}


def test_trace_dump_rules_and_logger_rows():
    """棄却が MY_HP_TRACE_DUMP_STREAK 回続くと my_hp_trace 行が書かれる (1 対戦で最大 MY_HP_TRACE_MAX_DUMPS 回)。
    対戦の終わり (次の対戦への切り替え) でも書かれ、前の対戦のファイルに入る。scene 行の state には載らない"""
    from battle_logger import BattleLogger
    from vision.state import my_hp_reject_streak
    assert my_hp_reject_streak(3, "commit") == 0 and my_hp_reject_streak(3, "pending_stable") == 0
    assert my_hp_reject_streak(3, "skip_no_my_hud") == 3 and my_hp_reject_streak(3, "no_text") == 3
    assert my_hp_reject_streak(3, "fraction_unparsable") == 4 and my_hp_reject_streak(3, "estimate") == 4

    tmp = Path(tempfile.mkdtemp())
    try:
        lg = BattleLogger(log_dir=tmp, guess_prob_fn=None)
        st, _me = _state()
        st.scene = "command"
        st.battle_seq = 1
        lg.on_frame(st.to_dict(), [])
        first = lg._file
        st.my_hp_trace_outbox = []
        st.record_my_hp_trace(_trace_row("commit", "stable"))
        for i in range(MY_HP_TRACE_DUMP_STREAK - 1):
            st.record_my_hp_trace(_trace_row("fraction_unparsable"))
            st.record_my_hp_trace(_trace_row("skip_no_my_hud"))   # HUD の無いフレームは連続を切らない
        assert st.my_hp_trace_outbox == [] and st.my_hp_reject_streak == MY_HP_TRACE_DUMP_STREAK - 1
        st.record_my_hp_trace(_trace_row("bar_mismatch", "frac=0.075 bar=0.700"))
        assert len(st.my_hp_trace_outbox) == 1 and st.my_hp_reject_streak == 0
        d = st.to_dict()
        assert len(d["my_hp_trace_dumps"][0]["rows"]) == MY_HP_TRACE_LEN   # 直近 MY_HP_TRACE_LEN 件
        lg.on_frame(d, [])
        lg.on_frame(d, [])   # 同じ分は 1 回だけ書く
        recs = [json.loads(x) for x in first.read_text(encoding="utf-8").splitlines()]
        tr = [r for r in recs if r["type"] == "my_hp_trace"]
        assert len(tr) == 1 and tr[0]["reason"] == "reject_streak" and tr[0]["battle_seq"] == 1, tr
        assert all("decision" in r and "reason" in r for r in tr[0]["rows"])
        assert tr[0]["rows"][-1]["decision"] == "bar_mismatch"
        assert all("my_hp_trace" not in r.get("state", {}) and "my_hp_trace_dumps" not in r.get("state", {})
                   for r in recs if r["type"] == "scene")
        # 上限: この対戦であと MY_HP_TRACE_MAX_DUMPS - 1 回まで
        st.my_hp_trace_outbox = []
        for _ in range(MY_HP_TRACE_DUMP_STREAK * (MY_HP_TRACE_MAX_DUMPS + 2)):
            st.record_my_hp_trace(_trace_row("fraction_unparsable"))
        assert st.my_hp_trace_dumps == MY_HP_TRACE_MAX_DUMPS
        assert len(st.my_hp_trace_outbox) == MY_HP_TRACE_MAX_DUMPS - 1
        assert st.dump_my_hp_trace("battle_end") is None

        # 次の対戦への切り替え: reset_battle が終わりの分を出し、ロガーは前の対戦のファイルに書く
        st2, _ = _state()
        st2.battle_seq = 3
        st2.scene = "command"
        st2.record_my_hp_trace(_trace_row("commit", "stable"))
        st2.my_hp_trace_outbox = []
        lg.on_frame(st2.to_dict(), [])          # 別の対戦 (seq 3) のファイルを開く
        second = lg._file
        assert second != first
        st2.reset_battle()
        assert st2.my_hp_trace == [] and len(st2.my_hp_trace_outbox) == 1 and st2.battle_seq == 4
        assert st2.my_hp_trace_outbox[0]["reason"] == "battle_end" and st2.my_hp_trace_outbox[0]["battle_seq"] == 3
        st2.scene = "selection"
        lg.on_frame(st2.to_dict(), [])
        recs2 = [json.loads(x) for x in second.read_text(encoding="utf-8").splitlines()]
        tr2 = [r for r in recs2 if r["type"] == "my_hp_trace"]
        assert len(tr2) == 1 and tr2[0]["reason"] == "battle_end" and tr2[0]["battle_seq"] == 3, tr2
        third = lg._file
        assert third is None or not any(json.loads(x)["type"] == "my_hp_trace"
                                        for x in third.read_text(encoding="utf-8").splitlines())
        # 終わりを出した後の reset_battle は出さない
        st3 = BattleStateV2()
        st3.record_my_hp_trace(_trace_row("commit"))
        st3.my_hp_trace_end_done = True
        st3.my_hp_trace_outbox = []
        st3.reset_battle()
        assert st3.my_hp_trace_outbox == []
    finally:
        shutil.rmtree(tmp)
    print("test_trace_dump_rules_and_logger_rows OK")


def test_pipeline_dumps_trace_at_battle_end():
    """pipeline は勝敗 (または終了の確定) を見たフレームで 1 回だけ終わりの分を出し、次のフレームで outbox を空にする"""
    from vision.pipeline import VisionPipeline
    pl = VisionPipeline()
    pl.state.record_my_hp_trace(_trace_row("fraction_unparsable"))
    pl.state.outcome = "win"
    img = np.zeros((720, 1280, 3), dtype=np.uint8)
    d, _f = pl.process(img)
    assert [x["reason"] for x in d["my_hp_trace_dumps"]] == ["battle_end"], d["my_hp_trace_dumps"]
    d, _f = pl.process(img)
    assert d["my_hp_trace_dumps"] == []
    print("test_pipeline_dumps_trace_at_battle_end OK")


def test_scene_row_hp_read_ts():
    """scene 行の state の個体 (両側) に hp_read_ts が載る。助言の行の state と digest は変わらない"""
    from battle_logger import _compact_state, scene_row_state, state_digest
    st, me = _state()
    before = st.to_dict()
    me.hp_read_ts = 1791503228.5
    d = st.to_dict()
    row = scene_row_state(d)
    assert row["player"]["party"][0]["hp_read_ts"] == 1791503228.5
    assert row["opponent"]["party"][0]["hp_read_ts"] is None
    assert "hp_read_ts" not in _compact_state(d)["player"]["party"][0]
    assert state_digest(_compact_state(d)) == state_digest(_compact_state(before))
    assert "watch_opp_rows" not in row   # scene が watch のときだけ
    print("test_scene_row_hp_read_ts OK")


# ------------------------------------------------------------------ 段 5
def _watch_state():
    st = BattleStateV2()
    st.battle_active = True
    st.scene = "watch"
    for ja, sid, hp in (("マニューラ", "weavile", 39.0), ("アシレーヌ", "primarina", 100.0)):
        m = PokemonState(species_ja=ja, species_id=sid, display_name=ja)
        m.hp_percent = hp
        st.opponent.party.append(m)
    st.opponent.active_index = 0
    return st


def _run_watch_right(st, texts: dict, hits: dict):
    """様子見画面の側柱の抽出を、右列の % の文字 (行 → 文字) と色照合の結果 (行 → (id, ja, score)) をモックして 1 回実行する"""
    from vision import extractors, spriteid
    rows = {id(z["hp_text"]): i for i, z in enumerate(zones.WATCH_OPP)}
    panels = {id(z["panel"]): i for i, z in enumerate(zones.WATCH_OPP)}
    orig = ocr.read_zone_text, spriteid.identify_species_color, extractors._reread_watch_opp_types, extractors.crop
    crops = []

    def fake_crop(img, zone):
        crops.append(panels.get(id(zone)))
        return zone

    def fake_ident(zone, cands):
        return hits.get(panels.get(id(zone)))

    ocr.read_zone_text = lambda _img, zone, **kw: texts.get(rows.get(id(zone)), "")
    spriteid.identify_species_color = fake_ident
    extractors._reread_watch_opp_types = lambda img, state: None
    extractors.crop = fake_crop
    try:
        extractors.extract_watch_side_columns(np.zeros((1080, 1920, 3), dtype=np.uint8), st, None)
    finally:
        ocr.read_zone_text, spriteid.identify_species_color, extractors._reread_watch_opp_types, extractors.crop = orig


def test_watch_right_rows_recorded():
    """右列の行ごとの読み (行, % の文字, 同定した種族, 方式, スコア, 書いたか) が状態と scene 行 (watch のとき) に載る"""
    from battle_logger import scene_row_state
    st = _watch_state()
    _run_watch_right(st, {0: "39%", 1: "100%", 2: "5"}, {0: ("weavile", "マニューラ", 0.61), 1: ("primarina", "アシレーヌ", 0.55)})
    rows = st.watch_opp_rows
    assert [(r["row"], r["hp_text"], r["pct"], r["species"], r["method"], r["score"], r["written"]) for r in rows] == [
        (0, "39%", 39, "weavile", "color", 0.61, "written"),
        (1, "100%", 100, "primarina", "color", 0.55, "written"),
        (2, "5", 5, None, "color", None, "no_match")], rows
    assert rows[0]["slot"] == 0 and rows[1]["slot"] == 1
    d = st.to_dict()
    row = scene_row_state(d)
    assert row["watch_opp_rows"] == rows
    json.dumps(row)
    # 次のフレームで読みが無ければ空になる (最新のフレームの分だけ)
    _run_watch_right(st, {}, {})
    assert st.watch_opp_rows == []
    print("test_watch_right_rows_recorded OK")


def test_watch_right_big_increase_rejected():
    """10/7 18:30 の形: マニューラ 39% の行が 100% と読まれて (同定の取り違えか OCR かは問わず) +60 超の増加 → 書かずに
    hp_reject に watch_right_big_increase で残す。交代の文言があれば従来どおり書く"""
    from vision.extractors import watch_right_big_increase
    assert WATCH_OPP_BIG_INCREASE == 60.0
    assert watch_right_big_increase(39.0, 100.0, False) and not watch_right_big_increase(39.0, 99.0, False)
    assert not watch_right_big_increase(39.0, 100.0, True) and not watch_right_big_increase(None, 100.0, False)
    saved = _saved_counts()
    try:
        st = _watch_state()
        weav = st.opponent.party[0]
        _run_watch_right(st, {1: "100%"}, {1: ("weavile", "マニューラ", 0.52)})
        assert weav.hp_percent == 39.0
        rej = st.hp_reject["opponent"]
        assert rej["reason"] == "watch_right_big_increase" and rej["hp_candidate"] == 100.0 and rej["hp_before"] == 39.0, rej
        assert (rej["row"], rej["species"], rej["method"], rej["score"], rej["slot"]) == (1, "weavile", "color", 0.52, 0)
        assert st.watch_opp_rows[0]["written"] == "watch_right_big_increase"
        # 交代の文言 (マニューラを繰り出した) の後なら書く
        st.log_event("message", "相手は マニューラを 繰り出した!", event_id="switch_opponent")
        _run_watch_right(st, {1: "100%"}, {1: ("weavile", "マニューラ", 0.52)})
        assert weav.hp_percent == 100.0 and st.watch_opp_rows[0]["written"] == "written"
    finally:
        _restore_counts(saved)
    print("test_watch_right_big_increase_rejected OK")


if __name__ == "__main__":
    t0 = time.time()
    test_field_reads_my_hp_without_opp_banner()
    test_field_passes_4of159_to_set_hp()
    test_field_skips_without_my_hud()
    test_split_guess_does_not_decide_max_hp()
    test_set_hp_split_guess_rules()
    test_opp_track_zone_matches_text()
    test_opp_bar_substitute_rule_unchanged()
    test_field_opp_side_uses_track_zone()
    test_hp_settle_step_pure()
    test_my_hud_trace_rows()
    test_trace_dump_rules_and_logger_rows()
    test_pipeline_dumps_trace_at_battle_end()
    test_scene_row_hp_read_ts()
    test_watch_right_rows_recorded()
    test_watch_right_big_increase_rejected()
    print(f"\nALL OK ({time.time() - t0:.1f}s)")
