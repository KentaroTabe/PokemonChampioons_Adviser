import cv2
import numpy as np
import easyocr
import re
import os
import warnings
import time
from pathlib import Path

# Mac(MPS)環境での不要な警告文をミュート
warnings.filterwarnings("ignore", category=UserWarning, module="torch.utils.data.dataloader")

# 外部ファイルから状態管理クラスと辞書を読み込む
from battle_state import battle_state, EVENT_DICTIONARY

print("Loading EasyOCR model...")
reader = easyocr.Reader(['ja', 'en'], gpu=True)
print("EasyOCR model loaded.")

# ==============================================================================
# 設定・定数・テンプレート読み込み
# ==============================================================================

# 濁点・半濁点を「清音」に強制変換するマッピング (メッセージパース用)
SEION_MAPPING = str.maketrans(
    'ガギグゲゴザジズゼゾダヂヅデドバビブベボパピプペポヴ',
    'カキクケコサシスセソタチツテトハヒフヘホハヒフヘホウ'
)

# クロプゾーン定義
CROP_ZONES = {
    # HPバーの領域
    "my_hp": {"y_start": 665, "y_end": 715, "x_start": 130, "x_end": 280},
    "opponent_hp": {"y_start": 70, "y_end": 120, "x_start": 1120, "x_end": 1250},
    
    # アイコンの領域 (マルチスケール探索のため、少し広めに確保)
    "my_icon": {"y_start": 580, "y_end": 670, "x_start": 20, "x_end": 120},
    "opponent_icon": {"y_start": 0, "y_end": 90, "x_start": 950, "x_end": 1060},
    
    # 名前の領域 (OCR用)
    "my_name": {"y_start": 605, "y_end": 645, "x_start": 105, "x_end": 260},
    "opponent_name": {"y_start": 15, "y_end": 50, "x_start": 1040, "x_end": 1210},
    
    # ウィンドウ検知用とOCR抽出用
    "message_window_detect": {"y_start": 600, "y_end": 640, "x_start": 400, "x_end": 800},
    "message_window_ocr": {"y_start": 510, "y_end": 660, "x_start": 120, "x_end": 900},
    "left_popup_detect": {"y_start": 320, "y_end": 420, "x_start": 100, "x_end": 350},
    "left_popup_ocr": {"y_start": 300, "y_end": 440, "x_start": 100, "x_end": 400},
    "right_popup_detect": {"y_start": 320, "y_end": 420, "x_start": 930, "x_end": 1180},
    "right_popup_ocr": {"y_start": 300, "y_end": 440, "x_start": 850, "x_end": 1180},
    
    # 選出画面のアイコン領域 (画像レイアウトに合わせて修正)
    "selection_my_party": [
        {"y_start": 120, "y_end": 200, "x_start": 60, "x_end": 160},
        {"y_start": 205, "y_end": 285, "x_start": 60, "x_end": 160},
        {"y_start": 290, "y_end": 370, "x_start": 60, "x_end": 160},
        {"y_start": 375, "y_end": 455, "x_start": 60, "x_end": 160},
        {"y_start": 460, "y_end": 540, "x_start": 60, "x_end": 160},
        {"y_start": 545, "y_end": 625, "x_start": 60, "x_end": 160},
    ],
    "selection_opp_party": [
        {"y_start": 120, "y_end": 200, "x_start": 990, "x_end": 1090},
        {"y_start": 205, "y_end": 285, "x_start": 990, "x_end": 1090},
        {"y_start": 290, "y_end": 370, "x_start": 990, "x_end": 1090},
        {"y_start": 375, "y_end": 455, "x_start": 990, "x_end": 1090},
        {"y_start": 460, "y_end": 540, "x_start": 990, "x_end": 1090},
        {"y_start": 545, "y_end": 625, "x_start": 990, "x_end": 1090},
    ],
    # 自パーティの種族名OCR領域 (アイコン右側の白文字部分、85pxピッチ)
    "selection_my_species_name": [
        {"y_start": 122 + i * 85, "y_end": 150 + i * 85, "x_start": 105, "x_end": 260}
        for i in range(6)
    ],
}

def _make_type_zones(x_start, x_end, y0=110, y1=146, pitch=85, count=6):
    """相手パーティのタイプアイコン領域を6枠分生成する (1280x720基準)"""
    return [
        {"y_start": y0 + i * pitch, "y_end": y1 + i * pitch, "x_start": x_start, "x_end": x_end}
        for i in range(count)
    ]

# 相手パーティのタイプアイコン領域 (アイコン右側、1枠につき最大2種)
CROP_ZONES["selection_opp_type1"] = _make_type_zones(1158, 1198)
CROP_ZONES["selection_opp_type2"] = _make_type_zones(1198, 1235)

TEMPLATE_DIR = Path('images/templetes')
TYPE_TEMPLATE_DIR = Path('images/type_templates')


# ハイブリッド照合用の特徴量サイズ
DHASH_SIZE = 16           # dHash の解像度 (16x16 -> 256bit)
HIST_BINS = [18, 8]       # HSV(H, S) の色ヒストグラムのビン数
NORM_ICON_SIZE = 64       # 特徴量計算時にアイコンを正規化する一辺のサイズ

