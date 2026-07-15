import cv2
import numpy as np
import easyocr
import re
import os
import argparse
import warnings
import difflib
from pathlib import Path

# Mac(MPS)環境での不要な警告文をミュート
warnings.filterwarnings("ignore", category=UserWarning, module="torch.utils.data.dataloader")

print("Loading EasyOCR model...")
reader = easyocr.Reader(['ja', 'en'], gpu=True)
print("EasyOCR model loaded.")

# ==============================================================================
# 設定・定数
# ==============================================================================

NAME_ALLOWLIST = 'ァアィイゥウェエォオカガキギクグケゲコゴサザシジスズセゼソゾタダチヂッツヅテデトドナニヌネノハバパヒビピフブプヘベペホボポマミムメモャヤュユョヨラリルレロヮワヰヱヲンヴー2Z' + 'oO0Qq♂♀'

# 濁点・半濁点を「清音」に強制変換するマッピング
SEION_MAPPING = str.maketrans(
    'ガギグゲゴザジズゼゾダヂヅデドバビブベボパピプペポヴ',
    'カキクケコサシスセソタチツテトハヒフヘホハヒフヘホウ'
)

# ロトム、フラエッテなどを追加
# POKEMON_ROSTER = [
#     "ペリッパー", "ハラバリー", "ヤバソチャ", "ブリジュラス", 
#     "ユキメノコ", "ラグラージ", "ハバタクカミ", "オーガポン",
#     "ロトム", "フラエッテ"
# ]

CROP_ZONES = {
    "my_hp": {"y_start": 665, "y_end": 715, "x_start": 130, "x_end": 280},
    "my_name": {"y_start": 608, "y_end": 648, "x_start": 100, "x_end": 300},
    "opponent_hp": {"y_start": 70, "y_end": 120, "x_start": 1120, "x_end": 1250},
    "opponent_name": {"y_start": 25, "y_end": 65, "x_start": 1040, "x_end": 1220}
}

# ==============================================================================
# 画像処理・抽出関数
# ==============================================================================

