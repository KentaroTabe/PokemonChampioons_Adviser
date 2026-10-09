"""相手枠のタイプアイコンの読み直しと種族推定 (2026-10-09 fix/type-recognition、KNOWN_ISSUES A3 の 10/9 の 2 行) のテスト。

10/9 08:42 の接続テスト 2 戦:
- 1 戦目: 相手 1 枠目 イエッサン [エスパー/ノーマル] の 1 つ目の欄がノーマルと読まれ [ノーマル, ノーマル] → メタモン と推定。
  場に出たイエッサンはタイプが完全一致する枠が無く対応待ちのまま終了 (後のフレームと様子を見る画面ではエスパーと読めていた)
- 2 戦目: サーナイト [エスパー/フェアリー] を [ノーマル/フェアリー] と読み、候補 プクリン だけ (事前確率 1.0) で照合なしに採用。
  カイリュー は実キャプチャのある ボーマンダ と誤認 (照合の式の水準が違う)

- 段 1: 1 つ目の欄のエスパーのテンプレート (保存フレームの原寸の切り出し)、同タイプ 2 つの読みは採用しない、読みの安定判定と
  訂正、訂正で推定を取り消して再計算、事前確率だけの採用はタイプが安定した枠に限る、対戦ログの roster_change の欄
- 段 2: 様子を見る画面の相手の列のタイプで訂正 → 対応待ちの自動解消 (完全一致・メガの前後)、部分一致は候補として出し
  人が枠を指定する
- 段 3: 照合の方式 (実キャプチャ / 図鑑画像) が混在する候補では点数で採用しない
- link_active_to_party が HP の推定の印も一緒に移す

    python -m tests.test_type_recognition
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from unittest import mock

import cv2
import numpy as np

from battle_logger import BattleLogger
from champions_agent.config import PARTY_SIZE, TYPE_ICON_EMPTY_STD_MAX, TYPE_READ_STABLE_FRAMES
from vision import extractors, zones
from vision import type_reading as TR
from vision.state import BattleStateV2, PokemonState, mega_family, mega_family_of
from vision.zones import crop

FIX = Path(__file__).resolve().parent / "fixtures" / "type_recognition"
MANIFEST = json.loads((FIX / "manifest.json").read_text(encoding="utf-8"))


def _frame(*names) -> np.ndarray:
    """fixture の原寸の切り出しを manifest の offset で黒い画像に貼り戻す"""
    w, h = MANIFEST[names[0]]["frame_size"]
    img = np.zeros((h, w, 3), np.uint8)
    for n in names:
        c = cv2.imread(str(FIX / n))
        ox, oy = MANIFEST[n]["offset"]
        img[oy:oy + c.shape[0], ox:ox + c.shape[1]] = c
    return img


def _sel_reading(img, slot):
    z = zones.SELECTION_OPP[slot]
    return TR.normalize_reading([extractors._type_cell(img, z["type1"]), extractors._type_cell(img, z["type2"])],
                                TR.SINGLE_COL_SELECTION)


def _watch_cells(img, row):
    z = zones.WATCH_OPP[row]
    (s1, l1), (s2, l2) = extractors._watch_type_cell(img, z["type1"]), extractors._watch_type_cell(img, z["type2"])
    return [s1, s2], [l1, l2]


def _watch_reading(img, row):
    """枠のタイプの訂正に使う読み (strict の欄だけ)"""
    return TR.normalize_reading(_watch_cells(img, row)[0], TR.SINGLE_COL_WATCH)


class FakeInference:
    """タイプ → 候補 (種族 id, 事前確率, 日本語名)。実際の使用率 DB に依らない"""
    TABLE = {frozenset({"エスパー", "ノーマル"}): [("indeedee", 1.0, "イエッサン")],
             frozenset({"ノーマル"}): [("ditto", 1.0, "メタモン")],
             frozenset({"ノーマル", "フェアリー"}): [("wigglytuff", 1.0, "プクリン")],
             frozenset({"エスパー", "フェアリー"}): [("gardevoir", 0.62, "サーナイト"), ("hatterene", 0.30, "ブリムオン")]}

    def candidates(self, types, top_k=5):
        return list(self.TABLE.get(frozenset(types), []))


def _records(path: Path) -> list:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _log(lg, st, scene, fired=()):
    st.scene = scene
    lg.on_frame(st.to_dict(), list(fired))


# ------------------------------------------------------------------ 純粋関数
def test_normalize_reading():
    ok, emp, bad = TR.CELL_OK, (TR.CELL_EMPTY, None), (TR.CELL_UNREADABLE, None)
    S, W = TR.SINGLE_COL_SELECTION, TR.SINGLE_COL_WATCH
    assert TR.normalize_reading([(ok, "エスパー"), (ok, "ノーマル")], S) == (TR.READ_OK, ["エスパー", "ノーマル"])
    # 同じタイプが 2 つ並ぶ読みは不整合 (重複を消して単タイプにしない)
    assert TR.normalize_reading([(ok, "ノーマル"), (ok, "ノーマル")], S) == (TR.READ_INCONSISTENT, ["ノーマル", "ノーマル"])
    # 単タイプ: 選出画面は 2 つ目の欄、様子を見る画面は 1 つ目の欄。逆の欄だけ読めたら取りこぼし (保留)
    assert TR.normalize_reading([emp, (ok, "みず")], S) == (TR.READ_OK, ["みず"])
    assert TR.normalize_reading([(ok, "みず"), emp], S) == (TR.READ_INCOMPLETE, None)
    assert TR.normalize_reading([(ok, "みず"), emp], W) == (TR.READ_OK, ["みず"])
    assert TR.normalize_reading([emp, (ok, "みず")], W) == (TR.READ_INCOMPLETE, None)
    # アイコンはあるが読めない欄があれば保留 (単タイプと取り違えない)
    assert TR.normalize_reading([(ok, "ドラゴン"), bad], W) == (TR.READ_INCOMPLETE, None)
    assert TR.normalize_reading([emp, emp], S) == (TR.READ_NONE, None)
    # 欄の状態: 読めた / 空 / 読めない (濃淡のばらつきで空と区別)
    assert TR.cell_of("みず", 70.0, TYPE_ICON_EMPTY_STD_MAX) == (TR.CELL_OK, "みず")
    assert TR.cell_of(None, 4.0, TYPE_ICON_EMPTY_STD_MAX) == (TR.CELL_EMPTY, None)
    assert TR.cell_of(None, None, TYPE_ICON_EMPTY_STD_MAX) == (TR.CELL_EMPTY, None)
    assert TR.cell_of(None, 36.5, TYPE_ICON_EMPTY_STD_MAX) == (TR.CELL_UNREADABLE, None)
    print("test_normalize_reading OK")


def test_streak_and_action():
    n = TYPE_READ_STABLE_FRAMES
    assert n == 3
    st = None
    st = TR.update_streak(st, TR.READ_OK, ["ノーマル", "フェアリー"])
    # タイプの無い枠には 1 回目の読みを仮に入れる (安定はまだ)
    assert TR.type_reread_action([], False, st, n, False) == TR.ACTION_ADOPT and not TR.is_stable(st, n)
    st = TR.update_streak(st, TR.READ_NONE, None)          # 空の読み (切り替わり) は連続を保つ
    st = TR.update_streak(st, TR.READ_OK, ["フェアリー", "ノーマル"])   # 並び順は問わない
    assert st["n"] == 2 and TR.type_reread_action(["ノーマル", "フェアリー"], False, st, n, False) is None
    st = TR.update_streak(st, TR.READ_OK, ["ノーマル", "フェアリー"])
    assert TR.type_reread_action(["ノーマル", "フェアリー"], False, st, n, False) == TR.ACTION_CONFIRM
    assert TR.type_reread_action(["ノーマル", "フェアリー"], True, st, n, False) is None
    # 違う読みは n 回続くまで訂正しない。不整合の読みは連続を切る
    st = TR.update_streak(st, TR.READ_OK, ["エスパー", "フェアリー"])
    st = TR.update_streak(st, TR.READ_OK, ["エスパー", "フェアリー"])
    assert TR.type_reread_action(["ノーマル", "フェアリー"], True, st, n, False) is None
    st2 = TR.update_streak(st, TR.READ_INCONSISTENT, ["ノーマル", "ノーマル"])
    assert st2["cand"] is None and TR.type_reread_action(["ノーマル", "フェアリー"], True, st2, n, False) is None
    st = TR.update_streak(st, TR.READ_OK, ["エスパー", "フェアリー"])
    assert TR.type_reread_action(["ノーマル", "フェアリー"], True, st, n, False) == TR.ACTION_CORRECT
    # 種が確定済み (手動確定・場で確認) の枠は読み直さない
    assert TR.type_reread_action(["ノーマル", "フェアリー"], True, st, n, True) is None
    p = PokemonState(types=["ノーマル"])
    assert not TR.species_locked(p)
    p.merge_species("メタモン", "ditto", guess=True, score=1.0)
    assert not TR.species_locked(p)
    p.merge_species("メタモン", "ditto")
    assert TR.species_locked(p)
    print("test_streak_and_action OK")


def test_partial_match_and_order_check():
    pend = [{"エスパー", "フェアリー"}]
    slots = [(0, ["みず"]), (2, ["はがね", "ドラゴン"]), (5, ["ノーマル", "フェアリー"])]
    assert TR.partial_match_slots(pend, slots) == [5]
    assert TR.partial_match_slots(pend, slots + [(1, ["はがね", "エスパー"])]) == [5, 1]
    assert TR.partial_match_slots(pend, [(5, ["エスパー", "フェアリー"])]) == []    # 完全一致は自動の規則の範囲
    fam = {"gardevoirmega": [["エスパー", "フェアリー"]], "charizard": [["ほのお", "ひこう"], ["ほのお", "ドラゴン"]]}
    assert TR.watch_rows_agree([(["ほのお", "ドラゴン"], "charizard"), (["みず"], None)], fam.get)
    assert not TR.watch_rows_agree([(["みず", "フェアリー"], "gardevoirmega")], fam.get)
    assert TR.watch_rows_agree([(None, "gardevoirmega")], fam.get)
    ids = {"charizard", "charizardmegax", "charizardmegay", "gardevoir", "gardevoirmega", "meganium", "yanmega"}
    assert mega_family_of("gardevoirmega", ids) == ["gardevoirmega", "gardevoir"]
    assert mega_family_of("charizard", ids) == ["charizard", "charizardmegax", "charizardmegay"]
    assert mega_family_of("charizardmegax", ids) == ["charizardmegax", "charizard", "charizardmegay"]
    assert mega_family_of("meganium", ids) == ["meganium"] and mega_family_of("yanmega", ids) == ["yanmega"]
    assert "gardevoirmega" in mega_family("gardevoir")   # 実際の図鑑
    print("test_partial_match_and_order_check OK")


# ------------------------------------------------------------------ 段 1: 保存フレームの読み
def test_first_column_psychic_on_saved_frames():
    """10/9 の 2 戦の誤読した選出画面のフレーム (テンプレートの出所とは別のフレーム) で、1 つ目の欄のエスパーが読める"""
    img = _frame("sel_502960_slot0.png")
    d = extractors.classify_type_icon_detail(crop(img, zones.SELECTION_OPP[0]["type1"]))
    assert d["type"] == "エスパー" and d["via"] == "shape" and d["hash_dist"] <= 60, d
    assert _sel_reading(img, 0) == (TR.READ_OK, ["エスパー", "ノーマル"])
    img = _frame("sel_503272_slot5.png")
    assert _sel_reading(img, 5) == (TR.READ_OK, ["エスパー", "フェアリー"])
    print("test_first_column_psychic_on_saved_frames OK")


def test_watch_column_readings():
    """様子を見る画面の相手の列: 6 行のタイプ (単タイプは 1 つ目の欄)。訂正には形状の照合が確かな欄だけを使い、確かさの足りない
    読み (カーソルの行の ノーマル 距離 78、行 5 の エスパー 61) は保留。弱い読みが対応待ちの個体のタイプと一致するかは候補にだけ使う。
    カーソルの行で取りこぼした欄は単タイプにしない"""
    img = _frame("watch_503118_col.png")
    truth = [None, ["かくとう", "どく"], ["みず"], ["ドラゴン", "ひこう"], ["はがね", "ドラゴン"], None]
    got = [_watch_reading(img, r) for r in range(6)]
    assert got == [(TR.READ_OK, t) if t else (TR.READ_INCOMPLETE, None) for t in truth], got
    sc, lc = _watch_cells(img, 0)
    assert TR.watch_loose_matches(sc, lc, [{"エスパー", "ノーマル"}])
    assert not TR.watch_loose_matches(sc, lc, [{"ノーマル", "フェアリー"}])
    sc1, lc1 = _watch_cells(img, 1)
    assert not TR.watch_loose_matches(sc1, lc1, [{"かくとう", "どく"}])    # 確かな読みで決まる行は候補の対象外
    img = _frame("watch_503393_rows.png")
    assert _watch_reading(img, 5) == (TR.READ_OK, ["エスパー", "フェアリー"])
    assert _watch_reading(img, 4) == (TR.READ_OK, ["ほのお", "ひこう"])
    v3 = _watch_reading(img, 3)
    assert v3 in ((TR.READ_OK, ["ドラゴン", "ひこう"]), (TR.READ_INCOMPLETE, None)), v3   # [ドラゴン] だけにはならない
    print("test_watch_column_readings OK")


# ------------------------------------------------------------------ 段 1: 選出画面の流れ
def _run_selection(state, img, n, classify=None):
    patches = [mock.patch.object(extractors.ocr, "read_zone_text", return_value=""),
               mock.patch.object(extractors, "_is_picked_panel", return_value=None),
               mock.patch("advisor.infer.get_inference", return_value=FakeInference())]
    if classify is not None:
        patches.append(mock.patch.object(extractors, "classify_type_icon", side_effect=classify))
    for p in patches:
        p.start()
    try:
        for _ in range(n):
            extractors.extract_selection(img, state, None)
    finally:
        for p in patches:
            p.stop()


def test_duplicate_type_reading_not_adopted():
    """[ノーマル, ノーマル] (1 つ目の欄のエスパーの誤読) は採用しない。不整合のイベントは読みごとに 1 回"""
    st = BattleStateV2()
    img = _frame("sel_502960_slot0.png")
    fake = lambda c: "ノーマル" if c is not None and c.std() > 20 else None   # 枠 0 の 2 欄だけアイコンがある
    _run_selection(st, img, 3, classify=fake)
    slot = st.opponent.party[0]
    assert slot.types == [] and slot.species_ja is None, slot
    ev = [e for e in st.events if e.get("event") == "type_inconsistent"]
    assert len(ev) == 1 and ev[0]["detail"]["types"] == ["ノーマル", "ノーマル"], ev
    # 以後の正しい読み (実際の画像) で入る
    _run_selection(st, img, 1)
    assert slot.types == ["エスパー", "ノーマル"] and not slot._types_stable
    print("test_duplicate_type_reading_not_adopted OK")


def test_prior_only_accept_waits_for_stable_types():
    """2 戦目: 候補が プクリン だけ (事前確率 1.0) でも、タイプが安定するまでは採用しない (候補は出す)"""
    st = BattleStateV2()
    img = _frame("sel_503272_slot5.png")
    # 枠 5 の 1 つ目の欄だけ ノーマル と誤読させる (10/9 の再現)。2 つ目の欄は本物の分類
    from vision.typeicons import classify_type_icon as real
    z = zones.SELECTION_OPP[5]
    t1_box = crop(img, z["type1"])

    def classify(c):
        if c is not None and c.shape == t1_box.shape and np.array_equal(c, t1_box):
            return "ノーマル"
        return real(c)

    _run_selection(st, img, 1, classify=classify)
    slot = st.opponent.party[5]
    assert slot.types == ["ノーマル", "フェアリー"] and slot.species_ja is None, slot
    held = [e for e in st.events if e.get("event") == "species_guess_held"]
    assert held and held[0]["detail"]["reason"] == "prior_needs_stable_types", held
    _run_selection(st, img, TYPE_READ_STABLE_FRAMES - 1, classify=classify)
    assert slot._types_stable and slot.species_id == "wigglytuff" and slot.species_guess   # 安定後は従来どおり採用
    # 以後の正しい読み (本物の分類) が 3 フレーム続くと訂正し、プクリンの推定を取り消す
    _run_selection(st, img, TYPE_READ_STABLE_FRAMES - 1)
    assert slot.species_id == "wigglytuff"
    _run_selection(st, img, 1)
    assert slot.types == ["エスパー", "フェアリー"] and slot.species_ja is None and not slot.species_guess, slot
    ev = [e for e in st.events if e.get("event") == "type_reread"]
    assert len(ev) == 1 and ev[0]["detail"]["types_from"] == ["ノーマル", "フェアリー"] \
        and ev[0]["detail"]["cancelled"] == "wigglytuff" and ev[0]["detail"]["source"] == "selection", ev
    print("test_prior_only_accept_waits_for_stable_types OK")


def test_selection_reread_cancels_dependent_guess():
    """1 戦目: 旧版で入った [ノーマル] + メタモン の推定の枠を、選出画面の読み直し (3 フレーム) で エスパー/ノーマル に訂正し、
    メタモンを取り消して イエッサン を推定し直す。手動確定した枠は読み直さない"""
    st = BattleStateV2()
    st.opponent.party = [PokemonState(types=["ノーマル"]) for _ in range(PARTY_SIZE)]
    st.opponent.party[0].merge_species("メタモン", "ditto", guess=True, score=1.0)
    img = _frame("sel_502960_slot0.png")
    _run_selection(st, img, TYPE_READ_STABLE_FRAMES - 1)
    s0 = st.opponent.party[0]
    assert s0.species_id == "ditto" and s0.types == ["ノーマル"]          # まだ訂正しない
    _run_selection(st, img, 1)
    assert s0.types == ["エスパー", "ノーマル"] and s0.species_id == "indeedee" and s0.species_guess, s0
    ev = [e["detail"] for e in st.events if e.get("event") == "type_reread"]
    assert ev == [{"slot": 0, "types_from": ["ノーマル"], "types_to": ["エスパー", "ノーマル"], "cancelled": "ditto",
                   "cancelled_ja": "メタモン", "source": "selection", "frames": 3}], ev
    # 手動確定した種の枠は、違う読みが続いても触らない
    st2 = BattleStateV2()
    st2.opponent.party = [PokemonState(types=["ノーマル"]) for _ in range(PARTY_SIZE)]
    st2.opponent.party[0].merge_species("メタモン", "ditto")
    _run_selection(st2, img, 4)
    assert st2.opponent.party[0].species_id == "ditto" and st2.opponent.party[0].types == ["ノーマル"]
    assert not any(e.get("event") == "type_reread" for e in st2.events)
    print("test_selection_reread_cancels_dependent_guess OK")


# ------------------------------------------------------------------ 段 2: 様子を見る画面 → 対応待ちの解消
def _game1_state():
    """10/9 1 戦目 (ミラー戦): 旧版の選出画面の読みで枠 0 が [ノーマル] + メタモン、場に出たイエッサンが対応待ち"""
    st = BattleStateV2()
    rows = [("メタモン", "ditto", ["ノーマル"]), ("オオニューラ", "sneasler", ["かくとう", "どく"]),
            ("カメックス", "blastoise", ["みず"]), ("フライゴン", "flygon", ["じめん", "ドラゴン"]),
            (None, None, ["はがね", "ドラゴン"]), ("グレンアルマ", "armarouge", ["ほのお", "エスパー"])]
    for ja, sid, types in rows:
        m = PokemonState(types=list(types))
        if ja:
            m.merge_species(ja, sid, guess=True, score=0.9)
        st.opponent.party.append(m)
    st.player.party = [PokemonState(species_ja="イエッサン", species_id="indeedee")]
    st.player.active_index = 0
    mon = st.opponent.switch_to_species("イエッサン", "indeedee", now=100.0)
    assert mon.pending and st.opponent.active_index == PARTY_SIZE
    mon.hp_percent, mon.revealed_moves = 45.0, ["サイコキネシス"]
    return st


def test_game1_selection_reread_resolves_pending():
    """1 戦目 (a): 選出画面の読み直し (足したテンプレートで 1 つ目の欄のエスパーが読める) で枠 0 を [ノーマル] → [エスパー/ノーマル] に
    訂正し、メタモンの推定を取り消す → 場のイエッサン (対応待ち) が枠 0 に解消し、HP・技が移る"""
    tmp = Path(tempfile.mkdtemp())
    try:
        st = _game1_state()
        lg = BattleLogger(log_dir=tmp, guess_prob_fn=lambda types, sid: 1.0)
        _log(lg, st, "command")
        f = lg._file
        img = _frame("sel_502960_slot0.png")
        _run_selection(st, img, TYPE_READ_STABLE_FRAMES - 1)
        assert st.opponent.party[0].species_id == "ditto" and len(st.opponent.party) == PARTY_SIZE + 1
        _run_selection(st, img, 1)
        _log(lg, st, "selection")
        s0 = st.opponent.party[0]
        assert s0.types == ["エスパー", "ノーマル"] and s0._types_stable
        res = st.resolve_pending("opponent")          # link_active_to_party が毎フレーム呼ぶ
        assert len(res) == 1 and res[0]["merged_to"] == 0, res
        opp = st.opponent
        assert len(opp.party) == PARTY_SIZE and opp.active_index == 0, [q.species_ja for q in opp.party]
        assert s0.species_id == "indeedee" and not s0.species_guess and not s0.pending
        assert set(s0.types) == {"エスパー", "ノーマル"} and s0.hp_percent == 45.0 and s0.revealed_moves == ["サイコキネシス"]
        assert [q.species_id for q in opp.party[1:3]] == ["sneasler", "blastoise"] and opp.party[5].species_id == "armarouge"
        _log(lg, st, "command")
        lg._finalize("win")
        rc = [r for r in _records(f) if r["type"] == "roster_change"]
        r0 = next(r for r in rc if r.get("reason") == "type_reread" and r["slot"] == 0)
        assert r0["types_from"] == ["ノーマル"] and r0["types_to"] == ["エスパー", "ノーマル"] and r0["type_source"] == "selection"
        assert r0["from"] == "ditto" and r0["from_guess"] is True, r0
        merged = [r for r in rc if r.get("merge_kind") == "pending"]
        assert len(merged) == 1 and merged[0]["merged_to"] == 0 and merged[0]["moved"]["hp"] == 45.0, rc
        assert not any(r.get("unresolved") for r in rc)
        print("test_game1_selection_reread_resolves_pending OK")
    finally:
        shutil.rmtree(tmp)


def test_game1_watch_weak_reading_gives_hint_only():
    """1 戦目 (b): 選出画面の訂正が無いまま様子を見る画面へ。カーソルの行 (枠 0) の ノーマル は弱い読み (距離 78) なので、
    イエッサンのタイプと一致しても枠 0 を自動では訂正しない。枠 0 は対応待ちの個体の候補 (watch_loose) に出て、人が枠を指定して
    解消する。確かな読みの行 (フライゴンの枠 [じめん/ドラゴン] → [ドラゴン/ひこう]) は従来どおり訂正する"""
    from advisor import engine
    tmp = Path(tempfile.mkdtemp())
    try:
        st = _game1_state()
        lg = BattleLogger(log_dir=tmp, guess_prob_fn=lambda types, sid: 1.0)
        _log(lg, st, "command")
        f = lg._file
        img = _frame("watch_503118_col.png")
        with mock.patch("advisor.infer.get_inference", return_value=FakeInference()):
            for _ in range(TYPE_READ_STABLE_FRAMES + 2):
                extractors._reread_watch_opp_types(img, st)
                _log(lg, st, "watch")
        opp = st.opponent
        s0 = opp.party[0]
        # 弱い読みでは自動訂正しない
        assert s0.types == ["ノーマル"] and s0.species_id == "ditto" and s0.species_guess, s0
        assert not any(e.get("event") == "type_reread" and e["detail"]["slot"] == 0 for e in st.events)
        assert len(opp.party) == PARTY_SIZE + 1 and opp.party[PARTY_SIZE].pending
        assert opp.party[3].types == ["ドラゴン", "ひこう"] and opp.party[3].species_id is None
        # 候補として出る (助言の注記と画面)
        hint = opp.pending_hint(opp.party[PARTY_SIZE])
        assert hint == [{"slot": 0, "ja": "メタモン", "types": ["ノーマル"], "source": "watch_loose"}], hint
        d = st.to_dict()
        assert d["opponent"]["party"][PARTY_SIZE]["pending_hint"] == hint
        note = engine.opp_pending_note(d["opponent"])
        assert "候補: 枠 1 (メタモン、様子見画面の弱い読み)" in note, note
        assert sum(1 for e in st.events if e.get("event") == "pending_hint_watch_loose") == 1   # 同じ候補は 1 回だけ記録
        # 人が枠 1 (番号 0) を指定して確定
        res = st.assign_pending("opponent", PARTY_SIZE, 0)
        assert res["ok"], res
        assert len(opp.party) == PARTY_SIZE and opp.active_index == 0
        s0 = opp.party[0]
        assert s0.species_id == "indeedee" and not s0.species_guess and s0.hp_percent == 45.0
        assert s0.revealed_moves == ["サイコキネシス"] and set(s0.types) == {"エスパー", "ノーマル"}
        _log(lg, st, "command")
        lg._finalize("win")
        rc = [r for r in _records(f) if r["type"] == "roster_change"]
        assert not any(r.get("reason") == "type_reread" and r["slot"] == 0 for r in rc), rc
        assert any(r.get("reason") == "type_reread" and r["slot"] == 3 and r["type_source"] == "watch" for r in rc), rc
        m = [r for r in rc if r.get("merge_kind") == "pending"]
        assert len(m) == 1 and m[0]["merged_to"] == 0 and m[0]["basis"] == "manual" and m[0]["from"] == "ditto", m
        print("test_game1_watch_weak_reading_gives_hint_only OK")
    finally:
        shutil.rmtree(tmp)


def test_game2_watch_reread_merges_mega_gardevoir():
    """2 戦目: プクリンの推定の枠 [ノーマル/フェアリー] → 様子を見る画面で [エスパー/フェアリー] に訂正 → 対応待ちの
    メガサーナイト (gardevoirmega) が同じ個体として枠 5 に入る (種族 id はメガのまま)"""
    st = BattleStateV2()
    rows = [("ミロカロス", "milotic", ["みず"]), ("マスカーニャ", "meowscarada", ["くさ", "あく"]),
            ("メタグロス", "metagross", ["はがね", "エスパー"]), ("ボーマンダ", "salamence", ["ドラゴン", "ひこう"]),
            ("リザードン", "charizard", ["ほのお", "ひこう"]), ("プクリン", "wigglytuff", ["ノーマル", "フェアリー"])]
    for ja, sid, types in rows:
        m = PokemonState(types=list(types))
        m.merge_species(ja, sid, guess=True, score=1.0)
        st.opponent.party.append(m)
    mon = st.opponent.switch_to_species("サーナイト", "gardevoir", now=200.0)
    assert mon.pending
    # 部分一致 (フェアリー / エスパー) の枠が 2 つ → 候補は出さない、自動でも統合しない
    assert st.opponent.pending_hint(mon) == []
    mon.species_id, mon.is_mega, mon.hp_percent = "gardevoirmega", True, 100.0
    img = _frame("watch_503393_rows.png")
    with mock.patch("advisor.infer.get_inference", return_value=FakeInference()):
        for _ in range(TYPE_READ_STABLE_FRAMES):
            extractors._reread_watch_opp_types(img, st)
    opp = st.opponent
    assert len(opp.party) == PARTY_SIZE and opp.active_index == 5, [q.species_ja for q in opp.party]
    s5 = opp.party[5]
    assert s5.species_id == "gardevoirmega" and s5.species_ja == "サーナイト" and s5.is_mega and s5.hp_percent == 100.0
    assert set(s5.types) == {"エスパー", "フェアリー"} and not s5.species_guess
    # 推定の ボーマンダ の枠 [ドラゴン/ひこう] はカーソルの行 (読みが保留か一致) なので変わらない
    assert opp.party[3].species_id == "salamence"
    print("test_game2_watch_reread_merges_mega_gardevoir OK")


def test_mega_form_matches_pre_mega_slot_types():
    """対応待ちのメガリザードンX [ほのお/ドラゴン] は、選出画面のタイプ (メガ前 [ほのお/ひこう]) の枠に入る。種族 id はメガのまま"""
    st = BattleStateV2()
    for types in (["みず"], ["くさ", "あく"], ["ほのお", "ひこう"], ["はがね"], ["でんき"], ["フェアリー"]):
        st.opponent.party.append(PokemonState(types=types))
    st.opponent.party[0].hp_percent = 50.0    # 種のない枠でも観測があれば別の個体 (未特定枠の規則に当たらないように全部タイプあり)
    mon = st.opponent.switch_to_species("メガリザードンX", "charizardmegax", now=1.0)
    mon.hp_percent = 70.0
    # 初登場の対応先の決定 (switch_to_species) は従来どおり形態のタイプだけで照合する → 対応待ち。
    # 対応待ちの解消 (resolve_pending、link_active_to_party が毎フレーム呼ぶ) がメガの前後を照合条件に含める
    assert mon.pending
    res = st.resolve_pending("opponent")
    assert len(res) == 1 and res[0]["merged_to"] == 2 and res[0]["basis"] == "rule", res
    assert st.opponent.active_index == 2 and len(st.opponent.party) == PARTY_SIZE
    assert st.opponent.party[2].species_id == "charizardmegax" and st.opponent.party[2].hp_percent == 70.0
    print("test_mega_form_matches_pre_mega_slot_types OK")


def test_partial_match_hint_and_manual_assign():
    """タイプが 1 個だけ重なる枠が 1 つしか無い: 自動では統合せず、候補として助言の注記と画面に出す → 人が枠を指定して確定"""
    from advisor import engine
    tmp = Path(tempfile.mkdtemp())
    try:
        st = BattleStateV2()
        rows = [("ミロカロス", "milotic", ["みず"]), ("マスカーニャ", "meowscarada", ["くさ", "あく"]),
                ("ドドゲザン", "kingambit", ["あく", "はがね"]), ("ボーマンダ", "salamence", ["ドラゴン", "ひこう"]),
                ("リザードン", "charizard", ["ほのお", "ひこう"]), ("プクリン", "wigglytuff", ["ノーマル", "フェアリー"])]
        for ja, sid, types in rows:
            m = PokemonState(types=list(types))
            m.merge_species(ja, sid, guess=True, score=1.0)
            st.opponent.party.append(m)
        lg = BattleLogger(log_dir=tmp, guess_prob_fn=lambda types, sid: 1.0)
        _log(lg, st, "selection")
        f = lg._file
        mon = st.opponent.switch_to_species("サーナイト", "gardevoir", now=300.0)
        mon.hp_percent = 80.0
        assert mon.pending and st.resolve_pending("opponent") == []      # 自動では統合しない
        assert st.opponent.pending_hint(mon) == [{"slot": 5, "ja": "プクリン", "types": ["ノーマル", "フェアリー"],
                                                  "source": "partial_type"}]
        d = st.to_dict()
        assert d["opponent"]["party"][PARTY_SIZE]["pending_hint"][0]["slot"] == 5
        assert "pending_hint" not in d["opponent"]["party"][0]
        note = engine.opp_pending_note(d["opponent"])
        assert note.startswith(engine.OPP_PENDING_NOTE) and "候補: 枠 6 (プクリン)" in note, note
        _log(lg, st, "command")
        # 人が枠を指定 (確定済みの別の種の枠には入れない)
        st.opponent.party[1].merge_species("マスカーニャ", "meowscarada")
        res = st.assign_pending("opponent", PARTY_SIZE, 1)
        assert not res["ok"] and "確定済み" in res["reason"], res
        assert not st.assign_pending("opponent", 0, 5)["ok"]                 # 対応待ちの個体ではない
        res = st.assign_pending("opponent", PARTY_SIZE, 5)
        assert res["ok"] and res["record"]["basis"] == "manual", res
        opp = st.opponent
        assert len(opp.party) == PARTY_SIZE and opp.active_index == 5 and opp.party[5].species_id == "gardevoir"
        assert opp.party[5].hp_percent == 80.0 and not opp.party[5].species_guess
        assert set(opp.party[5].types) == {"エスパー", "フェアリー"}  # 図鑑のタイプ (集合由来で順不同)
        assert engine.opp_pending_note(st.to_dict()["opponent"]) is None
        _log(lg, st, "command")
        lg._finalize("win")
        rc = [r for r in _records(f) if r["type"] == "roster_change"]
        assert len(rc) == 1 and rc[0]["basis"] == "manual" and rc[0]["merged_to"] == 5 and rc[0]["from"] == "wigglytuff", rc
        print("test_partial_match_hint_and_manual_assign OK")
    finally:
        shutil.rmtree(tmp)


def test_watch_order_mismatch_skips_frame():
    """確定済みの種の行の読みがその種のタイプと合わなければ、並びが違う疑いとしてそのフレームの読みを使わない"""
    st = BattleStateV2()
    for types in (["みず"],) * PARTY_SIZE:
        st.opponent.party.append(PokemonState(types=list(types)))
    st.opponent.party[1].merge_species("カメックス", "blastoise")        # 行 1 の読みは かくとう/どく (合わない)
    img = _frame("watch_503118_col.png")
    with mock.patch("advisor.infer.get_inference", return_value=FakeInference()):
        for _ in range(TYPE_READ_STABLE_FRAMES):
            extractors._reread_watch_opp_types(img, st)
    assert all(q.types == ["みず"] for q in (q for j, q in enumerate(st.opponent.party) if j != 1)), [q.types for q in st.opponent.party]
    assert any(e.get("event") == "watch_type_order_mismatch" for e in st.events)
    print("test_watch_order_mismatch_skips_frame OK")


# ------------------------------------------------------------------ 段 3: 照合の方式が混在する候補
def _rgba_sprite(shape):
    img = np.zeros((96, 96, 4), np.uint8)
    if shape == "circle":
        cv2.circle(img, (48, 48), 34, (200, 200, 200, 255), -1)
    else:
        pts = np.array([[48, 6], [90, 90], [6, 90]], np.int32)
        cv2.fillPoly(img, [pts], (200, 200, 200, 255))
    return img


def test_mixed_template_sources_not_adopted():
    import vision.spriteid as S
    from advisor.dex import get_dex
    old = (S.TEMPLATE_DIR, S.REAL_DIR, dict(S._sprite_cache), S._real_cache)
    tmp = Path(tempfile.mkdtemp())
    try:
        dex_dir, real_dir = tmp / "dex", tmp / "real"
        dex_dir.mkdir()
        real_dir.mkdir()
        S.TEMPLATE_DIR, S.REAL_DIR = dex_dir, real_dir
        S._sprite_cache.clear()
        S._real_cache = None
        n_a, n_b = get_dex().species("dragonite")["num"], get_dex().species("salamence")["num"]
        # (1) 合成: 全候補が図鑑画像だけなら従来どおり採用、片方にだけ実キャプチャがあれば採用しない
        cv2.imwrite(str(dex_dir / f"{n_a}.png"), _rgba_sprite("circle"))
        cv2.imwrite(str(dex_dir / f"{n_b}.png"), _rgba_sprite("triangle"))
        q = np.full((100, 120, 3), (60, 20, 110), np.uint8)
        cv2.circle(q, (60, 50), 34, (200, 200, 200), -1)
        cands = [("salamence", 0.54, "ボーマンダ"), ("dragonite", 0.45, "カイリュー")]
        hit = S.identify_species(q, cands)
        assert hit and hit[0] == "dragonite", hit
        tri = np.full((100, 120, 3), (60, 20, 110), np.uint8)
        cv2.fillPoly(tri, [np.array([[60, 8], [100, 92], [20, 92]], np.int32)], (200, 200, 200))
        assert S.harvest_species_icon("salamence", tri)
        why = {}
        assert S.identify_species(q, cands, detail=why) is None and why["reason"] == "mixed_template_sources", why
        assert S.mixed_template_sources(["real", "dex"]) and not S.mixed_template_sources(["dex", "dex"])
        # (2) 実例 (10/9 sel_1791503296 の カイリュー、実キャプチャのある ボーマンダ): 採用しない
        for p in list(dex_dir.iterdir()) + list(real_dir.iterdir()):
            p.unlink()
        shutil.copy(FIX / "dex_149.png", dex_dir / f"{n_a}.png")
        shutil.copy(FIX / "dex_373.png", dex_dir / f"{n_b}.png")
        shutil.copy(FIX / "salamence_real_0.png", real_dir / "salamence_0.png")
        S._sprite_cache.clear()
        S._real_cache = None
        icon = crop(_frame("sel_503296_slot3.png"), zones.SELECTION_OPP[3]["icon"])
        why = {}
        assert S.identify_species(icon, cands, detail=why) is None and why["reason"] == "mixed_template_sources", why
        # アイコンのゾーンの右端を広げた (0.878 → 0.903) ので、図鑑画像どうしでは カイリュー が上
        (real_dir / "salamence_0.png").unlink()
        S._real_cache = None
        top = S.identify_species(icon, cands, accept=0.0, margin=0.0)
        assert top and top[0] == "dragonite", top
        # 事前確率だけの採用はタイプが安定した枠に限る
        why = {}
        assert S.identify_species(None, [("wigglytuff", 1.0, "プクリン")], allow_prior_accept=False, detail=why) is None
        assert why["reason"] == "prior_needs_stable_types"
        assert S.identify_species(None, [("wigglytuff", 1.0, "プクリン")]) == ("wigglytuff", "プクリン", 1.0)
        print("test_mixed_template_sources_not_adopted OK")
    finally:
        S.TEMPLATE_DIR, S.REAL_DIR = old[0], old[1]
        S._sprite_cache.clear()
        S._sprite_cache.update(old[2])
        S._real_cache = old[3]
        shutil.rmtree(tmp)


# ------------------------------------------------------------------ HP の推定の印 (2026-10-09 追加の依頼)
def test_link_active_moves_hp_estimate_marks():
    """場の個体 (プレースホルダ) を枠へ統合するとき、HP の値と一緒に推定の印 (hp_estimated / hp_uncertain / hp_read_ts /
    hp_source) も移す。印なしで推定値が枠に移ると実測として扱われる"""
    st = BattleStateV2()
    for types in (["みず"], ["くさ", "あく"], ["ほのお", "ひこう"]):
        st.opponent.party.append(PokemonState(types=list(types)))
    act = PokemonState(species_ja="リザードン", species_id="charizard", hp_percent=62.0, hp_estimated=True,
                       hp_uncertain=True, hp_read_ts=123.0)
    act.hp_source = "bar"
    st.opponent.party.append(act)
    st.opponent.active_index = 3
    extractors.link_active_to_party(st, "opponent")
    s = st.opponent.party[2]
    assert len(st.opponent.party) == 3 and st.opponent.active_index == 2, [q.species_ja for q in st.opponent.party]
    assert s.species_id == "charizard" and s.hp_percent == 62.0
    assert s.hp_estimated is True and s.hp_uncertain is True and s.hp_read_ts == 123.0 and s.hp_source == "bar"
    # 実測の値を移すときは印も実測 (枠に残っていた古い印を消す)
    st2 = BattleStateV2()
    st2.opponent.party = [PokemonState(types=["みず"], hp_estimated=True, hp_uncertain=True)]
    st2.opponent.party.append(PokemonState(species_ja="カメックス", species_id="blastoise", hp_percent=80.0, hp_read_ts=5.0))
    st2.opponent.active_index = 1
    extractors.link_active_to_party(st2, "opponent")
    s2 = st2.opponent.party[0]
    assert s2.hp_percent == 80.0 and s2.hp_estimated is False and s2.hp_uncertain is False and s2.hp_read_ts == 5.0
    print("test_link_active_moves_hp_estimate_marks OK")


def main() -> None:
    test_normalize_reading()
    test_streak_and_action()
    test_partial_match_and_order_check()
    test_first_column_psychic_on_saved_frames()
    test_watch_column_readings()
    test_duplicate_type_reading_not_adopted()
    test_prior_only_accept_waits_for_stable_types()
    test_selection_reread_cancels_dependent_guess()
    test_game1_selection_reread_resolves_pending()
    test_game1_watch_weak_reading_gives_hint_only()
    test_game2_watch_reread_merges_mega_gardevoir()
    test_mega_form_matches_pre_mega_slot_types()
    test_partial_match_hint_and_manual_assign()
    test_watch_order_mismatch_skips_frame()
    test_mixed_template_sources_not_adopted()
    test_link_active_moves_hp_estimate_marks()
    print("ALL OK")


if __name__ == "__main__":
    main()