# 各特徴量ごとに事前計算したテンプレートを保持する
PRECOMPILED_TEMPLATES = {}   # {species_id: [(bgr, mask), ...]}  マルチスケールTM用
TEMPLATE_FEATURES = {}       # {species_id: {"hist": ndarray, "dhash": ndarray}}


def _compute_color_histogram(bgr, mask=None):
    """HSVの色相・彩度から正規化ヒストグラムを計算する"""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], mask, HIST_BINS, [0, 180, 0, 256])
    cv2.normalize(hist, hist, 0, 1, cv2.NORM_MINMAX)
    return hist


def _compute_dhash(bgr, mask=None):
    """difference hash (dHash) を計算しビット配列で返す"""
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    # マスクがある場合、背景(透明部分)を平均輝度で埋めてエッジノイズを抑える
    if mask is not None:
        valid = gray[mask > 0]
        fill = int(np.mean(valid)) if valid.size > 0 else 0
        gray = np.where(mask > 0, gray, fill).astype(np.uint8)
    # (DHASH_SIZE+1) x DHASH_SIZE にリサイズし、横方向の隣接差分でハッシュ化
    resized = cv2.resize(gray, (DHASH_SIZE + 1, DHASH_SIZE), interpolation=cv2.INTER_AREA)
    diff = resized[:, 1:] > resized[:, :-1]
    return diff.flatten()


def load_templates():
    if not TEMPLATE_DIR.exists():
        print(f"[Warning] Template directory '{TEMPLATE_DIR}' not found. Matching disabled.")
        return
    valid_exts = ['.png', '.jpg', '.jpeg']
    
    for file_path in TEMPLATE_DIR.iterdir():
        if file_path.suffix.lower() in valid_exts:
            if file_path.stem == '0': # はてなマークの除外
                continue
                
            tmpl = cv2.imread(str(file_path), cv2.IMREAD_UNCHANGED)
            if tmpl is not None and len(tmpl.shape) == 3 and tmpl.shape[2] == 4:
                alpha = tmpl[:, :, 3]
                x, y, w, h = cv2.boundingRect(alpha)
                if w > 0 and h > 0:
                    cropped = tmpl[y:y+h, x:x+w]
                    compiled_list = []
                    
                    for target_h in [36, 42, 48, 54, 60, 66]:
                        scale = target_h / h
                        target_w = int(w * scale)
                        if target_w > 0:
                            # ドット絵の輪郭とアルファチャンネルを正確に保持するため INTER_NEAREST を使用[cite: 1]
                            resized = cv2.resize(cropped, (target_w, target_h), interpolation=cv2.INTER_NEAREST)
                            bgr = resized[:, :, :3]
                            mask = resized[:, :, 3]
                            compiled_list.append((bgr, mask))
                            
                    PRECOMPILED_TEMPLATES[file_path.stem] = compiled_list

                    # --- ハイブリッド照合用の特徴量を事前計算 ---
                    # アイコン全体を固定サイズに正規化してから色ヒストグラム / dHash を求める
                    norm = cv2.resize(cropped, (NORM_ICON_SIZE, NORM_ICON_SIZE), interpolation=cv2.INTER_AREA)
                    norm_bgr = norm[:, :, :3]
                    norm_mask = norm[:, :, 3]
                    TEMPLATE_FEATURES[file_path.stem] = {
                        "hist": _compute_color_histogram(norm_bgr, norm_mask),
                        "dhash": _compute_dhash(norm_bgr, norm_mask),
                    }
                    
    print(f"Loaded {len(PRECOMPILED_TEMPLATES)} templates for matching "
          f"({len(TEMPLATE_FEATURES)} feature sets).")


# タイプアイコン用の特徴量 (種族アイコンとは別管理)
TYPE_TEMPLATE_FEATURES = {}  # {type_name: {"hist": ndarray, "dhash": ndarray}}


def load_type_templates():
    """
    images/type_templates/ から各タイプアイコンの特徴量を事前計算する。
    テンプレートはアルファ抜き済みの正方形画像だが、念のためクエリ側と
    同じ _extract_type_icon_foreground を通して特徴量を揃える。
    """
    if not TYPE_TEMPLATE_DIR.exists():
        print(f"[Warning] Type template directory '{TYPE_TEMPLATE_DIR}' not found. Type matching disabled.")
        return
    valid_exts = ['.png', '.jpg', '.jpeg']

    for file_path in TYPE_TEMPLATE_DIR.iterdir():
        if file_path.suffix.lower() not in valid_exts:
            continue

        tmpl = cv2.imread(str(file_path), cv2.IMREAD_UNCHANGED)
        if tmpl is None or len(tmpl.shape) != 3 or tmpl.shape[2] != 4:
            continue

        alpha = tmpl[:, :, 3]
        x, y, w, h = cv2.boundingRect(alpha)
        if w == 0 or h == 0:
            continue

        cropped = tmpl[y:y + h, x:x + w]
        bgr = cropped[:, :, :3]
        mask = cropped[:, :, 3]

        norm_bgr = cv2.resize(bgr, (NORM_ICON_SIZE, NORM_ICON_SIZE), interpolation=cv2.INTER_AREA)
        norm_mask = cv2.resize(mask, (NORM_ICON_SIZE, NORM_ICON_SIZE), interpolation=cv2.INTER_NEAREST)

        TYPE_TEMPLATE_FEATURES[file_path.stem] = {
            "hist": _compute_color_histogram(norm_bgr, norm_mask),
            "dhash": _compute_dhash(norm_bgr, norm_mask),
        }

    print(f"Loaded {len(TYPE_TEMPLATE_FEATURES)} type templates for matching.")


