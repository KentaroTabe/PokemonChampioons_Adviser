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
    "right_popup_ocr": {"y_start": 300, "y_end": 440, "x_start": 850, "x_end": 1180} 
}

# テンプレート画像（アイコン）の読み込み
TEMPLATE_DIR = Path('images/templetes')
pokemon_templates = {}

def load_templates():
    if not TEMPLATE_DIR.exists():
        print(f"[Warning] Template directory '{TEMPLATE_DIR}' not found. Matching disabled.")
        return
    valid_exts = ['.png', '.jpg', '.jpeg']
    for file_path in TEMPLATE_DIR.iterdir():
        if file_path.suffix.lower() in valid_exts:
            # 0.png（はてなマーク）などの不要なプレースホルダーを除外
            if file_path.stem == '0':
                continue
                
            # IMREAD_UNCHANGED でアルファチャンネルも含めて読み込む
            tmpl = cv2.imread(str(file_path), cv2.IMREAD_UNCHANGED)
            if tmpl is not None and len(tmpl.shape) == 3 and tmpl.shape[2] == 4:
                # 透過部分の余白をトリミングして本体（バウンディングボックス）だけにする
                alpha = tmpl[:, :, 3]
                x, y, w, h = cv2.boundingRect(alpha)
                if w > 0 and h > 0:
                    pokemon_templates[file_path.stem] = tmpl[y:y+h, x:x+w]
    print(f"Loaded {len(pokemon_templates)} templates for matching.")

# 起動時にテンプレートをロード
load_templates()

# ==============================================================================
# 画像処理・抽出・マッチング関数
# ==============================================================================

def match_pokemon_icon(crop_img):
    if not pokemon_templates or crop_img is None or crop_img.size == 0:
        return None
        
    best_match = None
    best_score = -1
    
    # マルチスケール探索用のテンプレート高さ (ピクセル)
    # 処理速度向上のため、想定されるサイズ(40, 50, 60)の3段階に絞り込み
    target_heights = [40, 50, 60]
    
    for name, tmpl in pokemon_templates.items():
        tmpl_h, tmpl_w = tmpl.shape[:2]
        
        for th in target_heights:
            if th >= crop_img.shape[0]: 
                continue 
                
            # アスペクト比を維持してテンプレートをリサイズ
            scale = th / tmpl_h
            tw = int(tmpl_w * scale)
            if tw >= crop_img.shape[1] or tw == 0: 
                continue
                
            resized_tmpl = cv2.resize(tmpl, (tw, th))
            
            tmpl_bgr = resized_tmpl[:, :, :3]
            alpha_channel = resized_tmpl[:, :, 3]
            
            # TM_CCORR_NORMED用の3チャンネルマスク
            alpha_mask = cv2.merge([alpha_channel, alpha_channel, alpha_channel])
            
            try:
                # BGRカラー＋アルファマスクでマッチング
                res = cv2.matchTemplate(crop_img, tmpl_bgr, cv2.TM_CCORR_NORMED, mask=alpha_mask)
                _, max_val, _, _ = cv2.minMaxLoc(res)
                
                if max_val > best_score:
                    best_score = max_val
                    best_match = name
            except Exception as e:
                continue
                
    # 閾値を0.6から0.90へ大幅に引き上げ。正解以外のノイズ誤爆を完全にシャットアウトする
    if best_score > 0.90:
        return best_match
    return None

