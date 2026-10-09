"""様子見画面の右列 (相手 6 行のアイコン + HP%) の色照合 (2026-10-09 fix/hp-ocr-watch、KNOWN_ISSUES A1「様子見画面の右列の色照合が
全行で不一致」)。

原因: 照合の切り出しが行全体 (zones.WATCH_OPP の panel: 地の色・性別の記号・タイプアイコン・HP の文字とバー) で、前景の抽出
(四隅の色との差) が行全体を前景にし、色ヒストグラムの相関が 0 付近 → 見た目のスコア 0.20〜0.38 で採用 (0.38) に届かなかった。
対処: 種族アイコンだけの範囲 (zones.WATCH_OPP の icon) で照合する。採用の閾値 (accept 0.38 / margin 0.05) は変えていない。
行ごとの記録 (state.watch_opp_rows) に、不一致の理由・1 位と 2 位の種族とスコア・差を足した。

画像: tests/fixtures/watch_opp/ (frame_1791513648 の右列の行の原寸の切り出しと、照合に使う図鑑スプライトの写し)。

使い方: python -m tests.test_watch_opp_color
"""
import json
import shutil
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np

from vision import ocr, zones
from vision.state import BattleStateV2, PokemonState
from vision.zones import crop

FIX = Path(__file__).resolve().parent / "fixtures" / "watch_opp"
MANIFEST = json.loads((FIX / "manifest.json").read_text(encoding="utf-8"))
# battle_20261009_113830 の相手 (行 i = 選出画面の相手枠 i)
PARTY = [("absol", "アブソル"), ("pawmot", "パーモット"), ("volcarona", "ウルガモス"),
         ("meowscarada", "マスカーニャ"), ("raichu", "ライチュウ"), ("corviknight", "アーマーガア")]
ROWS = ["row0_absol.png", "row4_raichu.png", "row5_corviknight.png"]


def _frame(names):
    """行の切り出しを元の解像度の黒い画面の元の位置に貼り戻す"""
    img = None
    for name in names:
        meta = MANIFEST[name]
        part = cv2.imread(str(FIX / name))
        assert part is not None, name
        w, h = meta["frame_size"]
        if img is None:
            img = np.zeros((h, w, 3), dtype=np.uint8)
        x0, y0 = meta["offset"]
        img[y0:y0 + part.shape[0], x0:x0 + part.shape[1]] = part
    return img


class _Sprites:
    """spriteid の図鑑スプライトの置き場を一時ディレクトリに差し替える (fixture の dex_<番号>.png を <番号>.png として置く)"""

    def __enter__(self):
        import vision.spriteid as S
        self.S = S
        self.old = (S.TEMPLATE_DIR, S.REAL_DIR, dict(S._sprite_cache), S._real_cache)
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "dex").mkdir()
        (self.tmp / "real").mkdir()
        for p in FIX.glob("dex_*.png"):
            shutil.copy(p, self.tmp / "dex" / (p.stem.split("_", 1)[1] + ".png"))
        S.TEMPLATE_DIR, S.REAL_DIR = self.tmp / "dex", self.tmp / "real"
        S._sprite_cache.clear()
        S._real_cache = None
        return self

    def put(self, num: int, rgba):
        cv2.imwrite(str(self.tmp / "dex" / f"{num}.png"), rgba)
        self.S._sprite_cache.clear()

    def __exit__(self, *a):
        S = self.S
        S.TEMPLATE_DIR, S.REAL_DIR = self.old[0], self.old[1]
        S._sprite_cache.clear()
        S._sprite_cache.update(self.old[2])
        S._real_cache = self.old[3]
        shutil.rmtree(self.tmp)


def _cands():
    return [(sid, 0.5, ja) for sid, ja in PARTY]


def test_icon_zone_identifies_rows():
    """10/9 の様子を見る画面の 3 行 (カーソルの行のライチュウ 100% を含む) が、アイコンの範囲では正しい種族に決まる。
    行全体 (panel) では従来どおり 3 行とも決まらない (不具合の再現: スコアが採用 0.38 に届かない)"""
    from vision.spriteid import identify_species_color
    out = []
    with _Sprites():
        for name in ROWS:
            meta = MANIFEST[name]
            img = _frame([name])
            z = zones.WATCH_OPP[meta["row"]]
            why = {}
            hit = identify_species_color(crop(img, z["icon"]), _cands(), detail=why)
            assert hit and hit[0] == meta["species"], (name, hit, why)
            assert why["reason"] is None and why["top"][0][0] == meta["species"] and why["margin"] >= 0.05, why
            old = {}
            assert identify_species_color(crop(img, z["panel"]), _cands(), detail=old) is None, name
            assert old["reason"] == "low_score" and old["top"][0][1] < 0.38, old
            out.append((name, hit[2], why["margin"], old["top"][0][1]))
    print("test_icon_zone_identifies_rows OK", out)