def _extract_type_icon_foreground(crop_img):
    """
    タイプアイコン(正方形、単色パネル+白抜き模様)専用の前景抽出。
    四隅(パネルの外側/角丸部分)から背景色を推定する。
    ポケモンアイコン用の _extract_icon_foreground は最頻色ベースだが、
    タイプアイコンは白模様の面積が大きい場合に誤検出するため、
    四隅ベースの背景推定に固定する。
    """
    if crop_img is None or crop_img.size == 0:
        return None, None

    h, w = crop_img.shape[:2]
    corner = max(2, min(h, w) // 6)
    corners = np.vstack([
        crop_img[:corner, :corner].reshape(-1, 3),
        crop_img[:corner, -corner:].reshape(-1, 3),
        crop_img[-corner:, :corner].reshape(-1, 3),
        crop_img[-corner:, -corner:].reshape(-1, 3),
    ])
    bg_color = np.median(corners, axis=0)

    dist = np.linalg.norm(crop_img.astype(np.float32) - bg_color, axis=2)
    fg_mask = (dist > 50).astype(np.uint8) * 255

    kernel = np.ones((2, 2), np.uint8)
    fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_CLOSE, kernel)

    coords = cv2.findNonZero(fg_mask)
    if coords is None:
        return None, None
    x, y, bw, bh = cv2.boundingRect(coords)
    if bw < 4 or bh < 4:
        return None, None

    icon = crop_img[y:y + bh, x:x + bw]
    icon_mask = fg_mask[y:y + bh, x:x + bw]

    norm_bgr = cv2.resize(icon, (NORM_ICON_SIZE, NORM_ICON_SIZE), interpolation=cv2.INTER_AREA)
    norm_mask = cv2.resize(icon_mask, (NORM_ICON_SIZE, NORM_ICON_SIZE), interpolation=cv2.INTER_NEAREST)
    return norm_bgr, norm_mask


def match_type_icon(crop_img, threshold=0.5):
    """
    タイプアイコン(正方形、色パネル+白抜き模様)をハイブリッド照合で判定する。
    ポケモンアイコンと異なりテンプレートマッチングは行わず、
    色ヒストグラム + dHash のみで判定する(単色パネルのため十分な精度が出るため)。
    戻り値: タイプ名の文字列 または None
    """
    if crop_img is None or crop_img.size == 0 or not TYPE_TEMPLATE_FEATURES:
        return None

    query_bgr, query_mask = _extract_type_icon_foreground(crop_img)
    if query_bgr is None:
        query_bgr = cv2.resize(crop_img, (NORM_ICON_SIZE, NORM_ICON_SIZE), interpolation=cv2.INTER_AREA)
        query_mask = None

    query_hist = _compute_color_histogram(query_bgr, query_mask)
    query_dhash = _compute_dhash(query_bgr, query_mask)
    dhash_bits = query_dhash.size

    best_name = None
    best_score = -1.0
    for name, feats in TYPE_TEMPLATE_FEATURES.items():
        hist_corr = cv2.compareHist(query_hist, feats["hist"], cv2.HISTCMP_CORREL)
        hist_score = max(0.0, min(1.0, hist_corr))

        hamming = int(np.count_nonzero(query_dhash != feats["dhash"]))
        dhash_score = 1.0 - (hamming / dhash_bits)

        # 色が近いタイプ(ほのお/エスパー等)の混同を避けるため、形状情報(dHash)を重視
        total = 0.3 * hist_score + 0.7 * dhash_score
        if total > best_score:
            best_score = total
            best_name = name

    if DEBUG_MATCH:
        print(f"  [Type Match Debug] best={best_name} score={best_score:.3f}")

    return best_name if best_score >= threshold else None


# 起動時にテンプレートをロード
load_templates()
load_type_templates()


# ==============================================================================
# 画像処理・抽出・マッチング関数
# ==============================================================================

icon_cache = {
    "my": {"last_image": None, "species": None},
    "opp": {"last_image": None, "species": None}
}

# ハイブリッド照合の各特徴量に対する重み (合計 1.0)
MATCH_WEIGHTS = {
    "template": 0.4,   # マルチスケール TM_CCORR_NORMED
    "hist": 0.3,       # 色ヒストグラム相関
    "dhash": 0.3,      # dHash ハミング類似度
}
# 総合スコアがこの値未満の場合は「特定できず」とする
HYBRID_ACCEPT_THRESHOLD = 0.55
# デバッグログを出したい場合 True
DEBUG_MATCH = False


