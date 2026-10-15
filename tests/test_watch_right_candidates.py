"""様子見画面の右列 (相手 6 行のアイコン + HP%) の書き込み先の絞り込み (2026-10-15、KNOWN_ISSUES A1「様子見画面の右列の色照合が
選出外の個体に当たり、別個体の HP を書き込む」)。

10/15 10:16 試験 (battle_20261015_100950 L308): 右列の行 5 (87% = メガカイリューの値) を、選出外のミミロップ (スコア 0.56、2 位 カバルドン
0.40) と同定し、HP の無かったミミロップの枠に 87 を書いた。照合の候補は相手の 6 体すべてで、カイリューの候補の見た目は図鑑番号 149 の
通常形態のスプライトだけ (置き場にメガ形態のスプライトが無い)。
対処: (a) 照合の候補を選出済みと判明した個体に限る (3 体判明していれば残りは外す)、(b) メガ形態の種族 id はメガ形態のスプライトが
置き場にあればそれも照合に使う、(c) 元の HP が無い枠へ、別の枠が既に持つ値を書かない (棄却 watch_right_duplicate_value)。
採用の閾値 (色照合 0.38 / 0.05) は変えていない。

画像: 10/15 の様子見画面のフレームは保存されていないため、10/9 の様子見画面の fixture (tests/fixtures/watch_opp/) を流用する。

使い方: python -m tests.test_watch_right_candidates
"""
import time

import cv2
import numpy as np

from vision import ocr, zones
from vision.state import BattleStateV2, PokemonState
from tests.test_watch_opp_color import PARTY, ROWS, _frame, _Sprites, _rgba

# battle_20261015_100950 の相手 (枠の並び)
PARTY_1015 = [("lopunny", "ミミロップ"), ("hippowdon", "カバルドン"), ("aegislash", "ギルガルド"),
              ("basculegion", "イダイトウ"), ("mimikyu", "ミミッキュ"), ("dragonitemega", "カイリュー")]


def _state_1015(known_three: bool = True) -> BattleStateV2:
    """10/15 10:16:28 の相手の状態: ギルガルド (ひんし)、ミミッキュ (場)、メガカイリュー (87%)。known_three=False は
    メガカイリューの判明の印 (メガ・HUD の確定) を外して、選出の判明を 2 体にしたもの"""
    st = BattleStateV2()
    st.battle_active = True
    st.scene = "watch"
    for sid, ja in PARTY_1015:
        st.opponent.party.append(PokemonState(species_ja=ja, species_id=sid, display_name=ja))
    p = st.opponent.party
    p[2].status, p[2].hp_percent, p[2].hp_read_ts = "fainted", 0.0, 1792026909.2
    p[4].hp_percent, p[4].hp_read_ts = 77.0, 1792026973.5
    p[5].hp_percent = 87.0
    if known_three:
        p[5].is_mega, p[5].hp_read_ts = True, 1792026817.4
    st.opponent.active_index = 4
    return st


def _mock_rows(pct_by_row: dict):
    """右列の % の文字の OCR をモックする (行 → 文字)。戻り値は元に戻す関数"""
    from vision import extractors
    rows = {id(z["hp_text"]): i for i, z in enumerate(zones.WATCH_OPP)}
    orig = ocr.read_zone_text, extractors._reread_watch_opp_types
    ocr.read_zone_text = lambda _img, zone, **kw: pct_by_row.get(rows.get(id(zone)), "")
    extractors._reread_watch_opp_types = lambda img, state: None

    def restore():
        ocr.read_zone_text, extractors._reread_watch_opp_types = orig
    return restore


def _paint_icon(img, row: int, q):
    z = zones.WATCH_OPP[row]["icon"]
    h, w = img.shape[:2]
    y0, y1, x0, x1 = int(z["y0"] * h), int(z["y1"] * h), int(z["x0"] * w), int(z["x1"] * w)
    img[y0:y1, x0:x1] = cv2.resize(q, (x1 - x0, y1 - y0))


# ------------------------------------------------------------------ 純粋な部分
def test_candidate_slots_pure():
    """10/15 の形: 選出済みと判明した枠 (ひんし・場・メガ / HUD の確定) が 3 体なら、残りの 3 体 (ミミロップ等) を候補から外す。
    2 体しか判明していなければ全枠を候補に残す。HP% だけ (右列の書き込みで入る値) は判明の根拠にしない"""
    from vision.extractors import opp_pick_known, watch_right_candidate_slots
    st = _state_1015()
    cand, excl = watch_right_candidate_slots(st.opponent.party, st.opponent.active_index)
    assert (cand, excl) == ([2, 4, 5], [0, 1, 3]), (cand, excl)
    st.opponent.party[0].hp_percent = 87.0   # 誤同定で書かれた値は判明の根拠にならない
    assert not opp_pick_known(st.opponent.party[0], 0, 4)
    assert watch_right_candidate_slots(st.opponent.party, 4)[0] == [2, 4, 5]
    st2 = _state_1015(known_three=False)
    cand2, excl2 = watch_right_candidate_slots(st2.opponent.party, st2.opponent.active_index)
    assert (cand2, excl2) == ([0, 1, 2, 3, 4, 5], []), (cand2, excl2)
    # 技を見た・選出の印も判明の根拠
    st2.opponent.party[1].revealed_moves = ["じしん"]
    assert watch_right_candidate_slots(st2.opponent.party, 4)[0] == [1, 2, 4]
    st3 = _state_1015(known_three=False)
    st3.opponent.party[3].is_picked = True
    assert watch_right_candidate_slots(st3.opponent.party, 4)[0] == [2, 3, 4]
    print("test_candidate_slots_pure OK")


