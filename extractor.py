import cv2
import numpy as np
import easyocr
import re
import os
import argparse
import warnings
import time
import json
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

SEION_MAPPING = str.maketrans(
    'ガギグゲゴザジズゼゾダヂヅデドバビブベボパピプペポヴ',
    'カキクケコサシスセソタチツテトハヒフヘホハヒフヘホウ'
)

# クロプゾーン定義
CROP_ZONES = {
    "my_hp": {"y_start": 665, "y_end": 715, "x_start": 130, "x_end": 280},
    "my_name": {"y_start": 608, "y_end": 648, "x_start": 100, "x_end": 300},
    "opponent_hp": {"y_start": 70, "y_end": 120, "x_start": 1120, "x_end": 1250},
    "opponent_name": {"y_start": 25, "y_end": 65, "x_start": 1040, "x_end": 1220},
    # メッセージウィンドウの検知用領域 (画面下部の黒帯部分を想定)
    "message_window": {"y_start": 550, "y_end": 650, "x_start": 300, "x_end": 980} 
}

# 辞書: キーワードによるイベント定義
EVENT_DICTIONARY = {
    "weather_events": {
        "sandstorm_start": {
            "keywords": ["砂あらし", "吹き始め"],
            "action": "set_weather",
            "value": "sandstorm",
            "duration": 5
        },
        "rain_start": {
            "keywords": ["雨", "降り出し"],
            "action": "set_weather",
            "value": "rain",
            "duration": 5
        }
    },
    "status_events": {
        "substitute_on": {
            "keywords": ["身代わり", "現れ"],
            "action": "set_status",
            "status_key": "substitute",
            "value": True
        }
    },
    "action_events": {
         "attack": {
             "keywords": ["襲う"],
             "action": "log_attack"
         }
    }
}

# ==============================================================================
# バトル状態管理クラス
# ==============================================================================
class BattleState:
    def __init__(self):
        self.field = {
            "weather": "none",
            "weather_turns_left": 0
        }
        self.player = {
            "active_pokemon": None,
            "hp_percent": None,
            "hp_raw": None,
            "substitute": False
        }
        self.opponent = {
            "active_pokemon": None,
            "hp_percent": None,
            "substitute": False
        }
        self.last_message = "" # 重複排除用
    
    def update_basic_info(self, info):
        if "my_pokemon" in info:
            self.player["active_pokemon"] = info["my_pokemon"]
        if "my_hp_percent" in info:
            self.player["hp_percent"] = info["my_hp_percent"]
        if "my_hp_raw" in info:
            self.player["hp_raw"] = info["my_hp_raw"]
            
        if "opponent_pokemon" in info:
            self.opponent["active_pokemon"] = info["opponent_pokemon"]
        if "opponent_hp_percent" in info:
            self.opponent["hp_percent"] = info["opponent_hp_percent"]

    def apply_event(self, event_def, raw_text):
        action = event_def.get("action")
        
        # ターゲットの推定 (テキストに「相手の」が含まれていればopponent、なければplayerと仮定)
        target = "opponent" if "相手" in raw_text else "player"

        if action == "set_weather":
            self.field["weather"] = event_def["value"]
            self.field["weather_turns_left"] = event_def["duration"]
            print(f"[State Update] 天候が {event_def['value']} になりました。")
            
        elif action == "set_status":
            status_key = event_def["status_key"]
            value = event_def["value"]
            if target == "opponent":
                self.opponent[status_key] = value
                print(f"[State Update] 相手の {status_key} が {value} になりました。")
            else:
                self.player[status_key] = value
                print(f"[State Update] 自分の {status_key} が {value} になりました。")
                
        elif action == "log_attack":
            print(f"[State Update] 攻撃アクションを検知: {raw_text}")

    def print_state(self):
        print("--- Current Battle State ---")
        print(f"Field: {self.field}")
        print(f"Player: {self.player}")
        print(f"Opponent: {self.opponent}")
        print("----------------------------")
        
    def to_dict(self):
         return {
             "field": self.field,
             "player": self.player,
             "opponent": self.opponent
         }

# グローバルなバトル状態
battle_state = BattleState()

# ==============================================================================
# 画像処理・抽出関数
# ==============================================================================

def clean_pokemon_name(ocr_text):
    text = re.sub(r'[^ァ-ンヴー2Z]', '', ocr_text)
    text = re.sub(r'[ハヘ]$', '', text)
    text = text.translate(SEION_MAPPING)
    return text

def preprocess_for_ocr(image, invert=True):
    if image is None or image.size == 0: return None
    resized = cv2.resize(image, None, fx=3.0, fy=3.0, interpolation=cv2.INTER_CUBIC)
    
    if len(resized.shape) == 3:
        gray = cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY)
    else:
        gray = resized
        
    # 文字を白、背景を黒にするかどうかの反転処理
    if invert:
        # 明るい文字を抽出する想定
        _, thresh = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY)
    else:
         _, thresh = cv2.threshold(gray, 100, 255, cv2.THRESH_BINARY_INV)
         
    padded = cv2.copyMakeBorder(thresh, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=0)
    return padded

