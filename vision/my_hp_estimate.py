"""自分の HP のバー推定 (純粋な計算。2026-10-09 ユーザー判断、KNOWN_ISSUES A1 の (3) 分母の OCR 落ち)。

自分の HUD の分数が読めない ('13723'、'167'、'1595' のような分母や区切りの OCR 落ち) か、読めても照合で捨てたフレームが
続くと、HP が古い値 (100% 等) のまま固着する (10/9 接続テスト: オオニューラ 4/159 が 100% のまま)。
そのあいだ、場の個体と HUD の対応が確かで (名前が一致し、最大 HP が既知)、バーの割合が安定しているなら、
既知の最大 HP × バーの割合で概算を入れる。概算は推定の印 (hp_estimated / hp_uncertain / hp_source="bar") つきで入れ、
分数の実測が照合を通れば置き換える (vision.extractors._set_hp)。

バーの割合は実際の値と数 % ずれる (frame_1791503228: 正解 4/159 = 2.5%、バー 4.4% → 概算 7/159)。正確な実数値ではない。
推定だけではひんしを確定しない: バーがほぼ 0 でも 1 HP を下限にして「ひんしの疑い」の印だけ付ける
(ひんしはひんしの文言・HUD の 0/最大 の実測・WIN / LOSE の画面の既存の経路で確定する)。

画像処理 (バーの割合の測定・OCR) と状態の書き込みは vision.extractors.extract_my_hud が行う。ここは値の計算と条件だけ。
"""
from __future__ import annotations

from typing import Optional

from champions_agent.config import HP_BAR_ESTIMATE_STABLE_FRAMES, HP_BAR_ESTIMATE_STABLE_TOL

# 推定値の出所 (PokemonState.hp_source)
HP_SOURCE_BAR = "bar"

# 推定を入れない理由 (estimate_block_reason の戻り値。テストと調査用)
BLOCK_NO_SPECIES = "species_unknown"       # 場の個体の種族が未特定
BLOCK_NAME = "name_mismatch"               # HUD の名前が場の個体の種族に解決できない (読めない・別の名前)
BLOCK_MAX_UNKNOWN = "max_hp_unknown"       # 最大 HP が分からない (型登録の理論値も過去の実測も無い)
BLOCK_FRACTION_OK = "fraction_readable"    # 分数が読めて照合を通った (実測の経路に任せる)
BLOCK_FAINTED = "fainted"                  # ひんし確定済み
BLOCK_NO_BAR = "bar_unreadable"            # バーの割合が測れない


def estimate_block_reason(species_known: bool, name_matches: bool, known_max: Optional[int],
                          fraction_unusable: bool, fainted: bool, bar: Optional[float]) -> Optional[str]:
    """このフレームのバーを推定の材料に数えてよいか (純粋)。None = 数えてよい。数えないときは理由。

    (a) 場の個体の種族が特定済みで、HUD の名前がその種族に解決できた (既存の照合 resolve_my_species / adopt_my_hud_species)
    (b) 最大 HP が既知 (型登録の理論値・実測採用値・過去に確定した実測値)
    (d) 分数が読めない (fraction_unparsable)、または読めたが照合で捨てた
    に加え、ひんし確定済みでないこと・バーの割合が測れたこと"""
    if fainted:
        return BLOCK_FAINTED
    if not species_known:
        return BLOCK_NO_SPECIES
    if not name_matches:
        return BLOCK_NAME
    if not known_max or known_max <= 0:
        return BLOCK_MAX_UNKNOWN
    if not fraction_unusable:
        return BLOCK_FRACTION_OK
    if bar is None:
        return BLOCK_NO_BAR
    return None


def push_bar(track: Optional[list], bar: float, keep: int = HP_BAR_ESTIMATE_STABLE_FRAMES) -> list:
    """連続フレームのバーの割合の列に bar を足し、末尾 keep 件だけ残した新しい列を返す (純粋)"""
    out = list(track or []) + [float(bar)]
    return out[-max(1, int(keep)):]


def stable_bar(track: Optional[list], n: int = HP_BAR_ESTIMATE_STABLE_FRAMES,
               tol: float = HP_BAR_ESTIMATE_STABLE_TOL) -> Optional[float]:
    """末尾 n 件のバーの割合がそろい、最大と最小の差が tol 以内なら代表値 (中央値) を返す。そろわなければ None (純粋)"""
    vals = list(track or [])[-max(1, int(n)):]
    if len(vals) < n or not vals:
        return None
    if max(vals) - min(vals) > tol + 1e-9:
        return None
    s = sorted(vals)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2.0


def bar_estimate(bar: float, max_hp: int) -> dict:
    """バーの割合と既知の最大 HP から概算を作る (純粋)。

    戻り値 {"pct", "cur", "max", "bar", "faint_suspect"}。pct = バー × 100、cur = round(バー × 最大) (概算)。
    推定だけでひんしを確定しないため、cur が 0 になるとき (バーがほぼ 0) は 1 HP を下限にし、
    pct も 1 HP ぶんの割合にして faint_suspect=True を付ける (助言側は hp_percent <= 0 をひんしとみなすため)"""
    b = max(0.0, min(1.0, float(bar)))
    mx = int(max_hp)
    cur = int(round(b * mx))
    pct = round(b * 100.0, 1)
    faint_suspect = cur <= 0
    if faint_suspect:
        cur = 1
        pct = round(100.0 / mx, 1)
    return {"pct": pct, "cur": cur, "max": mx, "bar": round(b, 3), "faint_suspect": faint_suspect}