def test_duplicate_slot_pure():
    """元の HP が無い枠へ、別の枠が既に持つ値 (差 0.5 以内) を書かない。元の HP がある枠・値が違う場合は対象外"""
    from vision.extractors import watch_right_duplicate_slot
    party = _state_1015().opponent.party
    assert watch_right_duplicate_slot(party, 0, 87.0) == 5
    assert watch_right_duplicate_slot(party, 0, 86.0) is None
    assert watch_right_duplicate_slot(party, 4, 87.0) is None   # 元の HP (77) がある枠は従来どおり (大きな増加の確認に任せる)
    assert watch_right_duplicate_slot(party, 5, 87.0) is None
    print("test_duplicate_slot_pure OK")


# ------------------------------------------------------------------ 実画像 (10/9 の様子見画面の fixture)
def test_unpicked_row_not_written_fixture():
    """10/9 の様子見画面 (アブソルの行 0 / ライチュウの行 4 / アーマーガアの行 5) で、選出済みと判明した 3 体を
    ライチュウ (場)・アーマーガア (ひんし)・パーモット (HUD の確定) にすると、選出外のアブソルは照合の候補から外れ、
    アブソルの行の % は (修正前は アブソル と同定して書いていた) アブソルに書かれない。ライチュウの行は従来どおり書く"""
    from vision import extractors
    from vision.spriteid import identify_species_color
    from vision.zones import crop
    img = _frame(ROWS)

    def state():
        st = BattleStateV2()
        st.battle_active = True
        st.scene = "watch"
        for sid, ja in PARTY:
            st.opponent.party.append(PokemonState(species_ja=ja, species_id=sid, display_name=ja))
        p = st.opponent.party
        p[4].hp_percent = 55.0
        p[5].status, p[5].hp_percent = "fainted", 0.0
        p[1].hp_percent, p[1].hp_read_ts = 40.0, 123.0
        st.opponent.active_index = 4
        return st

    st = state()
    restore = _mock_rows({0: "62%", 4: "100%"})
    try:
        with _Sprites():
            # 修正前の候補 (6 体すべて) ではアブソルの行はアブソルに決まる (不具合の形の再現)
            all6 = [(sid, 0.5, ja) for sid, ja in PARTY]
            hit = identify_species_color(crop(img, zones.WATCH_OPP[0]["icon"]), all6)
            assert hit and hit[0] == "absol", hit
            extractors.extract_watch_side_columns(img, st, None)
    finally:
        restore()
    rows = {r["row"]: r for r in st.watch_opp_rows}
    assert "absol" not in rows[0]["candidates"] and sorted(rows[0]["candidates"]) == ["corviknight", "pawmot", "raichu"], rows[0]
    assert st.opponent.party[0].hp_percent is None, st.opponent.party[0].hp_percent
    assert rows[0]["species"] != "absol" and rows[0].get("slot") != 0, rows[0]
    assert rows[4]["written"] == "written" and st.opponent.party[4].hp_percent == 100.0, rows[4]
    print("test_unpicked_row_not_written_fixture OK", {k: rows[0].get(k) for k in ("species", "written", "match_reason",
                                                                                     "top_species", "top_score")})