# メッセージウィンドウが開いているか検知する関数
def detect_message_window(img):
    zone = CROP_ZONES["message_window"]
    crop = img[zone["y_start"]:zone["y_end"], zone["x_start"]:zone["x_end"]]
    
    # メッセージウィンドウ特有の暗い背景色（または特定のUI要素）を検知
    # ここでは簡易的に、暗い領域が一定割合以上あるかで判定
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    _, thresh = cv2.threshold(gray, 50, 255, cv2.THRESH_BINARY_INV) # 暗い部分を白にする
    
    dark_ratio = cv2.countNonZero(thresh) / (crop.shape[0] * crop.shape[1])
    
    # 閾値は実際の画面に合わせて調整が必要
    if dark_ratio > 0.6: 
        return True, crop
    return False, None

def parse_message_text(text):
    if not text:
        return
        
    # 重複排除
    if text == battle_state.last_message:
        return
        
    battle_state.last_message = text
    print(f"[Event Parser] メッセージを解析: {text}")
    
    # 辞書と照合
    event_triggered = False
    for category, events in EVENT_DICTIONARY.items():
        for event_name, event_def in events.items():
            # すべてのキーワードが含まれているかチェック
            if all(keyword in text for keyword in event_def["keywords"]):
                print(f"  -> イベント発火: {event_name}")
                battle_state.apply_event(event_def, text)
                event_triggered = True
                break # 一つのメッセージで複数の競合イベントが発火するのを防ぐ
        if event_triggered:
            break

def process_frame(img, process_basic_info=True, process_message=True):
    original_h, original_w = img.shape[:2]
    if original_w != 1280 or original_h != 720:
        img = cv2.resize(img, (1280, 720))

    result_data = {}

    # 1. 基本情報の抽出 (HP, 名前) - 定期実行または初期待機時
    if process_basic_info:
        # Opponent Name
        opp_name_zone = CROP_ZONES["opponent_name"]
        crop_opp_name = img[opp_name_zone["y_start"]:opp_name_zone["y_end"], opp_name_zone["x_start"]:opp_name_zone["x_end"]]
        processed_opp_name = preprocess_for_ocr(crop_opp_name, invert=False)
        if processed_opp_name is not None:
            opp_name_result = reader.readtext(processed_opp_name, allowlist=NAME_ALLOWLIST, detail=0)
            if opp_name_result:
                result_data["opponent_pokemon"] = clean_pokemon_name("".join(opp_name_result))

        # My Name
        my_name_zone = CROP_ZONES["my_name"]
        crop_my_name = img[my_name_zone["y_start"]:my_name_zone["y_end"], my_name_zone["x_start"]:my_name_zone["x_end"]]
        processed_my_name = preprocess_for_ocr(crop_my_name, invert=False)
        if processed_my_name is not None:
            my_name_result = reader.readtext(processed_my_name, allowlist=NAME_ALLOWLIST, detail=0)
            if my_name_result:
                result_data["my_pokemon"] = clean_pokemon_name("".join(my_name_result))
                
        # 状態の更新
        battle_state.update_basic_info(result_data)

    # 2. メッセージウィンドウの検知と処理
    window_detected = False
    if process_message:
        is_open, crop_window = detect_message_window(img)
        if is_open:
            window_detected = True
            # メッセージ領域のOCR処理
            # 白文字を想定
            processed_msg = preprocess_for_ocr(crop_window, invert=True)
            if processed_msg is not None:
                # 日本語の文章として読み取るため allowlist は設定しない
                msg_result = reader.readtext(processed_msg, detail=0)
                if msg_result:
                    full_text = "".join(msg_result).replace(" ", "")
                    parse_message_text(full_text)

    return battle_state.to_dict(), window_detected

# バッチ処理用ラッパー
def process_batch(target_path):
    target = Path(target_path)
    
    if target.is_file():
        img = cv2.imread(str(target))
        print(f"\n[->] Analyzing single image: {target.name}")
        state, window = process_frame(img, process_basic_info=True, process_message=True)
        battle_state.print_state()
        
    elif target.is_dir():
        valid_extensions = ['.png', '.jpg', '.jpeg']
        image_files = sorted([f for f in target.iterdir() if f.suffix.lower() in valid_extensions])
        
        for img_file in image_files:
            img = cv2.imread(str(img_file))
            print(f"\n[->] Processing frame: {img_file.name}")
            state, window = process_frame(img, process_basic_info=True, process_message=True)
            if window:
                print("  [Info] メッセージウィンドウを検知")
            battle_state.print_state()

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        process_batch(sys.argv[1])
    else:
        print("Usage: python advanced_extractor.py <image_or_directory_path>")