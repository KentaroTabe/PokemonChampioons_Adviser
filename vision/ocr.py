"""OCRユーティリティ。

バックエンドは2系統:
- Apple Vision (macOS内蔵, pyobjc-framework-Vision): 主体。低解像度・低コントラストの
  小さい日本語UI文字に圧倒的に強く高速 (OBS実映像で実証済み)。前処理不要で生画像を読む。
- EasyOCR (日本語+英語): Apple Visionが使えない環境のフォールバック。
  白文字マスク等の前処理を併用する。

縁取り文字の「存在検知・描画完了検知」には引き続きマスク (outlined_text_mask) を使う。
"""
from __future__ import annotations

import warnings
from typing import Optional

import cv2
import numpy as np

warnings.filterwarnings("ignore", category=UserWarning, module="torch.utils.data.dataloader")

_reader = None
_apple_vision = None   # None=未判定, False=利用不可, それ以外=Visionモジュール


def _get_apple_vision():
    """Apple Visionフレームワークを遅延ロードする (macOSのみ)"""
    global _apple_vision
    if _apple_vision is None:
        try:
            import Vision  # noqa
            from Foundation import NSData  # noqa
            _apple_vision = Vision
            print("[vision.ocr] Apple Vision OCR を使用します")
        except Exception:
            _apple_vision = False
            print("[vision.ocr] Apple Vision が使えないため EasyOCR を使用します")
    return _apple_vision


def apple_ocr_text(bgr, scale: float = 2.0, langs=("ja-JP", "en-US")) -> str:
    """Apple VisionでOCRする。失敗時は空文字。

    数字ゾーンは langs=("en-US",) を使うと精度が上がる
    (日本語モードは斜体の「197/197」を「197mg7」等に誤読する)。
    """
    Vision = _get_apple_vision()
    if not Vision or bgr is None or bgr.size == 0:
        return ""
    from Foundation import NSData
    if scale != 1.0:
        bgr = cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    ok, buf = cv2.imencode(".png", bgr)
    if not ok:
        return ""
    data = NSData.dataWithBytes_length_(buf.tobytes(), len(buf))
    handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(data, None)
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(0)  # accurate
    req.setRecognitionLanguages_(list(langs))
    req.setUsesLanguageCorrection_(False)
    handler.performRequests_error_([req], None)
    results = req.results() or []
    return "".join(str(o.topCandidates_(1)[0].string()) for o in results).replace(" ", "")


def _is_ascii_allowlist(allowlist: Optional[str]) -> bool:
    return bool(allowlist) and all(ord(c) < 128 for c in allowlist)


def apple_ocr_lines(bgr, scale: float = 1.5, langs=("ja-JP", "en-US")) -> list:
    """Apple Visionで行ごとのOCR結果と位置を返す。

    戻り値: [(text, (x0, y0, x1, y1))] 座標は入力画像に対する相対値 (左上原点)。
    「場の状況」画面のように行位置が可変なレイアウトのアンカー検出に使う。
    """
    Vision = _get_apple_vision()
    if not Vision or bgr is None or bgr.size == 0:
        return []
    from Foundation import NSData
    if scale != 1.0:
        bgr = cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    ok, buf = cv2.imencode(".png", bgr)
    if not ok:
        return []
    data = NSData.dataWithBytes_length_(buf.tobytes(), len(buf))
    handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(data, None)
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(0)
    req.setRecognitionLanguages_(list(langs))
    req.setUsesLanguageCorrection_(False)
    handler.performRequests_error_([req], None)
    out = []
    for obs in (req.results() or []):
        cand = obs.topCandidates_(1)[0]
        bb = obs.boundingBox()  # Vision座標系: 左下原点・正規化済み
        x0 = float(bb.origin.x)
        y1v = float(bb.origin.y)
        w = float(bb.size.width)
        h = float(bb.size.height)
        # 左上原点に変換
        out.append((str(cand.string()).replace(" ", ""),
                    (x0, 1.0 - y1v - h, x0 + w, 1.0 - y1v)))
    return out


def _apply_ascii_allowlist(text: str, allowlist: Optional[str]) -> str:
    """数字系allowlist (ASCIIのみ) はVision出力にも文字フィルタとして適用する。

    カタカナ等の日本語allowlistはEasyOCR専用 (Visionはフィルタ不要の精度) なので適用しない。
    """
    if not allowlist or not text:
        return text
    if not all(ord(c) < 128 for c in allowlist):
        return text
    return "".join(c for c in text if c in allowlist)


