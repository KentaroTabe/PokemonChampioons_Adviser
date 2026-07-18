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
        import easyocr
        print("[vision.ocr] Loading EasyOCR model...")
        _reader = easyocr.Reader(["ja", "en"], gpu=True)
        print("[vision.ocr] EasyOCR model loaded.")
    return _reader


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
    kwargs = {"detail": 0}
    if allowlist:
        kwargs["allowlist"] = allowlist
    res = reader.readtext(processed, **kwargs)
    return "".join(res).replace(" ", "") if res else ""


def read_zone_text(img, zone, mode="panel", allowlist: Optional[str] = None,
                   val_min=170) -> str:
    """ゾーンを切り出してOCR。Vision利用時は前処理なしで生画像を読む"""
    from vision.zones import crop
    c = crop(img, zone)
    if c is None:
        return ""
    if _get_apple_vision():
        langs = ("en-US",) if _is_ascii_allowlist(allowlist) else ("ja-JP", "en-US")
        return _apply_ascii_allowlist(apple_ocr_text(c, scale=2.0, langs=langs),
                                      allowlist)
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
    m = re.search(r"(\d+)\s*[/1lI|]\s*(\d+)", text)
    if m:
        cur, mx = int(m.group(1)), int(m.group(2))
        if 0 < mx <= 999 and cur <= mx * 2:
            return (min(cur, mx), mx)
    digits = re.sub(r"\D", "", text)
    # 2桁のみ ("18") は「1/8」か「8/8の誤読」か判別不能なので採用しない
    if len(digits) >= 3:
        if len(digits) % 2 == 0:
            half = len(digits) // 2
            cur, mx = int(digits[:half]), int(digits[half:])
            if 0 < mx <= 999 and cur <= mx:
                return (cur, mx)
        else:
            half = len(digits) // 2
            cur, mx = int(digits[:half]), int(digits[half + 1:])
            if 0 < mx <= 999 and cur <= mx:
                return (cur, mx)
    return None


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