# ------------------------------------------------------------------ 合成
def test_duplicate_value_skipped_synthetic():
    """10/15 の形を合成で: 選出の判明が 2 体 (ミミロップが候補に残る) で、行 5 のアイコンがミミロップに同定されても、
    HP の無いミミロップの枠に、メガカイリューが既に持つ 87 を書かない (hp_reject に watch_right_duplicate_value)。
    3 体判明していればミミロップは候補から外れる"""
    from advisor.dex import get_dex
    from vision import extractors
    dex = get_dex()
    q = np.full((100, 120, 3), (110, 20, 140), np.uint8)   # 様子見の行の地 (マゼンタ)
    cv2.circle(q, (60, 50), 34, (60, 120, 200), -1)
    img = np.zeros((1080, 1920, 3), np.uint8)
    _paint_icon(img, 5, q)
    shapes = {"lopunny": ((60, 120, 200), "circle"), "hippowdon": ((40, 40, 40), "triangle"),
              "aegislash": ((200, 200, 30), "triangle"), "basculegion": ((30, 200, 30), "triangle"),
              "mimikyu": ((20, 220, 220), "triangle"), "dragonitemega": ((200, 30, 200), "triangle")}
    with _Sprites() as sp:
        for sid, (color, shape) in shapes.items():
            sp.put(dex.species(sid)["num"], _rgba(color, shape))
        st = _state_1015(known_three=False)
        restore = _mock_rows({5: "87%"})
        try:
            extractors.extract_watch_side_columns(img, st, None)
        finally:
            restore()
        (row,) = st.watch_opp_rows
        assert row["species"] == "lopunny" and row["slot"] == 0 and row["written"] == "watch_right_duplicate_value", row
        assert st.opponent.party[0].hp_percent is None and st.opponent.party[5].hp_percent == 87.0
        rej = st.hp_reject["opponent"]
        assert rej["reason"] == "watch_right_duplicate_value" and rej["slot"] == 0 and rej["dup_slot"] == 5, rej
        assert rej["dup_species"] == "dragonitemega" and rej["hp_candidate"] == 87.0 and rej["source"] == "watch_right", rej
        # 値が違えば従来どおり書く (重複の確認は同じ値だけ)
        st_b = _state_1015(known_three=False)
        restore = _mock_rows({5: "64%"})
        try:
            extractors.extract_watch_side_columns(img, st_b, None)
        finally:
            restore()
        assert st_b.watch_opp_rows[0]["written"] == "written" and st_b.opponent.party[0].hp_percent == 64.0
        # 選出が 3 体判明していれば、ミミロップは候補に無く、ミミロップには書かない
        st3 = _state_1015()
        restore = _mock_rows({5: "87%"})
        try:
            extractors.extract_watch_side_columns(img, st3, None)
        finally:
            restore()
        (row3,) = st3.watch_opp_rows
        assert "lopunny" not in row3["candidates"] and row3["species"] != "lopunny", row3
        assert st3.opponent.party[0].hp_percent is None
    print("test_duplicate_value_skipped_synthetic OK", {k: row3[k] for k in ("species", "written", "match_reason")})


def test_mega_sprite_variant_synthetic():
    """メガ形態の種族 id (dragonitemega) は、置き場にメガ形態のスプライト (149-mega.png) があればそれも照合に使う。
    無ければ図鑑番号 149 (通常形態) だけ (従来どおり)。ファイル名は置き場の形態違いの名前 ('412-sandy') に合わせる"""
    from advisor.dex import get_dex
    from vision.spriteid import identify_species_color, mega_sprite_stem
    assert mega_sprite_stem("dragonitemega", 149) == "149-mega"
    assert mega_sprite_stem("charizardmegax", 6) == "6-mega-x" and mega_sprite_stem("charizardmegay", 6) == "6-mega-y"
    assert mega_sprite_stem("dragonite", 149) is None and mega_sprite_stem("meganium", 154) is None
    dex = get_dex()
    n_dra, n_lop = dex.species("dragonitemega")["num"], dex.species("lopunny")["num"]
    cands = [("dragonitemega", 0.5, "カイリュー"), ("lopunny", 0.5, "ミミロップ")]
    q = np.full((100, 120, 3), (110, 20, 140), np.uint8)
    cv2.fillPoly(q, [np.array([[60, 10], [100, 90], [20, 90]], np.int32)], (40, 160, 230))   # メガ形態の見た目 (三角・橙)
    with _Sprites() as sp:
        sp.put(n_dra, _rgba((40, 200, 90), "circle"))        # 通常形態の見た目 (丸・緑): メガ後のアイコンとは合わない
        sp.put(n_lop, _rgba((60, 150, 200), "circle"))       # 別の種 (丸・橙寄り)
        why = {}
        hit = identify_species_color(q, cands, detail=why)
        assert not (hit and hit[0] == "dragonitemega"), (hit, why)   # 通常形態だけでは決まらない (修正前と同じ)
        cv2.imwrite(str(sp.tmp / "dex" / f"{n_dra}-mega.png"), _rgba((40, 160, 230), "triangle"))
        sp.S._sprite_cache.clear()
        why2 = {}
        hit2 = identify_species_color(q, cands, detail=why2)
        assert hit2 and hit2[0] == "dragonitemega" and why2["margin"] >= 0.05, (hit2, why2)
    print("test_mega_sprite_variant_synthetic OK", {"without": why.get("top"), "with": why2.get("top")})


if __name__ == "__main__":
    t0 = time.time()
    test_candidate_slots_pure()
    test_duplicate_slot_pure()
    test_unpicked_row_not_written_fixture()
    test_duplicate_value_skipped_synthetic()
    test_mega_sprite_variant_synthetic()
    print(f"ALL OK ({time.time() - t0:.1f}s)")