def _extract_icon_foreground(crop_img):
    """
    アイコン領域から前景(ポケモンのドット絵)を推定して切り出す。
    画像内の最頻色を背景色とみなし、それとの色差で前景マスクを作成。
    選出画面の色付きパネル上でも安定して動作する。
    戻り値: (正規化済みBGR, 正規化済みマスク) または (None, None)
    """
    if crop_img is None or crop_img.size == 0:
        return None, None

    h, w = crop_img.shape[:2]
    # 量子化して最頻色を背景色と推定
    pixels = crop_img.reshape(-1, 3)
    quant = pixels // 32 * 32
    unique, counts = np.unique(quant, axis=0, return_counts=True)
    bg_color = unique[counts.argmax()].astype(np.float32)

    # 背景色との色距離で前景マスクを作成
    dist = np.linalg.norm(crop_img.astype(np.float32) - bg_color, axis=2)
    fg_mask = (dist > 40).astype(np.uint8) * 255

    # ノイズ除去
    kernel = np.ones((3, 3), np.uint8)
    fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_OPEN, kernel)
    fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_CLOSE, kernel)

    coords = cv2.findNonZero(fg_mask)
    if coords is None:
        return None, None
    x, y, bw, bh = cv2.boundingRect(coords)
    if bw < 8 or bh < 8:
        return None, None

    icon = crop_img[y:y + bh, x:x + bw]
    icon_mask = fg_mask[y:y + bh, x:x + bw]

    norm_bgr = cv2.resize(icon, (NORM_ICON_SIZE, NORM_ICON_SIZE), interpolation=cv2.INTER_AREA)
    norm_mask = cv2.resize(icon_mask, (NORM_ICON_SIZE, NORM_ICON_SIZE), interpolation=cv2.INTER_NEAREST)
    return norm_bgr, norm_mask


def _template_score(crop_img, tmpls):
    """1種族のマルチスケールテンプレート群に対する最良TMスコアを返す"""
    best = -1.0
    for tmpl_bgr, alpha_mask in tmpls:
        if tmpl_bgr.shape[0] > crop_img.shape[0] or tmpl_bgr.shape[1] > crop_img.shape[1]:
            continue
        try:
            res = cv2.matchTemplate(crop_img, tmpl_bgr, cv2.TM_CCORR_NORMED, mask=alpha_mask)
            _, max_val, _, _ = cv2.minMaxLoc(res)
            if np.isfinite(max_val) and max_val > best:
                best = max_val
        except Exception:
            continue
    return best if best >= 0 else 0.0


def _hybrid_match(crop_img, search_list=None):
    """
    テンプレートマッチング + 色ヒストグラム + dHash を統合したハイブリッド照合。
    戻り値: (best_species_id, best_total_score, ソート済み候補リスト)
    """
    if not PRECOMPILED_TEMPLATES or crop_img is None or crop_img.size == 0:
        return None, 0.0, []

    # 対象種族の絞り込み
    candidate_ids = list(PRECOMPILED_TEMPLATES.keys())
    if search_list is not None and len(search_list) > 0:
        candidate_ids = [k for k in candidate_ids if k in search_list]
    if not candidate_ids:
        return None, 0.0, []

    # クエリ画像の特徴量を計算
    query_bgr, query_mask = _extract_icon_foreground(crop_img)
    if query_bgr is None:
        # 前景抽出に失敗した場合は crop 全体をそのまま使う
        query_bgr = cv2.resize(crop_img, (NORM_ICON_SIZE, NORM_ICON_SIZE), interpolation=cv2.INTER_AREA)
        query_mask = None

    query_hist = _compute_color_histogram(query_bgr, query_mask)
    query_dhash = _compute_dhash(query_bgr, query_mask)
    dhash_bits = query_dhash.size

    results = []
    for name in candidate_ids:
        feats = TEMPLATE_FEATURES.get(name)
        if feats is None:
            continue

        # 1) 色ヒストグラム相関 (0..1 にクリップ)
        hist_corr = cv2.compareHist(query_hist, feats["hist"], cv2.HISTCMP_CORREL)
        hist_score = max(0.0, min(1.0, hist_corr))

        # 2) dHash ハミング類似度
        hamming = int(np.count_nonzero(query_dhash != feats["dhash"]))
        dhash_score = 1.0 - (hamming / dhash_bits)

        # 3) テンプレートマッチング (元の crop に対して実施)
        tmpl_score = _template_score(crop_img, PRECOMPILED_TEMPLATES[name])

        total = (MATCH_WEIGHTS["hist"] * hist_score
                 + MATCH_WEIGHTS["dhash"] * dhash_score
                 + MATCH_WEIGHTS["template"] * tmpl_score)
        results.append((name, total, hist_score, dhash_score, tmpl_score))

    if not results:
        return None, 0.0, []

    results.sort(key=lambda r: r[1], reverse=True)
    best = results[0]

    if DEBUG_MATCH:
        print("  [Match Debug] Top candidates:")
        for name, total, hs, ds, ts in results[:3]:
            print(f"    id={name} total={total:.3f} (hist={hs:.3f}, dhash={ds:.3f}, tmpl={ts:.3f})")

    return best[0], best[1], results


def match_pokemon_icon(crop_img, side, search_list=None, threshold=None):
    """
    ハイブリッド照合でポケモンの種族IDを特定する。
    threshold を指定した場合は総合スコアの受理閾値として使用する。
    """
    if crop_img is None or crop_img.size == 0:
        return None

    accept_threshold = threshold if threshold is not None else HYBRID_ACCEPT_THRESHOLD

    cached = icon_cache.get(side)
    if side and search_list is not None and cached["last_image"] is not None:
        if crop_img.shape == cached["last_image"].shape:
            diff = cv2.absdiff(crop_img, cached["last_image"])
            if np.mean(diff) < 2.0:
                return cached["species"]

    best_id, best_score, _ = _hybrid_match(crop_img, search_list)
    result = best_id if (best_id is not None and best_score >= accept_threshold) else None

    if side:
        icon_cache[side]["last_image"] = crop_img.copy()
        icon_cache[side]["species"] = result

    return result

