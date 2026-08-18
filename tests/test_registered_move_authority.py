"""登録技を正とする技読取 (2026-08-19 第4回テスト指摘) のテスト。

    python -m tests.test_registered_move_authority

登録済みの型がある場合、技選択画面のOCRは技の同一性を変えず、
PP/相性ヒントの対応付けにのみ使う。OCR解決の誤り (実測: ムクホークに
bulkup / closecombat 等が混入) が助言に乗るのを防ぐ。
"""
from __future__ import annotations

from vision import extractors as ex
from vision.normalize import NameResolver
from vision.state import BattleStateV2, PokemonState


def _run_move_select(state, rows):
    """detect_move_rows/OCR/_read_pp をモックして extract_move_select を回す。

    rows: [(技名OCRテキスト, (pp, max_pp) | None, ヒントテキスト | "")]
    """
    import numpy as np
    sentinels = []
    for i, _ in enumerate(rows):
        sentinels.append({"name": {"i": i, "k": "name"},
                          "pp": {"i": i, "k": "pp"},
                          "hint": {"i": i, "k": "hint"}})

    def fake_rows(img):
        return sentinels

    def fake_read(img, zone, **kw):
        if isinstance(zone, dict) and "i" in zone and "k" in zone:
            i, k = zone["i"], zone["k"]
            if k == "name":
                return rows[i][0]
            if k == "hint":
                return rows[i][2]
        return ""

    def fake_pp(img, zone):
        return rows[zone["i"]][1]

    orig = (ex.detect_move_rows, ex.ocr.read_zone_text, ex._read_pp)
    ex.detect_move_rows, ex.ocr.read_zone_text, ex._read_pp = \
        fake_rows, fake_read, fake_pp
    try:
        img = np.zeros((720, 1280, 3), dtype=np.uint8)
        ex.extract_move_select(img, state, NameResolver())
    finally:
        ex.detect_move_rows, ex.ocr.read_zone_text, ex._read_pp = orig


def _state_with(ja, sid):
    st = BattleStateV2()
    st.player.party.append(PokemonState(species_ja=ja, species_id=sid))
    st.player.active_index = 0
    return st


def test_registered_moves_are_authoritative():
    from advisor import my_team as mt
    orig = mt.get_my_moves
    mt.get_my_moves = lambda ja: (
        ["ブレイブバード", "でんこうせっか", "すてみタックル", "ブレイズキック"]
        if ja == "ムクホーク" else [])
    try:
        st = _state_with("ムクホーク", "staraptor")
        _run_move_select(st, [
            ("ブレイブバード", (14, 16), "◎"),
            ("ビルドアップ", (20, 20), ""),      # OCR誤読 → 登録外 → 捨てる
            ("すてみタックル", (16, 16), ""),
            ("プレイスキック", (12, 12), ""),    # 化け → 制限付き再解決でブレイズキックへ
        ])
        me = st.player.party[0]
        ids = [m.move_id for m in me.moves]
        assert sorted(ids) == sorted(
            ["bravebird", "quickattack", "doubleedge", "blazekick"]), ids
        assert "bulkup" not in ids
        bb = next(m for m in me.moves if m.move_id == "bravebird")
        assert (bb.pp, bb.max_pp) == (14, 16), (bb.pp, bb.max_pp)
        bk = next(m for m in me.moves if m.move_id == "blazekick")
        assert (bk.pp, bk.max_pp) == (12, 12), (bk.pp, bk.max_pp)
        qa = next(m for m in me.moves if m.move_id == "quickattack")
        assert qa.pp is None, qa.pp   # 画面に出なかった登録技はPP不明のまま保持
    finally:
        mt.get_my_moves = orig
    print("test_registered_moves_are_authoritative OK")


def test_unregistered_species_keeps_ocr_behavior():
    from advisor import my_team as mt
    orig = mt.get_my_moves
    mt.get_my_moves = lambda ja: []
    try:
        st = _state_with("ゲンガー", "gengar")
        _run_move_select(st, [
            ("シャドーボール", (15, 15), ""),
            ("ヘドロばくだん", (10, 10), ""),
        ])
        ids = {m.move_id for m in st.player.party[0].moves}
        assert ids == {"shadowball", "sludgebomb"}, ids
    finally:
        mt.get_my_moves = orig
    print("test_unregistered_species_keeps_ocr_behavior OK")


def test_wholesale_mismatch_logs_reregister_hint():
    """画面の技が登録とほぼ全部違う場合、再登録を促すログを出す
    (型変更に黙って追従しないが、捨て続けもしない)"""
    from advisor import my_team as mt
    orig = mt.get_my_moves
    mt.get_my_moves = lambda ja: (
        ["ブレイブバード", "でんこうせっか", "すてみタックル", "ブレイズキック"]
        if ja == "ムクホーク" else [])
    try:
        st = _state_with("ムクホーク", "staraptor")
        _run_move_select(st, [
            ("インファイト", (5, 5), ""),
            ("ビルドアップ", (20, 20), ""),
            ("かわらわり", (15, 15), ""),
        ])
        texts = [e.get("text", "") for e in st.events]
        assert any("登録と大きく不一致" in x for x in texts), texts
        ids = [m.move_id for m in st.player.party[0].moves]
        assert "closecombat" not in ids, ids   # それでも登録が正
    finally:
        mt.get_my_moves = orig
    print("test_wholesale_mismatch_logs_reregister_hint OK")


if __name__ == "__main__":
    test_registered_moves_are_authoritative()
    test_unregistered_species_keeps_ocr_behavior()
    test_wholesale_mismatch_logs_reregister_hint()
    print("\nALL OK")