def preload():
    """サーバー起動時のウォームアップ (使用するバックエンドを初期化)"""
    if _get_apple_vision():
        # 小さいダミー画像で初回呼び出しのオーバーヘッドを消化
        apple_ocr_text(np.full((32, 96, 3), 255, dtype=np.uint8))
    else:
        get_reader()

# 自分側のポケモン名はカタカナ表記前提 (ニックネーム含む日本語UI)。
# OCRのallowlistに使うと「ワワ】・ア」のような記号混じりの誤読を防げる。
# 末尾の数字/英字はポリゴン2・ポリゴンZ等のため
KATAKANA_ALLOWLIST = "".join(chr(c) for c in range(0x30A1, 0x30F7)) + "ー・2Z"


def get_reader():
    global _reader
    if _reader is None:
        try:
            import easyocr
        except ImportError:
            # Apple Visionもeasyocrも無い環境 (CI等) ではOCRなしで劣化動作する。
            # 呼び出し側は空文字を「読めなかった」として扱う設計のため安全
            if _reader is not False:
                print("[vision.ocr] OCRバックエンドなし (Vision/easyocr不在)。"
                      "読取は常に空文字になります")
            _reader = False
            return None
        print("[vision.ocr] Loading EasyOCR model...")
        _reader = easyocr.Reader(["ja", "en"], gpu=True)
        print("[vision.ocr] EasyOCR model loaded.")
    return _reader or None


def _pad_invert(mask, pad=20):
    """白マスク -> 黒文字/白背景 のOCR入力へ"""
    inverted = cv2.bitwise_not(mask)
    return cv2.copyMakeBorder(inverted, pad, pad, pad, pad,
                              cv2.BORDER_CONSTANT, value=255)


def white_text_mask(img, val_min=170, sat_max=70, scale=2.5):
    """パネル上の白文字を抽出してOCR入力画像を返す"""
    if img is None or img.size == 0:
        return None
    resized = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    hsv = cv2.cvtColor(resized, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array([0, 0, val_min]), np.array([180, sat_max, 255]))
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    if cv2.countNonZero(mask) < 20:
        return None
    return _pad_invert(mask)


def outlined_text_mask(img, scale=2.5):
    """縁取り文字 (白文字+黒フチ、背景は任意のゲーム画面) を抽出する。

    白画素のうち「近傍に暗画素があるもの」だけを残すことで、
    背景の白っぽい模様 (観客席など) を除去する。
    """
    if img is None or img.size == 0:
        return None
    resized = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    hsv = cv2.cvtColor(resized, cv2.COLOR_BGR2HSV)
    white = cv2.inRange(hsv, np.array([0, 0, 175]), np.array([180, 75, 255]))
    gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
    dark = cv2.inRange(gray, 0, 90)
    # 黒フチを膨張させ、その近傍にある白画素のみ文字とみなす
    kernel = np.ones((9, 9), np.uint8)
    near_dark = cv2.dilate(dark, kernel)
    mask = cv2.bitwise_and(white, near_dark)
    kernel2 = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel2)
    # 小さすぎる成分 (ノイズ) を除去
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    out = np.zeros_like(mask)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] >= 25:
            out[labels == i] = 255
    if cv2.countNonZero(out) < 60:
        return None
    return _pad_invert(out)


def read_crop_direct(crop_img, scale=2.0, allowlist: Optional[str] = None) -> str:
    """マスク処理をせず、拡大した生画像を直接OCRする (Vision優先)"""
    if crop_img is None or crop_img.size == 0:
        return ""
    if _get_apple_vision():
        langs = ("en-US",) if _is_ascii_allowlist(allowlist) else ("ja-JP", "en-US")
        return _apply_ascii_allowlist(apple_ocr_text(crop_img, scale=scale, langs=langs),
                                      allowlist)
    resized = cv2.resize(crop_img, None, fx=scale, fy=scale,
                         interpolation=cv2.INTER_CUBIC)
    reader = get_reader()
    if reader is None:
        return ""
    kwargs = {"detail": 0}
    if allowlist:
        kwargs["allowlist"] = allowlist
    res = reader.readtext(resized, **kwargs)
    return "".join(res).replace(" ", "") if res else ""


def read_text(processed, allowlist: Optional[str] = None) -> str:
    """前処理済み画像をOCRして連結テキストを返す"""
    if processed is None:
        return ""
    reader = get_reader()
    if reader is None:
        return ""
    kwargs = {"detail": 0}
    if allowlist:
        kwargs["allowlist"] = allowlist
    res = reader.readtext(processed, **kwargs)
    return "".join(res).replace(" ", "") if res else ""