def preprocess_my_hp_for_ocr(image, name, save_debug=False):
    if image.size == 0: return None
    resized = cv2.resize(image, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC)
    lower_white = np.array([180, 180, 180], dtype=np.uint8)
    upper_white = np.array([255, 255, 255], dtype=np.uint8)
    mask = cv2.inRange(resized, lower_white, upper_white)
    inverted = cv2.bitwise_not(mask)
    padded = cv2.copyMakeBorder(inverted, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
    return padded

def preprocess_opp_hp_for_ocr(image, name, save_debug=False):
    """
    【相手HPのみ修正】
    1. inRangeで文字だけを抽出
    2. Dilationでかすれを防止
    3. Auto-cropで右寄せによる巨大な余白を消去し、文字を中央に配置
    """
    if image.size == 0: return None
    resized = cv2.resize(image, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC)
    
    # UIの黒帯やカラーのHPバーを無視して白テキストのみを抽出
    lower_white = np.array([140, 140, 140], dtype=np.uint8)
    upper_white = np.array([255, 255, 255], dtype=np.uint8)
    mask = cv2.inRange(resized, lower_white, upper_white)
    
    # 線の細り(かすれ)を防ぐため1pxだけ太らせる
    kernel = np.ones((2, 2), np.uint8)
    mask = cv2.dilate(mask, kernel, iterations=1)
    
    # テキストが存在するギリギリの範囲で自動クロップ(余白の排除)
    coords = cv2.findNonZero(mask)
    if coords is not None:
        x, y, w, h = cv2.boundingRect(coords)
        mask = mask[y:y+h, x:x+w]
        
    inverted = cv2.bitwise_not(mask)
    padded = cv2.copyMakeBorder(inverted, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
    
    if save_debug:
        os.makedirs("debug_crops", exist_ok=True)
        cv2.imwrite(f"debug_crops/{name}.jpg", padded)
        
    return padded

def preprocess_name_for_ocr(image, name, save_debug=False):
    if image.size == 0: return None
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)
    gray = cv2.resize(gray, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC)
    inverted = cv2.bitwise_not(gray)
    padded = cv2.copyMakeBorder(inverted, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
    return padded

def fuzzy_match_pokemon(ocr_text):
    text = re.sub(r'[^ァ-ンヴー2Z]', '', ocr_text)
    text = re.sub(r'[ハヘ]$', '', text)
    
    # if not text:
    #     return ""
        
    # matches = difflib.get_close_matches(text, POKEMON_ROSTER, n=1, cutoff=0.6)
    # if matches:
    #     return matches[0] 
    return text

def clean_pokemon_name(ocr_text):
    # 1. カタカナ、伸ばし棒、2、Z 以外の文字をすべて削除
    text = re.sub(r'[^ァ-ンヴー2Z]', '', ocr_text)
    
    # 2. 末尾のハ・ヘ(♂♀マークの誤認)を削除
    text = re.sub(r'[ハヘ]$', '', text)
    
    # 3. 濁点・半濁点をすべて清音に変換
    text = text.translate(SEION_MAPPING)
    
    return text

def extract_battle_info(img_path_or_array, save_debug=False):
    if isinstance(img_path_or_array, (str, Path)):
        img = cv2.imread(str(img_path_or_array))
    else:
        img = img_path_or_array

    original_h, original_w = img.shape[:2]
    if original_w != 1280 or original_h != 720:
        img = cv2.resize(img, (1280, 720))

    result_data = {}

    # ----- 1. 相手のHP (パーセント) -----
    opp_hp_zone = CROP_ZONES["opponent_hp"]
    crop_opp_hp = img[opp_hp_zone["y_start"]:opp_hp_zone["y_end"], opp_hp_zone["x_start"]:opp_hp_zone["x_end"]]
    processed_opp_hp = preprocess_opp_hp_for_ocr(crop_opp_hp, "opponent_hp", save_debug=save_debug)
    
    if processed_opp_hp is not None:
        opp_hp_text_result = reader.readtext(processed_opp_hp, allowlist='0123456789%', detail=0)
        if opp_hp_text_result:
            text = "".join(opp_hp_text_result)
            # %やゴミ記号を除去して数字だけを抽出
            digits = re.sub(r'\D', '', text)
            if digits:
                hp_val = int(digits)
                # 100以上の数値になった場合、末尾の文字を「%の誤認」として自動的に落とす強力な補正
                if hp_val > 100:
                    if str(hp_val).startswith('10') and len(str(hp_val)) == 3:
                        hp_val = 100  # 例: 105 -> 100
                    else:
                        hp_val = int(str(hp_val)[:-1]) # 例: 879 -> 87, 150 -> 15
                
                if 0 <= hp_val <= 100:
                    result_data["opponent_hp_percent"] = hp_val

    # ----- 2. 自分のHP (実数値) -----
    my_hp_zone = CROP_ZONES["my_hp"]
    crop_my_hp = img[my_hp_zone["y_start"]:my_hp_zone["y_end"], my_hp_zone["x_start"]:my_hp_zone["x_end"]]
    processed_my_hp = preprocess_my_hp_for_ocr(crop_my_hp, "my_hp", save_debug=save_debug)
    
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

    # ----- 3. ポケモン名 -----
    my_name_zone = CROP_ZONES["my_name"]
    crop_my_name = img[my_name_zone["y_start"]:my_name_zone["y_end"], my_name_zone["x_start"]:my_name_zone["x_end"]]
    processed_my_name = preprocess_name_for_ocr(crop_my_name, "my_name", save_debug=save_debug)
    if processed_my_name is not None:
        my_name_result = reader.readtext(processed_my_name, allowlist=NAME_ALLOWLIST, detail=0)
        if my_name_result:
            result_data["my_pokemon"] = clean_pokemon_name("".join(my_name_result))

    opp_name_zone = CROP_ZONES["opponent_name"]
    crop_opp_name = img[opp_name_zone["y_start"]:opp_name_zone["y_end"], opp_name_zone["x_start"]:opp_name_zone["x_end"]]
    processed_opp_name = preprocess_name_for_ocr(crop_opp_name, "opponent_name", save_debug=save_debug)
    if processed_opp_name is not None:
        opp_name_result = reader.readtext(processed_opp_name, allowlist=NAME_ALLOWLIST, detail=0)
        if opp_name_result:
            result_data["opponent_pokemon"] = clean_pokemon_name("".join(opp_name_result))

    return result_data

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Extract battle info from screenshots.')
    parser.add_argument('target_path', type=str, help='Path to an image file or a directory containing images.')
    args = parser.parse_args()

    target = Path(args.target_path)

    if target.is_file():
        result = extract_battle_info(target, save_debug=True)
        print("Extraction Result:")
        for key, value in result.items(): print(f"  {key}: {value}")

    elif target.is_dir():
        valid_extensions = ['.png', '.jpg', '.jpeg']
        image_files = [f for f in target.iterdir() if f.suffix.lower() in valid_extensions]
        for img_file in sorted(image_files):
            print(f"\n[->] Analyzing: {img_file.name}")
            try:
                result = extract_battle_info(img_file, save_debug=False)
                for key, value in result.items(): print(f"  {key}: {value}")
            except Exception as e: print(f"  Failed to process {img_file.name}: {e}")