def test_watch_rows_written_and_recorded():
    """右列の抽出 (_extract_watch_side_columns): ライチュウの行 (100%) が同定されて書かれ、行の記録に照合の材料が載る。
    % の文字は OCR をモックする (色照合とアイコンの切り出しは実物)"""
    from vision import extractors
    from battle_logger import scene_row_state
    img = _frame(ROWS)
    st = BattleStateV2()
    st.battle_active = True
    st.scene = "watch"
    for sid, ja in PARTY:
        st.opponent.party.append(PokemonState(species_ja=ja, species_id=sid, display_name=ja))
    st.opponent.active_index = 4
    st.opponent.party[4].hp_percent = 55.0
    rows = {id(z["hp_text"]): i for i, z in enumerate(zones.WATCH_OPP)}
    orig = ocr.read_zone_text, extractors._reread_watch_opp_types
    ocr.read_zone_text = lambda _img, zone, **kw: "100%" if rows.get(id(zone)) == 4 else ""
    extractors._reread_watch_opp_types = lambda img, state: None
    try:
        with _Sprites():
            extractors.extract_watch_side_columns(img, st, None)
    finally:
        ocr.read_zone_text, extractors._reread_watch_opp_types = orig
    assert st.opponent.party[4].hp_percent == 100.0
    (row,) = st.watch_opp_rows
    assert (row["row"], row["pct"], row["species"], row["method"], row["written"], row["slot"]) == \
        (4, 100, "raichu", "color", "written", 4), row
    assert row["match_reason"] is None and row["top_species"] == "raichu" and row["top_score"] == row["score"], row
    assert row["second_species"] and row["second_species"] != "raichu" and row["margin"] >= 0.05, row
    json.dumps(scene_row_state(st.to_dict())["watch_opp_rows"])
    print("test_watch_rows_written_and_recorded OK", {k: row[k] for k in ("score", "second_species", "second_score", "margin")})


def _rgba(color, shape="circle"):
    img = np.zeros((96, 96, 4), np.uint8)
    if shape == "circle":
        cv2.circle(img, (48, 48), 34, (*color, 255), -1)
    else:
        cv2.fillPoly(img, [np.array([[48, 6], [90, 90], [6, 90]], np.int32)], (*color, 255))
    return img


def test_close_candidates_not_decided():
    """10/7 の誤同定の形 (アシレーヌの行をマニューラと同定) を合成で: アシレーヌとマニューラの見た目のスコアの差が margin (0.05) 未満なら
    決めない (small_margin)。行の記録には 1 位・2 位と差が残り、HP は書かない"""
    from advisor.dex import get_dex
    from vision import extractors
    from vision.spriteid import identify_species_color
    dex = get_dex()
    n_pri, n_wea = dex.species("primarina")["num"], dex.species("weavile")["num"]
    cands = [("primarina", 0.5, "アシレーヌ"), ("weavile", 0.5, "マニューラ")]
    q = np.full((100, 120, 3), (110, 20, 140), np.uint8)   # 様子見の行の地 (マゼンタ)
    cv2.circle(q, (60, 50), 34, (200, 160, 120), -1)
    with _Sprites() as sp:
        # 同じ形・ほぼ同じ色のスプライト: 差が margin 未満
        sp.put(n_pri, _rgba((200, 160, 120)))
        sp.put(n_wea, _rgba((198, 160, 122)))
        why = {}
        assert identify_species_color(q, cands, detail=why) is None, why
        assert why["reason"] == "small_margin" and why["margin"] < 0.05 and len(why["top"]) == 2, why
        # 形も色も違えば決まる (照合そのものは働く)
        sp.put(n_wea, _rgba((40, 40, 200), "triangle"))
        why = {}
        hit = identify_species_color(q, cands, detail=why)
        assert hit and hit[0] == "primarina" and why["margin"] >= 0.05, (hit, why)
        # 抽出の経路: 差が小さい行は書かず、no_match と照合の材料を残す
        sp.put(n_wea, _rgba((198, 160, 122)))
        st = BattleStateV2()
        st.battle_active = True
        pri = PokemonState(species_ja="アシレーヌ", species_id="primarina", display_name="アシレーヌ")
        wea = PokemonState(species_ja="マニューラ", species_id="weavile", display_name="マニューラ")
        wea.hp_percent = 39.0
        st.opponent.party += [pri, wea]
        img = np.zeros((1080, 1920, 3), np.uint8)
        z = zones.WATCH_OPP[0]["icon"]
        y0, y1 = int(z["y0"] * 1080), int(z["y1"] * 1080)
        x0, x1 = int(z["x0"] * 1920), int(z["x1"] * 1920)
        img[y0:y1, x0:x1] = cv2.resize(q, (x1 - x0, y1 - y0))
        rows = {id(zz["hp_text"]): i for i, zz in enumerate(zones.WATCH_OPP)}
        orig = ocr.read_zone_text, extractors._reread_watch_opp_types
        ocr.read_zone_text = lambda _img, zone, **kw: "100%" if rows.get(id(zone)) == 0 else ""
        extractors._reread_watch_opp_types = lambda img, state: None
        try:
            extractors.extract_watch_side_columns(img, st, None)
        finally:
            ocr.read_zone_text, extractors._reread_watch_opp_types = orig
    (row,) = st.watch_opp_rows
    assert row["species"] is None and row["written"] == "no_match" and row["match_reason"] == "small_margin", row
    assert {row["top_species"], row["second_species"]} == {"primarina", "weavile"} and row["margin"] < 0.05, row
    assert wea.hp_percent == 39.0 and pri.hp_percent is None, (wea.hp_percent, pri.hp_percent)   # どちらにも書かない
    print("test_close_candidates_not_decided OK", {k: row[k] for k in ("top_species", "second_species", "margin")})


if __name__ == "__main__":
    t0 = time.time()
    test_icon_zone_identifies_rows()
    test_watch_rows_written_and_recorded()
    test_close_candidates_not_decided()
    print(f"\nALL OK ({time.time() - t0:.1f}s)")