class OcrText(str):
    """OCR の文字列に読み方 (method) を添えたもの。str としてそのまま扱える (2026-10-09 fix/hp-ocr-watch)。

    method: "direct" (ゾーン全体を 1 回で読んだ) / "split" (read_fraction_split で現在値と '/最大' を分けて読んだ)。
    呼び出し側は ocr_method(text) で取り出す (テストのモックが返す素の str は "direct" 扱い)"""
    method = "direct"

    def __new__(cls, s, method: str = "direct"):
        o = super().__new__(cls, s)
        o.method = method
        return o


def ocr_method(text) -> str:
    """read_zone_text の戻り値の読み方 ("direct" / "split")。素の str は "direct" """
    return getattr(text, "method", "direct")


def fraction_text_ok(text: str) -> bool:
    """'a/b' の形の分数として読めているか (純粋)。'/' で区切られ、b が HP_FRACTION_MAX_MIN 以上 999 以下、a <= b。

    '189/8' (分母の欠け)、'189/89' (現在値 > 最大)、'189' (分母の落ち)、'1897' (区切りの誤読) は False"""
    import re
    from champions_agent.config import HP_FRACTION_MAX_MIN
    if not text:
        return False
    m = re.search(r"(\d+)/(\d+)", text)
    if not m:
        return False
    cur, mx = int(m.group(1)), int(m.group(2))
    return HP_FRACTION_MAX_MIN <= mx <= 999 and cur <= mx


def hp_text_white_mask(c):
    """HP の数字 (白文字) の画素 (純粋)。彩度 < HP_TEXT_WHITE_SAT_MAX かつ明度 > HP_TEXT_WHITE_VAL_MIN"""
    from champions_agent.config import HP_TEXT_WHITE_SAT_MAX, HP_TEXT_WHITE_VAL_MIN
    hsv = cv2.cvtColor(c, cv2.COLOR_BGR2HSV)
    return (hsv[..., 1] < HP_TEXT_WHITE_SAT_MAX) & (hsv[..., 2] > HP_TEXT_WHITE_VAL_MIN)


def big_digits_right_col(c) -> Optional[int]:
    """自分の HP の文字の切り出しで、大きい数字 (現在値) の右端の列 (純粋)。見つからなければ None。

    zones.MY_HP_TEXT_SPLIT の big_band_y の行帯 (大きい数字だけがある高さ) で、白文字の画素が
    HP_TEXT_SPLIT_COL_MIN_PIXELS 以上ある最も右の列。big_right_max_x より右なら検出の失敗とみなす"""
    from champions_agent.config import HP_TEXT_SPLIT_COL_MIN_PIXELS
    from vision.zones import MY_HP_TEXT_SPLIT
    if c is None or c.size == 0:
        return None
    h, w = c.shape[:2]
    b0, b1 = MY_HP_TEXT_SPLIT["big_band_y"]
    band = hp_text_white_mask(c)[int(b0 * h):int(b1 * h)]
    cols = np.where(band.sum(axis=0) >= HP_TEXT_SPLIT_COL_MIN_PIXELS)[0]
    if len(cols) == 0:
        return None
    right = int(cols.max())
    return right if right < MY_HP_TEXT_SPLIT["big_right_max_x"] * w else None


def desaturate_gray(c, invert: bool = False):
    """彩度の高い画素 (HP バーの塗り) を暗くしたグレー (BGR 3 チャネル) (純粋)。白文字は白いまま残る。
    明度 × (1 - HP_TEXT_DESAT_GAIN × max(0, 彩度 - HP_TEXT_DESAT_SAT0))。invert=True で白黒を反転 (黒文字・明るい地)"""
    from champions_agent.config import HP_TEXT_DESAT_GAIN, HP_TEXT_DESAT_SAT0
    hsv = cv2.cvtColor(c, cv2.COLOR_BGR2HSV)
    sat = hsv[..., 1].astype(np.float32) / 255.0
    f = np.clip(1.0 - HP_TEXT_DESAT_GAIN * np.clip(sat - HP_TEXT_DESAT_SAT0, 0, 1), 0, 1)
    v = (hsv[..., 2].astype(np.float32) * f).astype(np.uint8)
    if invert:
        v = 255 - v
    return cv2.cvtColor(v, cv2.COLOR_GRAY2BGR)