def is_battle_scene(img):
    my_hp_zone = CROP_ZONES["my_hp"]
    crop = img[my_hp_zone["y_start"]:my_hp_zone["y_end"], my_hp_zone["x_start"]:my_hp_zone["x_end"]]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)
    return cv2.countNonZero(thresh) > 30

def is_selection_screen(img):
    """
    選出画面特有のUIカラー（相手パーティ枠の鮮やかなピンク色）を検知し、
    暗転中や技アニメーション中に重いマッチング処理が走るのを防ぐ高速判定関数
    """
    zone = CROP_ZONES["selection_opp_party"][0]
    crop = img[zone["y_start"]:zone["y_end"], zone["x_start"]:zone["x_end"]]
    if crop.size == 0:
        return False
        
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    # 選出画面のピンク〜赤系パネルを検出するマスク[cite: 1]
    lower_pink = np.array([140, 80, 80])
    upper_pink = np.array([180, 255, 255])
    lower_red = np.array([0, 80, 80])
    upper_red = np.array([10, 255, 255])
    
    mask1 = cv2.inRange(hsv, lower_pink, upper_pink)
    mask2 = cv2.inRange(hsv, lower_red, upper_red)
    mask = cv2.bitwise_or(mask1, mask2)
    
    # 該当する色が一定ピクセル以上あれば選出画面とみなす
    return cv2.countNonZero(mask) > 500

def analyze_selection_screen(img):
    """
    選出画面を解析する。
    - 自分のパーティ: ポケモンアイコンのテンプレートマッチングで種族IDを特定
    - 相手のパーティ: 種族の特定は行わず、タイプアイコン(1〜2個)からタイプ情報のみ取得
    """
    if len(battle_state.player_party) == 6 and len(battle_state.opponent_party) == 6:
        return

    my_party = battle_state.player_party.copy()
    opp_party = battle_state.opponent_party.copy()

    selection_threshold = 0.5

    # --- 相手パーティ: タイプアイコンから判定 ---
    if len(opp_party) < 6:
        type1_zones = CROP_ZONES["selection_opp_type1"]
        type2_zones = CROP_ZONES["selection_opp_type2"]
        for i in range(len(type1_zones)):
            z1 = type1_zones[i]
            z2 = type2_zones[i]
            crop1 = img[z1["y_start"]:z1["y_end"], z1["x_start"]:z1["x_end"]]
            crop2 = img[z2["y_start"]:z2["y_end"], z2["x_start"]:z2["x_end"]]

            type1 = match_type_icon(crop1)
            type2 = match_type_icon(crop2)

            types = [t for t in [type1, type2] if t]
            if types:
                entry = {"types": types}
                # 既に同一枠を登録済みでなければ追加 (簡易的にインデックスで管理)
                if i >= len(opp_party):
                    opp_party.append(entry)
                elif not opp_party[i]:
                    opp_party[i] = entry

    # --- 自分のパーティ: 種族名を文字認識(OCR)で取得 ---
    # 海外プレイヤーのポケモンは日本語表示されないため種族アイコンでの判定が難しい相手側とは異なり、
    # 自分のポケモンは常に日本語の種族名が表示されるため、OCRで直接名前を読み取る。
    if len(my_party) < 6:
        name_zones = CROP_ZONES["selection_my_species_name"]
        for i, zone in enumerate(name_zones):
            if i < len(my_party):
                continue
            crop = img[zone["y_start"]:zone["y_end"], zone["x_start"]:zone["x_end"]]
            processed = preprocess_name_for_ocr(crop)
            if processed is None:
                continue
            ocr_res = reader.readtext(processed, detail=0)
            if ocr_res:
                species_name = "".join(ocr_res).replace(" ", "")
                if species_name:
                    my_party.append(species_name)

    if len(my_party) > len(battle_state.player_party) or len(opp_party) > len(battle_state.opponent_party):
        battle_state.update_parties(my_party, opp_party)