def preprocess_name_for_ocr(image):
    if image is None or image.size == 0: return None
    resized = cv2.resize(image, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC)
    # HSV変換して「白文字」だけを綺麗に抽出する
    hsv = cv2.cvtColor(resized, cv2.COLOR_BGR2HSV)
    lower_white = np.array([0, 0, 150], dtype=np.uint8)
    upper_white = np.array([180, 60, 255], dtype=np.uint8)
    mask = cv2.inRange(hsv, lower_white, upper_white)
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
    """メッセージウィンドウ専用のグレースケール前処理"""
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
        
    # 重複排除
    if source_type == "message":
        if cleaned_text == battle_state.last_message:
            return
        battle_state.last_message = cleaned_text
    elif source_type == "left_popup":
        if cleaned_text == battle_state.last_left_popup:
            return
        battle_state.last_left_popup = cleaned_text
    elif source_type == "right_popup":
        if cleaned_text == battle_state.last_right_popup:
            return
        battle_state.last_right_popup = cleaned_text

    print(f"[Event Parser] {source_type} を解析: {cleaned_text}")
    
    # テキストを比較用に清音化
    text_seion = cleaned_text.translate(SEION_MAPPING)
    
    # 辞書と照合
    event_triggered = False
    for category, events in EVENT_DICTIONARY.items():
        for event_name, event_def in events.items():
            # 辞書のキーワードも清音化して照合
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

    result_data = {}

    # 1. 基本情報の抽出 (HP, ポケモンアイコン照合)
    if process_basic_info:
        # Opponent Icon & Name
        opp_icon_zone = CROP_ZONES["opponent_icon"]
        crop_opp_icon = img[opp_icon_zone["y_start"]:opp_icon_zone["y_end"], opp_icon_zone["x_start"]:opp_icon_zone["x_end"]]
        result_data["opp_species"] = match_pokemon_icon(crop_opp_icon, "opp")

        opp_name_zone = CROP_ZONES["opponent_name"]
        crop_opp_name = img[opp_name_zone["y_start"]:opp_name_zone["y_end"], opp_name_zone["x_start"]:opp_name_zone["x_end"]]
        processed_opp_name = preprocess_name_for_ocr(crop_opp_name)
        if processed_opp_name is not None:
            name_res = reader.readtext(processed_opp_name, detail=0)
            if name_res:
                result_data["opp_display_name"] = "".join(name_res).replace(" ", "")

        # My Icon & Name
        my_icon_zone = CROP_ZONES["my_icon"]
        crop_my_icon = img[my_icon_zone["y_start"]:my_icon_zone["y_end"], my_icon_zone["x_start"]:my_icon_zone["x_end"]]
        result_data["my_species"] = match_pokemon_icon(crop_my_icon, "my")

        my_name_zone = CROP_ZONES["my_name"]
        crop_my_name = img[my_name_zone["y_start"]:my_name_zone["y_end"], my_name_zone["x_start"]:my_name_zone["x_end"]]
        processed_my_name = preprocess_name_for_ocr(crop_my_name)
        if processed_my_name is not None:
            name_res = reader.readtext(processed_my_name, detail=0)
            if name_res:
                result_data["my_display_name"] = "".join(name_res).replace(" ", "")
                
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

        # 状態の更新
        battle_state.update_basic_info(result_data)

    # 2. メッセージウィンドウの検知と処理
    window_detected = False
    if process_message:
        trigger_ocr, stable_img = message_detector.update(img)
        
        if trigger_ocr and stable_img is not None:
            window_detected = True
            msg_result = reader.readtext(stable_img, detail=0)
            if msg_result:
                full_text = "".join(msg_result).replace(" ", "")
                parse_text(full_text, source_type="message")

    # 3. 左ポップアップウィンドウの検知と処理
    if process_message:
        trigger_left_ocr, stable_left_img = left_popup_detector.update(img)
        
        if trigger_left_ocr and stable_left_img is not None:
            window_detected = True
            left_result = reader.readtext(stable_left_img, detail=0)
            if left_result:
                full_text = "".join(left_result).replace(" ", "")
                parse_text(full_text, source_type="left_popup")

    # 4. 右ポップアップウィンドウの検知と処理
    if process_message:
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
            for i in range(5):
                state, window = process_frame(img, process_basic_info=(i==0), process_message=True)
            if window:
                print("  [Info] ウィンドウを検知")
            battle_state.print_state()
            print(f"  [Time] 処理時間: {time.time() - start_time:.3f}秒")

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