def denominator_digits(text: str) -> str:
    """分母側 ('/189' の切り出し) の読みから最大 HP の数字を取り出す (純粋)。

    '/' は '7' '2' '1' に読まれることが多い (10/9 の満タンのバーストで '7189' '2189' '1189')。最大 HP は 3 桁以下なので、
    数字が 4 桁なら先頭 ('/' の誤読) を落とす。3 桁以下はそのまま ('/' が読み落とされた '189')。5 桁以上は読めないとみなして空"""
    import re
    d = re.sub(r"\D", "", text or "")
    if len(d) == 4:
        d = d[1:]
    return d if len(d) <= 3 else ""


def read_fraction_split(c) -> Optional[str]:
    """自分の HP の文字の切り出し c を、大きい現在値と小さい '/最大' に分けて読み、'現在値/最大' を返す。読めなければ None。

    満タン付近では HP バーの塗りが小さい分母の上端とつながり、ゾーン全体の読みが '189' '189/g' '1897' 等になる (KNOWN_ISSUES A1)。
    現在値: 大きい数字の右端 (big_digits_right_col) までを生のまま (読めなければ減光グレーで) 読む。
    分母: その右を、バーの塗りを暗くしたグレーの反転 + 余白で、HP_TEXT_DENOM_SCALES の倍率を順に試して読む (3 桁で採用)。
    Apple Vision が使えないときは None"""
    import re
    from champions_agent.config import HP_TEXT_DENOM_PAD, HP_TEXT_DENOM_SCALES
    from vision.zones import MY_HP_TEXT_SPLIT
    if not _get_apple_vision() or c is None or c.size == 0:
        return None
    right = big_digits_right_col(c)
    if right is None:
        return None
    h = c.shape[0]
    big = c[:, :right + 3]
    cur = re.sub(r"\D", "", apple_ocr_text(big, scale=2.0, langs=("en-US",)))
    if not cur:
        cur = re.sub(r"\D", "", apple_ocr_text(desaturate_gray(big), scale=2.0, langs=("en-US",)))
    if not cur:
        return None
    y0, y1 = MY_HP_TEXT_SPLIT["denom_y"]
    den = c[int(y0 * h):int(y1 * h), right + 2:]
    if den.size == 0:
        return None
    p = HP_TEXT_DENOM_PAD
    den = cv2.copyMakeBorder(desaturate_gray(den, invert=True), p, p, p, p, cv2.BORDER_CONSTANT,
                             value=(255, 255, 255))
    best = ""
    for s in HP_TEXT_DENOM_SCALES:
        d = denominator_digits(apple_ocr_text(den, scale=s, langs=("en-US",)))
        if len(d) == 3:
            best = d
            break
        if len(d) == 2 and not best:
            best = d
    if not best:
        return None
    return f"{cur}/{best}"


def read_zone_text(img, zone, mode="panel", allowlist: Optional[str] = None,
                   val_min=170, fraction: bool = False) -> str:
    """ゾーンを切り出してOCR。Vision利用時は前処理なしで生画像を読む。

    fraction=True (自分の HP の分数のゾーン): ゾーン全体の読みが 'a/b' の分数にならないとき (fraction_text_ok)、
    read_fraction_split で現在値と '/最大' に分けて読み直し、分数になればそれを返す (2026-10-09 fix/hp-ocr-watch)。
    戻り値は OcrText (読み方 method つきの str)。fraction=False の戻り値と挙動は従来どおり"""
    from vision.zones import crop
    c = crop(img, zone)
    if c is None:
        return OcrText("", "direct") if fraction else ""
    if _get_apple_vision():
        langs = ("en-US",) if _is_ascii_allowlist(allowlist) else ("ja-JP", "en-US")
        text = _apply_ascii_allowlist(apple_ocr_text(c, scale=2.0, langs=langs),
                                      allowlist)
        if not fraction:
            return text
        if not fraction_text_ok(text):
            sp = read_fraction_split(c)
            if sp and fraction_text_ok(sp):
                return OcrText(sp, "split")
        return OcrText(text, "direct")
    if mode == "outline":
        processed = outlined_text_mask(c)
    else:
        processed = white_text_mask(c, val_min=val_min)
    return read_text(processed, allowlist)