def preprocess_name_for_ocr(image):
    if image is None or image.size == 0: return None
    resized = cv2.resize(image, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC)
    hsv = cv2.cvtColor(resized, cv2.COLOR_BGR2HSV)
    lower_white = np.array([0, 0, 150], dtype=np.uint8)
    upper_white = np.array([180, 60, 255], dtype=np.uint8)
    mask = cv2.inRange(hsv, lower_white, upper_white)
    # 文字の縁取り(黒フチ)により内部に隙間が生じるため、close処理で穴埋めして滑らかにする
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    inverted = cv2.bitwise_not(mask)
    padded = cv2.copyMakeBorder(inverted, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
    return padded

def preprocess_my_hp_for_ocr(image):
    if image is None or image.size == 0: return None
    resized = cv2.resize(image, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC)
    lower_white = np.array([180, 180, 180], dtype=np.uint8)
    upper_white = np.array([255, 255, 255], dtype=np.uint8)
    mask = cv2.inRange(resized, lower_white, upper_white)
    inverted = cv2.bitwise_not(mask)
    padded = cv2.copyMakeBorder(inverted, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
    return padded

def preprocess_opp_hp_for_ocr(image):
    if image is None or image.size == 0: return None
    resized = cv2.resize(image, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC)
    lower_white = np.array([140, 140, 140], dtype=np.uint8)
    upper_white = np.array([255, 255, 255], dtype=np.uint8)
    mask = cv2.inRange(resized, lower_white, upper_white)
    kernel = np.ones((2, 2), np.uint8)
    mask = cv2.dilate(mask, kernel, iterations=1)
    coords = cv2.findNonZero(mask)
    if coords is not None:
        x, y, w, h = cv2.boundingRect(coords)
        mask = mask[y:y+h, x:x+w]
    inverted = cv2.bitwise_not(mask)
    padded = cv2.copyMakeBorder(inverted, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
    return padded

def preprocess_message_window(image, save_debug=True):
    if image is None or image.size == 0: return None
    resized = cv2.resize(image, None, fx=2.5, fy=2.5, interpolation=cv2.INTER_CUBIC)
    gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 140, 255, cv2.THRESH_BINARY)
    kernel = np.ones((2, 2), np.uint8)
    mask = cv2.dilate(thresh, kernel, iterations=1)
    inverted = cv2.bitwise_not(mask)
    padded = cv2.copyMakeBorder(inverted, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
    
    if save_debug:
        os.makedirs("debug_crops", exist_ok=True)
        filename = f"debug_crops/msg_{int(time.time()*1000)}.jpg"
        cv2.imwrite(filename, padded)
    
    return padded

class WindowDetector:
    def __init__(self, detect_func, stable_frames_threshold=3, diff_pixel_threshold=200, reset_pixel_threshold=1000, cooldown_frames=30):
        self.detect_func = detect_func
        self.is_open = False
        self.last_text_crop = None
        self.stable_frames = 0
        self.ocr_done = False
        self.stable_frames_threshold = stable_frames_threshold
        self.diff_pixel_threshold = diff_pixel_threshold
        self.reset_pixel_threshold = reset_pixel_threshold 
        self.cooldown_frames = cooldown_frames 
        self.current_cooldown = 0 

    def update(self, img):
        is_now_open, crop_ocr = self.detect_func(img)
        trigger_ocr = False
        stable_img = None

        if is_now_open:
            curr_text_crop = preprocess_message_window(crop_ocr, save_debug=False)
            if curr_text_crop is None:
                return False, None

            if self.current_cooldown > 0:
                self.current_cooldown -= 1
                self.last_text_crop = curr_text_crop
                return False, None

            if not self.is_open:
                self.is_open = True
                self.ocr_done = False
                self.stable_frames = 0
            else:
                diff = cv2.absdiff(self.last_text_crop, curr_text_crop)
                changed_pixels = cv2.countNonZero(diff)
                
                if self.ocr_done:
                    if changed_pixels > self.reset_pixel_threshold:
                        self.ocr_done = False
                        self.stable_frames = 0
                else:
                    if changed_pixels <= self.diff_pixel_threshold:
                        self.stable_frames += 1
                    else:
                        self.stable_frames = 0 
                        
                    if self.stable_frames >= self.stable_frames_threshold:
                        trigger_ocr = True
                        self.ocr_done = True
                        stable_img = curr_text_crop
                        self.current_cooldown = self.cooldown_frames 
                        
            self.last_text_crop = curr_text_crop
        else:
            self.is_open = False
            self.last_text_crop = None
            self.ocr_done = False 
            self.current_cooldown = 0 
            
        return trigger_ocr, stable_img


def detect_message_window(img):
    detect_zone = CROP_ZONES["message_window_detect"]
    crop_detect = img[detect_zone["y_start"]:detect_zone["y_end"], detect_zone["x_start"]:detect_zone["x_end"]]
    gray = cv2.cvtColor(crop_detect, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 80, 255, cv2.THRESH_BINARY_INV) 
    dark_ratio = cv2.countNonZero(thresh) / (crop_detect.shape[0] * crop_detect.shape[1])
    
    if dark_ratio > 0.5: 
        ocr_zone = CROP_ZONES["message_window_ocr"]
        crop_ocr = img[ocr_zone["y_start"]:ocr_zone["y_end"], ocr_zone["x_start"]:ocr_zone["x_end"]]
        return True, crop_ocr
    return False, None

def detect_left_popup_window(img):
    detect_zone = CROP_ZONES["left_popup_detect"]
    crop_detect = img[detect_zone["y_start"]:detect_zone["y_end"], detect_zone["x_start"]:detect_zone["x_end"]]
    gray = cv2.cvtColor(crop_detect, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 110, 255, cv2.THRESH_BINARY_INV) 
    dark_ratio = cv2.countNonZero(thresh) / (crop_detect.shape[0] * crop_detect.shape[1])
    
    if dark_ratio > 0.4: 
        ocr_zone = CROP_ZONES["left_popup_ocr"]
        crop_ocr = img[ocr_zone["y_start"]:ocr_zone["y_end"], ocr_zone["x_start"]:ocr_zone["x_end"]]
        return True, crop_ocr
    return False, None

def detect_right_popup_window(img):
    detect_zone = CROP_ZONES["right_popup_detect"]
    crop_detect = img[detect_zone["y_start"]:detect_zone["y_end"], detect_zone["x_start"]:detect_zone["x_end"]]
    gray = cv2.cvtColor(crop_detect, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 110, 255, cv2.THRESH_BINARY_INV) 
    dark_ratio = cv2.countNonZero(thresh) / (crop_detect.shape[0] * crop_detect.shape[1])
    
    if dark_ratio > 0.4: 
        ocr_zone = CROP_ZONES["right_popup_ocr"]
        crop_ocr = img[ocr_zone["y_start"]:ocr_zone["y_end"], ocr_zone["x_start"]:ocr_zone["x_end"]]
        return True, crop_ocr
    return False, None


# グローバルインスタンスの作成
message_detector = WindowDetector(detect_message_window)
left_popup_detector = WindowDetector(detect_left_popup_window, stable_frames_threshold=2, diff_pixel_threshold=150, reset_pixel_threshold=800, cooldown_frames=30)
right_popup_detector = WindowDetector(detect_right_popup_window, stable_frames_threshold=2, diff_pixel_threshold=150, reset_pixel_threshold=800, cooldown_frames=30)

def parse_text(text, source_type="message"):
    if not text:
        return
        
    cleaned_text = re.sub(r'[^ぁ-んァ-ン一-龥ー]', '', text)
        
    if source_type == "message":
        if cleaned_text == battle_state.last_message: return
        battle_state.last_message = cleaned_text
    elif source_type == "left_popup":
        if cleaned_text == battle_state.last_left_popup: return
        battle_state.last_left_popup = cleaned_text
    elif source_type == "right_popup":
        if cleaned_text == battle_state.last_right_popup: return
        battle_state.last_right_popup = cleaned_text

    print(f"[Event Parser] {source_type} を解析: {cleaned_text}")
    text_seion = cleaned_text.translate(SEION_MAPPING)
    
    event_triggered = False
    for category, events in EVENT_DICTIONARY.items():
        for event_name, event_def in events.items():
            if all(keyword.translate(SEION_MAPPING) in text_seion for keyword in event_def["keywords"]):
                print(f"  -> イベント発火: {event_name}")
                battle_state.apply_event(event_def, text, source_type=source_type)
                event_triggered = True
                break
        if event_triggered:
            break

def process_frame(img, process_basic_info=True, process_message=True):
    original_h, original_w = img.shape[:2]
    if original_w != 1280 or original_h != 720:
        img = cv2.resize(img, (1280, 720))

    # --- シーン判定ルーティングの改善 ---
    is_battle = is_battle_scene(img)
    window_detected = False

    if not is_battle:
        # HPバーが見えない場合、選出画面かどうかの高速チェックを行う[cite: 1]
        if is_selection_screen(img):
            if process_basic_info:
                analyze_selection_screen(img)
            return battle_state.to_dict(), False
        else:
            # アニメーション中や暗転などの場合は重い処理をスキップ
            # メッセージ検知のみ継続させたい場合は以降の処理を許可する設計も可能ですが、
            # 今回はHP等基本情報の更新は行わずにリターンします
            pass

    result_data = {}

    # 1. 基本情報の抽出 (バトル画面中のみ)
    if is_battle and process_basic_info:
        # Opponent: 種族の特定は行わず、選出画面で取得したタイプ情報のみ利用する。
        # (相手ポケモンのアイコンマッチングは廃止。海外プレイヤー対策も兼ねる)
        result_data["opp_species"] = None

        opp_name_zone = CROP_ZONES["opponent_name"]

        crop_opp_name = img[opp_name_zone["y_start"]:opp_name_zone["y_end"], opp_name_zone["x_start"]:opp_name_zone["x_end"]]
        processed_opp_name = preprocess_name_for_ocr(crop_opp_name)
        # OCRから表示名（ニックネーム等）を独立して取得[cite: 1]
        if processed_opp_name is not None:
            name_res = reader.readtext(processed_opp_name, detail=0)
            if name_res:
                result_data["opp_display_name"] = "".join(name_res).replace(" ", "")

        # My Name: 自パーティの種族名は文字認識(OCR)で取得する。
        # ニックネームが付いていない場合、表示名=種族名となるためそのまま種族名として扱う。
        # (種族名と個体名は概念として別管理だが、取得元は同じOCR結果を利用する)
        my_name_zone = CROP_ZONES["my_name"]
        crop_my_name = img[my_name_zone["y_start"]:my_name_zone["y_end"], my_name_zone["x_start"]:my_name_zone["x_end"]]
        processed_my_name = preprocess_name_for_ocr(crop_my_name)
        if processed_my_name is not None:
            name_res = reader.readtext(processed_my_name, detail=0)
            if name_res:
                display_name = "".join(name_res).replace(" ", "")
                result_data["my_display_name"] = display_name
                result_data["my_species"] = display_name
                
        # Opponent HP
        opp_hp_zone = CROP_ZONES["opponent_hp"]
        crop_opp_hp = img[opp_hp_zone["y_start"]:opp_hp_zone["y_end"], opp_hp_zone["x_start"]:opp_hp_zone["x_end"]]
        processed_opp_hp = preprocess_opp_hp_for_ocr(crop_opp_hp)
        if processed_opp_hp is not None:
            opp_hp_text_result = reader.readtext(processed_opp_hp, allowlist='0123456789%', detail=0)
            if opp_hp_text_result:
                text = "".join(opp_hp_text_result)
                digits = re.sub(r'\D', '', text)
                if digits:
                    hp_val = int(digits)
                    if hp_val > 100:
                        if str(hp_val).startswith('10') and len(str(hp_val)) == 3:
                            hp_val = 100
                        else:
                            hp_val = int(str(hp_val)[:-1])
                    if 0 <= hp_val <= 100:
                        result_data["opponent_hp_percent"] = hp_val
                        
        # My HP
        my_hp_zone = CROP_ZONES["my_hp"]
        crop_my_hp = img[my_hp_zone["y_start"]:my_hp_zone["y_end"], my_hp_zone["x_start"]:my_hp_zone["x_end"]]
        processed_my_hp = preprocess_my_hp_for_ocr(crop_my_hp)
        if processed_my_hp is not None:
            my_hp_text_result = reader.readtext(processed_my_hp, allowlist='0123456789/', detail=0)
            if my_hp_text_result:
                text = "".join(my_hp_text_result).replace(" ", "")
                current_hp = max_hp = -1
                match = re.search(r'(\d+)\D+(\d+)', text)
                if match:
                    c_hp, m_hp = int(match.group(1)), int(match.group(2))
                    if m_hp <= 999: current_hp, max_hp = c_hp, m_hp
                if current_hp == -1 or max_hp == -1:
                    digits = re.sub(r'\D', '', text)
                    if len(digits) >= 2:
                        half = len(digits) // 2
                        if len(digits) % 2 == 0:
                            current_hp, max_hp = int(digits[:half]), int(digits[half:])
                        else:
                            current_hp, max_hp = int(digits[:half]), int(digits[half+1:])
                if current_hp != -1 and max_hp != -1 and max_hp > 0:
                    current_hp = min(current_hp, max_hp)
                    result_data["my_hp_percent"] = int((current_hp / max_hp) * 100)
                    result_data["my_hp_raw"] = f"{current_hp}/{max_hp}"

        battle_state.update_basic_info(result_data)

    # 2. メッセージウィンドウの検知と処理 (アニメーション中も判定を継続)
    if process_message:
        trigger_ocr, stable_img = message_detector.update(img)
        if trigger_ocr and stable_img is not None:
            window_detected = True
            msg_result = reader.readtext(stable_img, detail=0)
            if msg_result:
                full_text = "".join(msg_result).replace(" ", "")
                parse_text(full_text, source_type="message")

        # 左ポップアップ
        trigger_left_ocr, stable_left_img = left_popup_detector.update(img)
        if trigger_left_ocr and stable_left_img is not None:
            window_detected = True
            left_result = reader.readtext(stable_left_img, detail=0)
            if left_result:
                full_text = "".join(left_result).replace(" ", "")
                parse_text(full_text, source_type="left_popup")

        # 右ポップアップ
        trigger_right_ocr, stable_right_img = right_popup_detector.update(img)
        if trigger_right_ocr and stable_right_img is not None:
            window_detected = True
            right_result = reader.readtext(stable_right_img, detail=0)
            if right_result:
                full_text = "".join(right_result).replace(" ", "")
                parse_text(full_text, source_type="right_popup")


    return battle_state.to_dict(), window_detected

def process_batch(target_path):
    target = Path(target_path)
    
    if target.is_file():
        start_time = time.time()
        img = cv2.imread(str(target))
        print(f"\n[->] Analyzing single image: {target.name}")
        for i in range(5):
            state, window = process_frame(img, process_basic_info=(i==0), process_message=True)
        battle_state.print_state()
        print(f"  [Time] 処理時間: {time.time() - start_time:.3f}秒")
        
    elif target.is_dir():
        valid_extensions = ['.png', '.jpg', '.jpeg']
        image_files = sorted([f for f in target.iterdir() if f.suffix.lower() in valid_extensions])
        
        for img_file in image_files:
            start_time = time.time()
            img = cv2.imread(str(img_file))
            print(f"\n[->] Processing frame: {img_file.name}")
            message_detector.is_open = False
            left_popup_detector.is_open = False
            right_popup_detector.is_open = False
            
            t_match_total = 0
            t_ocr_total = 0
            
            for i in range(5):
                t0 = time.time()
                state, window = process_frame(img, process_basic_info=(i==0), process_message=True)
                t1 = time.time()
                
                if i == 0:
                    t_match_total += (t1 - t0)
                else:
                    t_ocr_total += (t1 - t0)
                    
            if window:
                print("  [Info] ウィンドウを検知")
            battle_state.print_state()
            total_time = time.time() - start_time
            print(f"  [Time] 全体: {total_time:.3f}秒 (基本抽出/マッチング: {t_match_total:.3f}秒, ウィンドウ/OCR監視: {t_ocr_total:.3f}秒)")

def process_video(video_path):
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        print(f"Error: Could not open video {video_path}")
        return

    fps = cap.get(cv2.CAP_PROP_FPS)
    print(f"\n[->] Processing video at {fps} FPS: {Path(video_path).name}")
    
    frame_count = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
            
        frame_count += 1
        
        state, triggered_ocr = process_frame(
            frame, 
            process_basic_info=(frame_count % 30 == 0), 
            process_message=True
        )
        
        if triggered_ocr:
            print(f"  [Info] Frame {frame_count} ({frame_count/fps:.2f}秒): テキスト描画完了を検知、OCR実行")
            battle_state.print_state()
            
    cap.release()

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        target = Path(sys.argv[1])
        if target.suffix.lower() in ['.mp4', '.mov', '.avi']:
            process_video(target)
        else:
            process_batch(target)
    else:
        print("Usage: python advanced_extractor.py <image_or_video_or_directory_path>")