def parse_fraction(text: str):
    """'197/197' 形式 -> (cur, max)。読めなければ None"""
    import re
    if not text:
        return None
    # 明示的なスラッシュを最優先。スラッシュの誤読 (1/l/I/|) をセパレータと
    # みなすのは両側2桁以上のときのみ (「715」を7/5と解釈する誤りを防ぐ)
    for pat in (r"(\d+)\s*/\s*(\d+)", r"(\d{2,})\s*[1lI|]\s*(\d{2,})"):
        m = re.search(pat, text)
        if m:
            cur, mx = int(m.group(1)), int(m.group(2))
            if 0 < mx <= 999 and cur <= mx * 2:
                return (min(cur, mx), mx)
    digits = re.sub(r"\D", "", text)
    # スラッシュ取りこぼし時の桁分割は、両側が2桁以上になる場合のみ試す。
    # 「167」(=現在値のみ読めてスラッシュと最大値を取りこぼしたケース) を
    # 1/7に分割する誤りが実運用で起きたため、3桁以下は採用しない。
    # 最大HPはLv50では実質50以上なので下限も要求する
    if len(digits) >= 4:
        if len(digits) % 2 == 0:
            half = len(digits) // 2
            cur, mx = int(digits[:half]), int(digits[half:])
        else:
            half = len(digits) // 2
            cur, mx = int(digits[:half]), int(digits[half + 1:])
        if 50 <= mx <= 999 and cur <= mx:
            return (cur, mx)
    return None


def fraction_is_split_guess(text: str) -> bool:
    """parse_fraction(text) の分数が、区切り '/' ではなく数字の並びを分けて推測したものか (純粋)。分数として読めなければ False。

    '1595' → 15/95 (桁分割) や、'1' を区切りとみなした読みは True、'4/159' のように '/' で区切られた読みは False。
    最大 HP の実測採用 (vision.extractors.extract_my_hud の MY_MAX_ADOPT_READS) の根拠にしないために使う (2026-10-09)"""
    import re
    if parse_fraction(text) is None:
        return False
    m = re.search(r"(\d+)\s*/\s*(\d+)", text)
    if m:
        cur, mx = int(m.group(1)), int(m.group(2))
        if 0 < mx <= 999 and cur <= mx * 2:
            return False
    return True


def parse_percent(text: str):
    """'79%' -> 79。読めなければ None"""
    import re
    if not text:
        return None
    digits = re.sub(r"\D", "", text)
    if not digits:
        return None
    val = int(digits)
    if val > 100:
        # '100' の誤読 / % が数字扱いされた場合の補正
        if str(val).startswith("100"):
            return 100
        val = int(str(val)[:2])
    return val if 0 <= val <= 100 else None


def count_pokeballs(img) -> Optional[int]:
    """残数インジケータの緑のボール個数を数える (0-3)。

    ボールが検出できない (アニメーション中/ゾーン外) 場合は None。
    """
    if img is None or img.size == 0:
        return None
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    green = cv2.inRange(hsv, np.array([35, 70, 90]), np.array([80, 255, 255]))
    green = cv2.morphologyEx(green, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(green, 8)
    h, w = green.shape
    min_area = h * w * 0.02   # ボール1個はゾーンの数%を占める
    count = 0
    for i in range(1, n):
        area = stats[i, cv2.CC_STAT_AREA]
        bw, bh = stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]
        if area < min_area:
            continue
        # 隣接するボールは1ブロブに結合するため、幅/高さ比から個数を推定する
        est = max(1, round(bw / max(1, bh) * 0.9))
        count += est
    if count == 0:
        return None
    return min(count, 3)


def hp_bar_ratio(img) -> Optional[float]:
    """HPバー領域から残量比率 (0..1) を色で推定する。

    バーの色は 緑(高) / 黄(中) / 赤(低)。バー背景は暗色。
    """
    if img is None or img.size == 0:
        return None
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    green = cv2.inRange(hsv, np.array([35, 90, 110]), np.array([85, 255, 255]))
    yellow = cv2.inRange(hsv, np.array([18, 90, 130]), np.array([34, 255, 255]))
    red1 = cv2.inRange(hsv, np.array([0, 110, 120]), np.array([9, 255, 255]))
    red2 = cv2.inRange(hsv, np.array([170, 110, 120]), np.array([180, 255, 255]))
    fill = cv2.bitwise_or(cv2.bitwise_or(green, yellow), cv2.bitwise_or(red1, red2))

    col = (fill > 0).sum(axis=0)
    h, w = fill.shape
    filled_cols = (col > h * 0.3).astype(np.uint8)
    if filled_cols.sum() < 2:
        return 0.0 if (fill > 0).sum() < 10 else None
    # バーは左詰め。右端の連続した空きを除いた割合
    idx = np.where(filled_cols > 0)[0]
    left, right = idx[0], idx[-1]
    # バー全幅はゾーン幅とみなす (ゾーンをバーにフィットさせる前提)
    ratio = (right - left + 1) / float(w)
    return max(0.0, min(1.0, ratio))
